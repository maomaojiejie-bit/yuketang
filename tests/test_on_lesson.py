"""查询进行中课堂的健壮性：未登录 / 非 JSON / CORS 回退。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from cjsolver.browser.driver import fetch_on_lesson, on_lesson_report, parse_on_lesson
from cjsolver.yuketang.constants import site_by_key

SITE = site_by_key("changjiang")


class FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    async def text(self) -> str:
        return self._text


class FakeRequest:
    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self.text = text
        self.exc = exc
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.calls.append((url, headers))
        if self.exc is not None:
            raise self.exc
        return FakeResponse(self.text or "")


class FakePage:
    def __init__(
        self,
        *,
        text: str | None = None,
        exc: Exception | None = None,
        evaluate_result: Any = None,
        url: str = "https://changjiang.yuketang.cn/web",
        with_request: bool = True,
    ) -> None:
        self.url = url
        self.request = FakeRequest(text, exc)
        self.context = SimpleNamespace(request=self.request) if with_request else SimpleNamespace()
        self.evaluate_result = evaluate_result
        self.evaluate_calls = 0

    async def evaluate(self, script: str, arg: Any) -> Any:
        self.evaluate_calls += 1
        return self.evaluate_result


def test_returns_lessons_and_hits_the_right_url() -> None:
    payload = json.dumps(
        {"code": 0, "data": {"onLessonClassrooms": [{"lessonId": 7, "lessonName": "操作系统"}]}}
    )
    page = FakePage(text=payload)

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert note == ""
    assert lessons == [{"id": "7", "title": "操作系统", "classroom_id": None, "presentation_id": None}]
    url, headers = page.request.calls[0]
    assert url == SITE.api("/api/v3/classroom/on-lesson")
    # 带上 Referer，尽量贴近真实浏览器请求
    assert headers and headers.get("Referer") == page.url


def test_unauthenticated_is_reported_not_silently_empty() -> None:
    page = FakePage(text=json.dumps({"code": 50000, "msg": "UNAUTHENTICATED", "data": ""}))

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert lessons == []
    assert "未登录" in note


def test_login_page_is_reported() -> None:
    page = FakePage(text="<html><body>请先登录</body></html>")

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert lessons == []
    assert "登录页" in note


def test_non_json_is_reported() -> None:
    page = FakePage(text="<html>502 Bad Gateway</html>")

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert lessons == []
    assert "不是 JSON" in note


def test_other_error_code_is_reported() -> None:
    page = FakePage(text=json.dumps({"code": 40001, "msg": "参数错误", "data": {}}))

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert lessons == []
    assert "40001" in note and "参数错误" in note


def test_falls_back_to_in_page_fetch_when_request_api_fails() -> None:
    page = FakePage(
        exc=RuntimeError("request api 不可用"),
        evaluate_result={
            "ok": True,
            "status": 200,
            "text": json.dumps({"code": 0, "data": {"onLessonClassrooms": [{"lessonId": "9"}]}}),
        },
    )

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert note == ""
    assert lessons and lessons[0]["id"] == "9"
    assert page.evaluate_calls == 1


def test_empty_result_without_error_is_normal() -> None:
    """登录正常、但确实没有课：不能报错，只返回空列表。"""
    page = FakePage(text=json.dumps({"code": 0, "data": {"onLessonClassrooms": []}}))

    lessons, note = asyncio.run(on_lesson_report(page, SITE))  # type: ignore[arg-type]

    assert lessons == []
    assert note == ""


def test_fetch_on_lesson_wrapper_keeps_old_contract() -> None:
    page = FakePage(text=json.dumps({"code": 0, "data": {"onLessonClassrooms": [{"lessonId": 1}]}}))

    assert asyncio.run(fetch_on_lesson(page, SITE))[0]["id"] == "1"  # type: ignore[arg-type]


def test_parse_on_lesson_tolerates_more_shapes() -> None:
    assert parse_on_lesson({"data": {"list": [{"lessonId": 1}]}})[0]["id"] == "1"
    assert parse_on_lesson({"data": [{"lessonId": 2}]})[0]["id"] == "2"
