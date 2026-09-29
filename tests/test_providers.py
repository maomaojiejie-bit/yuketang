"""模型服务商预设与 Key 解析测试。"""

from __future__ import annotations

import pytest

from cjsolver import providers
from cjsolver.config import Config, ConfigError, apply_provider_key, load_config


# --------------------------------------------------------------------------- #
# 预设数据本身
# --------------------------------------------------------------------------- #
def test_preset_ids_are_unique() -> None:
    ids = [provider.id for provider in providers.PRESETS]
    assert len(ids) == len(set(ids))


def test_every_preset_is_well_formed() -> None:
    for provider in providers.PRESETS:
        assert provider.id == provider.id.lower().strip()
        assert provider.label.strip()
        assert provider.key_env.strip()
        if provider.custom:
            # 「自定义」是让人自己填的，不该预置地址
            assert provider.base_url == ""
            continue
        assert provider.base_url.startswith("https://"), provider.id
        # base_url 是给 SDK 用的，不该带 /chat/completions 尾巴
        assert not provider.base_url.endswith("/chat/completions"), provider.id
        assert not provider.base_url.endswith("/"), provider.id
        assert provider.models, provider.id
        assert provider.docs.startswith("https://"), provider.id


def test_preset_key_envs_are_distinct() -> None:
    """每家一个 Key 变量，避免切服务商时串用。"""
    envs = [p.key_env for p in providers.PRESETS if not p.custom]
    assert len(envs) == len(set(envs))


def test_expected_providers_exist() -> None:
    ids = {provider.id for provider in providers.PRESETS}
    # 用户点名要的千问 / 豆包 / 元宝（混元）必须在
    assert {"deepseek", "qwen", "doubao", "hunyuan"} <= ids
    assert "custom" in ids


def test_provider_by_id_falls_back() -> None:
    assert providers.provider_by_id("qwen").id == "qwen"
    assert providers.provider_by_id("QWEN").id == "qwen"
    assert providers.provider_by_id("nope").id == providers.DEFAULT_PROVIDER_ID
    assert providers.provider_by_id(None).id == providers.DEFAULT_PROVIDER_ID
    assert providers.has_provider("doubao") is True
    assert providers.has_provider("nope") is False


def test_provider_for_base_url() -> None:
    qwen = providers.provider_for_base_url(
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert qwen is not None and qwen.id == "qwen"
    # 结尾斜杠不该影响判定
    ark = providers.provider_for_base_url("https://ark.cn-beijing.volces.com/api/v3/")
    assert ark is not None and ark.id == "doubao"
    # 只改了路径，靠域名也能认出来
    guess = providers.provider_for_base_url("https://api.moonshot.cn/v1/other")
    assert guess is not None and guess.id == "moonshot"
    assert providers.provider_for_base_url("https://example.com/v1") is None
    assert providers.provider_for_base_url("") is None


# --------------------------------------------------------------------------- #
# Key 解析
# --------------------------------------------------------------------------- #
def test_resolve_api_key_prefers_explicit() -> None:
    qwen = providers.provider_by_id("qwen")
    assert providers.resolve_api_key(qwen, "sk-explicit", {"DASHSCOPE_API_KEY": "sk-env"}) == (
        "sk-explicit"
    )


def test_resolve_api_key_uses_provider_env() -> None:
    qwen = providers.provider_by_id("qwen")
    assert providers.resolve_api_key(qwen, "", {"DASHSCOPE_API_KEY": "sk-qwen"}) == "sk-qwen"


def test_resolve_api_key_does_not_borrow_other_providers_key() -> None:
    """这是换服务商最容易踩的坑：拿 DeepSeek 的 Key 去请求百炼，只会 401。"""
    qwen = providers.provider_by_id("qwen")
    assert providers.resolve_api_key(qwen, "", {"DEEPSEEK_API_KEY": "sk-deep"}) == ""


def test_resolve_api_key_generic_fallback() -> None:
    qwen = providers.provider_by_id("qwen")
    assert providers.resolve_api_key(qwen, "", {"AI_API_KEY": "sk-any"}) == "sk-any"
    # 专属变量优先于通用变量
    both = {"DASHSCOPE_API_KEY": "sk-qwen", "AI_API_KEY": "sk-any"}
    assert providers.resolve_api_key(qwen, "", both) == "sk-qwen"


def test_resolve_api_key_empty_when_nothing_configured() -> None:
    assert providers.resolve_api_key(providers.provider_by_id("zhipu"), "", {}) == ""


def test_key_env_hint() -> None:
    assert providers.key_env_hint(providers.provider_by_id("qwen")) == "DASHSCOPE_API_KEY"
    assert providers.key_env_hint(providers.provider_by_id("custom")) == (
        providers.GENERIC_KEY_ENV
    )


def test_describe_all_reports_key_status() -> None:
    described = providers.describe_all({"DASHSCOPE_API_KEY": "sk-qwen"})
    by_id = {item["id"]: item for item in described}
    assert by_id["qwen"]["key_configured"] is True
    assert by_id["deepseek"]["key_configured"] is False
    assert by_id["qwen"]["models"]


def test_available_models_is_deduped() -> None:
    models = providers.available_models()
    assert len(models) == len(set(models))
    assert "qwen-plus" in models


# --------------------------------------------------------------------------- #
# 与配置系统的联动
# --------------------------------------------------------------------------- #
def test_apply_provider_key_picks_matching_env(tmp_dir: object) -> None:
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    config.deepseek.provider = "qwen"
    config.deepseek.api_key = ""

    updated = apply_provider_key(config, {"DASHSCOPE_API_KEY": "sk-qwen"})
    assert updated.deepseek.api_key == "sk-qwen"

    # 没有对应变量时保持为空，而不是回落到别家的 Key
    empty = apply_provider_key(config, {"DEEPSEEK_API_KEY": "sk-deep"})
    assert empty.deepseek.api_key == ""


def test_apply_provider_key_keeps_explicit_value(tmp_dir: object) -> None:
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    config.deepseek.api_key = "sk-explicit"
    updated = apply_provider_key(config, {"DASHSCOPE_API_KEY": "sk-qwen"})
    assert updated.deepseek.api_key == "sk-explicit"


def test_validate_message_names_the_right_env_var(tmp_dir: object) -> None:
    config: Config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    config.deepseek.provider = "qwen"
    config.deepseek.api_key = ""
    with pytest.raises(ConfigError, match="DASHSCOPE_API_KEY"):
        config.validate()

    config.deepseek.provider = "doubao"
    with pytest.raises(ConfigError, match="ARK_API_KEY"):
        config.validate()


def test_provider_survives_config_roundtrip(tmp_dir: object) -> None:
    (tmp_dir / "config.yaml").write_text(  # type: ignore[operator]
        "deepseek:\n  provider: moonshot\n  model: moonshot-v1-32k\n", encoding="utf-8"
    )
    config = load_config(project_root=tmp_dir)  # type: ignore[arg-type]
    assert config.deepseek.provider == "moonshot"
    assert config.deepseek.model == "moonshot-v1-32k"
