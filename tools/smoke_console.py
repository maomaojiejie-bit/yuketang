"""控制台前端冒烟测试：用真实浏览器验证自动滚动。

自动滚动是布局层面的行为（scrollHeight / scrollTop / 容器高度），
光看代码看不出问题——之前的 bug 就是 CSS 的 `scroll-behavior: smooth`
让 `scrollTo({behavior:'auto'})` 也走动画，连续推送时永远追不到底部。
所以这里真的开一个浏览器，灌一堆事件进去，然后量滚动位置。

    python tools/smoke_console.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cjsolver import console  # noqa: E402
from cjsolver.browser import attach, ensure_chrome  # noqa: E402
from cjsolver.browser.launcher import kill_chrome  # noqa: E402
from cjsolver.config import BrowserConfig, load_config  # noqa: E402
from cjsolver.web.runtime import ConsoleRuntime  # noqa: E402
from cjsolver.web.server import create_app  # noqa: E402

PORT = 8791
EVENT_COUNT = 45

#: 量一下滚动容器离底部还有多远
_MEASURE_JS = """
() => {
  const el = document.getElementById('stream');
  if (!el) return null;
  return {
    scrollTop: el.scrollTop,
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    distanceFromBottom: el.scrollHeight - el.scrollTop - el.clientHeight,
    children: el.children.length,
    autoScroll: document.getElementById('autoscroll').checked,
    jumpHidden: document.getElementById('btn-jump').hidden
  };
}
"""


async def start_server(runtime: ConsoleRuntime) -> web.AppRunner:
    runner = web.AppRunner(create_app(runtime), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    return runner


async def main() -> int:
    config = load_config(project_root=ROOT)
    runtime = ConsoleRuntime(config)
    await runtime.start()
    runner = await start_server(runtime)

    browser_config = BrowserConfig(
        user_data_dir=".chrome-profile-console", auto_launch=True, launch_timeout=45
    )
    handle = await ensure_chrome(browser_config, ROOT / browser_config.user_data_dir)
    failures: list[str] = []
    try:
        attached = await attach(handle.cdp_url, runtime.site)
        page = await attached.context.new_page()
        url = f"http://127.0.0.1:{PORT}/"
        console.info(f"打开控制台 {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # 让控制台标签页成为前台：后台标签页里 rAF 不触发，
        # 会掩盖真实行为（虽然代码已经为此做了同步滚动兜底）
        await page.bring_to_front()
        await page.wait_for_selector("#stream", timeout=15_000)
        # 等 SSE 连上（hello 帧会立刻把状态推下来）
        await asyncio.sleep(1.5)

        print(f"[1] 推送 {EVENT_COUNT} 条日志事件…")
        flipped_at = None
        for index in range(EVENT_COUNT):
            runtime.bus.log(
                f"第 {index + 1} 条测试日志：用来把事件流撑到超过一屏，"
                "验证自动滚动会不会一直贴在底部。",
                level="info",
            )
            await asyncio.sleep(0.02)
            if flipped_at is None:
                checked = await page.evaluate(
                    "() => document.getElementById('autoscroll').checked"
                )
                if not checked:
                    flipped_at = index + 1
        await asyncio.sleep(1.2)
        if flipped_at is not None:
            print(f"    自动滚动在第 {flipped_at} 条推送后被关闭")

        state = await page.evaluate(_MEASURE_JS)
        if not state:
            failures.append("页面上找不到 #stream")
        else:
            print(
                f"[2] 容器：{state['children']} 个子节点，"
                f"内容高 {state['scrollHeight']}，可视高 {state['clientHeight']}，"
                f"距底部 {state['distanceFromBottom']:.0f}px"
            )
            if state["scrollHeight"] <= state["clientHeight"]:
                failures.append("事件流没有超出容器高度，这个测试没测到东西")
            if state["distanceFromBottom"] > 40:
                failures.append(
                    f"自动滚动没贴到底：距底部 {state['distanceFromBottom']:.0f}px"
                )
            if not state["autoScroll"]:
                failures.append("自动滚动开关不应该是关闭状态")

        print("[3] 模拟用户往上滚，应暂停自动滚动…")
        box = await page.evaluate(
            "() => { const r = document.getElementById('stream').getBoundingClientRect();"
            " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; }"
        )
        top_of = "() => document.getElementById('stream').scrollTop"
        before = await page.evaluate(top_of)
        await page.mouse.move(box["x"], box["y"])
        await page.mouse.wheel(0, -800)
        await asyncio.sleep(0.5)
        after = await page.evaluate(top_of)
        if after >= before:
            # CDP 接管的浏览器里 mouse.wheel 有时到不了容器（实测这台机器上就是）。
            # 退化成：先把视图挪上去，再派发一个真正的 WheelEvent，
            # 与浏览器自己产生的事件类型一致，走的还是同一段处理逻辑。
            print("    mouse.wheel 未生效，改用「移动视图 + 派发 WheelEvent」")
            await page.evaluate(
                "() => {"
                "  const el = document.getElementById('stream');"
                "  el.scrollTop = Math.max(0, el.scrollTop - 800);"
                "  el.dispatchEvent(new WheelEvent('wheel', {deltaY: -800, bubbles: true}));"
                "}"
            )
            await asyncio.sleep(0.5)

        paused = await page.evaluate(_MEASURE_JS)
        if paused and paused["autoScroll"]:
            failures.append("往上滚之后自动滚动没有暂停")
        if paused and paused["jumpHidden"]:
            failures.append("暂停后「回到底部」按钮应当出现")
        if paused and paused["distanceFromBottom"] < 100:
            failures.append("往上滚之后并没有真的离开底部，这一步没测到东西")

        # 再推一条，不应该把我们拽回底部
        runtime.bus.log("暂停期间的一条日志")
        await asyncio.sleep(0.6)
        still = await page.evaluate(_MEASURE_JS)
        if still and still["distanceFromBottom"] < 100:
            failures.append("暂停自动滚动后，新事件仍然把视图拽到了底部")

        print("[4] 点「回到底部」应恢复并贴底…")
        await page.click("#btn-jump")
        await asyncio.sleep(0.9)
        resumed = await page.evaluate(_MEASURE_JS)
        if resumed:
            if not resumed["autoScroll"]:
                failures.append("点回到底部后自动滚动没有重新打开")
            if resumed["distanceFromBottom"] > 40:
                failures.append(
                    f"点回到底部后仍距底部 {resumed['distanceFromBottom']:.0f}px"
                )

        print("[5] 恢复后继续推送，应持续贴底…")
        for index in range(10):
            runtime.bus.log(f"恢复后的第 {index + 1} 条日志")
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.8)
        final = await page.evaluate(_MEASURE_JS)
        if final and final["distanceFromBottom"] > 40:
            failures.append(
                f"恢复自动滚动后没有持续贴底：距底部 {final['distanceFromBottom']:.0f}px"
            )

        await attached.close()
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
        console.error(failures[-1])
    finally:
        await runner.cleanup()
        await runtime.close()
        await asyncio.sleep(0.5)
        if handle.launched:
            kill_chrome(handle)

    print()
    if failures:
        console.error("控制台冒烟测试失败：")
        for item in failures:
            console.console.print(f"  [red]-[/red] {item}")
        return 1
    console.success("控制台冒烟测试全部通过（自动滚动、暂停、回到底部）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
