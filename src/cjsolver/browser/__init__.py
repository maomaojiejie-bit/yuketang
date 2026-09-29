"""浏览器层：调试 Chrome 的启动/复用与 Playwright 接管。"""

from .driver import (
    AttachedBrowser,
    attach,
    fetch_on_lesson,
    on_lesson_report,
    goto_lesson,
    parse_on_lesson,
    pick_page,
    resize_window,
)
from .launcher import ChromeHandle, ensure_chrome, is_cdp_ready, kill_chrome

__all__ = [
    "AttachedBrowser",
    "ChromeHandle",
    "attach",
    "ensure_chrome",
    "fetch_on_lesson",
    "goto_lesson",
    "is_cdp_ready",
    "kill_chrome",
    "on_lesson_report",
    "parse_on_lesson",
    "pick_page",
    "resize_window",
]
