"""核心数据模型：题目、选项、AI 建议答案。

答案结构严格对齐雨课堂网页端实际的提交格式：
  - 单选 / 投票  -> ["A"]
  - 多选         -> ["A", "C"]
  - 填空         -> ["第一空", "第二空"]
  - 主观题       -> {"content": "...", "pics": []}
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .yuketang.constants import PROBLEM_TYPE_BY_CODE


class ProblemType(str, Enum):
    UNKNOWN = "unknown"
    SINGLE_CHOICE = "single-choice"
    MULTIPLE_CHOICE = "multiple-choice"
    POLL = "poll"
    FILL_BLANK = "fill-blank"
    SUBJECTIVE = "subjective"

    @property
    def label(self) -> str:
        return TYPE_LABELS[self]


TYPE_LABELS: dict[ProblemType, str] = {
    ProblemType.UNKNOWN: "未知题型",
    ProblemType.SINGLE_CHOICE: "单选题",
    ProblemType.MULTIPLE_CHOICE: "多选题",
    ProblemType.POLL: "投票题",
    ProblemType.FILL_BLANK: "填空题",
    ProblemType.SUBJECTIVE: "主观题",
}

#: 需要选字母的题型
CHOICE_TYPES = frozenset(
    {ProblemType.SINGLE_CHOICE, ProblemType.MULTIPLE_CHOICE, ProblemType.POLL}
)
#: 允许选多个字母的题型
MULTI_LETTER_TYPES = frozenset({ProblemType.MULTIPLE_CHOICE})

_ALIASES: dict[str, ProblemType] = {
    "single_choice": ProblemType.SINGLE_CHOICE,
    "singlechoice": ProblemType.SINGLE_CHOICE,
    "single": ProblemType.SINGLE_CHOICE,
    "radio": ProblemType.SINGLE_CHOICE,
    "multiple_choice": ProblemType.MULTIPLE_CHOICE,
    "multiplechoice": ProblemType.MULTIPLE_CHOICE,
    "multiple": ProblemType.MULTIPLE_CHOICE,
    "multi": ProblemType.MULTIPLE_CHOICE,
    "checkbox": ProblemType.MULTIPLE_CHOICE,
    "poll": ProblemType.POLL,
    "vote": ProblemType.POLL,
    "fill_blank": ProblemType.FILL_BLANK,
    "fillblank": ProblemType.FILL_BLANK,
    "fill": ProblemType.FILL_BLANK,
    "blank": ProblemType.FILL_BLANK,
    "subjective": ProblemType.SUBJECTIVE,
    "essay": ProblemType.SUBJECTIVE,
    "short_answer": ProblemType.SUBJECTIVE,
    "unknown": ProblemType.UNKNOWN,
}


#: 选项行里允许出现的字母。雨课堂偶尔会出 5 个以上选项的题，
#: 早期只写了 A-H，导致第 9 个及其后的选项整行被丢掉，这里统一放宽到 A-Z。
OPTION_LETTERS = "A-Za-z"

#: 「A. 文本 / B、文本 / C）文本」这类选项行的正则。
#: Python 侧（adhoc 手动粘贴）与注入页面的 JS（DOM 兜底抓题）共用同一份定义，避免规则漂移。
OPTION_LINE_PATTERN = rf"^([{OPTION_LETTERS}])\s*[.、．)）:：]\s*(\S[\s\S]*)$"


def letter_for(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA。"""
    if index < 0:
        raise ValueError("index must be non-negative")
    out = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        out = string.ascii_uppercase[remainder] + out
    return out


