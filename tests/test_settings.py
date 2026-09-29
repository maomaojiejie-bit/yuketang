"""控制台设置：校验、边界与落盘回读。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cjsolver.config import load_config
from cjsolver.web import settings


def test_collect_and_describe_shape(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)
    values = settings.collect(config)

    assert values["answer.mode"] == "manual"
    assert values["solver.auto_enter_lesson"] is False
    assert values["browser.window_width"] == 800

    described = settings.describe(config)
    assert described["values"]["solver"]["enter_lesson_interval"] == 20.0
    assert described["values"]["answer"]["dry_run"] is False
    assert described["modes"] == ["manual", "dom", "api"]
    assert described["path"].endswith("console-settings.json")


def test_apply_accepts_nested_and_dotted_payloads(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)

    updated, values = settings.apply(
        config, {"answer": {"mode": "dom"}, "solver.auto_enter_lesson": True}
    )

    assert updated.answer.mode == "dom"
    assert updated.solver.auto_enter_lesson is True
    assert values["answer.mode"] == "dom"
    # 没提到的字段保持原样，且原配置不被就地改动
    assert updated.answer.min_confidence == 0.0
    assert config.answer.mode == "manual"
    assert config.solver.auto_enter_lesson is False


def test_apply_rejects_bad_input(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)

    with pytest.raises(settings.SettingsError, match="没有可应用"):
        settings.apply(config, {})
    with pytest.raises(settings.SettingsError, match="不支持"):
        settings.apply(config, {"deepseek.api_key": "sk-x"})
    with pytest.raises(settings.SettingsError, match="作答模式"):
        settings.apply(config, {"answer.mode": "telepathy"})
    with pytest.raises(settings.SettingsError, match="数字"):
        settings.apply(config, {"browser.window_width": "宽"})


def test_out_of_range_numbers_are_clamped(tmp_dir: Path) -> None:
    """越界只夹到边界，不能整单拒绝——否则改一个字段会把别的改动一起丢掉。"""
    config = load_config(project_root=tmp_dir)

    updated, _ = settings.apply(
        config, {"solver": {"poll_interval": 999, "enter_lesson_interval": 1}}
    )

    assert updated.solver.poll_interval == 120.0
    assert updated.solver.enter_lesson_interval == 5.0


def test_nan_is_rejected(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)
    with pytest.raises(settings.SettingsError, match="有效数字"):
        settings.apply(config, {"solver.poll_interval": float("nan")})


def test_ints_are_rounded_and_window_can_be_disabled(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)

    updated, _ = settings.apply(
        config, {"browser.window_width": 800.6, "browser.window_height": 0}
    )

    assert updated.browser.window_width == 801
    assert updated.browser.window_height == 0


def test_save_then_load_round_trip(tmp_dir: Path) -> None:
    config = load_config(project_root=tmp_dir)
    updated, values = settings.apply(
        config,
        {
            "answer.mode": "dom",
            "answer.dry_run": True,
            "solver.auto_enter_lesson": True,
            "solver.enter_lesson_interval": 30,
            "browser.window_width": 1024,
        },
    )

    path = settings.save(updated, values)
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["solver"]["auto_enter_lesson"] is True
    assert raw["browser"]["window_width"] == 1024

    reloaded = settings.load(load_config(project_root=tmp_dir))
    assert reloaded.answer.mode == "dom"
    assert reloaded.answer.dry_run is True
    assert reloaded.solver.auto_enter_lesson is True
    assert reloaded.solver.enter_lesson_interval == 30.0
    assert reloaded.browser.window_width == 1024
    # 没保存过的字段仍走默认值
    assert reloaded.browser.window_height == 600
    assert reloaded.solver.dom_fallback is True


def test_broken_settings_file_is_ignored(tmp_dir: Path) -> None:
    path = tmp_dir / "console-settings.json"

    path.write_text("{ 不是 JSON", encoding="utf-8")
    assert settings.load(load_config(project_root=tmp_dir)).answer.mode == "manual"

    path.write_text(json.dumps({"answer": {"mode": "nope"}}), encoding="utf-8")
    assert settings.load(load_config(project_root=tmp_dir)).answer.mode == "manual"

    path.write_text(json.dumps(["不是对象"]), encoding="utf-8")
    assert settings.load(load_config(project_root=tmp_dir)).answer.mode == "manual"
