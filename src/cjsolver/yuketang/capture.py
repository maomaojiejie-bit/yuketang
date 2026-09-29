"""在页面上挂网络 / WebSocket 监听，实时捕获新下发的随堂习题。

除了真实的 Playwright 事件，本模块还暴露 `feed_payload` / `feed_frame_text`，
可以把任意一份 JSON 载荷当成"刚收到的流量"直接走同一条解析与去重链路。
模拟检测功能正是靠这条路径，在没有真实课堂的情况下验证抓题能力。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from playwright.async_api import Page, Response, WebSocket

from ..models import Problem
from .constants import PROBLEM_BEARING_PATHS, Site, is_yuketang_host
from .parsing import extract_problems

logger = logging.getLogger(__name__)

ProblemHandler = Callable[[Problem], Awaitable[None]]


class QuestionCapture:
    """监听一个页面的流量，把新题目推给回调。

    `page` 可以为 None——此时只启用离线投喂能力（供模拟检测使用）。
    """

    def __init__(
        self,
        page: Page | None,
        site: Site,
        handler: ProblemHandler,
        *,
        dump_dir: Path | None = None,
    ) -> None:
        self._page = page
        self._site = site
        self._handler = handler
        self._dump_dir = dump_dir
        self._seen: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._started = False
        self.stats: Counter[str] = Counter()
        #: 已捕获题目（按捕获顺序），供控制台展示
        self.captured: list[Problem] = []

    # -- 生命周期 ---------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        if self._page is not None:
            self._page.on("response", self._on_response)
            self._page.on("websocket", self._on_websocket)
        self._started = True
        logger.info("已开始监听页面流量：%s", self._site.host)

    async def stop(self) -> None:
        if not self._started:
            return
        if self._page is not None:
            try:
                self._page.remove_listener("response", self._on_response)
                self._page.remove_listener("websocket", self._on_websocket)
            except Exception:  # pragma: no cover - 页面可能已关闭
                pass
        self._started = False
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # -- 离线投喂（模拟检测 / 手工导入） ----------------------------------
    async def feed_payload(
        self,
        payload: Any,
        *,
        origin: str = "manual",
        presentation_id: str = "",
        source: str = "network",
    ) -> list[Problem]:
        """把一份 JSON 载荷当成刚抓到的流量处理，返回新识别出的题目。"""
        problems = extract_problems(
            payload, presentation_id=presentation_id, source=source
        )
        return await self._emit(problems, origin=origin)

    async def feed_frame_text(self, text: str, *, origin: str = "simulation:ws") -> list[Problem]:
        """把一帧 WebSocket 文本当成刚收到的事件处理。"""
        data = _loads(text)
        if data is None:
            return []
        self.stats["ws_frame"] += 1
        self._maybe_dump("ws", origin, data)
        return await self.feed_payload(data, origin=origin, source="websocket")

    # -- 回调（Playwright 事件是同步调用的，异步工作转成任务） ------------
    def _on_response(self, response: Response) -> None:
        if not is_interesting_url(response.url, self._site):
            return
        self._spawn(self._process_response(response))

    def _on_websocket(self, websocket: WebSocket) -> None:
        self.stats["websocket"] += 1
        websocket.on(
            "framereceived",
            lambda payload: self._spawn(self._process_frame(payload)),
        )

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- 解析 -------------------------------------------------------------
    async def _process_response(self, response: Response) -> None:
        try:
            if response.status < 200 or response.status >= 300:
                return
            headers = await response.all_headers()
            content_type = headers.get("content-type", "").lower()
            if "json" not in content_type:
                return
            body = await response.text()
        except Exception as exc:  # 请求可能被取消或响应体不可读
            logger.debug("读取响应失败 %s：%s", response.url, exc)
            return

        payload = _loads(body)
        if payload is None:
            return
        self.stats["response"] += 1
        self._maybe_dump("http", response.url, payload)
        await self.feed_payload(
            payload,
            origin=response.url,
            presentation_id=_presentation_id_from_url(response.url),
        )

    async def _process_frame(self, payload: Any) -> None:
        if isinstance(payload, (bytes, bytearray)):
            try:
                text = payload.decode("utf-8", errors="ignore")
            except Exception:
                return
        else:
            text = payload if isinstance(payload, str) else ""
        await self.feed_frame_text(text)

    async def _emit(self, problems: list[Problem], *, origin: str) -> list[Problem]:
        fresh: list[Problem] = []
        for problem in problems:
            if problem.key in self._seen:
                continue
            self._seen.add(problem.key)
            self.stats["problem"] += 1
            self.captured.append(problem)
            fresh.append(problem)
            logger.info("捕获题目 %s（来自 %s）", problem.id, origin)
            try:
                await self._handler(problem)
            except Exception:
                logger.exception("处理题目 %s 时出错", problem.id)
        return fresh

    def mark_seen(self, problem: Problem) -> None:
        """把 DOM 兜底发现的题目登记为已见，避免重复上报。"""
        self._seen.add(problem.key)
        self.captured.append(problem)

    def _maybe_dump(self, kind: str, url: str, payload: Any) -> None:
        if self._dump_dir is None:
            return
        try:
            self._dump_dir.mkdir(parents=True, exist_ok=True)
            self.stats["dump"] = self.stats["dump"] + 1
            path = self._dump_dir / f"{self.stats['dump']:04d}-{kind}.json"
            path.write_text(
                json.dumps({"url": url, "payload": payload}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover - 磁盘问题
            logger.debug("落盘原始载荷失败：%s", exc)


def is_interesting_url(url: str, site: Site) -> bool:
    """判断某个响应是否值得解析。"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not is_yuketang_host(host):
        return False
    if is_interesting_path(parsed.path):
        return True
    # 有些部署把接口放在网关路径下，靠查询串识别
    return bool(parsed.query) and any(
        token in parsed.query.lower() for token in ("problem", "presentation")
    )


def is_interesting_path(path: str) -> bool:
    lowered = path.lower()
    return any(token in lowered for token in PROBLEM_BEARING_PATHS)


def _presentation_id_from_url(url: str) -> str:
    try:
        query = urlparse(url).query
    except Exception:
        return ""
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key in ("presentation_id", "presentationId"):
            return value
    return ""


def _loads(text: str) -> Any:
    if not text:
        return None
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
