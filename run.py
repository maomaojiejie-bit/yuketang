#!/usr/bin/env python
"""便捷启动脚本，等价于安装后的 `cjsolver` 命令。

直接运行本文件即可，无需 pip install：
    python run.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cjsolver.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
