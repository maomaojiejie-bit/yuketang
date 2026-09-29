"""事件总线测试。"""

from __future__ import annotations

import asyncio

from cjsolver.web.bus import EventBus


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_publish_accepts_dict_form() -> None:
    bus = EventBus()
    bus.publish({"type": "answer", "data": {"ok": True}})

    events = bus.snapshot()
    assert len(events) == 1
    assert events[0]["type"] == "answer"
    assert events[0]["data"] == {"ok": True}
    assert "seq" in events[0] and "ts" in events[0]


def test_publish_accepts_type_and_data_form() -> None:
    """Solver 走字典形式，运行时走 (type, data=...) 形式，两者都必须支持。"""
    bus = EventBus()
    bus.publish("browser", data={"attached": True})
    bus.publish("watching")

    types = [event["type"] for event in bus.snapshot()]
    assert types == ["browser", "watching"]
    assert bus.snapshot()[0]["data"] == {"attached": True}
    assert bus.snapshot()[1]["data"] == {}


def test_sequence_numbers_increase() -> None:
    bus = EventBus()
    for index in range(5):
        bus.publish({"type": "tick", "data": {"index": index}})

    seqs = [event["seq"] for event in bus.snapshot()]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5


def test_subscriber_receives_published_events() -> None:
    async def scenario() -> list[dict]:
        bus = EventBus()
        queue = bus.subscribe(replay=False)
        bus.publish("log", data={"message": "hello"})
        first = await asyncio.wait_for(queue.get(), timeout=1)
        bus.unsubscribe(queue)
        bus.publish("log", data={"message": "after"})
        assert queue.empty()
        return [first]

    received = run(scenario())
    assert received[0]["data"]["message"] == "hello"  # type: ignore[index]


def test_subscriber_gets_replay_history() -> None:
    async def scenario() -> list[dict]:
        bus = EventBus()
        bus.publish("log", data={"message": "old-1"})
        bus.publish("log", data={"message": "old-2"})
        queue = bus.subscribe(replay=True)
        return [await asyncio.wait_for(queue.get(), timeout=1) for _ in range(2)]

    received = run(scenario())
    assert [event["data"]["message"] for event in received] == ["old-1", "old-2"]  # type: ignore[index]


def test_slow_subscriber_drops_oldest_instead_of_blocking() -> None:
    async def scenario() -> tuple[int, int, int]:
        bus = EventBus()
        queue = bus.subscribe(replay=False)
        for index in range(700):  # 超过队列容量
            bus.publish("log", data={"index": index})
        assert queue.qsize() == queue.maxsize
        front = await asyncio.wait_for(queue.get(), timeout=1)
        # 把剩下的全部取空，最后一条应当是最新发布的那条
        last = front
        while not queue.empty():
            last = queue.get_nowait()
        return queue.maxsize, int(front["data"]["index"]), int(last["data"]["index"])

    maxsize, front, last = run(scenario())
    # 队满后丢最旧的：留下的是最近 maxsize 条，队首是 700-maxsize，队尾是 699
    assert front == 700 - maxsize
    assert last == 699


def test_subscriber_does_not_block_publisher() -> None:
    """即使没人消费，publish 也必须立即返回。"""
    bus = EventBus()
    bus.subscribe(replay=False)
    for index in range(5000):
        bus.publish("log", data={"index": index})
    assert len(bus.snapshot()) <= 200  # 历史也有上限


def test_subscriber_count_and_clear() -> None:
    async def scenario() -> int:
        bus = EventBus()
        queue = bus.subscribe()
        count = bus.subscriber_count
        bus.unsubscribe(queue)
        return count

    assert run(scenario()) == 1

    bus = EventBus()
    bus.publish("log", data={"message": "x"})
    assert bus.snapshot()
    bus.clear()
    assert bus.snapshot() == []


def test_log_helper_sets_level() -> None:
    bus = EventBus()
    bus.log("出问题了", level="warn")
    event = bus.snapshot()[0]
    assert event["data"] == {"message": "出问题了", "level": "warn"}


def test_replay_is_bounded() -> None:
    bus = EventBus(replay_size=10)
    for index in range(50):
        bus.publish("log", data={"index": index})
    assert len(bus.snapshot()) == 10
    assert bus.snapshot()[-1]["data"]["index"] == 49
