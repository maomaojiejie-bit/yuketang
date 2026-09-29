"""浏览器驱动：通过 CDP 接管 Chrome 并定位到雨课堂页面。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..console import info, warn
from ..yuketang.constants import API_ON_LESSON, Site, is_yuketang_host

logger = logging.getLogger(__name__)


@dataclass
class AttachedBrowser:
    """一份已连接的浏览器资源，由调用方负责关闭 Playwright。"""

    playwright: Any
    browser: Browser
    context: BrowserContext
    page: Page
    site: Site

    async def close(self) -> None:
        # CDP 接管模式下 browser.close() 会连带关掉用户的 Chrome，这里只断开连接
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass


async def attach(cdp_url: str, site: Site) -> AttachedBrowser:
    """连接到调试 Chrome，并选出一个雨课堂页面（没有就新开一个）。"""
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        await playwright.stop()
        raise RuntimeError(
            f"无法通过 CDP 连接 {cdp_url}：{exc}\n"
            "请确认 Chrome 是以 --remote-debugging-port 启动的，且端口未被防火墙拦截。"
        ) from exc

    contexts = browser.contexts
    context = contexts[0] if contexts else await browser.new_context()
    page = pick_page(context, site) or await open_site_page(context, site)
    return AttachedBrowser(playwright, browser, context, page, site)


def pick_page(context: BrowserContext, site: Site) -> Page | None:
    """优先选当前站点页面，其次任意雨课堂页面，最后任意普通页面。"""
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        return None

    exact = [p for p in pages if _host_of(p.url) == site.host]
    if exact:
        return exact[0]

    any_yuketang = [p for p in pages if is_yuketang_host(_host_of(p.url))]
    if any_yuketang:
        warn(
            f"当前打开的雨课堂页面属于 {_host_of(any_yuketang[0].url)}，"
            f"与配置站点 {site.host} 不一致，仍将使用该页面。"
        )
        return any_yuketang[0]

    ordinary = [p for p in pages if not p.url.startswith(("devtools://", "chrome://", "edge://"))]
    if ordinary:
        warn(f"没有找到雨课堂标签页，将复用当前标签页：{ordinary[0].url[:80]}")
        return ordinary[0]

    # 只剩 chrome://newtab 这类内部页面：返回 None 让调用方另开一个站点标签页
    return None


async def open_site_page(context: BrowserContext, site: Site) -> Page:
    """新开一个标签页并导航到站点首页。"""
    page = await context.new_page()
    info(f"打开新标签页：{site.start_url}")
    try:
        await page.goto(site.start_url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as exc:
        warn(f"打开 {site.start_url} 失败（可稍后手动刷新）：{exc}")
    return page


async def resize_window(page: Page, width: int, height: int) -> bool:
    """把浏览器窗口调成指定大小。

    走 CDP 的 Browser.setWindowBounds，改的是真实窗口（不是页面视口），
    所以复用已经在跑的 Chrome 同样生效。失败只记日志，不影响解题主流程。
    """
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return False
    if width <= 0 or height <= 0:
        return False

    session = None
    try:
        session = await page.context.new_cdp_session(page)
        window = await session.send("Browser.getWindowForTarget")
        window_id = window.get("windowId") if isinstance(window, dict) else None
        if window_id is None:
            warn("没能取到 Chrome 窗口 id，跳过窗口尺寸调整。")
            return False
        await session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "left": 40,
                    "top": 40,
                    "width": width,
                    "height": height,
                    "windowState": "normal",
                },
            },
        )
        return True
    except Exception as exc:  # pragma: no cover - 依浏览器版本而异
        warn(f"调整浏览器窗口大小失败（可忽略，不影响解题）：{exc}")
        return False
    finally:
        if session is not None:
            try:
                await session.detach()
            except Exception:
                pass


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


async def goto_lesson(page: Page, site: Site, lesson_id: str, classroom_id: str | None = None) -> None:
    """跳到进行中的课堂页面。"""
    if classroom_id:
        url = f"{site.origin}/lesson/fullscreen/v3/{lesson_id}/{classroom_id}"
    else:
        url = f"{site.origin}/lesson/fullscreen/v3/{lesson_id}"
    info(f"跳转课堂：{url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)


#: 页内 fetch 兜底（页面必须停在雨课堂域名下，否则会被同源策略拦掉）
_FETCH_JSON_JS = """
async (url) => {
  try {
    const response = await fetch(url, { credentials: 'include' });
    const text = await response.text();
    return { ok: response.ok, status: response.status, text };
  } catch (error) {
    return { ok: false, status: 0, text: String(error) };
  }
}
"""

#: 这些 code / 关键字代表「没登录」，要和「确实没有课」区分开
_UNAUTHENTICATED_CODES = {401, 403, 50000}


def _is_unauthenticated(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    code = payload.get("code")
    if isinstance(code, (int, float)) and int(code) in _UNAUTHENTICATED_CODES:
        return True
    message = str(payload.get("msg") or payload.get("message") or "").upper()
    return "UNAUTHENTICATED" in message or "NOT LOGIN" in message


async def _on_lesson_text(page: Page, url: str) -> tuple[str | None, str]:
    """取 on-lesson 的响应正文，返回 (正文, 错误说明)。

    优先用 context.request：它自动带上浏览器 Cookie，而且**不受页面当前域名限制**
    （页面停在非雨课堂站点时，页内 fetch 会因 CORS 直接失败）。
    """
    context = getattr(page, "context", None)
    request = getattr(context, "request", None) if context is not None else None
    if request is not None:
        try:
            response = await request.get(
                url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": page.url or "",
                },
            )
            return await response.text(), ""
        except Exception as exc:
            logger.debug("context.request 查询 on-lesson 失败，回退页内 fetch：%s", exc)

    try:
        result = await page.evaluate(_FETCH_JSON_JS, url)
    except Exception as exc:
        return None, f"请求失败（页面可能已关闭）：{exc}"
    if not isinstance(result, dict) or not result.get("text"):
        return None, "请求失败：页面没有返回内容"
    if not result.get("ok"):
        status = result.get("status") or 0
        return str(result.get("text")), f"请求返回 HTTP {status}"
    return str(result.get("text")), ""


async def on_lesson_report(page: Page, site: Site) -> tuple[list[dict[str, Any]], str]:
    """查询进行中的课堂，返回 (课堂列表, 诊断信息)。

    诊断信息为空串表示查询本身没问题；非空时调用方应当把它展示给用户，
    否则「没登录」「接口变了」都会表现成「什么都没有发生」。
    """
    text, error = await _on_lesson_text(page, site.api(API_ON_LESSON))
    if text is None:
        return [], error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        lowered = text.lower()
        if "login" in lowered or "登录" in text:
            return [], (
                f"雨课堂返回了登录页（站点 {site.host}），说明当前浏览器还没登录（或登录已过期）"
            )
        return [], f"雨课堂返回的不是 JSON（站点 {site.host}），接口可能已变更"
    if _is_unauthenticated(payload):
        return [], (
            f"雨课堂提示未登录 / 登录已过期（站点 {site.host}），请在浏览器里重新登录"
        )
    if error:
        return [], error

    lessons = parse_on_lesson(payload)
    if not lessons and isinstance(payload, dict):
        code = payload.get("code")
        if code not in (None, 0):
            reason = payload.get("msg") or payload.get("message") or "未知原因"
            return [], f"接口返回 code={code}：{reason}"
    return lessons, ""


async def fetch_on_lesson(page: Page, site: Site) -> list[dict[str, Any]]:
    """只要列表的简易入口（保留给旧调用方）。"""
    lessons, _ = await on_lesson_report(page, site)
    return lessons


def parse_on_lesson(payload: Any) -> list[dict[str, Any]]:
    """解析 on-lesson 响应，兼容 data / result 包裹与多种字段命名。"""
    if not isinstance(payload, dict):
        return []
    root = payload.get("data") or payload.get("result") or payload
    if isinstance(root, list):
        raw = root
    elif isinstance(root, dict):
        raw = (
            root.get("onLessonClassrooms")
            or root.get("on_lesson_classrooms")
            or root.get("classrooms")
            or root.get("list")
            or root.get("items")
            or []
        )
    else:
        return []

    lessons: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        lesson_id = item.get("lessonId") or item.get("lesson_id") or item.get("id")
        if lesson_id is None:
            continue
        lessons.append(
            {
                "id": str(lesson_id),
                "title": str(
                    item.get("title")
                    or item.get("lessonName")
                    or item.get("lesson_name")
                    or item.get("name")
                    or ""
                ),
                "classroom_id": _str_or_none(
                    item.get("classroomId") or item.get("classroom_id")
                ),
                "presentation_id": _str_or_none(
                    item.get("presentationId") or item.get("presentation_id")
                ),
            }
        )
    return lessons


def _str_or_none(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    return str(value)
