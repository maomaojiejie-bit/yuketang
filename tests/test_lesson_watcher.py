"""自动进入课堂：轮询、去重与诊断（用桩替换 CDP 调用）。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from cjsolver.config import load_config
from cjsolver.solver import Solver
from cjsolver.yuketang.constants import site_by_key


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url


def build(tmp_dir: Path, url: str, interval: float = 5.0) -> tuple[Solver, FakePage, list]:
    config = load_config(project_root=tmp_dir)
    config = replace(
        config,
        solver=replace(
            config.solver, auto_enter_lesson=True, enter_lesson_interval=interval
        ),
    )
    page = FakePage(url)
    events: list[dict[str, Any]] = []
    site = site_by_key(config.site)
    solver = Solver(config, page, site, client=None, on_event=events.append)  # type: ignore[arg-type]
    return solver, page, events


async def drive(solver: Solver, calls: dict, *, until, timeout: float = 3.0) -> None:
    """跑 watcher 直到条件满足，然后收尾（避免白等一个轮询间隔）。"""
    task = asyncio.create_task(solver._lesson_watcher())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline and not until():
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)
    solver.request_stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def patch_driver(
    monkeypatch,
    calls: dict,
    lessons: list[dict[str, Any]],
    *,
    note: str = "",
    fail_goto: bool = False,
) -> None:
    async def fake_report(page: object, site: object) -> tuple[list[dict[str, Any]], str]:
        calls["report"] += 1
        return list(lessons), note

    async def fake_goto(
        page: object, site: object, lesson_id: str, classroom_id: str | None = None
    ) -> None:
        calls["goto"].append((lesson_id, classroom_id))
        if fail_goto:
            raise RuntimeError("页面跳转失败")

    monkeypatch.setattr("cjsolver.browser.driver.on_lesson_report", fake_report)
    monkeypatch.setattr("cjsolver.browser.driver.goto_lesson", fake_goto)


def new_calls() -> dict:
    return {"report": 0, "goto": []}


def test_enters_in_progress_lesson_immediately(monkeypatch, tmp_dir: Path) -> None:
    calls = new_calls()
    patch_driver(monkeypatch, calls, [{"id": "lesson-1", "title": "操作系统", "classroom_id": "c1"}])
    solver, _, events = build(tmp_dir, "https://changjiang.yuketang.cn/web")

    asyncio.run(drive(solver, calls, until=lambda: bool(calls["goto"])))

    # 第一轮立刻查，不等 enter_lesson_interval
    assert calls["goto"] == [("lesson-1", "c1")]
    stages = [e["data"]["stage"] for e in events if e["type"] == "lesson"]
    assert stages == ["entering", "entered"]
    assert events[0]["data"]["title"] == "操作系统"


def test_no_lesson_means_no_navigation(monkeypatch, tmp_dir: Path) -> None:
    calls = new_calls()
    patch_driver(monkeypatch, calls, [])
    solver, _, events = build(tmp_dir, "https://changjiang.yuketang.cn/web")

    asyncio.run(drive(solver, calls, until=lambda: calls["report"] >= 1))

    assert calls["goto"] == []
    assert [e for e in events if e["type"] == "lesson"] == []
    # 查过一轮但没课，至少要告诉用户「查过了」
    logs = [e["data"]["message"] for e in events if e["type"] == "log"]
    assert any("没有进行中的课堂" in m for m in logs)


def test_already_inside_lesson_is_not_reentered(monkeypatch, tmp_dir: Path) -> None:
    calls = new_calls()
    patch_driver(monkeypatch, calls, [{"id": "lesson-1", "title": "操作系统"}])
    solver, _, events = build(
        tmp_dir, "https://changjiang.yuketang.cn/lesson/fullscreen/v3/lesson-1/c1"
    )

    asyncio.run(drive(solver, calls, until=lambda: calls["report"] >= 1))

    assert calls["goto"] == []
    assert [e for e in events if e["type"] == "lesson"] == []


def test_failed_navigation_is_reported(monkeypatch, tmp_dir: Path) -> None:
    calls = new_calls()
    patch_driver(monkeypatch, calls, [{"id": "lesson-9", "title": "编译原理"}], fail_goto=True)
    solver, _, events = build(tmp_dir, "https://changjiang.yuketang.cn/web")

    asyncio.run(drive(solver, calls, until=lambda: bool(calls["goto"])))

    stages = [e["data"]["stage"] for e in events if e["type"] == "lesson"]
    assert stages == ["entering", "failed"]
    failed = [e for e in events if e["type"] == "lesson"][-1]
    assert "页面跳转失败" in failed["data"]["message"]


def test_query_diagnostic_is_surfaced(monkeypatch, tmp_dir: Path) -> None:
    """未登录之类的诊断必须发到事件流，否则用户只看到「什么都没发生」。"""
    calls = new_calls()
    patch_driver(monkeypatch, calls, [], note="雨课堂提示未登录 / 登录已过期，请在浏览器里重新登录")
    solver, _, events = build(tmp_dir, "https://changjiang.yuketang.cn/web")

    asyncio.run(drive(solver, calls, until=lambda: calls["report"] >= 1))

    warnings = [e["data"]["message"] for e in events if e["type"] == "warn"]
    assert any("未登录" in m for m in warnings)
