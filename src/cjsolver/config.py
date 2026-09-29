"""配置加载：默认值 <- config.yaml <- 环境变量(.env) <- 命令行覆盖。"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

try:  # python-dotenv 是可选的，缺失时静默降级
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml")
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


@dataclass
class BrowserConfig:
    cdp_url: str = "http://127.0.0.1:9222"
    chrome_path: str = ""
    user_data_dir: str = ".chrome-profile"
    auto_launch: bool = True
    launch_timeout: int = 40
    # 启动监听时把 Chrome 窗口调整到的尺寸；任意一项 <= 0 表示不调整
    window_width: int = 800
    window_height: int = 600


@dataclass
class DeepSeekConfig:
    """模型接入配置。

    名字沿用 deepseek（配置键 `deepseek.*` 不能改，会破坏既有 config.yaml），
    但它描述的是**任意 OpenAI 兼容端点**：换个服务商只改
    provider / base_url / model 三项即可。
    """

    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    temperature: float = 0.0
    timeout: int = 90
    max_tokens: int = 1200
    json_mode: bool = True
    extra_prompt: str = ""
    vision_enabled: bool = False
    vision_model: str = ""
    max_retries: int = 2

    @property
    def effective_model(self) -> str:
        if self.vision_enabled and self.vision_model:
            return self.vision_model
        return self.model


@dataclass
class AnswerConfig:
    mode: str = "manual"  # manual | dom | api
    dry_run: bool = False
    min_confidence: float = 0.0
    auto_submit: bool = True
    click_delay: float = 0.8
    submit_button_texts: list[str] = field(
        default_factory=lambda: ["提交", "确定", "交卷", "提交答案", "确认提交"]
    )
    option_selectors: list[str] = field(default_factory=list)
    question_selectors: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.mode not in ("manual", "dom", "api"):
            raise ConfigError(
                f"answer.mode 必须是 manual / dom / api 之一，当前为 {self.mode!r}"
            )


@dataclass
class SolverConfig:
    poll_interval: float = 2.0
    dom_fallback: bool = True
    auto_enter_lesson: bool = False
    enter_lesson_interval: float = 20.0
    heartbeat_interval: float = 30.0
    record_dir: str = "records"
    log_level: str = "INFO"


@dataclass
class Config:
    site: str = "changjiang"
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    deepseek: DeepSeekConfig = field(default_factory=DeepSeekConfig)
    answer: AnswerConfig = field(default_factory=AnswerConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    config_path: Path | None = None
    project_root: Path = field(default_factory=Path.cwd)

    def validate(self) -> None:
        self.answer.validate()
        if not self.deepseek.api_key.strip():
            from .providers import key_env_hint, provider_by_id

            provider = provider_by_id(self.deepseek.provider)
            raise ConfigError(
                f"未配置 {provider.label} 的 API Key。"
                f"请在项目目录的 .env 里填写 {key_env_hint(provider)}"
                "（或通用的 AI_API_KEY），也可以在 config.yaml 的 deepseek.api_key 中填写。"
            )
        if not self.deepseek.base_url.strip():
            raise ConfigError(
                "deepseek.base_url 不能为空。请在控制台「设置 → 模型服务」里选一个服务商，"
                "或手动填入 OpenAI 兼容端点地址。"
            )

    @property
    def record_path(self) -> Path:
        return self.project_root / self.solver.record_dir

    @property
    def user_data_path(self) -> Path:
        raw = Path(self.browser.user_data_dir)
        return raw if raw.is_absolute() else self.project_root / raw


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


def load_config(
    path: str | Path | None = None,
    *,
    project_root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """载入配置。overrides 支持点号路径，如 {"answer.mode": "dom"}。"""
    root = Path(project_root or Path.cwd()).resolve()
    if load_dotenv is not None:
        env_file = root / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)

    config_path = _resolve_config_path(path, root)
    data: dict[str, Any] = {}
    if config_path is not None:
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"config.yaml 解析失败：{exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError("config.yaml 顶层必须是映射（key: value）。")
        data = loaded

    config = Config(config_path=config_path, project_root=root)
    _apply_mapping(config, data)

    # 环境变量覆盖。注意这里**故意不映射** DEEPSEEK_API_KEY：
    # 它属于 DeepSeek 这一家，切到千问还把它发过去只会 401。
    # 具体用哪把 Key 交给 apply_provider_key 按服务商解析。
    env_map = {
        "DEEPSEEK_BASE_URL": "deepseek.base_url",
        "DEEPSEEK_MODEL": "deepseek.model",
        "CJ_PROVIDER": "deepseek.provider",
        "CJ_CDP_URL": "browser.cdp_url",
        "CJ_SITE": "site",
    }
    env_overrides = {
        target: os.environ[source]
        for source, target in env_map.items()
        if os.environ.get(source)
    }
    _apply_mapping(config, _nest(env_overrides))

    if overrides:
        _apply_mapping(config, _nest(overrides))

    config = apply_provider_key(config)
    config.answer.validate()
    return config


def apply_provider_key(config: Config, env: Mapping[str, str] | None = None) -> Config:
    """按当前服务商决定用哪把 Key，返回一份新配置。

    优先级：显式配置的 key > 该服务商的 key_env > AI_API_KEY。
    换服务商之后必须重新跑一次，否则会拿旧家的 Key 去请求新家。
    """
    from .providers import provider_by_id, resolve_api_key

    provider = provider_by_id(config.deepseek.provider)
    lookup = env if env is not None else os.environ
    key = resolve_api_key(provider, config.deepseek.api_key, lookup)
    if key == config.deepseek.api_key:
        return config
    return dataclasses.replace(
        config, deepseek=dataclasses.replace(config.deepseek, api_key=key)
    )


def _resolve_config_path(path: str | Path | None, root: Path) -> Path | None:
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.exists():
            raise ConfigError(f"找不到配置文件：{candidate}")
        return candidate
    for name in DEFAULT_CONFIG_NAMES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _nest(flat: dict[str, Any]) -> dict[str, Any]:
    """把 {"answer.mode": "dom"} 展开成 {"answer": {"mode": "dom"}}。"""
    nested: dict[str, Any] = {}
    for key, value in flat.items():
        cursor = nested
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return nested


def _apply_mapping(config: Config, data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if value is None:
            continue
        if key == "site":
            config.site = str(value)
            continue
        section = getattr(config, key, None)
        if section is None or not is_dataclass(section):
            continue
        if not isinstance(value, dict):
            continue
        known = {f.name: f for f in fields(section)}
        for sub_key, sub_value in value.items():
            if sub_key not in known or sub_value is None:
                continue
            setattr(section, sub_key, _coerce(known[sub_key].type, sub_value))


def _coerce(annotation: Any, value: Any) -> Any:
    """把 YAML 里读到的值粗略对齐到数据类字段类型。"""
    name = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
    try:
        if name == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if name == "int":
            return int(value)
        if name == "float":
            return float(value)
        if name == "str":
            return str(value)
    except (TypeError, ValueError):
        return value
    return value


def find_chrome(chrome_path: str = "") -> str:
    """定位 Chrome 可执行文件，找不到时返回空串。"""
    if chrome_path:
        candidate = Path(chrome_path)
        if candidate.exists():
            return str(candidate)
        raise ConfigError(f"browser.chrome_path 指向的文件不存在：{chrome_path}")
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""
