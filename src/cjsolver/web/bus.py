"""进程内事件总线：把 Solver / 模拟器的进展推给网页控制台。

同一个事件循环内运行，所以不需要锁；订阅者各自持有一个有界队列，
满了就丢最旧的，避免慢客户端拖垮主流程。
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Any, Iterator

#: 每个订阅者的队列容量
QUEUE_SIZE = 500
#: 新订阅者能补收到的历史事件条数
REPLAY_SIZE = 200


class EventBus:
    """轻量发布订阅。"""

    def __init__(self, *, replay_size: int = REPLAY_SIZE) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._replay: deque[dict[str, Any]] = deque(maxlen=replay_size)
        self._counter = itertools.count(1)

    # -- 发布 -------------------------------------------------------------
    def publish(
        self,
        event: dict[str, Any] | str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """发布一条事件。

        两种写法都支持：
          - `publish({"type": "answer", "data": {...}})`（Solver 直接把它当回调用）
          - `publish("answer", data={...})`
        event 必须是可 JSON 序列化的普通字典。
        """
        if isinstance(event, str):
            payload: dict[str, Any] = {"type": event, "data": data or {}}
        else:
            payload = dict(event)
            if data is not None:
                payload.setdefault("data", data)

        payload.setdefault("seq", next(self._counter))
        payload.setdefault("ts", time.time())
        self._replay.append(payload)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # 丢最旧的，保证订阅者不会因为处理慢而阻塞发布方
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass

    def log(self, message: str, level: str = "info") -> None:
        """发布一条纯文本日志。"""
        self.publish({"type": "log", "data": {"message": message, "level": level}})

    # -- 订阅 -------------------------------------------------------------
    def subscribe(self, *, replay: bool = True) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        if replay:
            for event in list(self._replay)[-REPLAY_SIZE:]:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:  # pragma: no cover
                    break
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._replay)

    def iter_snapshot(self, limit: int = 100) -> Iterator[dict[str, Any]]:
        for event in list(self._replay)[-limit:]:
            yield event

    def clear(self) -> None:
        self._replay.clear()
