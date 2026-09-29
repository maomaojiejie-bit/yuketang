"""提示词构造与模型输出解析。

提示词结构对齐雨课堂网页端实际使用的格式（题型 / 题目 / 选项 / 答案格式），
并把答案归一化成雨课堂能接受的 result 结构。
"""

from __future__ import annotations

import json
import re
import string
from typing import Any

from ..models import (
    CHOICE_TYPES,
    MULTI_LETTER_TYPES,
    Problem,
    ProblemType,
    Suggestion,
    index_for_letter,
    letter_for,
)

SYSTEM_PROMPT = (
    "你是一个学习辅助助手。请仔细分析题目，然后只输出一个 JSON 对象，"
    "字段为 answer、explanation、confidence、failureReason。"
    "confidence 是 0 到 1 之间的小数；无法确定答案时 answer 设为 null 并填写 failureReason。"
    "不要输出 JSON 以外的任何内容，也不要声称执行了任何提交操作。"
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_ANSWER_LINE_RE = re.compile(r"答案\s*[:：]\s*([\s\S]*?)(?=\n\s*(?:解析|解释|说明)\s*[:：]|$)")
_EXPLANATION_RE = re.compile(r"(?:解析|解释|说明)\s*[:：]\s*([\s\S]*)")


def answer_format_hint(problem: Problem) -> str:
    """告诉模型该用哪种答案格式，避免拿到无法提交的自然语言。"""
    if problem.type in (ProblemType.SINGLE_CHOICE, ProblemType.POLL):
        return 'answer 必须是一个大写字母字符串，例如 "A"。'
    if problem.type is ProblemType.MULTIPLE_CHOICE:
        return 'answer 必须是大写字母组成的数组，例如 ["A", "C"]，按字母升序。'
    if problem.type is ProblemType.FILL_BLANK:
        return "answer 必须是字符串数组，每个元素按顺序对应一个空。"
    if problem.type is ProblemType.SUBJECTIVE:
        return "answer 必须是完整的回答字符串，条理清晰。"
    if problem.options:
        return 'answer 使用选项字母，例如 "A" 或 ["A", "C"]。'
    return "answer 使用能直接作答的字符串。"


def format_options(problem: Problem) -> str:
    return "\n".join(f"{option.letter}. {option.text}" for option in problem.options)


def build_messages(
    problem: Problem,
    *,
    extra_prompt: str = "",
    images: list[str] | None = None,
) -> list[dict[str, Any]]:
    """构造 chat/completions 的 messages。"""
    parts = [f"题型：{problem.type.label}"]
    parts.append(f"题目：{problem.prompt.strip() or '（题干位于课件图片中，请结合图片作答）'}")
    if problem.options:
        parts.append(f"选项：\n{format_options(problem)}")
    if problem.blanks:
        blanks = "\n".join(f"第{index}空：{text}" for index, text in enumerate(problem.blanks, 1))
        parts.append(f"待填空位：\n{blanks}")
    parts.append(answer_format_hint(problem))
    if extra_prompt.strip():
        parts.append(f"额外要求：{extra_prompt.strip()}")

    text = "\n\n".join(part for part in parts if part)
    usable_images = [url for url in (images or []) if url]
    if usable_images:
        content: Any = [
            {"type": "image_url", "image_url": {"url": url}} for url in usable_images
        ]
        content.append({"type": "text", "text": text})
    else:
        content = text

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def parse_suggestion(
    problem: Problem,
    raw_text: str,
    *,
    model: str = "",
    elapsed: float = 0.0,
) -> Suggestion:
    """把模型输出解析成 Suggestion，兼容 JSON 与「答案：X」两种风格。"""
    suggestion = Suggestion(problem_id=problem.id, model=model, elapsed=elapsed, raw=raw_text)

    payload = _extract_json_object(raw_text)
    if payload is not None:
        letters, texts = normalize_answer(problem, payload.get("answer"))
        suggestion.letters = letters
        suggestion.texts = texts
        suggestion.explanation = _as_text(payload.get("explanation"))
        suggestion.confidence = _normalize_confidence(payload.get("confidence"))
        reason = payload.get("failureReason")
        if isinstance(reason, str) and reason.strip():
            suggestion.failure_reason = reason.strip()
        if not suggestion.answerable and not suggestion.failure_reason:
            suggestion.failure_reason = "模型返回的 answer 为空"
        return suggestion

    # 退化路径：模型没按 JSON 输出
    answer_match = _ANSWER_LINE_RE.search(raw_text)
    text = (answer_match.group(1) if answer_match else raw_text.splitlines()[0] if raw_text else "").strip()
    letters, texts = normalize_answer(problem, text)
    suggestion.letters = letters
    suggestion.texts = texts
    explanation_match = _EXPLANATION_RE.search(raw_text)
    if explanation_match:
        suggestion.explanation = explanation_match.group(1).strip()
    suggestion.confidence = _normalize_confidence(
        re.search(r"置信度\s*[:：]\s*(\d+(?:\.\d+)?%?)", raw_text)
    )
    if not suggestion.answerable:
        suggestion.failure_reason = "模型输出无法解析出答案"
    return suggestion


def normalize_answer(problem: Problem, value: Any) -> tuple[list[str], list[str]]:
    """把模型给出的答案归一化成 (字母列表, 文本列表)。"""
    if value is None:
        return [], []

    if isinstance(value, dict):
        content = value.get("content")
        if not content:
            return [], []
        return [], _split_text(_as_text(content))

    if isinstance(value, (list, tuple)):
        items = [_as_text(item) for item in value]
        items = [item for item in items if item]
        if not items:
            return [], []
        if problem.type in CHOICE_TYPES:
            return _pick_letters(problem, ",".join(items)), []
        return [], items

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)

    if not isinstance(value, str):
        return [], []

    text = value.strip()
    if not text:
        return [], []

    if problem.type in CHOICE_TYPES:
        letters = _pick_letters(problem, text)
        if letters:
            return letters, []
        option = problem.find_option_by_text(text)
        return ([option.letter] if option else []), []

    if problem.type is ProblemType.FILL_BLANK:
        return [], _split_text(text)

    if problem.type is ProblemType.SUBJECTIVE:
        return [], [text]

    # 未知题型：先试字母，再当文本
    letters = _pick_letters(problem, text)
    if letters:
        return letters, []
    return [], _split_text(text)