def index_for_letter(letter: str) -> int | None:
    """A -> 0, Z -> 25, AA -> 26；非法输入返回 None。"""
    text = letter.strip().upper()
    if not text or not text.isalpha():
        return None
    value = 0
    for char in text:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def parse_problem_type(value: Any) -> ProblemType:
    """兼容数字编码、下划线命名、连字符命名。"""
    if isinstance(value, ProblemType):
        return value
    if isinstance(value, bool):
        return ProblemType.UNKNOWN
    if isinstance(value, (int, float)):
        return _code_to_type(int(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ProblemType.UNKNOWN
        if text.isdigit():
            return _code_to_type(int(text))
        normalized = text.lower().replace("-", "_").replace(" ", "_")
        if normalized in _ALIASES:
            return _ALIASES[normalized]
        try:
            return ProblemType(text.lower())
        except ValueError:
            return ProblemType.UNKNOWN
    return ProblemType.UNKNOWN


def _code_to_type(code: int) -> ProblemType:
    name = PROBLEM_TYPE_BY_CODE.get(code)
    if not name:
        return ProblemType.UNKNOWN
    try:
        return ProblemType(name)
    except ValueError:
        return ProblemType.UNKNOWN


_TRUTHY_OPTIONS = frozenset(
    {"正确", "错误", "对", "错", "是", "否", "t", "f", "true", "false", "√", "×"}
)


def infer_problem_type(prompt: str, options: list["Option"]) -> ProblemType:
    """按题面关键字 + 选项形态推断题型（用于拿不到 problemType 的场景，如 DOM 抓取）。"""
    head = prompt[:160]
    if any(word in head for word in ("多选", "多项选择")):
        return ProblemType.MULTIPLE_CHOICE
    if any(word in head for word in ("单选", "单项选择", "判断")):
        return ProblemType.SINGLE_CHOICE
    if "投票" in head:
        return ProblemType.POLL
    if "填空" in head:
        return ProblemType.FILL_BLANK
    if any(word in head for word in ("简答", "简述", "论述", "主观", "问答", "名词解释", "计算", "证明")):
        return ProblemType.SUBJECTIVE
    if len(options) == 2 and all(
        option.text.strip().lower() in _TRUTHY_OPTIONS for option in options
    ):
        return ProblemType.SINGLE_CHOICE
    if len(options) >= 2:
        return ProblemType.SINGLE_CHOICE
    return ProblemType.UNKNOWN


@dataclass
class Option:
    """一个选项。index 为 0 基下标，letter 为 UI 上显示的 A/B/C。"""

    index: int
    text: str

    @property
    def letter(self) -> str:
        return letter_for(self.index)

    def __str__(self) -> str:
        return f"{self.letter}. {self.text}"


@dataclass
class Problem:
    """一道随堂习题。"""

    id: str
    type: ProblemType = ProblemType.UNKNOWN
    prompt: str = ""
    options: list[Option] = field(default_factory=list)
    blanks: list[str] = field(default_factory=list)
    lesson_id: str = ""
    presentation_id: str = ""
    slide_id: str = ""
    image_url: str = ""
    source: str = "network"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """跨来源去重用的稳定键。"""
        return f"{self.lesson_id or '-'}:{self.id}"

    @property
    def already_answered(self) -> bool:
        result = self.raw.get("result")
        return result not in (None, "", [], {})

    @property
    def needs_vision(self) -> bool:
        """题干为空说明题目内容画在课件图片里，需要视觉模型。"""
        return not self.prompt.strip() and bool(self.image_url)

    def letter_to_option(self, letter: str) -> Option | None:
        index = index_for_letter(letter)
        if index is None or index >= len(self.options):
            return None
        return self.options[index]

    def find_option_by_text(self, text: str) -> Option | None:
        """按选项正文反查选项，用于把模型给出的文字答案映射回字母。"""
        needle = _normalize_text(text)
        if not needle:
            return None
        for option in self.options:
            if _normalize_text(option.text) == needle:
                return option
        for option in self.options:
            candidate = _normalize_text(option.text)
            if candidate and (candidate in needle or needle in candidate):
                return option
        return None

    @classmethod
    def from_raw(
        cls,
        raw: dict[str, Any],
        *,
        lesson_id: str = "",
        presentation_id: str = "",
        slide_id: str = "",
        image_url: str = "",
        source: str = "network",
    ) -> "Problem | None":
        """从雨课堂下发的题目 JSON 构造 Problem，缺少 id 时返回 None。"""
        if not isinstance(raw, dict):
            return None
        problem_id = _string_id(
            raw.get("problemId") or raw.get("problem_id") or raw.get("id")
        )
        if not problem_id:
            return None

        raw_options = raw.get("options")
        if not isinstance(raw_options, list):
            raw_options = raw.get("answers")
        options: list[Option] = []
        if isinstance(raw_options, list):
            for item in raw_options:
                text = _option_text(item)
                if text:
                    options.append(Option(index=len(options), text=text))

        blanks = [
            _as_text(item)
            for item in (raw.get("blanks") or [])
            if _as_text(item)
        ]
        prompt = _as_text(
            raw.get("prompt") or raw.get("content") or raw.get("body") or raw.get("title")
        )

        return cls(
            id=problem_id,
            type=parse_problem_type(
                raw.get("problemType") or raw.get("problem_type") or raw.get("type")
            ),
            prompt=prompt,
            options=options,
            blanks=blanks,
            lesson_id=lesson_id,
            presentation_id=presentation_id,
            slide_id=slide_id,
            image_url=image_url,
            source=source,
            raw=dict(raw),
        )


@dataclass
class Suggestion:
    """DeepSeek 给出的建议答案。"""

    problem_id: str
    letters: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    explanation: str = ""
    confidence: float | None = None
    failure_reason: str | None = None
    model: str = ""
    elapsed: float = 0.0
    raw: str = ""

    @property
    def answerable(self) -> bool:
        return bool(self.letters or self.texts)

    def submit_payload(self, problem: Problem) -> Any:
        """构造提交给雨课堂的 result 字段。"""
        if problem.type in CHOICE_TYPES:
            return list(self.letters)
        if problem.type is ProblemType.FILL_BLANK:
            return list(self.texts)
        if problem.type is ProblemType.SUBJECTIVE:
            return {"content": "\n".join(self.texts), "pics": []}
        # 未知题型：有字母按字母交，否则按文本交
        return list(self.letters) if self.letters else list(self.texts)

    def display(self, problem: Problem) -> str:
        """人类可读的答案文本。"""
        if problem.type in CHOICE_TYPES:
            if not self.letters:
                return "(无)"
            parts = []
            for letter in self.letters:
                option = problem.letter_to_option(letter)
                parts.append(f"{letter}. {option.text}" if option else letter)
            return " / ".join(parts)
        return " | ".join(self.texts) if self.texts else "(无)"

    def option_texts(self, problem: Problem) -> list[str]:
        """dom 模式点击选项时需要的选项正文。"""
        texts: list[str] = []
        for letter in self.letters:
            option = problem.letter_to_option(letter)
            if option is not None:
                texts.append(option.text)
        return texts


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _string_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _option_text(value: Any) -> str:
    """选项既可能是纯字符串，也可能是 {content|text|value} 对象。"""
    if isinstance(value, dict):
        return _as_text(
            value.get("content") or value.get("text") or value.get("value") or value.get("title")
        )
    return _as_text(value)


def _normalize_text(value: str) -> str:
    return "".join(value.split()).strip().lower()
