"""心跳日志测试：监听期间按间隔汇报状态。

全部走真实的 Solver.run()，不往生产代码里塞测试专用钩子。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from cjsolver.config import Config, load_config
from cjsolver.solver import Solver, _format_duration
from cjsolver.yuketang.constants import site_by_key


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


class FakePage:
    """够用的假页面：心跳只用到 is_closed / url / title，capture 只用到事件注册。"""

    def __init__(self, *, closed: bool = False) -> None:
        self.url = "https://changjiang.yuketang.cn/lesson/fullscreen/v3/1/2"
        self.title_text = "模拟课堂"
        self._closed = closed
        self.listeners = 0

    def is_closed(self) -> bool:
        return self._closed

    async def title(self) -> str:
        if self._closed:
            raise RuntimeError("page closed")
        return self.title_text

    # QuestionCapture 会在 page 上挂网络监听
    def on(self, *_args: object) -> None:
        self.listeners += 1

    def remove_listener(self, *_args: object) -> None:
        self.listeners -= 1


class FakeClient:
    """心跳测试不调用模型。"""

    async def ask(self, *_args: object, **_kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("心跳测试不应该调用模型")


def make_solver(
    tmp_dir: object, *, page: object | None = None, interval: float = 30.0
) -> Solver:
    config: Config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    config.solver.heartbeat_interval = interval
    # DOM 轮询会用到 evaluate，假页面没有；心跳测试不需要它
    config.solver.dom_fallback = False
    config.solver.auto_enter_lesson = False
    return Solver(
        config,
        page if page is not None else FakePage(),  # type: ignore[arg-type]
        site_by_key("changjiang"),
        FakeClient(),  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def silence_heartbeat_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """心跳会往终端画 rich 面板，测试里静音掉，免得刷屏。"""
    from cjsolver import console

    monkeypatch.setattr(console, "heartbeat_panel", lambda _data: None)


# --------------------------------------------------------------------------- #
# 时长格式化
# --------------------------------------------------------------------------- #
def test_format_duration() -> None:
    assert _format_duration(0) == "0s"
    assert _format_duration(42) == "42s"
    assert _format_duration(60) == "01m00s"
    assert _format_duration(125) == "02m05s"
    assert _format_duration(3600) == "1h00m00s"
    assert _format_duration(3725) == "1h02m05s"
    # 负数（时钟回拨）不应该输出奇怪的东西
    assert _format_duration(-5) == "0s"


# --------------------------------------------------------------------------- #
# 内容组装
# --------------------------------------------------------------------------- #
def test_heartbeat_payload_shape(tmp_dir: object) -> None:
    solver = make_solver(tmp_dir)
    payload = run(solver.heartbeat_payload())

    assert set(payload) >= {
        "uptime", "uptime_text", "stats", "capture", "queue", "page", "record_file"
    }
    assert isinstance(payload["uptime"], int)
    assert payload["page"]["alive"] is True
    assert payload["page"]["title"] == "模拟课堂"
    assert payload["page"]["url"].startswith("https://changjiang.yuketang.cn")
    assert payload["stats"]["problems"] == 0


def test_heartbeat_reports_closed_page(tmp_dir: object) -> None:
    solver = make_solver(tmp_dir, page=FakePage(closed=True))
    payload = run(solver.heartbeat_payload())

    assert payload["page"]["alive"] is False
    assert payload["page"]["url"] == ""


def test_heartbeat_includes_capture_stats(tmp_dir: object) -> None:
    solver = make_solver(tmp_dir, interval=0)  # 关掉自动心跳，手动观察

    async def scenario() -> dict:
        task = asyncio.create_task(solver.run())
        assert await solver.wait_ready(timeout=5)
        assert solver.capture is not None

        await solver.capture.feed_payload(
            {
                "code": 0,
                "data": {
                    "id": "p1",
                    "lessonId": "L1",
                    "slides": [
                        {
                            "id": "s1",
                            "problem": {
                                "problemId": "q1",
                                "problemType": 1,
                                "prompt": "题干",
                                "options": ["甲", "乙"],
                            },
                        }
                    ],
                },
            }
        )
        payload = await solver.heartbeat_payload()
        solver.request_stop()
        await asyncio.wait_for(task, timeout=10)
        return payload

    payload = run(scenario())
    assert payload["capture"].get("problem") == 1
    assert payload["capture"].get("response", 0) == 0  # 离线投喂不算接口响应


# --------------------------------------------------------------------------- #
# 定时器行为
# --------------------------------------------------------------------------- #
def test_heartbeat_fires_on_schedule(tmp_dir: object) -> None:
    """跑一轮真实的心跳周期（下限 1s）。"""
    events: list[dict] = []
    solver = make_solver(tmp_dir, interval=1.0)
    solver.on_event = events.append

    async def scenario() -> None:
        task = asyncio.create_task(solver.run())
        assert await solver.wait_ready(timeout=5)
        await asyncio.sleep(1.3)  # 够跑完一轮
        solver.request_stop()
        await asyncio.wait_for(task, timeout=10)

    run(scenario())

    heartbeats = [event for event in events if event["type"] == "heartbeat"]
    assert heartbeats, "应当发出 heartbeat 事件"
    data = heartbeats[0]["data"]
    assert "uptime_text" in data
    assert "stats" in data
    assert "capture" in data
    assert "page" in data
    assert data["page"]["alive"] is True


def test_no_heartbeat_when_disabled(tmp_dir: object) -> None:
    events: list[dict] = []
    solver = make_solver(tmp_dir, interval=0)
    solver.on_event = events.append

    async def scenario() -> None:
        task = asyncio.create_task(solver.run())
        assert await solver.wait_ready(timeout=5)
        await asyncio.sleep(1.2)
        solver.request_stop()
        await asyncio.wait_for(task, timeout=10)

    run(scenario())
    assert not [event for event in events if event["type"] == "heartbeat"]


def test_heartbeat_task_exits_promptly_on_stop(tmp_dir: object) -> None:
    """停止时不应等满一个心跳周期。"""
    solver = make_solver(tmp_dir, interval=600.0)

    async def scenario() -> float:
        task = asyncio.ensure_future(solver._heartbeat())  # noqa: SLF001
        await asyncio.sleep(0.05)
        started = time.perf_counter()
        solver.request_stop()
        await asyncio.wait_for(task, timeout=2.0)
        return time.perf_counter() - started

    elapsed = run(scenario())
    assert elapsed < 1.0, f"停止耗时 {elapsed:.2f}s，应该立刻返回"
