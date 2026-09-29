"""解析手动粘贴的题目文本，便于不打开浏览器也能验证 AI 链路。

支持的格式（选项行以 A. / A、/ A） 开头）：

    单选 下列关于…的说法正确的是
    A. 选项一
    B. 选项二
"""

from __future__ import annotations

import hashlib
import re

from .models import OPTION_LINE_PATTERN, Option, Problem, ProblemType, infer_problem_type

#: 选项行形如 A. / A、/ A）；字母范围见 models.OPTION_LINE_PATTERN（A-Z）
OPTION_LINE_RE = re.compile(OPTION_LINE_PATTERN)
BLANK_LINE_RE = re.compile(r"^\s*(?:第\s*\d+\s*空|_+|\{\d+\})\s*[:：]?\s*(.*)$")
TYPE_WORDS = r"(单选|多选|判断|填空|投票|简答|简述|论述|主观|问答|名词解释|计算|证明)"
TYPE_HINT_RE = re.compile(rf"^\s*(?:题型\s*[:：]\s*)?{TYPE_WORDS}\s*题?\s*$")
LEADING_TYPE_RE = re.compile(rf"^\s*(?:题型\s*[:：]\s*)?{TYPE_WORDS}\s*题?\s*(?:[:：、.．)）]\s*|\s+)")


def parse_adhoc_problem(text: str) -> Problem:
    """把一段文本解析成 Problem。"""
    lines = [line.rstrip() for line in (text or "").splitlines()]
    prompt_lines: list[str] = []
    options: list[Option] = []
    blanks: list[str] = []
    type_hint = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 「单选」独占一行
        hint = TYPE_HINT_RE.match(stripped)
        if hint and not options and not prompt_lines:
            type_hint = hint.group(1)
            continue

        # 「单选 下列关于…」这种把题型写在题干前面的写法
        if not options and not prompt_lines and not type_hint:
            leading = LEADING_TYPE_RE.match(stripped)
            if leading:
                type_hint = leading.group(1)
                stripped = stripped[leading.end() :].strip()
                if not stripped:
                    continue

        option_match = OPTION_LINE_RE.match(stripped)
        if option_match:
            options.append(Option(index=len(options), text=option_match.group(2).strip()))
            continue

        blank_match = BLANK_LINE_RE.match(stripped)
        if blank_match and not options:
            blanks.append(blank_match.group(1).strip())
            continue

        if not options:
            prompt_lines.append(stripped)

    prompt = "\n".join(prompt_lines).strip()
    problem_type = infer_problem_type(f"{type_hint}题 {prompt}", options)
    if type_hint in ("填空",):
        problem_type = ProblemType.FILL_BLANK

    digest = hashlib.sha1((prompt + "|" + "|".join(o.text for o in options)).encode("utf-8"))
    return Problem(
        id=f"adhoc-{digest.hexdigest()[:12]}",
        type=problem_type,
        prompt=prompt,
        options=options,
        blanks=blanks,
        source="adhoc",
    )
