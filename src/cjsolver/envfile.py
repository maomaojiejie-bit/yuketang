"""读写 .env 的小工具。

把 Key 写进 .env 而不是 console-settings.json，是因为后者**已经被提交进仓库**，
把密钥写进去等于推到 GitHub。.env 在 .gitignore 里，是本项目约定的密钥落点。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

#: 兼容 `KEY=值`、`export KEY=值`、`  KEY = 值`
ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def read_env(path: Path) -> dict[str, str]:
    """读成字典；不存在或读不动就返回空字典。"""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = ENV_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1)
        value = line.split("=", 1)[1].strip() if "=" in line else ""
        values[key] = value.strip().strip('"').strip("'")
    return values


def upsert_env(path: Path, values: Mapping[str, str]) -> None:
    """更新若干键，保留其它行与注释。

    空字符串表示「清空这一项」：写成 `KEY=`，读取时视为未配置。
    """
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    remaining = dict(values)
    output: list[str] = []
    for line in lines:
        match = ENV_LINE_RE.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    for key, value in remaining.items():
        output.append(f"{key}={value}")

    path.write_text("\n".join(output).strip("\n") + "\n", encoding="utf-8")


def mask_secret(value: str, *, head: int = 6, tail: int = 4) -> str:
    """把密钥打码，用于回显给界面或写日志。

    前端永远只拿得到这个结果，真正的 Key 不出后端。
    """
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= head + tail:
        return "*" * len(text)
    return f"{text[:head]}{'*' * 6}{text[-tail:]}"
