"""从雨课堂下发的 JSON 中提取题目。

这里是纯函数，不依赖 Playwright，因此可以脱离浏览器直接单测。
两种策略叠加使用：
  1. 结构化解析：按 `slides[].problem` 的已知形状读（最准，还能顺带拿到课件图）
  2. 通用递归扫描：任何长得像题目的对象都捞出来（抗结构变更）
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator

from ..models import Problem

_ID_KEYS = ("problemId", "problem_id")
_TYPE_KEYS = ("problemType", "problem_type")
_BODY_KEYS = ("prompt", "content", "body", "options", "answers", "blanks")
_IMAGE_KEYS = ("imageUrl", "image_url", "cover", "coverUrl", "cover_url", "src")
#: 雨课堂各处对 lesson / presentation 的命名并不统一，含 WebSocket 全小写写法
_LESSON_ID_KEYS = ("lessonId", "lesson_id", "lessonid")
_PRESENTATION_ID_KEYS = ("presentationId", "presentation_id", "presentationid", "id")


def extract_problems(
    payload: Any,
    *,
    lesson_id: str = "",
    presentation_id: str = "",
    source: str = "network",
) -> list[Problem]:
    """从任意响应/推送载荷中提取题目，按 key 去重（结构化结果优先）。"""
    found: dict[str, Problem] = {}
    for problem in _walk_presentation(payload, lesson_id, presentation_id, source):
        found.setdefault(problem.key, problem)
    for problem in _walk_generic(payload, lesson_id, presentation_id, source):
        found.setdefault(problem.key, problem)
    return list(found.values())


def _walk_presentation(
    payload: Any, lesson_id: str, presentation_id: str, source: str
) -> Iterator[Problem]:
    """按 `slides[].problem` 的已知形状解析，同时携带 slides[].imageUrl。"""
    lesson = lesson_id or _deep_first_id(payload, _LESSON_ID_KEYS)
    for group in _iter_slide_groups(payload):
        # 同一层的 id 通常就是 presentation_id（响应里的 data.id）
        group_presentation = _first_id(group, _PRESENTATION_ID_KEYS)
        for slide in _iter_slides_of(group):
            raw_problem = slide.get("problem")
            if not isinstance(raw_problem, dict):
                continue
            problem = Problem.from_raw(
                raw_problem,
                lesson_id=lesson,
                presentation_id=presentation_id or group_presentation,
                slide_id=_first_id(slide, ("id", "slideId", "slide_id", "pageId", "page_id")),
                image_url=_first_text(slide, _IMAGE_KEYS),
                source=source,
            )
            if problem is not None:
                yield problem


def _iter_slide_groups(node: Any) -> Iterator[dict[str, Any]]:
    """产出所有含 slides / pages 数组的对象。"""
    if isinstance(node, dict):
        if any(isinstance(node.get(key), list) for key in ("slides", "pages")):
            yield node
        for value in node.values():
            yield from _iter_slide_groups(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_slide_groups(value)


def _iter_slides_of(group: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for key in ("slides", "pages"):
        slides = group.get(key)
        if isinstance(slides, list):
            for slide in slides:
                if isinstance(slide, dict):
                    yield slide
            return


def _walk_generic(
    payload: Any, lesson_id: str, presentation_id: str, source: str
) -> Iterator[Problem]:
    """递归扫描任何形似题目的对象。"""
    context_lesson = lesson_id or _deep_first_id(payload, _LESSON_ID_KEYS)
    context_presentation = presentation_id or _deep_first_id(
        payload, ("presentationId", "presentation_id", "presentationid")
    )
    for node in _walk_dicts(payload):
        if not looks_like_problem(node):
            continue
        # 题目对象自身可能带 lessonId / presentationId
        problem = Problem.from_raw(
            node,
            lesson_id=_first_id(node, _LESSON_ID_KEYS) or context_lesson,
            presentation_id=_first_id(node, ("presentationId", "presentation_id", "presentationid"))
            or context_presentation,
            slide_id=_first_id(node, ("slideId", "slide_id", "pageId", "page_id")),
            image_url=_first_text(node, _IMAGE_KEYS),
            source=source,
        )
        if problem is not None:
            yield problem


def _walk_dicts(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_dicts(value)


def looks_like_problem(node: Any) -> bool:
    """判断一个对象是否像雨课堂的题目结构。"""
    if not isinstance(node, dict):
        return False
    has_explicit_id = any(key in node for key in _ID_KEYS)
    has_type = any(key in node for key in _TYPE_KEYS)
    has_body = any(key in node for key in _BODY_KEYS)
    if has_explicit_id and (has_type or has_body):
        return True
    # 有些接口用 id + problemType/options 组合
    return has_type and has_body and "id" in node


def _first_text(node: Any, keys: Iterable[str]) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_id(node: Any, keys: Iterable[str]) -> str:
    if not isinstance(node, dict):
        return ""
    for key in keys:
        value = node.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _deep_first_id(node: Any, keys: Iterable[str], *, max_depth: int = 6) -> str:
    """在嵌套结构里广度优先查找第一个命中的 id 字段。"""
    queue: list[tuple[Any, int]] = [(node, 0)]
    while queue:
        current, depth = queue.pop(0)
        if isinstance(current, dict):
            found = _first_id(current, keys)
            if found:
                return found
            if depth < max_depth:
                queue.extend((value, depth + 1) for value in current.values())
        elif isinstance(current, list) and depth < max_depth:
            queue.extend((value, depth + 1) for value in current)
    return ""
