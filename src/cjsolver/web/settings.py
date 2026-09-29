"""控制台可调的运行时设置：校验、落盘、回读。

优先级：控制台设置（console-settings.json）> config.yaml > 内置默认值。
只暴露「改错了也不会毁数据」的字段，其余仍以 config.yaml 为准。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from pathlib import Path
from typing import Any

from .. import providers
from ..config import Config, apply_provider_key

logger = logging.getLogger(__name__)

SETTINGS_FILE = "console-settings.json"
ALLOWED_MODES = ("manual", "dom", "api")

#: 允许控制台修改的字段：点号路径 -> 类型
FIELDS: dict[str, str] = {
    "answer.mode": "mode",
    "answer.dry_run": "bool",
    "answer.auto_submit": "bool",
    "answer.min_confidence": "float",
    "answer.click_delay": "float",
    "solver.dom_fallback": "bool",
    "solver.poll_interval": "float",
    "solver.auto_enter_lesson": "bool",
    "solver.enter_lesson_interval": "float",
    "solver.heartbeat_interval": "float",
    "browser.window_width": "int",
    "browser.window_height": "int",
    # 模型服务：换服务商只动这三项
    "deepseek.provider": "provider",
    "deepseek.model": "text",
    "deepseek.base_url": "url",
}

#: 数值上下限，挡住手写请求里离谱的值
LIMITS: dict[str, tuple[float, float]] = {
    "answer.min_confidence": (0.0, 1.0),
    "answer.click_delay": (0.0, 10.0),
    "solver.poll_interval": (0.5, 120.0),
    "solver.enter_lesson_interval": (5.0, 600.0),
    # 0 表示关闭心跳日志
    "solver.heartbeat_interval": (0.0, 3600.0),
    "browser.window_width": (0, 4000),
    "browser.window_height": (0, 4000),
}


class SettingsError(RuntimeError):
    """设置非法，消息可以直接展示给用户。"""


def settings_path(config: Config) -> Path:
    return config.project_root / SETTINGS_FILE


def collect(config: Config) -> dict[str, Any]:
    """把配置里可调的部分拍平成 {点号路径: 值}。"""
    return {path: _get(config, path) for path in FIELDS}


def nested(flat: dict[str, Any]) -> dict[str, Any]:
    """{"answer.mode": "dom"} -> {"answer": {"mode": "dom"}}，给前端用。"""
    out: dict[str, Any] = {}
    for path, value in flat.items():
        cursor = out
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return out


def apply(config: Config, payload: dict[str, Any]) -> tuple[Config, dict[str, Any]]:
    """校验一份设置并生成新 Config，返回 (新配置, 拍平后的完整设置)。"""
    flat_payload = _flatten(payload)
    if not flat_payload:
        raise SettingsError("没有可应用的设置项。")
    unknown = sorted(set(flat_payload) - set(FIELDS))
    if unknown:
        raise SettingsError("不支持的设置项：" + "、".join(unknown))

    values = collect(config)
    for path, raw in flat_payload.items():
        values[path] = _coerce(path, raw, FIELDS[path])

    updated = _rebuild(config, values)
    if updated.deepseek.provider != config.deepseek.provider:
        # 换了服务商，旧 Key 一定不对（拿 DeepSeek 的 Key 请求百炼只会 401）。
        # 丢掉旧值，按新家的环境变量重新挑一把。
        updated = dataclasses.replace(
            updated, deepseek=dataclasses.replace(updated.deepseek, api_key="")
        )
        updated = apply_provider_key(updated)
    return updated, values


def load(config: Config) -> Config:
    """读取已保存的控制台设置并应用；文件不存在或损坏时原样返回。"""
    path = settings_path(config)
    if not path.exists():
        return config
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("控制台设置读取失败，已忽略：%s", exc)
        return config
    if not isinstance(payload, dict):
        return config
    try:
        config, _ = apply(config, payload)
    except SettingsError as exc:
        logger.warning("控制台设置不合法，已忽略：%s", exc)
    return config


def save(config: Config, values: dict[str, Any]) -> Path:
    """把拍平后的设置写回磁盘（失败只记日志）。"""
    path = settings_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(nested(values), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - 磁盘异常
        logger.warning("控制台设置写入失败：%s", exc)
    return path


def describe(config: Config) -> dict[str, Any]:
    """给前端的设置视图：当前值 + 可选项 + 落盘位置。"""
    values = collect(config)
    return {
        "values": nested(values),
        "flat": values,
        "modes": list(ALLOWED_MODES),
        "limits": {path: list(bounds) for path, bounds in LIMITS.items()},
        "path": str(settings_path(config)),
    }


# -- 内部 -------------------------------------------------------------------
def _get(config: Config, path: str) -> Any:
    section, _, name = path.partition(".")
    return getattr(getattr(config, section), name)


def _flatten(payload: dict[str, Any]) -> dict[str, Any]:
    """同时接受 {answer: {mode: x}} 和 {"answer.mode": x} 两种写法。"""
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}.{sub_key}"] = sub_value
        else:
            flat[key] = value
    return flat


def _coerce(path: str, raw: Any, kind: str) -> Any:
    if kind == "mode":
        text = str(raw).strip()
        if text not in ALLOWED_MODES:
            raise SettingsError("作答模式必须是 " + " / ".join(ALLOWED_MODES) + " 之一。")
        return text
    if kind == "provider":
        text = str(raw).strip().lower()
        if not providers.has_provider(text):
            known = " / ".join(item.id for item in providers.PRESETS)
            raise SettingsError(f"未知的服务商 {text!r}，可选：{known}")
        return text
    if kind in ("text", "url"):
        text = str(raw).strip()
        if kind == "url" and text and not text.startswith(("http://", "https://")):
            raise SettingsError(f"{path} 必须以 http:// 或 https:// 开头。")
        return text
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{path} 需要一个数字。") from exc
    if not math.isfinite(number):
        raise SettingsError(f"{path} 不是有效数字。")
    low, high = LIMITS.get(path, (None, None))
    if low is not None and high is not None:
        # 越界只夹到边界，不整单拒绝：否则改一个字段会把别的改动一起弄丢
        number = min(max(number, low), high)
    return int(round(number)) if kind == "int" else number


def _rebuild(config: Config, values: dict[str, Any]) -> Config:
    """按点号路径把值写回一份新的 Config。"""
    sections: dict[str, dict[str, Any]] = {}
    for path, value in values.items():
        section, _, name = path.partition(".")
        sections.setdefault(section, {})[name] = value

    updated: dict[str, Any] = {}
    for section, fields in sections.items():
        current = getattr(config, section)
        try:
            updated[section] = dataclasses.replace(current, **fields)
        except TypeError as exc:  # pragma: no cover - 字段名写错才会走到
            raise SettingsError(f"{section} 不支持的字段：{exc}") from exc
    return dataclasses.replace(config, **updated)
