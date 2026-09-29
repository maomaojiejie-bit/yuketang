"""DeepSeek 客户端测试：用 MockTransport 覆盖请求形状与各种失败路径。"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import httpx
import pytest

from cjsolver.ai.deepseek import AIError, DeepSeekClient
from cjsolver.config import DeepSeekConfig
from cjsolver.models import Option, Problem, ProblemType


def make_problem() -> Problem:
    return Problem(
        id="p1",
        type=ProblemType.SINGLE_CHOICE,
        prompt="HTTP 默认端口是多少？",
        options=[Option(0, "21"), Option(1, "80"), Option(2, "443")],
    )


def config(**overrides: object) -> DeepSeekConfig:
    base = DeepSeekConfig(api_key="sk-test", max_retries=1, base_url="https://api.deepseek.com")
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_successful_call_and_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_response(json.dumps({"answer": "B", "explanation": "80", "confidence": 0.95}))

    async def scenario() -> object:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(), transport=transport) as client:
            return await client.ask(make_problem())

    suggestion = run(scenario())

    assert suggestion.letters == ["B"]
    assert suggestion.confidence == 0.95
    assert suggestion.model == "deepseek-chat"

    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"
    assert "HTTP 默认端口" in body["messages"][1]["content"]


def test_base_url_with_v1_suffix_is_kept() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return chat_response('{"answer": "A"}')

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(
            config(base_url="https://api.deepseek.com/v1/"), transport=transport
        ) as client:
            await client.ask(make_problem())

    run(scenario())
    assert seen == ["https://api.deepseek.com/v1/chat/completions"]


def test_falls_back_when_json_mode_unsupported() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
        return chat_response('{"answer": "B"}')

    async def scenario() -> object:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(), transport=transport) as client:
            return await client.ask(make_problem())

    suggestion = run(scenario())
    assert suggestion.letters == ["B"]
    assert len(bodies) == 2
    assert "response_format" in bodies[0]
    assert "response_format" not in bodies[1]


def test_retries_on_429_then_succeeds() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return chat_response('{"answer": "C"}')

    async def scenario() -> object:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(max_retries=2), transport=transport) as client:
            return await client.ask(make_problem())

    # 退避 sleep 会真的等待，这里把 2s 的退避压到最小
    import cjsolver.ai.deepseek as module

    original = module.asyncio.sleep
    module.asyncio.sleep = lambda _seconds: original(0)  # type: ignore[assignment]
    try:
        suggestion = run(scenario())
    finally:
        module.asyncio.sleep = original  # type: ignore[assignment]

    assert suggestion.letters == ["C"]
    assert len(calls) == 2


def test_auth_error_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(max_retries=3), transport=transport) as client:
            await client.ask(make_problem())

    with pytest.raises(AIError, match="401"):
        run(scenario())
    assert len(calls) == 1  # 鉴权错误不应重试


def test_malformed_success_body_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(), transport=transport) as client:
            await client.ask(make_problem())

    with pytest.raises(AIError, match="choices"):
        run(scenario())


def test_content_as_parts_is_joined() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": [{"type": "text", "text": '{"answer": "A"}'}]}}]},
        )

    async def scenario() -> object:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(), transport=transport) as client:
            return await client.ask(make_problem())

    assert run(scenario()).letters == ["A"]  # type: ignore[union-attr]


def test_vision_model_switches_when_images_present() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return chat_response('{"answer": "A"}')

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        cfg = config(vision_enabled=True, vision_model="qwen-vl-max")
        async with DeepSeekClient(cfg, transport=transport) as client:
            await client.ask(make_problem(), images=["https://example.com/slide.png"])

    run(scenario())
    assert bodies[0]["model"] == "qwen-vl-max"
    content = bodies[0]["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"


def test_vision_disabled_ignores_images() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return chat_response('{"answer": "A"}')

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(), transport=transport) as client:
            await client.ask(make_problem(), images=["https://example.com/slide.png"])

    run(scenario())
    assert bodies[0]["model"] == "deepseek-chat"
    assert isinstance(bodies[0]["messages"][1]["content"], str)


def test_extra_prompt_is_included() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return chat_response('{"answer": "A"}')

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(
            config(extra_prompt="务必给出中文解析"), transport=transport
        ) as client:
            await client.ask(make_problem())

    run(scenario())
    assert "务必给出中文解析" in bodies[0]["messages"][1]["content"]


def test_list_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200, json={"data": [{"id": "deepseek-reasoner"}, {"id": "deepseek-chat"}, {"x": 1}]}
        )

    async def scenario() -> list[str]:
        transport = httpx.MockTransport(handler)
        async with DeepSeekClient(config(), transport=transport) as client:
            return await client.list_models()

    assert run(scenario()) == ["deepseek-chat", "deepseek-reasoner"]
