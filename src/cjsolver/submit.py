"""把 AI 给出的答案落到雨课堂上。

三种模式：
  - manual：只打印，由使用者自己在浏览器里点（默认，最安全）
  - dom   ：在页面上按选项文字点击，可选再点提交按钮
  - api   ：在页面上下文里直接 POST /api/v3/lesson/problem/answer（实验性）
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from .config import AnswerConfig
from .models import Problem, Suggestion
from .yuketang.constants import API_PROBLEM_ANSWER, API_PROBLEM_RETRY, Site
from .yuketang.dom import click_option, click_submit

logger = logging.getLogger(__name__)

_POST_JS = r"""
async ({ url, body }) => {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'include',
    });
    const text = await response.text();
    return { ok: response.ok, status: response.status, text };
  } catch (error) {
    return { ok: false, status: 0, text: String(error) };
  }
}
"""


@dataclass
class SubmitResult:
    ok: bool
    mode: str
    detail: str = ""

    @property
    def skipped(self) -> bool:
        return not self.ok and self.mode == "manual"


async def submit(
    page: Page,
    problem: Problem,
    suggestion: Suggestion,
    config: AnswerConfig,
    site: Site,
    *,
    dry_run: bool = False,
) -> SubmitResult:
    """按配置模式提交答案。"""
    if config.mode == "manual":
        return SubmitResult(ok=False, mode="manual", detail="仅提示，等待人工作答")
    if dry_run or config.dry_run:
        return SubmitResult(ok=False, mode=config.mode, detail="dry-run，未真正提交")
    if not suggestion.answerable:
        return SubmitResult(ok=False, mode=config.mode, detail="没有可用答案")

    if config.mode == "api":
        return await _submit_api(page, problem, suggestion, site)
    return await _submit_dom(page, problem, suggestion, config)


async def _submit_dom(
    page: Page,
    problem: Problem,
    suggestion: Suggestion,
    config: AnswerConfig,
) -> SubmitResult:
    """在页面上依次点击选项。"""
    targets = _dom_targets(problem, suggestion)
    if not targets:
        return SubmitResult(ok=False, mode="dom", detail="答案无法映射到页面选项")

    clicked = 0
    for letter, text in targets:
        if await click_option(
            page, letter=letter, text=text, selectors=config.option_selectors
        ):
            clicked += 1
            await asyncio.sleep(config.click_delay)
        else:
            logger.warning("选项 %s 未能在页面上定位，跳过。", letter)

    if clicked == 0:
        return SubmitResult(
            ok=False,
            mode="dom",
            detail="页面上没有找到任何匹配的选项元素（可用 --dump 导出页面结构后配置 option_selectors）",
        )

    if config.auto_submit:
        await asyncio.sleep(config.click_delay)
        submitted = await click_submit(page, config.submit_button_texts)
        detail = (
            f"已点击 {clicked}/{len(targets)} 个选项并提交"
            if submitted
            else f"已点击 {clicked}/{len(targets)} 个选项，但没找到提交按钮（单选可能已自动提交）"
        )
    else:
        detail = f"已点击 {clicked}/{len(targets)} 个选项，未自动提交"

    return SubmitResult(ok=True, mode="dom", detail=detail)


def _dom_targets(problem: Problem, suggestion: Suggestion) -> list[tuple[str, str]]:
    """把字母答案映射成 (字母, 选项正文)，供页面点击使用。"""
    if problem.options and suggestion.letters:
        pairs: list[tuple[str, str]] = []
        for letter in suggestion.letters:
            option = problem.letter_to_option(letter)
            if option is not None:
                pairs.append((letter, option.text))
        if pairs:
            return pairs
    # 填空题 / 主观题在页面上的落点无法靠文字可靠定位，交给人工
    return []


async def _submit_api(
    page: Page,
    problem: Problem,
    suggestion: Suggestion,
    site: Site,
) -> SubmitResult:
    """直接调用作答接口（实验性）。"""
    payload: dict[str, Any] = {
        "problemId": problem.id,
        "result": suggestion.submit_payload(problem),
    }
    path = API_PROBLEM_ANSWER
    if problem.raw.get("result") not in (None, "", [], {}):
        # 已经答过，走重试接口
        path = API_PROBLEM_RETRY
        payload = {"problems": [payload]}

    url = site.api(path)
    try:
        result = await page.evaluate(_POST_JS, {"url": url, "body": payload})
    except Exception as exc:
        return SubmitResult(ok=False, mode="api", detail=f"页面内请求失败：{exc}")

    if not isinstance(result, dict):
        return SubmitResult(ok=False, mode="api", detail="页面内请求返回了意外结果")

    text = str(result.get("text") or "")
    if not result.get("ok"):
        return SubmitResult(
            ok=False, mode="api", detail=f"HTTP {result.get('status')}：{_shorten(text)}"
        )
    if not _looks_successful(text):
        return SubmitResult(ok=False, mode="api", detail=f"接口返回失败：{_shorten(text)}")
    return SubmitResult(ok=True, mode="api", detail=f"已提交 {json.dumps(payload['result'], ensure_ascii=False)}")


def _looks_successful(text: str) -> bool:
    """雨课堂成功响应通常形如 {"code": 0} 或 {"success": true}。"""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is True:
        return True
    code = payload.get("code")
    if isinstance(code, int):
        return code == 0
    if isinstance(code, str) and code.isdigit():
        return int(code) == 0
    return False


def _shorten(text: str, limit: int = 160) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")
