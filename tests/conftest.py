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


#: 会被运行时或 .env 影响、必须逐条测试隔离的变量
_MUTABLE_ENV = (
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "CJ_PROVIDER",
    "CJ_SITE",
    "CJ_CDP_URL",
)


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离环境变量，避免测试互相串。

    控制台改 API Key 会写 `os.environ`（这是刻意的，好让后续解析拿得到），
    但同进程的下一个测试的 `load_config` 也会读到它——不清干净就会出现
    「上一个测试写入的 Key 出现在下一个测试的断言里」这种脏结果。
    """
    from cjsolver.providers import GENERIC_KEY_ENV, PRESETS

    names = {provider.key_env for provider in PRESETS}
    names.add(GENERIC_KEY_ENV)
    names.update(_MUTABLE_ENV)
    for name in sorted(names):
        monkeypatch.delenv(name, raising=False)
