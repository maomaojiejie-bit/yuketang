"""浏览器层冒烟测试：启动调试 Chrome -> CDP 接管 -> DOM 抓题 -> 点击选项。

不会访问雨课堂，而是往页面里塞一段仿真的答题卡片，用来验证：
  - Chrome 能否以调试模式被拉起并被 Playwright 接管
  - DOM 扫描能否识别出题干与 A/B/C 选项
  - 点击选项能否真的选中对应的单选框
  - 提交按钮能否被找到并点击

    python tools/smoke_browser.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cjsolver.browser import attach, ensure_chrome  # noqa: E402
from cjsolver.browser.launcher import kill_chrome  # noqa: E402
from cjsolver.config import BrowserConfig  # noqa: E402
from cjsolver.yuketang.constants import site_by_key  # noqa: E402
from cjsolver.yuketang.dom import click_option, click_submit, scan_problem  # noqa: E402

MOCK_PAGE = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>仿真随堂练习</title></head>
<body style="font-family: sans-serif; padding: 40px">
  <div class="problem-card" style="border:1px solid #ccc; padding:24px; width:640px">
    <div class="stem">单选题 下列关于进程与线程的说法，正确的是？</div>
    <div class="options" style="margin-top:16px">
      <label style="display:block; padding:8px"><input type="radio" name="q1"> <span>A. 进程是资源分配的基本单位</span></label>
      <label style="display:block; padding:8px"><input type="radio" name="q1"> <span>B. 线程是资源分配的基本单位</span></label>
      <label style="display:block; padding:8px"><input type="radio" name="q1"> <span>C. 同一进程内的线程不共享地址空间</span></label>
    </div>
    <button id="submit" style="margin-top:16px; padding:8px 24px">提交</button>
  </div>
</body></html>
"""


async def main() -> int:
    browser_config = BrowserConfig(
        user_data_dir=".chrome-profile-smoke", auto_launch=True, launch_timeout=45
    )
    handle = await ensure_chrome(browser_config, ROOT / browser_config.user_data_dir)
    failures: list[str] = []
    try:
        attached = await attach(handle.cdp_url, site_by_key("changjiang"))
        print(f"[1] 已接管浏览器，当前页面：{attached.page.url}")

        # 不能直接往「新标签页」写内容：chrome://newtab 启用了 Trusted Types
        page = await attached.context.new_page()
        await page.set_content(MOCK_PAGE)
        print("[2] 已注入仿真答题卡片")

        problem = await scan_problem(page, [])
        if problem is None:
            failures.append("DOM 扫描没有识别出题目")
        else:
            print(f"[3] 扫描到题目 id={problem.id} 题型={problem.type.label}")
            print(f"    题干：{problem.prompt!r}")
            for option in problem.options:
                print(f"    {option.letter}. {option.text}")
            if len(problem.options) != 3:
                failures.append(f"选项数量不对：{len(problem.options)} != 3")
            if "进程与线程" not in problem.prompt:
                failures.append(f"题干没抓对：{problem.prompt!r}")

        clicked = await click_option(
            page, letter="B", text="线程是资源分配的基本单位", selectors=[]
        )
        checked = await page.evaluate(
            "() => [...document.querySelectorAll('input')].findIndex(i => i.checked)"
        )
        print(f"[4] 点击选项 B：{clicked}，被选中的下标：{checked}")
        if not clicked:
            failures.append("点击选项 B 失败")
        if checked != 1:
            failures.append(f"点错了选项：期望下标 1，实际 {checked}")

        submitted = await click_submit(page, ["提交", "确定"])
        print(f"[5] 点击提交按钮：{submitted}")
        if not submitted:
            failures.append("没有找到提交按钮")

        await attached.close()
    finally:
        await asyncio.sleep(0.5)
        if handle.launched:
            kill_chrome(handle)

    if failures:
        print("\n冒烟测试失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n浏览器层冒烟测试全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
