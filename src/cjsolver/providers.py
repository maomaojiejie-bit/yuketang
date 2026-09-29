"""常用大模型服务商预设。

都是 OpenAI 兼容端点，所以只要换 base_url + 模型名 + 对应的 Key 就能切换。

两点设计取舍：
  - **每个 Key 走自己的环境变量**（DASHSCOPE_API_KEY / ARK_API_KEY / …）。
    切到千问却还把 DeepSeek 的 Key 发过去，只会拿到一个 401，
    不如按服务商取对应的那一个。
  - **base_url 一律可在控制台里改**。这些地址是外部事实，会变；
    预设只是省得手输，不是硬约束。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

#: 谁都认的兜底 Key 变量名
GENERIC_KEY_ENV = "AI_API_KEY"


@dataclass(frozen=True)
class Provider:
    """一个 OpenAI 兼容服务商。"""

    id: str
    label: str
    base_url: str
    key_env: str
    models: tuple[str, ...] = ()
    docs: str = ""
    note: str = ""
    #: 是否为「自己填」的占位项
    custom: bool = False

    def to_dict(self, *, key_configured: bool | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "key_env": self.key_env,
            "models": list(self.models),
            "docs": self.docs,
            "note": self.note,
            "custom": self.custom,
        }
        if key_configured is not None:
            payload["key_configured"] = key_configured
        return payload


PRESETS: tuple[Provider, ...] = (
    Provider(
        id="deepseek",
        label="DeepSeek 深度求索",
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        models=("deepseek-chat", "deepseek-reasoner"),
        docs="https://platform.deepseek.com/api-docs/",
        note="deepseek-chat 稳定快速；deepseek-reasoner 会先推理再作答，适合难题但更慢。",
    ),
    Provider(
        id="qwen",
        label="通义千问（阿里云百炼）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_API_KEY",
        models=("qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"),
        docs="https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope",
        note="百炼的 API Key 按地域绑定，Key 与 base_url 必须属于同一地域。",
    ),
    Provider(
        id="doubao",
        label="豆包（火山方舟）",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        key_env="ARK_API_KEY",
        models=(
            "doubao-seed-1-6-250615",
            "doubao-1-5-pro-32k-250115",
            "doubao-1-5-lite-32k-250115",
        ),
        docs="https://www.volcengine.com/docs/82379",
        note="方舟也支持把推理接入点 ID（ep-xxxxxxxx）当模型名填。",
    ),
    Provider(
        id="hunyuan",
        label="腾讯混元（元宝同源）",
        base_url="https://api.hunyuan.cloud.tencent.com/v1",
        key_env="HUNYUAN_API_KEY",
        models=("hunyuan-turbos-latest", "hunyuan-pro", "hunyuan-standard"),
        docs="https://cloud.tencent.com/document/product/1729",
        note="元宝 App 本身没有公开 API，这里用的是同底座的混元开放接口。",
    ),
    Provider(
        id="zhipu",
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        key_env="ZHIPU_API_KEY",
        models=("glm-4-plus", "glm-4-air", "glm-4-flash"),
        docs="https://docs.bigmodel.cn/cn/guide/develop/openai/introduction",
    ),
    Provider(
        id="moonshot",
        label="Kimi（月之暗面）",
        base_url="https://api.moonshot.cn/v1",
        key_env="MOONSHOT_API_KEY",
        models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        docs="https://platform.moonshot.cn/docs",
    ),
    Provider(
        id="siliconflow",
        label="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        key_env="SILICONFLOW_API_KEY",
        models=("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"),
        docs="https://docs.siliconflow.cn/",
        note="聚合平台，一个 Key 可以用很多开源模型。",
    ),
    Provider(
        id="custom",
        label="自定义（任何 OpenAI 兼容端点）",
        base_url="",
        key_env=GENERIC_KEY_ENV,
        docs="",
        note="自己填 Base URL 和模型名；Key 放 .env 的 AI_API_KEY，或直接用下面填的。",
        custom=True,
    ),
)

_BY_ID: dict[str, Provider] = {provider.id: provider for provider in PRESETS}

DEFAULT_PROVIDER_ID = "deepseek"


def provider_by_id(provider_id: str | None) -> Provider:
    """按 id 取预设；未知 id 回退到 DeepSeek。"""
    if provider_id:
        found = _BY_ID.get(provider_id.strip().lower())
        if found is not None:
            return found
    return _BY_ID[DEFAULT_PROVIDER_ID]


def has_provider(provider_id: str | None) -> bool:
    return bool(provider_id) and provider_id.strip().lower() in _BY_ID


def provider_for_base_url(base_url: str) -> Provider | None:
    """按 base_url 反查服务商（用于回填下拉框）。"""
    target = (base_url or "").strip().rstrip("/").lower()
    if not target:
        return None
    for provider in PRESETS:
        if provider.custom or not provider.base_url:
            continue
        if provider.base_url.rstrip("/").lower() == target:
            return provider
    # 域名匹配兜底：用户可能只改了路径
    for provider in PRESETS:
        if provider.custom or not provider.base_url:
            continue
        host = provider.base_url.split("//", 1)[-1].split("/", 1)[0].lower()
        if host and host in target:
            return provider
    return None


def resolve_api_key(
    provider: Provider,
    configured: str | None,
    env: Mapping[str, str] | Iterable[str] = (),
) -> str:
    """决定用哪把 Key：显式配置 > 该服务商的环境变量 > 通用变量。"""
    explicit = (configured or "").strip()
    if explicit:
        return explicit
    lookup = env if isinstance(env, Mapping) else _as_mapping(env)
    for name in (provider.key_env, GENERIC_KEY_ENV):
        value = (lookup.get(name) or "").strip()
        if value:
            return value
    return ""


def _as_mapping(env: Iterable[str]) -> dict[str, str]:
    import os

    return {name: os.environ.get(name, "") for name in env}


def key_env_hint(provider: Provider) -> str:
    if provider.custom:
        return GENERIC_KEY_ENV
    return provider.key_env


def available_models() -> list[str]:
    """所有预设里出现过的模型名，供自动补全。"""
    seen: list[str] = []
    for provider in PRESETS:
        for model in provider.models:
            if model not in seen:
                seen.append(model)
    return seen


def describe_all(env: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    """给控制台用的清单，带每个服务商的 Key 是否已配置。"""
    import os

    lookup = env if env is not None else os.environ
    result: list[dict[str, object]] = []
    for provider in PRESETS:
        names = (provider.key_env, GENERIC_KEY_ENV)
        configured = any((lookup.get(name) or "").strip() for name in names)
        result.append(provider.to_dict(key_configured=configured))
    return result
