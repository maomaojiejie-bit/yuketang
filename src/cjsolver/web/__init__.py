"""本地网页控制台。

`cjsolver --web` 会在本机起一个小型 HTTP 服务：
  - `/`            单页控制台
  - `/api/*`       JSON 接口
  - `/api/events`  SSE 实时事件流（题目、答案、诊断日志）
"""

from .bus import EventBus
from .runtime import ConsoleRuntime
from .server import serve

__all__ = ["ConsoleRuntime", "EventBus", "serve"]
