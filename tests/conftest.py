"""测试用的小工具。

这里不使用 pytest 的 `tmp_path`：它的基线目录在系统临时目录下，
在某些受限沙箱里不可写。统一改用仓库内的临时目录。
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Iterator

import pytest

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"
TMP_ROOT = TESTS_DIR / ".tmp"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """在 tests/.tmp 下开一个用完即删的目录。"""
    target = TMP_ROOT / uuid.uuid4().hex[:8]
    target.mkdir(parents=True, exist_ok=True)
    try:
        yield target
    finally:
        shutil.rmtree(target, ignore_errors=True)
