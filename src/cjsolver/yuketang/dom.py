"""DOM 兜底：当网络层没抓到题时，直接从页面里读题；以及自动点击选项。

页面结构可能随雨课堂发版变化，所以：
  - 默认用「选项以 A. / B、/ C） 开头」这一稳定视觉约定做启发式定位
  - 允许在 config.yaml 的 answer.option_selectors / question_selectors 里写死选择器覆盖
  - `dump_page` 可以把当前页面结构导出，方便定位问题
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from ..models import OPTION_LINE_PATTERN, Option, Problem, infer_problem_type

logger = logging.getLogger(__name__)

#: 在页面里扫描「当前正在展示的题目」
_SCAN_JS = r"""
(selectors) => {
  const norm = (s) => (s || '').replace(/\s+/g, '');
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
  };
  const OPTION_RE = /__OPTION_LINE_PATTERN__/;
  const NOISE = new Set(['提交', '确定', '交卷', '提交答案', '确认提交', '下一题', '上一题', '关闭', '取消', '返回', '答题卡']);

  const scope = selectors && selectors.length
    ? [...document.querySelectorAll(selectors.join(','))]
    : [...document.querySelectorAll('body *')];

  const candidates = [];
  for (const el of scope) {
    if (!(el instanceof HTMLElement) || !visible(el)) continue;
    const text = (el.innerText || '').replace(/\r/g, '').trim();
    if (!text || text.length > 400) continue;
    const firstLine = text.split('\n')[0].trim();
    const match = OPTION_RE.exec(firstLine);
    if (!match) continue;
    // 只保留最内层：子元素里还有选项文本的，这一层是容器
    const nested = [...el.querySelectorAll('*')].some((child) => {
      const childText = (child.innerText || '').trim().split('\n')[0].trim();
      return !!childText && OPTION_RE.test(childText);
    });
    if (nested) continue;
    const rect = el.getBoundingClientRect();
    candidates.push({
      el,
      letter: match[1].toUpperCase(),
      text: match[2].trim(),
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      area: rect.width * rect.height,
    });
  }
  if (!candidates.length) return { found: false };

  // 聚类：每个选项向上找到第一个「至少包含两个候选选项」的祖先
  const groups = new Map();
  for (const candidate of candidates) {
    let node = candidate.el;
    let container = null;
    for (let depth = 0; node && depth < 10; depth += 1) {
      let count = 0;
      for (const other of candidates) {
        if (node.contains(other.el)) count += 1;
      }
      if (count >= 2) { container = node; break; }
      node = node.parentElement;
    }
    if (!container) container = candidate.el.parentElement || candidate.el;
    if (!groups.has(container)) groups.set(container, []);
    groups.get(container).push(candidate);
  }

  let best = null;
  let bestNode = null;
  for (const [node, list] of groups) {
    if (!best || list.length > best.length) { best = list; bestNode = node; }
  }
  best.sort((a, b) => a.y - b.y || a.x - b.x);

  const buildPrompt = (node) => {
    if (!node) return null;
    const raw = (node.innerText || '').trim();
    if (raw.length > 800) return null;
    const lines = raw.split('\n').map((line) => line.trim()).filter(Boolean);
    // 题干一定在选项之前：从第一个选项行截断，天然排除页脚/按钮一类噪声
    const firstOption = lines.findIndex((line) => OPTION_RE.test(line));
    const head = firstOption === -1 ? lines : lines.slice(0, firstOption);
    return head.filter((line) => !NOISE.has(line)).join('\n');
  };
  // 题干通常在选项容器的上一层，但页面结构可能更扁平
  let prompt = buildPrompt(bestNode && bestNode.parentElement);
  if (prompt === null) prompt = buildPrompt(bestNode);
  if (prompt === null) prompt = '';

  return {
    found: true,
    prompt,
    letters: best.map((option) => option.letter).join(''),
    options: best.map((option) => ({ letter: option.letter, text: option.text })),
    anchors: best.map((option) => ({ letter: option.letter, x: option.x, y: option.y })),
  };
}
""".replace("__OPTION_LINE_PATTERN__", OPTION_LINE_PATTERN)

#: 找到某个选项并返回可点击坐标
_FIND_OPTION_JS = r"""
({ letter, needle, selectors }) => {
  const norm = (s) => (s || '').replace(/\s+/g, '').toLowerCase();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const OPTION_RE = /__OPTION_LINE_PATTERN__/;
  const target = norm(needle);

  const scope = selectors && selectors.length
    ? [...document.querySelectorAll(selectors.join(','))]
    : [...document.querySelectorAll('body *')];

  let best = null;
  for (const el of scope) {
    if (!(el instanceof HTMLElement) || !visible(el)) continue;
    const text = (el.innerText || '').replace(/\r/g, '').trim();
    if (!text || text.length > 400) continue;
    const firstLine = text.split('\n')[0].trim();
    const match = OPTION_RE.exec(firstLine);
    const body = match ? match[2].trim() : firstLine;
    const bodyNorm = norm(body);
    if (!bodyNorm) continue;

    let score = 0;
    if (bodyNorm === target) score = 3;
    else if (bodyNorm.includes(target) && target.length >= 2) score = 2;
    else if (target.includes(bodyNorm) && bodyNorm.length >= 2) score = 1;
    if (!score) continue;
    if (match && letter && match[1].toUpperCase() === letter.toUpperCase()) score += 1;

    const rect = el.getBoundingClientRect();
    const area = rect.width * rect.height;
    if (!best || score > best.score || (score === best.score && area < best.area)) {
      best = {
        score,
        area,
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
        text: body,
        letter: match ? match[1].toUpperCase() : '',
      };
    }
  }
  return best;
}
""".replace("__OPTION_LINE_PATTERN__", OPTION_LINE_PATTERN)

#: 找到提交按钮
_FIND_BUTTON_JS = r"""
(texts) => {
  const norm = (s) => (s || '').replace(/\s+/g, '');
  const wanted = texts.map(norm);
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  let best = null;
  for (const el of document.querySelectorAll('button, a, div, span, input[type=button], input[type=submit]')) {
    if (!(el instanceof HTMLElement) || !visible(el)) continue;
    if (el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true') continue;
    const text = norm(el.innerText || el.value || '');
    if (!text || text.length > 12) continue;
    if (!wanted.includes(text)) continue;
    const rect = el.getBoundingClientRect();
    const area = rect.width * rect.height;
    if (!best || area < best.area) {
      best = { area, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, text };
    }
  }
  return best;
}
"""


def _stamp(prompt: str, options: list[tuple[str, str]], letters: str) -> str:
    """为 DOM 抓到的题目生成稳定 id，便于跨轮询去重。"""
    payload = "\u0001".join([prompt, letters, *[f"{a}={b}" for a, b in options]])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"dom-{digest}"


async def scan_problem(page: Page, question_selectors: list[str]) -> Problem | None:
    """读取页面上当前显示的题目，读不到返回 None。"""
    try:
        raw = await page.evaluate(_SCAN_JS, question_selectors or [])
    except Exception as exc:
        logger.debug("DOM 扫描失败：%s", exc)
        return None
    if not raw or not raw.get("found"):
        return None

    prompt = str(raw.get("prompt") or "").strip()
    raw_options = raw.get("options") or []
    options = [
        Option(index=index, text=str(item.get("text") or "").strip())
        for index, item in enumerate(raw_options)
        if str(item.get("text") or "").strip()
    ]
    if not prompt and len(options) < 2:
        return None

    letters = str(raw.get("letters") or "")
    letters = "".join(ch for ch in letters if ch.isalpha())
    problem_type = infer_problem_type(prompt, options)
    problem_id = _stamp(prompt, [(o.letter, o.text) for o in options], letters)

    return Problem(
        id=problem_id,
        type=problem_type,
        prompt=prompt,
        options=options,
        source="dom",
        raw={"letters": letters},
    )


async def click_option(
    page: Page,
    *,
    letter: str,
    text: str,
    selectors: list[str],
) -> bool:
    """按选项文字点击页面上的对应选项。"""
    if not text.strip():
        return False
    try:
        target = await page.evaluate(
            _FIND_OPTION_JS,
            {"letter": letter, "needle": text, "selectors": selectors or []},
        )
    except Exception as exc:
        logger.debug("定位选项失败：%s", exc)
        return False
    if not target:
        logger.warning("页面上找不到选项 %s. %s", letter, text)
        return False
    try:
        await page.mouse.click(float(target["x"]), float(target["y"]))
        logger.info("已点击选项 %s. %s", letter, target.get("text"))
        return True
    except Exception as exc:
        logger.warning("点击选项失败：%s", exc)
        return False


async def click_submit(page: Page, button_texts: list[str]) -> bool:
    """点击提交按钮，找不到时返回 False（不报错）。"""
    try:
        button = await page.evaluate(_FIND_BUTTON_JS, button_texts or [])
    except Exception as exc:
        logger.debug("查找提交按钮失败：%s", exc)
        return False
    if not button:
        return False
    try:
        await page.mouse.click(float(button["x"]), float(button["y"]))
        logger.info("已点击按钮「%s」", button.get("text"))
        return True
    except Exception as exc:
        logger.warning("点击提交按钮失败：%s", exc)
        return False


async def dump_page(page: Page, out_dir: Path, question_selectors: list[str]) -> Path:
    """导出当前页面结构与扫描结果，用于校准选择器。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "page.html"
    scan_path = out_dir / "scan.json"
    try:
        html_path.write_text(await page.content(), encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        logger.warning("导出 HTML 失败：%s", exc)
    try:
        scan = await page.evaluate(_SCAN_JS, question_selectors or [])
    except Exception as exc:
        scan = {"found": False, "error": str(exc)}
    scan_path.write_text(
        json.dumps(
            {"url": page.url, "title": await _safe_title(page), "scan": scan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_dir


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


async def page_signature(page: Page, question_selectors: list[str]) -> str:
    """页面题目指纹，用于判断 DOM 上是否出现了一道新题。"""
    problem = await scan_problem(page, question_selectors)
    return problem.key if problem else ""
