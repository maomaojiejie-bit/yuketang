"""浏览器窗口尺寸调整的单元测试（不真的连浏览器）。"""

from __future__ import annotations

import asyncio
from typing import Any

from cjsolver.browser.driver import resize_window


class FakeCdpSession:
    """记录 CDP 调用的桩；默认能取到 windowId。"""

    def __init__(self, window: dict[str, Any] | None = None, boom: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, Any] | None]] = []
        self.detached = False
        self._window = {"windowId": 7, "bounds": {"width": 1600, "height": 900}} if window is None else window
        self._boom = boom

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append((method, params))
        if method == "Browser.getWindowForTarget":
            return self._window
        return {}

    async def detach(self) -> None:
        self.detached = True


class FakePage:
    """够用的 page 桩：resize_window 只用到 page.context.new_cdp_session。"""

    def __init__(self, session: FakeCdpSession | None = None, boom: bool = False) -> None:
        self.context = self
        self.session = session or FakeCdpSession()
        self._boom = boom

    async def new_cdp_session(self, page: object) -> FakeCdpSession:
        if self._boom:
            raise RuntimeError("CDP 不可用")
        return self.session


def _resize(width: int, height: int, page: FakePage | None = None) -> tuple[bool, FakePage]:
    target = page or FakePage()
    ok = asyncio.run(resize_window(target, width, height))  # type: ignore[arg-type]
    return ok, target


def test_resize_window_sets_bounds() -> None:
    ok, page = _resize(800, 600)

    assert ok is True
    assert [method for method, _ in page.session.sent] == [
        "Browser.getWindowForTarget",
        "Browser.setWindowBounds",
    ]
    params = page.session.sent[1][1]
    assert params is not None
    assert params["windowId"] == 7
    assert params["bounds"]["width"] == 800
    assert params["bounds"]["height"] == 600
    # 窗口可能处于最大化，必须显式回到 normal，尺寸才会生效
    assert params["bounds"]["windowState"] == "normal"
    assert page.session.detached is True


def test_resize_window_non_positive_is_noop() -> None:
    for width, height in ((0, 600), (800, 0), (-1, -1)):
        ok, page = _resize(width, height)
        assert ok is False
        assert page.session.sent == []


def test_resize_window_survives_cdp_failure() -> None:
    ok, _ = _resize(800, 600, FakePage(boom=True))
    assert ok is False


def test_resize_window_without_window_id_skips_set_bounds() -> None:
    ok, page = _resize(800, 600, FakePage(FakeCdpSession(window={})))

    assert ok is False
    assert [method for method, _ in page.session.sent] == ["Browser.getWindowForTarget"]
    assert page.session.detached is True


def test_resize_window_rejects_garbage() -> None:
    ok, page = _resize("宽", 600)  # type: ignore[arg-type]
    assert ok is False
    assert page.session.sent == []
