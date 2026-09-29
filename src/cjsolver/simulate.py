"""模拟检测：在没有真实课堂的情况下验证"能不能收到题目"。

三种模拟，覆盖的链路深度不同：

1. `run_offline`   —— 纯离线。把仿真的雨课堂载荷直接投喂给真实的
   `QuestionCapture`，验证解析、去重、字段提取和答案格式；可选再打一次模型。
   不需要浏览器，不需要登录。

2. `run_browser`   —— 往真实页面上注入一张仿真答题卡，验证 DOM 兜底抓题、
   选项定位、点击、提交按钮识别。不访问雨课堂，也不会改动线上数据。

3. `feed_live`     —— 把仿真载荷投喂给**正在运行的**监听会话，
   让真实的 Solver 走完"抓题 → 问模型 → 作答 → 落盘"整条链路。

每种都会产出一份带勾选项的诊断清单。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .ai.deepseek import AIError, DeepSeekClient
from .config import Config
from .models import Problem, ProblemType, Suggestion
from .yuketang.capture import QuestionCapture
from .yuketang.constants import Site
from .yuketang.dom import click_option, click_submit, scan_problem

# --------------------------------------------------------------------------- #
# 诊断结果
# --------------------------------------------------------------------------- #


@dataclass
class Check:
    """一条诊断项。"""

    key: str
    label: str
    ok: bool = False
    detail: str = ""
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return self.ok or self.skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "ok": self.ok,
            "skipped": self.skipped,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class SimulationReport:
    """一次模拟检测的完整结果。"""

    kind: str
    title: str
    checks: list[Check] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all(check.passed for check in self.checks)

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    def add(self, key: str, label: str, ok: bool, detail: str = "") -> Check:
        check = Check(key=key, label=label, ok=ok, detail=detail)
        self.checks.append(check)
        return check

    def skip(self, key: str, label: str, detail: str = "") -> Check:
        check = Check(key=key, label=label, ok=False, detail=detail, skipped=True)
        self.checks.append(check)
        return check

    def finish(self) -> "SimulationReport":
        self.finished_at = time.time()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "ok": self.ok,
            "error": self.error,
            "elapsed": round(self.elapsed, 2),
            "checks": [check.to_dict() for check in self.checks],
            "problems": self.problems,
        }


# --------------------------------------------------------------------------- #
# 仿真载荷
# --------------------------------------------------------------------------- #

_SINGLE_PROMPT = "下列关于进程与线程的说法，正确的是？"
_SINGLE_OPTIONS = [
    "进程是资源分配的基本单位",
    "线程是资源分配的基本单位",
    "同一进程内的线程不共享地址空间",
    "线程的创建开销大于进程",
]
_MULTI_PROMPT = "以下属于进程调度算法的有？"
_MULTI_OPTIONS = ["先来先服务", "短作业优先", "时间片轮转", "银行家算法"]
_BLANK_PROMPT = "在页式存储管理中，把逻辑地址映射为物理地址的部件是____。"
_WS_PROMPT = "HTTP 协议默认使用的端口号是？"
_WS_OPTIONS = ["21", "80", "443", "8080"]


def build_presentation_payload(nonce: str, *, lesson_id: str = "") -> dict[str, Any]:
    """构造一份形状与 `presentation/fetch` 响应一致的仿真载荷。

    刻意混用了几种雨课堂真实出现过的字段写法：
    选项既有 `{content: ...}` 对象，也有纯字符串；题干既有 `prompt` 也有 `content`。
    """
    lesson = lesson_id or f"sim-lesson-{nonce}"
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "id": f"sim-presentation-{nonce}",
            "lessonId": lesson,
            "title": "模拟课堂 · 随堂练习",
            "width": 1280,
            "height": 720,
            "slides": [
                {
                    "id": f"sim-slide-{nonce}-0",
                    "index": 0,
                    "title": "封面",
                    "imageUrl": "https://changjiang.yuketang.cn/slide/sim-cover.png",
                    "problem": None,
                },
                {
                    "id": f"sim-slide-{nonce}-1",
                    "index": 1,
                    "imageUrl": "https://changjiang.yuketang.cn/slide/sim-q1.png",
                    "problem": {
                        "problemId": f"sim-{nonce}-1",
                        "problemType": 1,
                        "prompt": _SINGLE_PROMPT,
                        "options": [{"content": text} for text in _SINGLE_OPTIONS],
                        "blanks": [],
                        "result": None,
                    },
                },
                {
                    "id": f"sim-slide-{nonce}-2",
                    "index": 2,
                    "imageUrl": "https://changjiang.yuketang.cn/slide/sim-q2.png",
                    "problem": {
                        # 故意用字符串 id，验证兼容性
                        "problem_id": f"sim-{nonce}-2",
                        "problemType": 2,
                        # 题干用 content 字段
                        "content": _MULTI_PROMPT,
                        "answers": list(_MULTI_OPTIONS),
                    },
                },
                {
                    "id": f"sim-slide-{nonce}-3",
                    "index": 3,
                    "imageUrl": "https://changjiang.yuketang.cn/slide/sim-q3.png",
                    "problem": {
                        "problemId": f"sim-{nonce}-3",
                        "problemType": 4,
                        "prompt": _BLANK_PROMPT,
                        "blanks": ["页表"],
                        "result": None,
                    },
                },
            ],
        },
    }


def build_websocket_frame(nonce: str, *, lesson_id: str = "") -> str:
    """构造一帧形状与 `wsapp` 推送一致的仿真消息（注意 lessonid 是全小写）。"""
    import json

    lesson = lesson_id or f"sim-lesson-{nonce}"
    return json.dumps(
        {
            "op": "problem",
            "lessonid": lesson,
            "data": {
                "problemId": f"sim-{nonce}-ws",
                "problemType": 1,
                "prompt": _WS_PROMPT,
                "options": [{"content": text} for text in _WS_OPTIONS],
            },
        },
        ensure_ascii=False,
    )


def build_mock_problem(nonce: str = "demo") -> dict[str, Any]:
    """浏览器内注入用的题目结构。"""
    letters = "ABCD"
    return {
        "prompt": f"[模拟题] {_SINGLE_PROMPT}",
        "multiple": False,
        "options": [
            {"letter": letters[index], "text": text}
            for index, text in enumerate(_SINGLE_OPTIONS)
        ],
        "answer_letter": "B",
        "answer_text": _SINGLE_OPTIONS[1],
    }


# --------------------------------------------------------------------------- #
# 浏览器内注入
# --------------------------------------------------------------------------- #

_INJECT_JS = r"""
(problem) => {
  const existing = document.getElementById('__cjsolver_mock__');
  if (existing) existing.remove();

  const wrapper = document.createElement('div');
  wrapper.id = '__cjsolver_mock__';
  wrapper.style.cssText = [
    'position:fixed', 'left:24px', 'top:24px', 'z-index:2147483647',
    'width:min(440px, calc(100vw - 60px))',
    'font-family:system-ui, sans-serif'
  ].join(';');

  // 徽标放在答题卡外面：真实的雨课堂页面里，题目卡片内也不会混进别家的文案
  const badge = document.createElement('div');
  badge.textContent = '模拟答题卡 · 由 cjsolver 注入';
  badge.style.cssText = 'font-size:12px;color:#0284c7;margin-bottom:8px;letter-spacing:.02em';
  wrapper.appendChild(badge);

  const card = document.createElement('div');
  card.style.cssText = [
    'background:#ffffff', 'color:#111827',
    'border:2px solid #0ea5e9', 'border-radius:12px', 'padding:18px',
    'font-size:15px', 'line-height:1.6',
    'box-shadow:0 12px 32px rgba(0,0,0,.35)'
  ].join(';');

  const stem = document.createElement('div');
  stem.textContent = problem.prompt;
  stem.style.cssText = 'font-weight:600;margin-bottom:12px';
  card.appendChild(stem);

  const list = document.createElement('div');
  problem.options.forEach((opt) => {
    const label = document.createElement('label');
    label.style.cssText = 'display:block;padding:8px 6px;border-radius:6px;cursor:pointer';
    const input = document.createElement('input');
    input.type = problem.multiple ? 'checkbox' : 'radio';
    input.name = '__cjsolver_mock_q__';
    input.value = opt.letter;
    input.style.cssText = 'margin-right:8px';
    const span = document.createElement('span');
    span.textContent = `${opt.letter}. ${opt.text}`;
    label.appendChild(input);
    label.appendChild(span);
    list.appendChild(label);
  });
  card.appendChild(list);

  const button = document.createElement('button');
  button.textContent = '提交';
  button.style.cssText = 'margin-top:14px;padding:8px 24px;cursor:pointer;border-radius:6px;border:1px solid #0284c7;background:#0ea5e9;color:#fff;font-size:14px';
  button.addEventListener('click', () => { wrapper.dataset.submitted = '1'; });
  card.appendChild(button);

  wrapper.appendChild(card);
  document.body.appendChild(wrapper);
  return true;
}
"""

_READ_JS = r"""
() => {
  const box = document.getElementById('__cjsolver_mock__');
  if (!box) return null;
  const inputs = [...box.querySelectorAll('input')];
  return {
    optionCount: inputs.length,
    checked: inputs.filter((input) => input.checked).map((input) => input.value),
    submitted: box.dataset.submitted === '1',
  };
}
"""

_CLEANUP_JS = r"""
() => {
  const box = document.getElementById('__cjsolver_mock__');
  if (box) box.remove();
  return true;
}
"""


# --------------------------------------------------------------------------- #
# 模拟器
# --------------------------------------------------------------------------- #


class Simulator:
    """跑一次模拟检测并产出诊断清单。"""

    def __init__(
        self,
        config: Config,
        site: Site,
        *,
        client: DeepSeekClient | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.site = site
        self.client = client
        self._emit = emit

    def _log(self, message: str) -> None:
        if self._emit is not None:
            self._emit({"type": "sim-log", "data": {"message": message}})

    # -- 1. 离线：验证能否"解析出题目" ------------------------------------
    async def run_offline(self, *, test_model: bool = True) -> SimulationReport:
        nonce = str(int(time.time() * 1000))[-9:]
        report = SimulationReport(kind="offline", title="离线抓题自检")
        handler_problems: list[Problem] = []

        async def collect(problem: Problem) -> None:
            handler_problems.append(problem)

        capture = QuestionCapture(None, self.site, collect)
        await capture.start()
        try:
            # 1.1 课件接口载荷
            self._log("投喂 presentation/fetch 仿真载荷…")
            presentation = build_presentation_payload(nonce)
            first = await capture.feed_payload(
                presentation, origin="simulate:presentation", presentation_id=presentation["data"]["id"]
            )
            report.add(
                "capture.presentation",
                "解析课件接口（presentation/fetch）载荷",
                len(first) == 3,
                f"识别出 {len(first)} 道题，期望 3 道",
            )

            # 1.2 WebSocket 帧
            self._log("投喂 wsapp 仿真帧…")
            frame = await capture.feed_frame_text(build_websocket_frame(nonce))
            report.add(
                "capture.websocket",
                "解析 WebSocket（wsapp）推送帧",
                len(frame) == 1,
                f"识别出 {len(frame)} 道题，期望 1 道",
            )

            # 1.3 去重：同一份载荷再投一次不应重复上报
            repeat = await capture.feed_payload(
                presentation, origin="simulate:presentation-repeat"
            )
            report.add(
                "capture.dedupe",
                "重复载荷去重",
                len(repeat) == 0,
                f"重复投喂产生 {len(repeat)} 道新题，期望 0 道",
            )

            # 1.4 字段提取
            by_id = {problem.id: problem for problem in handler_problems}
            single = by_id.get(f"sim-{nonce}-1")
            ws_problem = by_id.get(f"sim-{nonce}-ws")
            lesson = f"sim-lesson-{nonce}"
            if single is None:
                report.add("parse.fields", "题目字段提取", False, "没有解析出单选题")
            else:
                problems_found: list[str] = []
                if single.type is not ProblemType.SINGLE_CHOICE:
                    problems_found.append(f"题型={single.type.value}")
                if len(single.options) != 4:
                    problems_found.append(f"选项数={len(single.options)}")
                if [o.letter for o in single.options] != ["A", "B", "C", "D"]:
                    problems_found.append("选项字母不连续")
                if single.presentation_id != f"sim-presentation-{nonce}":
                    problems_found.append(f"presentation_id={single.presentation_id!r}")
                if not single.image_url.endswith("sim-q1.png"):
                    problems_found.append("课件图片没带过来")
                # 课件载荷里是小驼峰 lessonId
                if single.lesson_id != lesson:
                    problems_found.append(f"lesson_id={single.lesson_id!r}")
                # wsapp 推送里是全小写 lessonid，走的是另一条查找分支
                if ws_problem is None:
                    problems_found.append("没有解析出 WebSocket 题目")
                elif ws_problem.lesson_id != lesson:
                    problems_found.append(f"WebSocket lesson_id={ws_problem.lesson_id!r}")
                report.add(
                    "parse.fields",
                    "题目字段提取（题型/选项/关联 id/课件图）",
                    not problems_found,
                    "；".join(problems_found)
                    or f"id={single.id}，4 个选项，lesson/presentation/课件图均已关联",
                )

            # 1.5 题型映射
            types = {problem.id: problem.type for problem in handler_problems}
            expected = {
                f"sim-{nonce}-1": ProblemType.SINGLE_CHOICE,
                f"sim-{nonce}-2": ProblemType.MULTIPLE_CHOICE,
                f"sim-{nonce}-3": ProblemType.FILL_BLANK,
                f"sim-{nonce}-ws": ProblemType.SINGLE_CHOICE,
            }
            mismatched = [
                f"{key}: {types.get(key)}"
                for key, want in expected.items()
                if types.get(key) is not want
            ]
            report.add(
                "parse.types",
                "题型编码映射（1 单选 / 2 多选 / 4 填空）",
                not mismatched,
                "；".join(mismatched) or "单选、多选、填空、WS 推送均映射正确",
            )

            # 1.6 答案格式
            report.add(*self._check_answer_format(handler_problems))

            # 1.7 模型（可选）
            if test_model:
                report.checks.append(await self._check_model(by_id.get(f"sim-{nonce}-1")))
            else:
                report.skip("model.deepseek", "调用 DeepSeek 生成答案", "本次未启用模型检测")

            report.problems = [_problem_summary(problem) for problem in handler_problems]
        except Exception as exc:  # pragma: no cover - 防御性
            report.error = f"{type(exc).__name__}: {exc}"
        finally:
            await capture.stop()
        return report.finish()

    # -- 2. 浏览器：验证页面里能不能"抓到题" ------------------------------
    async def run_browser(self, page: Any, *, test_model: bool = False) -> SimulationReport:
        report = SimulationReport(kind="browser", title="浏览器抓题自检")
        mock = build_mock_problem()
        try:
            # 雨课堂首页会做客户端跳转，先等页面稳定再注入，否则 evaluate 会撞上导航
            await _wait_settled(page, self._log)

            self._log("向页面注入模拟答题卡…")
            try:
                await page.evaluate(_INJECT_JS, mock)
                injected = await page.evaluate(_READ_JS)
            except Exception as exc:
                report.add(
                    "browser.inject",
                    "注入模拟答题卡",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
                return report.finish()
            report.add(
                "browser.inject",
                "向当前页面注入模拟答题卡",
                bool(injected) and injected.get("optionCount") == 4,
                f"页面内检测到 {injected.get('optionCount') if injected else 0} 个选项",
            )

            # 2.1 DOM 扫题
            problem = await scan_problem(page, self.config.answer.question_selectors)
            if problem is None:
                report.add("dom.scan", "DOM 扫描识别题干与选项", False, "没有识别出题目")
                return report.finish()

            issues: list[str] = []
            if len(problem.options) != 4:
                issues.append(f"选项数={len(problem.options)}")
            if "进程与线程" not in problem.prompt:
                issues.append(f"题干={problem.prompt[:40]!r}")
            if "模拟答题卡" in problem.prompt:
                issues.append("题干里混入了注入面板自己的文案")
            report.add(
                "dom.scan",
                "DOM 扫描识别题干与选项",
                not issues,
                "；".join(issues) or f"题干与 4 个选项均正确，题型={problem.type.label}",
            )
            report.problems = [_problem_summary(problem)]

            # 2.2 点击选项
            target = mock["answer_text"]
            clicked = await click_option(
                page,
                letter=mock["answer_letter"],
                text=target,
                selectors=self.config.answer.option_selectors,
            )
            state = await _safe_read(page)
            checked = state.get("checked") or []
            report.add(
                "dom.click",
                f"点击选项 {mock['answer_letter']} 并命中对应控件",
                bool(clicked) and checked == [mock["answer_letter"]],
                f"点击={clicked}，实际选中={checked or '无'}，期望=['{mock['answer_letter']}']",
            )

            # 2.3 提交按钮（页面可能仍在跳转，单独兜住异常）
            try:
                submitted = await click_submit(page, self.config.answer.submit_button_texts)
                state = await _safe_read(page)
                report.add(
                    "dom.submit",
                    "识别并点击提交按钮",
                    bool(submitted) and state.get("submitted") is True,
                    f"找到按钮={submitted}，按钮回调触发={state.get('submitted')}",
                )
            except Exception as exc:
                report.add(
                    "dom.submit",
                    "识别并点击提交按钮",
                    False,
                    f"页面在检测过程中发生了跳转：{type(exc).__name__}",
                )

            # 2.4 模型（可选）
            if test_model:
                report.checks.append(await self._check_model(problem))
            else:
                report.skip("model.deepseek", "调用 DeepSeek 生成答案", "本次未启用模型检测")
        except Exception as exc:  # pragma: no cover - 防御性
            report.error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                await page.evaluate(_CLEANUP_JS)
            except Exception:  # pragma: no cover - 页面可能已经跳走
                pass
        return report.finish()

    # -- 3. 端到端：投喂给正在运行的监听会话 ------------------------------
    async def feed_live(self, capture: QuestionCapture) -> SimulationReport:
        report = SimulationReport(kind="live", title="端到端投喂检测")
        nonce = str(int(time.time() * 1000))[-9:]
        try:
            self._log("向正在运行的监听会话投喂仿真题目…")
            presentation = build_presentation_payload(nonce)
            first = await capture.feed_payload(
                presentation,
                origin="simulate:live",
                presentation_id=presentation["data"]["id"],
            )
            report.add(
                "live.presentation",
                "监听会话接收课件载荷",
                len(first) == 3,
                f"会话接收 {len(first)} 道题，已进入作答队列",
            )
            frame = await capture.feed_frame_text(build_websocket_frame(nonce))
            report.add(
                "live.websocket",
                "监听会话接收 WebSocket 帧",
                len(frame) == 1,
                f"会话接收 {len(frame)} 道题",
            )
            repeat = await capture.feed_payload(presentation, origin="simulate:live-repeat")
            report.add(
                "live.dedupe",
                "监听会话去重",
                len(repeat) == 0,
                f"重复投喂产生 {len(repeat)} 道新题",
            )
            report.problems = [_problem_summary(p) for p in (*first, *frame)]
            report.add(
                "live.queue",
                "题目已进入作答队列（答案请见右侧事件流）",
                True,
                "AI 作答是异步的，稍后会在实时事件流里出现对应的答案卡片",
            )
        except Exception as exc:  # pragma: no cover - 防御性
            report.error = f"{type(exc).__name__}: {exc}"
        return report.finish()

    # -- 公共检查项 -------------------------------------------------------
    def _check_answer_format(self, problems: list[Problem]) -> tuple[str, str, bool, str]:
        """验证生成的提交载荷符合雨课堂要求的形状。"""
        by_type: dict[ProblemType, Problem] = {}
        for problem in problems:
            by_type.setdefault(problem.type, problem)

        issues: list[str] = []
        single = by_type.get(ProblemType.SINGLE_CHOICE)
        if single is not None:
            payload = Suggestion(problem_id=single.id, letters=["B"]).submit_payload(single)
            if payload != ["B"]:
                issues.append(f"单选载荷={payload!r}")

        multiple = by_type.get(ProblemType.MULTIPLE_CHOICE)
        if multiple is not None:
            payload = Suggestion(problem_id=multiple.id, letters=["A", "C"]).submit_payload(multiple)
            if payload != ["A", "C"]:
                issues.append(f"多选载荷={payload!r}")

        blank = by_type.get(ProblemType.FILL_BLANK)
        if blank is not None:
            payload = Suggestion(problem_id=blank.id, texts=["页表"]).submit_payload(blank)
            if payload != ["页表"]:
                issues.append(f"填空载荷={payload!r}")

        detail = "；".join(issues) or "单选 -> ['B']，多选 -> ['A','C']，填空 -> ['页表']"
        return (
            "answer.format",
            "提交载荷格式（单选/多选/填空）",
            not issues,
            detail,
        )

    async def _check_model(self, problem: Problem | None) -> Check:
        """真的打一次模型，验证端到端可用。"""
        if self.client is None:
            return Check(
                key="model.deepseek",
                label="调用 DeepSeek 生成答案",
                ok=False,
                skipped=True,
                detail="未配置 API Key，已跳过",
            )
        if problem is None:
            return Check(
                key="model.deepseek",
                label="调用 DeepSeek 生成答案",
                ok=False,
                skipped=True,
                detail="没有可用于提问的题目",
            )
        self._log("调用 DeepSeek…")
        try:
            suggestion = await self.client.ask(problem)
        except AIError as exc:
            return Check(
                key="model.deepseek",
                label="调用 DeepSeek 生成答案",
                ok=False,
                detail=f"{exc}",
            )
        detail = (
            f"模型={suggestion.model} 答案={suggestion.display(problem)} "
            f"耗时={suggestion.elapsed:.1f}s"
        )
        if not suggestion.answerable:
            return Check(
                key="model.deepseek",
                label="调用 DeepSeek 生成答案",
                ok=False,
                detail=f"{detail}（未能给出可提交的答案）",
            )
        return Check(key="model.deepseek", label="调用 DeepSeek 生成答案", ok=True, detail=detail)


def _problem_summary(problem: Problem) -> dict[str, Any]:
    return {
        "id": problem.id,
        "type": problem.type.value,
        "type_label": problem.type.label,
        "prompt": problem.prompt,
        "options": [
            {"letter": option.letter, "text": option.text} for option in problem.options
        ],
        "source": problem.source,
        "simulated": problem.id.startswith("sim-"),
    }


async def _wait_settled(page: Any, log: Callable[[str], None] | None = None, *, timeout: float = 8.0) -> None:
    """等页面稳定下来。

    雨课堂首页会做客户端跳转，注入太早会让 evaluate 撞上导航
    （Execution context was destroyed）。
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=int(timeout * 1000))
    except Exception:
        if log:
            log("页面加载状态等待超时，继续尝试注入。")
    await asyncio.sleep(0.5)


async def _safe_read(page: Any) -> dict[str, Any]:
    """读取模拟卡片状态；页面正在跳转时返回空字典而不是抛异常。"""
    try:
        return await page.evaluate(_READ_JS) or {}
    except Exception:
        return {}
