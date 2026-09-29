"""配置加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cjsolver.config import ConfigError, load_config


def test_defaults_without_config_file(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)

    assert config.config_path is None
    assert config.site == "changjiang"
    assert config.answer.mode == "manual"
    assert config.deepseek.model == "deepseek-flash"
    assert config.user_data_path == tmp_dir / ".chrome-profile"
    # 监听时用的窗口尺寸，默认 800x600
    assert config.browser.window_width == 800
    assert config.browser.window_height == 600


def test_config_yaml_is_applied(tmp_dir: Path) -> None:
    (tmp_dir / "config.yaml").write_text(
        """
site: pro
browser:
  cdp_url: http://127.0.0.1:9333
  auto_launch: false
  window_width: 1280
deepseek:
  temperature: 0.7
  api_key: from-yaml
answer:
  mode: dom
  submit_button_texts: [提交, 交卷]
solver:
  poll_interval: 5
""",
        encoding="utf-8",
    )
    config = load_config(project_root=tmp_dir)

    assert config.config_path == tmp_dir / "config.yaml"
    assert config.site == "pro"
    assert config.browser.cdp_url == "http://127.0.0.1:9333"
    assert config.browser.auto_launch is False
    assert config.browser.window_width == 1280
    assert config.browser.window_height == 600
    assert config.deepseek.temperature == 0.7
    assert config.deepseek.api_key == "from-yaml"
    assert config.answer.mode == "dom"
    assert config.answer.submit_button_texts == ["提交", "交卷"]
    assert config.solver.poll_interval == 5.0


def test_dotted_overrides_win(tmp_dir: Path) -> None:
    (tmp_dir / "config.yaml").write_text("answer:\n  mode: manual\n", encoding="utf-8")
    config = load_config(
        project_root=tmp_dir,
        overrides={"answer.mode": "dom", "site": "standard", "answer.dry_run": True},
    )

    assert config.answer.mode == "dom"
    assert config.answer.dry_run is True
    assert config.site == "standard"


def test_absolute_user_data_dir_is_preserved(tmp_dir: Path) -> None:
    absolute = tmp_dir / "profile"
    absolute.mkdir()
    (tmp_dir / "config.yaml").write_text(
        f'browser:\n  user_data_dir: "{absolute.as_posix()}"\n', encoding="utf-8"
    )
    config = load_config(project_root=tmp_dir)
    assert config.user_data_path == absolute


def test_validate_requires_api_key(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)
    with pytest.raises(ConfigError, match="API Key"):
        config.validate()


def test_validate_passes_with_key(tmp_dir: Path) -> None:
    (tmp_dir / "config.yaml").write_text("deepseek:\n  api_key: sk-test\n", encoding="utf-8")
    load_config(project_root=tmp_dir).validate()


def test_invalid_mode_rejected(tmp_dir: Path) -> None:
    with pytest.raises(ConfigError, match="answer.mode"):
        load_config(project_root=tmp_dir, overrides={"answer.mode": "telepathy"})


def test_missing_explicit_config_raises(tmp_dir: Path) -> None:
    with pytest.raises(ConfigError, match="找不到配置文件"):
        load_config(tmp_dir / "nope.yaml", project_root=tmp_dir)


def test_broken_yaml_raises(tmp_dir: Path) -> None:
    (tmp_dir / "config.yaml").write_text("answer: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="解析失败"):
        load_config(project_root=tmp_dir)


def test_unknown_keys_are_ignored(tmp_dir: Path) -> None:
    (tmp_dir / "config.yaml").write_text(
        "unknown_section:\n  a: 1\nanswer:\n  nope: 1\n  mode: api\n", encoding="utf-8"
    )
    config = load_config(project_root=tmp_dir)
    assert config.answer.mode == "api"
