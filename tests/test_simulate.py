"""模拟检测功能测试（全部离线，不访问雨课堂、不启动 Chrome）。"""

from __future__ import annotations

import asyncio
import json

from cjsolver.config import load_config
from cjsolver.models import ProblemType
from cjsolver.simulate import (
    Simulator,
    build_mock_problem,
    build_presentation_payload,
    build_websocket_frame,
)
from cjsolver.yuketang.capture import QuestionCapture
from cjsolver.yuketang.constants import site_by_key


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def make_simulator(tmp_dir: object) -> Simulator:
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    return Simulator(config, site_by_key("changjiang"))


# --------------------------------------------------------------------------- #
# 仿真载荷
# --------------------------------------------------------------------------- #
def test_presentation_payload_shape() -> None:
    payload = build_presentation_payload("abc")
    slides = payload["data"]["slides"]
    assert len(slides) == 4
    assert slides[0]["problem"] is None
    assert payload["data"]["id"] == "sim-presentation-abc"
    assert payload["data"]["lessonId"] == "sim-lesson-abc"


def test_presentation_payload_covers_field_variants() -> None:
    """刻意混用了对象/字符串选项、prompt/content 题干、camel/snake id。"""
    slides = build_presentation_payload("abc")["data"]["slides"]
    first = slides[1]["problem"]
    second = slides[2]["problem"]
    third = slides[3]["problem"]

    assert isinstance(first["options"][0], dict)   # {content: ...}
    assert isinstance(second["answers"][0], str)   # 纯字符串 + answers 别名
    assert "problem_id" in second                  # snake_case id
    assert "content" in second                     # 题干用 content
    assert third["problemType"] == 4               # 填空
    assert third["blanks"] == ["页表"]


def test_nonce_produces_distinct_ids() -> None:
    """两次模拟的题目 id 必须不同，否则会被去重吃掉。"""
    first = build_presentation_payload("aaa")["data"]["slides"][1]["problem"]["problemId"]
    second = build_presentation_payload("bbb")["data"]["slides"][1]["problem"]["problemId"]
    assert first != second


def test_websocket_frame_is_valid_json_with_lowercase_lessonid() -> None:
    frame = json.loads(build_websocket_frame("abc"))
    assert frame["op"] == "problem"
    assert frame["lessonid"] == "sim-lesson-abc"   # wsapp 用的是全小写
    assert frame["data"]["problemId"] == "sim-abc-ws"


def test_mock_problem_shape() -> None:
    mock = build_mock_problem()
    assert len(mock["options"]) == 4
    assert [option["letter"] for option in mock["options"]] == ["A", "B", "C", "D"]
    # 正确答案必须确实存在于选项里，否则自检会误报
    assert mock["answer_letter"] == "B"
    assert mock["answer_text"] == mock["options"][1]["text"]


# --------------------------------------------------------------------------- #
# 离线自检
# --------------------------------------------------------------------------- #
def test_offline_simulation_passes_without_model(tmp_dir: object) -> None:
    simulator = make_simulator(tmp_dir)
    report = run(simulator.run_offline(test_model=False))

    assert report.ok, [check.to_dict() for check in report.checks]
    assert report.error == ""
    keys = [check.key for check in report.checks]
    assert keys == [
        "capture.presentation",
        "capture.websocket",
        "capture.dedupe",
        "parse.fields",
        "parse.types",
        "answer.format",
        "model.deepseek",
    ]
    model_check = report.checks[-1]
    assert model_check.skipped and not model_check.ok


def test_offline_simulation_reports_types(tmp_dir: object) -> None:
    simulator = make_simulator(tmp_dir)
    report = run(simulator.run_offline(test_model=False))

    assert len(report.problems) == 4
    types = {problem["type"] for problem in report.problems}
    assert types == {
        ProblemType.SINGLE_CHOICE.value,
        ProblemType.MULTIPLE_CHOICE.value,
        ProblemType.FILL_BLANK.value,
    }
    assert all(problem["simulated"] for problem in report.problems)


def test_offline_simulation_serialises_cleanly(tmp_dir: object) -> None:
    """报告要能直接 json 序列化，网页控制台靠这个回传。"""
    simulator = make_simulator(tmp_dir)
    report = run(simulator.run_offline(test_model=False))
    text = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "capture.presentation" in text


def test_two_offline_runs_do_not_interfere(tmp_dir: object) -> None:
    """每次模拟都用新 nonce，第二次不应被去重逻辑吃掉。"""
    simulator = make_simulator(tmp_dir)
    first = run(simulator.run_offline(test_model=False))
    second = run(simulator.run_offline(test_model=False))

    assert first.ok and second.ok
    for report in (first, second):
        presentation = next(c for c in report.checks if c.key == "capture.presentation")
        assert presentation.ok, presentation.detail


def test_simulation_emits_progress_logs(tmp_dir: object) -> None:
    events: list[dict] = []
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    simulator = Simulator(config, site_by_key("changjiang"), emit=events.append)
    run(simulator.run_offline(test_model=False))

    assert events, "模拟过程应该通过 emit 输出进度"
    assert all(event["type"] == "sim-log" for event in events)
    messages = [event["data"]["message"] for event in events]
    assert any("presentation" in message for message in messages)
    assert any("wsapp" in message for message in messages)


# --------------------------------------------------------------------------- #
# 端到端投喂
# --------------------------------------------------------------------------- #
def test_feed_live_into_capture(tmp_dir: object) -> None:
    """feed_live 走的是真实 QuestionCapture，应当产出一道道新题。"""
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    received: list[object] = []

    async def handler(problem: object) -> None:
        received.append(problem)

    async def scenario() -> object:
        capture = QuestionCapture(None, site_by_key("changjiang"), handler)
        await capture.start()
        try:
            simulator = Simulator(config, site_by_key("changjiang"))
            return await simulator.feed_live(capture)
        finally:
            await capture.stop()

    report = run(scenario())
    assert report.ok, [c.to_dict() for c in report.checks]  # type: ignore[union-attr]
    assert len(received) == 4
    assert len(report.problems) == 4  # type: ignore[union-attr]


def test_feed_payload_dedupes_by_key(tmp_dir: object) -> None:
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    payload = build_presentation_payload("zzz")

    async def scenario() -> tuple[int, int]:
        async def handler(problem: object) -> None:
            return None

        capture = QuestionCapture(None, site_by_key("changjiang"), handler)
        await capture.start()
        try:
            first = await capture.feed_payload(payload)
            second = await capture.feed_payload(payload)
            return len(first), len(second)
        finally:
            await capture.stop()

    first, second = run(scenario())
    assert first == 3
    assert second == 0


def test_capture_without_page_is_supported() -> None:
    """模拟功能依赖「没有页面也能构造抓题器」。"""
    async def scenario() -> bool:
        async def handler(problem: object) -> None:
            return None

        capture = QuestionCapture(None, site_by_key("changjiang"), handler)
        await capture.start()
        started = capture.stats["problem"] == 0
        await capture.stop()
        return started

    assert run(scenario()) is True


def test_feed_frame_ignores_non_json() -> None:
    async def scenario() -> int:
        async def handler(problem: object) -> None:
            return None

        capture = QuestionCapture(None, site_by_key("changjiang"), handler)
        await capture.start()
        try:
            assert await capture.feed_frame_text("not json") == []
            assert await capture.feed_frame_text("") == []
            return len(await capture.feed_frame_text(build_websocket_frame("q")))
        finally:
            await capture.stop()

    assert run(scenario()) == 1
