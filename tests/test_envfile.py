"""`.env` 读写与密钥打码测试。"""

from __future__ import annotations

from pathlib import Path

from cjsolver.envfile import ENV_LINE_RE, mask_secret, read_env, upsert_env


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def test_read_env_missing_file(tmp_dir: Path) -> None:
    assert read_env(tmp_dir / "nope.env") == {}


def test_read_env_parses_common_forms(tmp_dir: Path) -> None:
    path = tmp_dir / ".env"
    path.write_text(
        "\n".join(
            [
                "# 注释",
                "PLAIN=value",
                "  SPACED  =  padded  ",
                "export EXPORTED=from-export",
                'QUOTED="quoted value"',
                "SINGLE='single'",
                "EMPTY=",
                "NO_EQUALS_SIGN",
                "",
            ]
        ),
        encoding="utf-8",
    )
    values = read_env(path)
    assert values["PLAIN"] == "value"
    assert values["SPACED"] == "padded"
    assert values["EXPORTED"] == "from-export"
    assert values["QUOTED"] == "quoted value"
    assert values["SINGLE"] == "single"
    assert values["EMPTY"] == ""
    assert "NO_EQUALS_SIGN" not in values


def test_env_line_re_ignores_comments() -> None:
    assert ENV_LINE_RE.match("# KEY=value") is None
    assert ENV_LINE_RE.match("KEY=value").group(1) == "KEY"
    assert ENV_LINE_RE.match("export KEY=value").group(1) == "KEY"
    assert ENV_LINE_RE.match("  KEY = value").group(1) == "KEY"


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def test_upsert_env_creates_file(tmp_dir: Path) -> None:
    path = tmp_dir / ".env"
    upsert_env(path, {"A": "1", "B": "2"})
    assert read_env(path) == {"A": "1", "B": "2"}


def test_upsert_env_replaces_and_preserves(tmp_dir: Path) -> None:
    path = tmp_dir / ".env"
    path.write_text(
        "# 头部注释\nA=old\n# 中间注释\nB=keep\n", encoding="utf-8"
    )
    upsert_env(path, {"A": "new", "C": "added"})
    text = path.read_text(encoding="utf-8")

    assert read_env(path) == {"A": "new", "B": "keep", "C": "added"}
    # 注释不能被吃掉
    assert "# 头部注释" in text
    assert "# 中间注释" in text
    # 追加在末尾
    assert text.rstrip().endswith("C=added")


def test_upsert_env_empty_clears_value(tmp_dir: Path) -> None:
    path = tmp_dir / ".env"
    path.write_text("KEY=secret\nOTHER=1\n", encoding="utf-8")
    upsert_env(path, {"KEY": ""})
    values = read_env(path)
    assert values["KEY"] == ""
    assert values["OTHER"] == "1"
    # 键仍在（值清空），这样再次填写时是替换而不是追加
    assert "KEY=" in path.read_text(encoding="utf-8")


def test_upsert_env_is_idempotent(tmp_dir: Path) -> None:
    path = tmp_dir / ".env"
    for _ in range(3):
        upsert_env(path, {"KEY": "v"})
    text = path.read_text(encoding="utf-8")
    assert text.count("KEY=") == 1
    assert text.endswith("\n")


def test_upsert_env_handles_export_lines(tmp_dir: Path) -> None:
    path = tmp_dir / ".env"
    path.write_text("export KEY=old\n", encoding="utf-8")
    upsert_env(path, {"KEY": "new"})
    text = path.read_text(encoding="utf-8")
    assert text.count("KEY=") == 1
    assert read_env(path)["KEY"] == "new"


def test_upsert_env_on_mojibake_file_does_not_crash(tmp_dir: Path) -> None:
    """用户可能用记事本存成 GBK，别因此炸掉。"""
    path = tmp_dir / ".env"
    path.write_bytes("# 注释\nKEY=old\n".encode("gbk"))
    upsert_env(path, {"KEY": "new"})
    assert read_env(path)["KEY"] == "new"


# --------------------------------------------------------------------------- #
# 打码
# --------------------------------------------------------------------------- #
def test_mask_secret_hides_middle() -> None:
    masked = mask_secret("sk-0ab83962c1f24f5b94e91ee7f3b99aac")
    assert masked.startswith("sk-0ab")
    assert masked.endswith("9aac")
    assert "*" in masked
    # 原始内容不能出现在打码结果里
    assert "c1f24f5b94e91ee7f3b" not in masked


def test_mask_secret_edge_cases() -> None:
    assert mask_secret("") == ""
    assert mask_secret("   ") == ""
    # 太短就整体打掉，不泄露长度以外的信息
    assert mask_secret("abc") == "***"
    assert set(mask_secret("1234567890")) == {"*"}


def test_mask_secret_never_returns_full_value() -> None:
    for value in ("sk-abcdefghijklmnop", "x" * 100, "短"):
        assert value not in mask_secret(value)