def _pick_letters(problem: Problem, text: str) -> list[str]:
    """从文本中提取合法选项字母。"""
    upper = text.upper()
    candidates = [char for char in upper if char in string.ascii_uppercase]
    if not candidates:
        return []
    ordered: list[str] = []
    for char in candidates:
        if char not in ordered:
            ordered.append(char)
    ordered.sort(key=lambda item: index_for_letter(item) or 0)

    if problem.options:
        valid = {letter_for(index) for index in range(len(problem.options))}
        # 多字母题型的选项可能超过 Z，但实际不会；这里做保守过滤
        letters_only = [item for item in ordered if item in valid]
    else:
        letters_only = [item for item in ordered if len(item) == 1]

    if not letters_only:
        return []
    if problem.type in MULTI_LETTER_TYPES:
        return letters_only
    return [letters_only[0]]


def _split_text(text: str) -> list[str]:
    """填空题答案切分：按换行/中英文逗号分号/顿号切。"""
    if not text:
        return []
    parts = re.split(r"[\n,，;；、]+", text)
    values = [part.strip() for part in parts]
    return [value for value in values if value]


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象。"""
    if not text:
        return None
    cleaned = _CODE_FENCE_RE.sub("", text.strip())
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    while start != -1:
        end = _matching_brace(cleaned, start)
        if end == -1:
            return None
        chunk = cleaned[start : end + 1]
        try:
            loaded = json.loads(chunk)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{", start + 1)
    return None


def _matching_brace(text: str, start: int) -> int:
    """找到与 text[start] 匹配的右花括号位置（跳过字符串内的括号）。"""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _normalize_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        percent = text.endswith("%")
        text = text.rstrip("%").strip()
        try:
            numeric = float(text)
        except ValueError:
            return None
        if percent:
            numeric /= 100
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
    else:
        return None
    if numeric > 1 and numeric <= 100:
        numeric /= 100
    return max(0.0, min(1.0, numeric))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""
