"""流量过滤与站点常量测试。"""

from __future__ import annotations

from cjsolver.browser.driver import parse_on_lesson
from cjsolver.yuketang.capture import is_interesting_path, is_interesting_url
from cjsolver.yuketang.constants import site_by_key, site_for_host


def test_site_lookup() -> None:
    assert site_by_key("changjiang").host == "changjiang.yuketang.cn"
    assert site_by_key("PRO").host == "pro.yuketang.cn"
    assert site_by_key("不存在").host == "changjiang.yuketang.cn"
    assert site_by_key(None).host == "changjiang.yuketang.cn"
    assert site_for_host("www.yuketang.cn").key == "standard"  # type: ignore[union-attr]
    assert site_for_host("example.com") is None


def test_site_derived_urls() -> None:
    site = site_by_key("changjiang")
    assert site.api("/api/v3/user/basic-info").startswith(
        "https://changjiang.yuketang.cn/api/v3/"
    )
    assert site.websocket_url == "wss://changjiang.yuketang.cn/wsapp/"


def test_is_interesting_url_filters_third_party() -> None:
    site = site_by_key("changjiang")
    assert is_interesting_url(
        "https://changjiang.yuketang.cn/api/v3/lesson/presentation/fetch?presentation_id=1", site
    )
    assert is_interesting_url(
        "https://changjiang.yuketang.cn/api/v3/lesson/problem/answer", site
    )
    # 非雨课堂域名
    assert not is_interesting_url("https://cdn.example.com/presentation.json", site)
    # 雨课堂域名但不相关
    assert not is_interesting_url("https://changjiang.yuketang.cn/api/v3/user/basic-info", site)


def test_is_interesting_path() -> None:
    assert is_interesting_path("/api/v3/lesson/problem/answer")
    assert is_interesting_path("/api/v3/lesson/presentation/fetch")
    assert not is_interesting_path("/api/v3/user/basic-info")


def test_parse_on_lesson_variants() -> None:
    wrapped = {
        "code": 0,
        "data": {
            "onLessonClassrooms": [
                {
                    "lessonId": 123,
                    "lessonName": "操作系统",
                    "classroomId": 456,
                    "presentationId": 789,
                }
            ]
        },
    }
    lessons = parse_on_lesson(wrapped)
    assert lessons == [
        {
            "id": "123",
            "title": "操作系统",
            "classroom_id": "456",
            "presentation_id": "789",
        }
    ]

    snake = {"data": {"on_lesson_classrooms": [{"lesson_id": "a", "name": "数据结构"}]}}
    assert parse_on_lesson(snake)[0]["id"] == "a"
    assert parse_on_lesson(snake)[0]["title"] == "数据结构"

    assert parse_on_lesson({"data": {}}) == []
    assert parse_on_lesson(None) == []
    assert parse_on_lesson({"data": {"onLessonClassrooms": [{"title": "没有 id"}]}}) == []
