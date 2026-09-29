"""网页控制台服务测试：用 aiohttp 自带的测试服务器，不占用真实端口。"""

from __future__ import annotations

import asyncio
import json
import re

from aiohttp.test_utils import TestClient, TestServer

from cjsolver.config import load_config
from cjsolver.web.runtime import ConsoleRuntime
from cjsolver.web.server import create_app


async def with_client(tmp_dir: object, action: object) -> object:
    """起一个临时服务，执行 action(client)，最后关闭。"""
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    runtime = ConsoleRuntime(config)
    await runtime.start()
    client = TestClient(TestServer(create_app(runtime)))
    await client.start_server()
    try:
        return await action(client)  # type: ignore[operator]
    finally:
        await client.close()
        await runtime.close()


def run(tmp_dir: object, action: object) -> object:
    return asyncio.run(with_client(tmp_dir, action))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 页面与状态
# --------------------------------------------------------------------------- #
def test_index_page_is_served_with_utf8(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, str, str]:
        response = await client.get("/")
        body = await response.text()
        return response.status, response.headers.get("Content-Type", ""), body

    status, content_type, body = run(tmp_dir, action)
    assert status == 200
    assert "utf-8" in content_type.lower()
    assert "本地控制台" in body
    assert "EventSource" in body
    assert "/api/simulate" in body



def test_index_page_exposes_custom_background(tmp_dir: object) -> None:
    """控制台要带上自定义背景的控件与持久化键，防止改版时被误删。"""

    async def action(client: TestClient) -> str:
        response = await client.get("/")
        return await response.text()

    body = run(tmp_dir, action)
    for marker in (
        'id="bg-layer"',
        'id="bg-veil"',
        'id="bg-enabled"',
        'id="bg-kind"',
        'id="bg-color"',
        'id="bg-grad-a"',
        'id="bg-url"',
        'id="bg-file"',
        'id="bg-veil"',
        'id="bg-blur"',
        'id="btn-bg-reset"',
        'id="settings-panel"',
        'id="btn-settings-toggle"',
        'id="btn-settings-close"',
        'id="auto-enter-lesson"',
        'id="txt-lesson"',
        'id="dot-lesson"',
        'id="enter-lesson-interval"',
        'id="window-width"',
        'id="window-height"',
        "cjsolver.background.v1",
        "自定义背景",
    ):
        assert marker in body, marker


def test_state_endpoint(tmp_dir: object) -> None:
    async def action(client: TestClient) -> dict:
        response = await client.get("/api/state")
        assert response.status == 200
        return await response.json()

    state = run(tmp_dir, action)
    assert state["site"]["key"] == "changjiang"
    assert state["answer"]["mode"] == "manual"
    assert state["model"]["configured"] is False   # 测试环境下没有 Key
    assert state["browser"]["attached"] is False
    assert state["watching"] is False
    assert state["stats"] is None
    assert state["last_simulation"] is None


def test_settings_endpoint_round_trip(tmp_dir: object) -> None:
    """控制台设置要能读、能写，并且立刻反映到 /api/state。"""

    async def action(client: TestClient) -> tuple[dict, int, dict, dict]:
        first = await (await client.get("/api/settings")).json()
        response = await client.post(
            "/api/settings",
            json={
                "answer": {"mode": "dom"},
                "solver": {"auto_enter_lesson": True, "enter_lesson_interval": 45},
                "browser": {"window_width": 1024},
            },
        )
        updated = await response.json()
        state = await (await client.get("/api/state")).json()
        return first, response.status, updated, state

    first, status, updated, state = run(tmp_dir, action)

    assert first["ok"] is True
    assert first["values"]["solver"]["auto_enter_lesson"] is False
    assert first["modes"] == ["manual", "dom", "api"]
    assert status == 200
    assert updated["values"]["solver"]["auto_enter_lesson"] is True
    assert updated["values"]["solver"]["enter_lesson_interval"] == 45.0
    assert updated["values"]["browser"]["window_width"] == 1024
    # 未提交的字段不受影响
    assert updated["values"]["browser"]["window_height"] == 600
    assert state["answer"]["mode"] == "dom"
    assert state["solver"]["auto_enter_lesson"] is True


