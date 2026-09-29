"""站点与接口常量。

这里的接口路径、题目类型枚举、答案结构均来自对雨课堂网页端真实流量的归纳：
  - 课件题目随 `GET /api/v3/lesson/presentation/fetch?presentation_id=...` 下发
  - 课堂事件（含新题目）也会从 `wss://<host>/wsapp/` 推送
  - 作答走 `POST /api/v3/lesson/problem/answer`
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    """一个雨课堂部署站点（标准版 / 荷塘版 / 长江版）。"""

    key: str
    label: str
    host: str
    start_url: str

    @property
    def origin(self) -> str:
        return f"https://{self.host}"

    @property
    def websocket_url(self) -> str:
        return f"wss://{self.host}/wsapp/"

    def api(self, path: str) -> str:
        return f"{self.origin}{path}"

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.label}({self.host})"


SITES: dict[str, Site] = {
    "changjiang": Site(
        key="changjiang",
        label="长江雨课堂",
        host="changjiang.yuketang.cn",
        start_url="https://changjiang.yuketang.cn/web",
    ),
    "standard": Site(
        key="standard",
        label="雨课堂",
        host="www.yuketang.cn",
        start_url="https://www.yuketang.cn/web",
    ),
    "pro": Site(
        key="pro",
        label="荷塘雨课堂",
        host="pro.yuketang.cn",
        start_url="https://pro.yuketang.cn/web",
    ),
}

DEFAULT_SITE_KEY = "changjiang"

# --- 接口路径 ---------------------------------------------------------------
API_USER = "/api/v3/user/basic-info"
API_ON_LESSON = "/api/v3/classroom/on-lesson"
API_CHECKIN = "/api/v3/lesson/checkin"
API_PROBLEM_ANSWER = "/api/v3/lesson/problem/answer"
API_PROBLEM_RETRY = "/api/v3/lesson/problem/retry"
API_PRESENTATION_FETCH = "/api/v3/lesson/presentation/fetch"

# 只有这些路径的响应会被当作「可能含题目」的载荷解析
PROBLEM_BEARING_PATHS = (
    "presentation",
    "problem",
    "exercise",
    "exam",
    "question",
    "slide",
)

# --- 题目类型 ---------------------------------------------------------------
# 雨课堂用数字编码下发 problemType
PROBLEM_TYPE_BY_CODE: dict[int, str] = {
    0: "unknown",
    1: "single-choice",
    2: "multiple-choice",
    3: "poll",
    4: "fill-blank",
    5: "subjective",
}


def site_by_key(key: str | None) -> Site:
    """按 key 取站点，未知 key 回退到长江雨课堂。"""
    if key:
        normalized = key.strip().lower()
        if normalized in SITES:
            return SITES[normalized]
    return SITES[DEFAULT_SITE_KEY]


def site_for_host(host: str) -> Site | None:
    """按 hostname 反查站点，非雨课堂域名返回 None。"""
    for site in SITES.values():
        if site.host == host:
            return site
    return None


def is_yuketang_host(host: str) -> bool:
    return host.endswith(".yuketang.cn") or host == "yuketang.cn"
