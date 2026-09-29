"""AI 层：DeepSeek 客户端与提示词/答案解析。"""

from .deepseek import AIError, DeepSeekClient
from .prompts import answer_format_hint, build_messages, normalize_answer, parse_suggestion

__all__ = [
    "AIError",
    "DeepSeekClient",
    "answer_format_hint",
    "build_messages",
    "normalize_answer",
    "parse_suggestion",
]