def test_settings_endpoint_rejects_bad_values(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post("/api/settings", json={"answer": {"mode": "nope"}})
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    assert status == 400
    assert payload["ok"] is False
    assert "作答模式" in payload["error"]


def test_settings_persist_to_disk(tmp_dir: object) -> None:
    async def action(client: TestClient) -> None:
        await client.post("/api/settings", json={"solver": {"auto_enter_lesson": True}})

    run(tmp_dir, action)
    saved = (tmp_dir / "console-settings.json").read_text(encoding="utf-8")  # type: ignore[operator]
    assert "auto_enter_lesson" in saved


def test_probe_endpoint(tmp_dir: object) -> None:
    async def action(client: TestClient) -> dict:
        response = await client.get("/api/probe")
        return await response.json()

    probe = run(tmp_dir, action)
    assert probe["api_key_configured"] is False
    assert probe["cdp_url"].startswith("http://127.0.0.1:")
    assert isinstance(probe["cdp_ready"], bool)


# --------------------------------------------------------------------------- #
# 模型服务商
# --------------------------------------------------------------------------- #
def test_providers_endpoint(tmp_dir: object) -> None:
    async def action(client: TestClient) -> dict:
        response = await client.get("/api/providers")
        assert response.status == 200
        return await response.json()

    payload = run(tmp_dir, action)
    assert payload["ok"] is True
    ids = [item["id"] for item in payload["providers"]]
    # 用户点名要的三家
    for wanted in ("qwen", "doubao", "hunyuan", "deepseek"):
        assert wanted in ids, wanted
    assert payload["current"] == "deepseek"
    assert payload["generic_key_env"] == "AI_API_KEY"
    qwen = next(item for item in payload["providers"] if item["id"] == "qwen")
    assert qwen["key_env"] == "DASHSCOPE_API_KEY"
    assert qwen["models"]


def test_console_page_has_model_service_controls(tmp_dir: object) -> None:
    """控制台页面里要有服务商/模型/Base URL 三个控件，以及自动滚动的相关标记。"""

    async def action(client: TestClient) -> str:
        return await (await client.get("/")).text()

    html = run(tmp_dir, action)

    for marker in ('id="provider"', 'id="model-options"', 'id="base-url"', 'id="provider-hint"'):
        assert marker in html, marker
    assert "/api/providers" in html
    assert "loadProviders" in html

    # 自动滚动：CSS 里不能有 scroll-behavior: smooth（会让自动滚动追不到底）。
    # 先剥掉注释，否则会误伤解释这条规则的说明文字。
    style = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    assert "scroll-behavior: smooth" not in style
    assert "'instant'" in html
    # 只看用户手势，不再用自己滚出来的 scroll 事件推断意图
    assert "addEventListener('wheel'" in html
    assert "programmaticScrollUntil" not in html
    assert "visibilitychange" in html


def test_settings_can_switch_provider(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[dict, dict]:
        response = await client.post(
            "/api/settings",
            json={
                "deepseek": {
                    "provider": "qwen",
                    "model": "qwen-max",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                }
            },
        )
        assert response.status == 200, await response.text()
        saved = await response.json()
        after = await (await client.get("/api/providers")).json()
        return saved, after

    saved, after = run(tmp_dir, action)
    assert saved["ok"] is True
    assert saved["values"]["deepseek"]["provider"] == "qwen"
    assert saved["values"]["deepseek"]["model"] == "qwen-max"
    assert after["current"] == "qwen"
    assert after["model"] == "qwen-max"


def test_settings_reject_unknown_provider(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post(
            "/api/settings", json={"deepseek": {"provider": "not-a-provider"}}
        )
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    assert status == 400
    assert "未知的服务商" in payload["error"]


def test_settings_reject_bad_base_url(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post(
            "/api/settings", json={"deepseek": {"base_url": "ftp://nope"}}
        )
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    assert status == 400
    assert "http://" in payload["error"]


def test_switching_provider_drops_foreign_key(tmp_dir: object) -> None:
    """切到千问后不能还把 DeepSeek 的 Key 带着——那只会换来一个 401。"""
    import os

    async def action(client: TestClient) -> tuple[dict, dict]:
        os.environ["DEEPSEEK_API_KEY"] = "sk-deep"
        try:
            await client.post("/api/settings", json={"deepseek": {"provider": "qwen"}})
            providers_payload = await (await client.get("/api/providers")).json()
            state = await (await client.get("/api/state")).json()
        finally:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        return providers_payload, state

    providers_payload, state = run(tmp_dir, action)
    assert providers_payload["current"] == "qwen"
    # 没有 DASHSCOPE_API_KEY，所以模型应当被视为"未配置"
    assert providers_payload["key_configured"] is False
    assert state["model"]["configured"] is False


# --------------------------------------------------------------------------- #
# 模拟检测
# --------------------------------------------------------------------------- #
def test_simulate_offline_endpoint(tmp_dir: object) -> None:
    async def action(client: TestClient) -> dict:
        response = await client.post("/api/simulate", json={"kind": "offline"})
        assert response.status == 200, await response.text()
        return await response.json()

    payload = run(tmp_dir, action)
    assert payload["ok"] is True
    assert payload["report"]["ok"] is True
    keys = [check["key"] for check in payload["report"]["checks"]]
    assert "capture.presentation" in keys
    assert "answer.format" in keys
    # 没有 Key，模型项应当被跳过而不是判失败
    model = next(c for c in payload["report"]["checks"] if c["key"] == "model.deepseek")
    assert model["skipped"] is True and model["passed"] is True


def test_simulate_unknown_kind_returns_400(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post("/api/simulate", json={"kind": "nope"})
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    assert status == 400
    assert payload["ok"] is False
    assert "未知的模拟类型" in payload["error"]


def test_simulate_live_without_watcher_returns_400(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post("/api/simulate", json={"kind": "live"})
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    assert status == 400
    assert "先启动监听" in payload["error"]


def test_last_simulation_is_remembered_in_state(tmp_dir: object) -> None:
    async def action(client: TestClient) -> dict:
        await client.post("/api/simulate", json={"kind": "offline"})
        response = await client.get("/api/state")
        return await response.json()

    state = run(tmp_dir, action)
    assert state["last_simulation"]["ok"] is True


# --------------------------------------------------------------------------- #
# 其它接口
# --------------------------------------------------------------------------- #
def test_ask_without_api_key_is_rejected(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post("/api/ask", json={"text": "单选 1+1=?\nA. 1\nB. 2"})
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    assert status == 400
    assert "API Key" in payload["error"]


def test_ask_with_empty_text_is_rejected(tmp_dir: object) -> None:
    async def action(client: TestClient) -> int:
        response = await client.post("/api/ask", json={"text": "   "})
        return response.status

    assert run(tmp_dir, action) == 400


def test_watch_start_without_api_key_is_rejected(tmp_dir: object) -> None:
    async def action(client: TestClient) -> tuple[int, dict]:
        response = await client.post("/api/watch/start", json={"mode": "manual"})
        return response.status, await response.json()

    status, payload = run(tmp_dir, action)
    # 没有 Key 时应当明确报错，而不是尝试启动会话
    assert status == 400
    assert payload["ok"] is False


def test_watch_stop_is_idempotent(tmp_dir: object) -> None:
    async def action(client: TestClient) -> bool:
        response = await client.post("/api/watch/stop", json={})
        payload = await response.json()
        return payload["ok"]

    assert run(tmp_dir, action) is True


def test_events_json_endpoint(tmp_dir: object) -> None:
    async def action(client: TestClient) -> dict:
        await client.post("/api/simulate", json={"kind": "offline"})
        response = await client.get("/api/events.json?limit=100")
        return await response.json()

    payload = run(tmp_dir, action)
    types = [event["type"] for event in payload["events"]]
    assert "simulation" in types
    assert "sim-log" in types


def test_sse_stream_sends_hello_frame(tmp_dir: object) -> None:
    captured: list[dict] = []

    async def action(client: TestClient) -> None:
        response = await client.get("/api/events")
        assert response.status == 200
        assert "text/event-stream" in response.headers["Content-Type"]
        try:
            # 单次 read 可能把一帧截断，这里累积到出现空行为止
            buffer = ""
            while "\n\n" not in buffer:
                chunk = await asyncio.wait_for(response.content.read(1024), timeout=5)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
            frame = buffer.split("\n\n", 1)[0]
            assert frame.startswith("data: ")
            captured.append(json.loads(frame[len("data: "):]))
        finally:
            response.close()

    run(tmp_dir, action)
    assert captured, "SSE 应当立刻推送第一帧"
    event = captured[0]
    # hello 是服务端合成的一帧（不走总线，因此没有 seq），用来让新页面立刻拿到状态
    assert event["type"] == "hello"
    assert event["data"]["site"]["key"] == "changjiang"
    assert event["data"]["watching"] is False


def test_unknown_route_returns_404(tmp_dir: object) -> None:
    async def action(client: TestClient) -> int:
        response = await client.get("/api/nope")
        return response.status

    assert run(tmp_dir, action) == 404
