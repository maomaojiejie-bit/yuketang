"""以调试模式启动 / 复用本机 Chrome。

只做两件事：确保 `http://127.0.0.1:<port>` 上有一个可用的 CDP 端点，
以及知道这个 Chrome 用的是哪个 user-data-dir。登录状态保存在后者里。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import BrowserConfig, ConfigError, find_chrome
from ..console import info, success, warn

LAUNCH_FLAGS = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate,OptimizationHints",
    "--disable-popup-blocking",
    "--restore-last-session=false",
)


@dataclass
class ChromeHandle:
    """已就绪的调试 Chrome。"""

    cdp_url: str
    user_data_dir: Path
    process: subprocess.Popen[bytes] | None = None
    launched: bool = False

    def describe(self) -> str:
        origin = "本程序启动" if self.launched else "复用已有进程"
        return f"{self.cdp_url}（{origin}，配置目录 {self.user_data_dir}）"


async def ensure_chrome(
    browser: BrowserConfig,
    user_data_dir: Path,
    *,
    headless_probe: bool = True,
) -> ChromeHandle:
    """返回一个可用的调试 Chrome，必要时自动拉起。"""
    port = port_from_cdp_url(browser.cdp_url)
    if await is_cdp_ready(browser.cdp_url):
        handle = ChromeHandle(browser.cdp_url, user_data_dir)
        info(f"已发现调试端口：{handle.describe()}")
        return handle

    if not browser.auto_launch:
        raise ConfigError(
            f"{browser.cdp_url} 上没有可用的调试端口，且 browser.auto_launch 为 false。\n"
            "请手动启动 Chrome（见 README「手动启动调试 Chrome」），或把 auto_launch 设为 true。"
        )

    chrome = find_chrome(browser.chrome_path)
    if not chrome:
        raise ConfigError(
            "没有找到 Chrome / Edge 可执行文件。请在 config.yaml 的 browser.chrome_path 中指定完整路径。"
        )

    user_data_dir.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        *LAUNCH_FLAGS,
    ]
    info(f"正在启动 Chrome：{' '.join(command)}")
    creation = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
    )

    if not await wait_for_cdp(browser.cdp_url, browser.launch_timeout):
        process.terminate()
        raise ConfigError(
            f"等待 {browser.launch_timeout}s 后调试端口仍未就绪：{browser.cdp_url}\n"
            "常见原因：该 user-data-dir 已被另一个 Chrome 实例占用。请关闭对应窗口后重试。"
        )

    success(f"Chrome 已就绪：{browser.cdp_url}")
    if headless_probe:
        info("首次使用时请在弹出的浏览器窗口里登录雨课堂，登录状态会被保存。")
    return ChromeHandle(browser.cdp_url, user_data_dir, process=process, launched=True)


def port_from_cdp_url(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    if parsed.port:
        return parsed.port
    return 9222


async def is_cdp_ready(cdp_url: str, timeout: float = 2.0) -> bool:
    """探测调试端点是否可用。"""
    url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return response.status_code == 200
    except Exception:
        return False


async def wait_for_cdp(cdp_url: str, timeout: float) -> bool:
    """轮询等待调试端点就绪。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await is_cdp_ready(cdp_url, timeout=1.5):
            return True
        await asyncio.sleep(0.5)
    return False


def kill_chrome(handle: ChromeHandle) -> None:
    """关闭由本程序启动的 Chrome。"""
    if handle.process is None:
        return
    try:
        handle.process.terminate()
    except Exception:  # pragma: no cover - 进程可能已退出
        warn("关闭 Chrome 时出现异常，可忽略。")
