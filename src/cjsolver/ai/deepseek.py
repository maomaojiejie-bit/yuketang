"""DeepSeek（以及任何 OpenAI 兼容端点）的异步客户端。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..config import DeepSeekConfig
from ..models import Problem, Suggestion
from .prompts import build_messages, parse_suggestion

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class AIError(RuntimeError):
    """调用模型失败。"""


class DeepSeekClient:
    """最小实现的 chat/completions 客户端。"""

    def __init__(
        self,
        config: DeepSeekConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # -- 生命周期 ---------------------------------------------------------
    async def __aenter__(self) -> "DeepSeekClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout, connect=15.0),
            # 这里**不放** Authorization：Key 可能被控制台在运行中改掉，
            # 写死在 client 默认头里就不会生效。改为每次请求现取。
            headers={"Content-Type": "application/json"},
            transport=self._transport,
        )
        return self

    def update_config(self, config: DeepSeekConfig) -> None:
        """热更新配置（换 Key / 换模型 / 换端点），无需重建客户端。"""
        self.config = config

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise AIError("客户端尚未初始化，请使用 async with DeepSeekClient(...) 。")
        return self._client

    @property
    def endpoint(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    # -- 对外接口 ---------------------------------------------------------
    async def ask(
        self,
        problem: Problem,
        *,
        extra_prompt: str = "",
        images: list[str] | None = None,
    ) -> Suggestion:
        """让模型作答一道题。"""
        use_vision = bool(images) and self.config.vision_enabled
        messages = build_messages(
            problem,
            extra_prompt=extra_prompt or self.config.extra_prompt,
            images=images if use_vision else None,
        )
        model = self.config.effective_model if use_vision else self.config.model

        started = time.perf_counter()
        text = await self._chat(messages, model=model)
        elapsed = time.perf_counter() - started
        return parse_suggestion(problem, text, model=model, elapsed=elapsed)

    async def list_models(self) -> list[str]:
        """拉取端点可用模型，用于 --check。"""
        url = f"{self.config.base_url.rstrip('/')}/models"
        response = await self.client.get(url)
        if response.status_code >= 400:
            raise AIError(_describe(response))
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return sorted(
            str(item.get("id"))
            for item in data
            if isinstance(item, dict) and item.get("id")
        )

    # -- 内部 -------------------------------------------------------------
    async def _chat(self, messages: list[dict[str, Any]], *, model: str) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                await asyncio.sleep(min(2.0 * attempt, 6.0))
            try:
                response = await self.client.post(
                    self.endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.api_key.strip()}"},
                )
            except httpx.HTTPError as exc:
                last_error = AIError(f"请求 {self.endpoint} 失败：{exc}")
                logger.warning("网络错误，准备重试（第 %s 次）：%s", attempt + 1, exc)
                continue

            if response.status_code < 400:
                return _extract_content(response.json())

            # 鉴权失败最常见的原因是 Key 被撤销或写错，给一句能直接照做的提示
            if response.status_code in (401, 403):
                from ..providers import key_env_hint, provider_by_id

                env_name = key_env_hint(provider_by_id(self.config.provider))
                raise AIError(
                    f"鉴权失败（HTTP {response.status_code}）：API Key 无效或已失效。"
                    f"可以打开控制台「设置 → 模型服务」重新填一把，"
                    f"或直接改项目目录 .env 里的 {env_name}。"
                )

            # 端点不支持 JSON 模式时降级重试一次
            if response.status_code == 400 and "response_format" in payload:
                logger.info("端点不支持 response_format=json_object，改为纯提示词约束。")
                payload.pop("response_format", None)
                continue

            last_error = AIError(_describe(response))
            if response.status_code not in RETRYABLE_STATUS:
                raise last_error
            logger.warning("上游返回 %s，准备重试。", response.status_code)

        raise last_error or AIError("调用模型失败，且没有捕获到具体原因。")


def _extract_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise AIError("模型响应不是 JSON 对象。")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            raise AIError(f"上游返回错误：{error['message']}")
        raise AIError("模型响应中没有 choices。")
    first = choices[0]
    if not isinstance(first, dict):
        raise AIError("choices[0] 格式异常。")
    message = first.get("message")
    if not isinstance(message, dict):
        raise AIError("choices[0].message 缺失。")
    content = message.get("content")
    if isinstance(content, list):  # 少数端点返回分段内容
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    if not isinstance(content, str):
        raise AIError("模型没有返回文本内容。")
    return content


def _describe(response: httpx.Response) -> str:
    snippet = response.text[:300].replace("\n", " ")
    return f"上游返回 HTTP {response.status_code}：{snippet}"
