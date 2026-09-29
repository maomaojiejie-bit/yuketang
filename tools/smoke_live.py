"""端到端冒烟测试：走完整的运行时链路。

覆盖 网页控制台 → 浏览器接管 → 监听会话 → 抓题 → DeepSeek → 事件流，
但不向页面提交任何答案（以 manual + dry-run 运行）。

    python tools/smoke_live.py

需要 .env 里已经配置好 DEEPSEEK_API_KEY，并且允许启动 Chrome。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cjsolver import console  # noqa: E402
from cjsolver.config import load_config  # noqa: E402
from cjsolver.web.runtime import ConsoleRuntime  # noqa: E402

TIMEOUT = 120.0


async def main() -> int:
    config = load_config(project_root=ROOT)
    console.rule("端到端冒烟测试")
    console.info(f"站点 {config.site} · 模型 {config.deepseek.model}")

    if not config.deepseek.api_key.strip():
        console.error("未配置 DEEPSEEK_API_KEY，无法运行本测试。")
        return 2

    # 心跳压到 1 秒，好在这次短测试里跑到。
    # 注意：ConsoleRuntime 会用 console-settings.json 覆盖 config.yaml，
    # 所以必须写在构造 runtime 之后（见下面那行）。
    collected: list[dict] = []
    runtime = ConsoleRuntime(config)
    runtime.config.solver.heartbeat_interval = 1.0
    runtime.bus.subscribe(replay=False)  # 占位订阅者，确认发布路径不会因为没人收而报错
    original_publish = runtime.bus.publish

    def capture_event(event: object, data: dict | None = None) -> None:
        """记录事件，同时转发给真正的总线。

        总线的 publish 同时支持字典形式和 (type, data=...) 形式，这里要一并兼容。
        """
        if isinstance(event, str):
            collected.append({"type": event, "data": data or {}})
        elif isinstance(event, dict):
            collected.append(dict(event))
        original_publish(event, data)  # type: ignore[arg-type]

    runtime.bus.publish = capture_event  # type: ignore[method-assign]

    failures: list[str] = []
    try:
        await runtime.start()

        console.info("接管浏览器…")
        attached = await runtime.ensure_browser()
        console.success(f"已接管：{attached.page.url}")

        console.info("启动监听（manual + dry-run，不会改动页面）…")
        await runtime.start_watch(mode="manual", dry_run=True)

        console.info("执行端到端投喂…")
        report = await runtime.simulate("live")
        for check in report["checks"]:
            mark = "○" if check["skipped"] else ("✓" if check["ok"] else "✗")
            console.console.print(f"  {mark} {check['label']}  [dim]{check['detail']}[/dim]")
        if not report["ok"]:
            failures.append("端到端投喂检测未通过")

        # 等 Solver 把 4 道题都处理完（每题一次模型调用）
        console.info("等待 Solver 逐题作答…")
        solver = runtime._solver  # noqa: SLF001 - 冒烟测试需要观察内部状态
        deadline = asyncio.get_running_loop().time() + TIMEOUT
        while solver is not None and asyncio.get_running_loop().time() < deadline:
            stats = solver.stats.to_dict()
            finished = stats["answered"] + stats["skipped"] + stats["failed"]
            if stats["problems"] >= 4 and finished >= 4:
                break
            await asyncio.sleep(0.5)

        stats = solver.stats.to_dict() if solver else {}
        finished = stats.get("answered", 0) + stats.get("skipped", 0) + stats.get("failed", 0)
        console.console.print()
        console.info(
            f"统计：遇到 {stats.get('problems', 0)} 题，处置完成 {finished} 题"
            f"（作答 {stats.get('answered', 0)}，跳过 {stats.get('skipped', 0)}，"
            f"失败 {stats.get('failed', 0)}）"
        )

        # 心跳间隔 1s，而整轮可能 1s 内就跑完，所以显式等一下，否则会误报"没有心跳"
        console.info("等待至少一次心跳…")
        heartbeat_deadline = asyncio.get_running_loop().time() + 6
        while asyncio.get_running_loop().time() < heartbeat_deadline:
            if any(event.get("type") == "heartbeat" for event in collected):
                break
            await asyncio.sleep(0.2)

        if stats.get("problems", 0) < 4:
            failures.append(f"只有 {stats.get('problems', 0)} 道题进入了作答队列，期望 4 道")
        if finished < 4:
            failures.append(f"只有 {finished} 道题处理完成，期望 4 道")
        if stats.get("failed", 0):
            failures.append(f"有 {stats['failed']} 道题处理失败")

        # 事件流应当包含题目与答案
        types = [event.get("type") for event in collected]
        for expected in ("problem", "thinking", "answer", "simulation", "watching", "heartbeat"):
            if expected not in types:
                failures.append(f"事件流里缺少 {expected} 事件")
        answer_events = [event for event in collected if event.get("type") == "answer"]
        for event in answer_events:
            data = event.get("data") or {}
            suggestion = data.get("suggestion") or {}
            console.console.print(
                f"  [green]答案[/green] {suggestion.get('display')} "
                f"[dim]({suggestion.get('model')} · {suggestion.get('elapsed')}s)[/dim]"
            )

        if len(answer_events) < 4:
            failures.append(f"只产生了 {len(answer_events)} 个答案事件，期望 4 个")

        # 心跳内容检查
        heartbeats = [event for event in collected if event.get("type") == "heartbeat"]
        console.console.print()
        console.info(f"心跳事件：{len(heartbeats)} 次")
        if not heartbeats:
            failures.append("监听期间没有产生心跳事件")
        else:
            payload = heartbeats[-1].get("data") or {}
            for key in ("uptime_text", "stats", "capture", "queue", "page"):
                if key not in payload:
                    failures.append(f"心跳载荷缺少 {key}")
            page = payload.get("page") or {}
            console.console.print(
                f"  最后一次心跳：[cyan]{payload.get('uptime_text')}[/cyan] · "
                f"题目 {payload.get('stats', {}).get('problems', 0)} · "
                f"页面存活={page.get('alive')} · {page.get('title', '')}"
            )
            if not page.get("alive"):
                failures.append("心跳报告页面不可用")

        console.info("停止监听…")
        await runtime.stop_watch()
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
        console.error(failures[-1])
    finally:
        try:
            await runtime.close()
        except Exception:  # pragma: no cover
            pass

    console.console.print()
    if failures:
        console.error("端到端冒烟测试失败：")
        for item in failures:
            console.console.print(f"  [red]-[/red] {item}")
        return 1
    console.success("端到端冒烟测试全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
