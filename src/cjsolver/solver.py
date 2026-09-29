"""编排器：抓题 -> 问 DeepSeek -> 作答 -> 记录。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page

from . import console
from .ai.deepseek import AIError, DeepSeekClient
from .config import Config
from .models import Problem, Suggestion
from .submit import SubmitResult, submit
from .yuketang.capture import QuestionCapture
from .yuketang.constants import Site
from .yuketang.dom import dump_page, scan_problem

logger = logging.getLogger(__name__)

#: 事件回调：接收一个可 JSON 序列化的字典
EventSink = Callable[[dict[str, Any]], None]


@dataclass
class SolverStats:
    problems: int = 0
    answered: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: float = field(default_factory=time.time)

    def summary(self) -> str:
        elapsed = time.time() - self.started_at
        return (
            f"共遇到 {self.problems} 题，作答 {self.answered} 题，"
            f"跳过 {self.skipped} 题，失败 {self.failed} 题，耗时 {elapsed:.0f}s"
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "problems": self.problems,
            "answered": self.answered,
            "skipped": self.skipped,
            "failed": self.failed,
            "elapsed": int(time.time() - self.started_at),
        }


class Solver:
    """把捕获到的题目排队，逐题交给模型并落盘。"""

    def __init__(
        self,
        config: Config,
        page: Page,
        site: Site,
        client: DeepSeekClient,
        *,
        on_event: EventSink | None = None,
    ) -> None:
        self.config = config
        self.page = page
        self.site = site
        self.client = client
        self.stats = SolverStats()
        self.on_event = on_event

        self._queue: asyncio.Queue[Problem] = asyncio.Queue()
        self._handled: set[str] = set()
        self._stop = asyncio.Event()
        #: run() 真正挂上抓题器之后置位，供调用方等待"监听已就绪"
        self._ready = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._capture: QuestionCapture | None = None
        self._record_file: Path | None = None

    # -- 事件 -------------------------------------------------------------
    def emit(self, event_type: str, **data: Any) -> None:
        """向外发布一条事件（网页控制台据此渲染实时流）。"""
        if self.on_event is None:
            return
        try:
            self.on_event({"type": event_type, "data": data})
        except Exception:  # pragma: no cover - 事件订阅方不应影响主流程
            logger.debug("事件回调失败", exc_info=True)

    # -- 生命周期 ---------------------------------------------------------
    async def run(self, *, duration: float | None = None) -> SolverStats:
        console.rule(f"开始监听 · {self.site.label} · 作答模式 {self.config.answer.mode}")
        if self.config.answer.dry_run or self.config.answer.mode == "manual":
            console.warn("当前不会自动提交答案。需要自动作答请修改 config.yaml 的 answer.mode。")

        self._open_record_file()
        self._capture = QuestionCapture(self.page, self.site, self.enqueue)
        await self._capture.start()
        self._ready.set()
        self.emit(
            "watching",
            active=True,
            mode=self.config.answer.mode,
            dry_run=self.config.answer.dry_run,
            site=self.site.key,
            record_file=str(self._record_file) if self._record_file else None,
        )

        self._tasks.append(asyncio.create_task(self._worker(), name="solver-worker"))
        if self.config.solver.dom_fallback:
            self._tasks.append(asyncio.create_task(self._dom_poller(), name="dom-poller"))
        if self.config.solver.auto_enter_lesson:
            console.info("已开启「自动进入上课中的课堂」，正在检查进行中的课堂…")
            self.emit("log", level="info", message="已开启「自动进入上课中的课堂」")
            self._tasks.append(
                asyncio.create_task(self._lesson_watcher(), name="lesson-watcher")
            )
        if self.config.solver.heartbeat_interval > 0:
            self._tasks.append(asyncio.create_task(self._heartbeat(), name="heartbeat"))
            console.info(
                f"心跳日志每 {self.config.solver.heartbeat_interval:g} 秒输出一次"
                "（solver.heartbeat_interval，设 0 可关闭）"
            )

        try:
            if duration is None:
                await self._stop.wait()
            else:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=duration)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.shutdown()
        return self.stats

    async def shutdown(self) -> None:
        self._stop.set()
        if self._capture is not None:
            await self._capture.stop()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        console.rule("监听结束")
        console.info(self.stats.summary())
        self.emit("watching", active=False, stats=self.stats.to_dict())

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def ready(self) -> bool:
        """抓题器是否已经挂上（即 feed/投喂可以安全调用了）。"""
        return self._ready.is_set()

    async def wait_ready(self, timeout: float = 15.0) -> bool:
        """等待监听真正就绪。run() 是异步任务，调用方需要这个才能安全地投喂。"""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @property
    def capture(self) -> QuestionCapture | None:
        """当前会话的抓题器（未启动时为 None），供控制台查询统计。"""
        return self._capture

    @property
    def active(self) -> bool:
        return not self._stop.is_set()

    # -- 入队 -------------------------------------------------------------
    async def enqueue(self, problem: Problem) -> None:
        if problem.key in self._handled:
            return
        self._handled.add(problem.key)
        await self._queue.put(problem)

    # -- 消费者 -----------------------------------------------------------
    async def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                problem = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self._process(problem)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("处理题目 %s 时出现未预期错误", problem.id)
                self.stats.failed += 1
            finally:
                self._queue.task_done()

    async def _process(self, problem: Problem) -> None:
        self.stats.problems += 1
        index = self.stats.problems
        console.problem_panel(f"第 {index} 题", problem)
        self.emit("problem", index=index, problem=_problem_payload(problem))

        if problem.already_answered:
            console.info("该题此前已作答，跳过。")
            self.stats.skipped += 1
            self.emit("skip", problem_id=problem.id, reason="此前已作答")
            return

        images = [problem.image_url] if problem.needs_vision and self.config.deepseek.vision_enabled else []
        if problem.needs_vision and not images:
            console.warn(
                "这道题的题干在课件图片里，而 deepseek.vision_enabled 为 false；"
                "模型只能靠选项推断，准确率会下降。"
            )
            self.emit(
                "warn",
                problem_id=problem.id,
                message="题干位于课件图片中，未启用视觉模型，仅能靠选项推断。",
            )

        self.emit("thinking", problem_id=problem.id)
        try:
            suggestion = await self.client.ask(problem, images=images)
        except AIError as exc:
            console.error(f"模型调用失败：{exc}")
            self.stats.failed += 1
            self.emit("error", problem_id=problem.id, message=f"模型调用失败：{exc}")
            return

        if not suggestion.answerable:
            console.warn(f"模型没能给出答案：{suggestion.failure_reason or '未知原因'}")
            console.answer_panel(suggestion, problem, mode=self.config.answer.mode, applied=False)
            self.stats.skipped += 1
            self.emit(
                "answer",
                problem_id=problem.id,
                ok=False,
                reason=suggestion.failure_reason or "模型未给出答案",
                suggestion=_suggestion_payload(suggestion, problem),
            )
            await self._record(problem, suggestion, None, note="模型未给出答案")
            return

        threshold = self.config.answer.min_confidence
        low_confidence = (
            suggestion.confidence is not None and suggestion.confidence < threshold
        )
        if low_confidence:
            console.warn(
                f"置信度 {suggestion.confidence:.0%} 低于阈值 {threshold:.0%}，只提示不自动作答。"
            )

        result = await submit(
            self.page,
            problem,
            suggestion,
            self.config.answer,
            self.site,
            dry_run=low_confidence,
        )
        if result.ok:
            self.stats.answered += 1
            console.success(result.detail)
        elif result.skipped:
            self.stats.skipped += 1
        else:
            self.stats.failed += 1
            console.error(result.detail)

        console.answer_panel(
            suggestion, problem, mode=result.mode, applied=result.ok
        )
        self.emit(
            "answer",
            problem_id=problem.id,
            ok=result.ok,
            suggestion=_suggestion_payload(suggestion, problem),
            submit={
                "ok": result.ok,
                "mode": result.mode,
                "detail": result.detail,
                "skipped": result.skipped,
            },
            stats=self.stats.to_dict(),
        )
        await self._record(problem, suggestion, result, note=result.detail)
        await asyncio.sleep(0.3)

    # -- 心跳 -------------------------------------------------------------
    async def _heartbeat(self) -> None:
        """监听期间每隔一段时间汇报一次状态，让"还在跑"这件事可见。"""
        interval = max(1.0, self.config.solver.heartbeat_interval)
        while not self._stop.is_set():
            # 用 wait 而不是 sleep：停止时能立刻退出，不用等满一个周期
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                payload = await self.heartbeat_payload()
            except Exception:  # pragma: no cover - 心跳绝不能拖垮会话
                logger.debug("生成心跳失败", exc_info=True)
                continue
            console.heartbeat_panel(payload)
            self.emit("heartbeat", **payload)

    async def heartbeat_payload(self) -> dict[str, Any]:
        """组装一次心跳的内容（纯数据，便于测试与网页复用）。"""
        uptime = int(time.time() - self.stats.started_at)
        page_alive = False
        page_url = ""
        page_title = ""
        try:
            page_alive = not self.page.is_closed()
            if page_alive:
                page_url = self.page.url
                page_title = await self.page.title()
        except Exception:  # pragma: no cover - 页面可能已被关闭
            page_alive = False

        return {
            "uptime": uptime,
            "uptime_text": _format_duration(uptime),
            "stats": self.stats.to_dict(),
            "capture": dict(self._capture.stats) if self._capture is not None else {},
            "queue": self._queue.qsize(),
            "page": {"alive": page_alive, "url": page_url, "title": page_title},
            "record_file": str(self._record_file) if self._record_file else None,
        }

    # -- DOM 兜底 ---------------------------------------------------------
    async def _dom_poller(self) -> None:
        interval = max(0.5, self.config.solver.poll_interval)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            try:
                if self.page.is_closed():
                    return
                problem = await scan_problem(self.page, self.config.answer.question_selectors)
            except Exception as exc:
                logger.debug("DOM 轮询失败：%s", exc)
                continue
            if problem is None or problem.key in self._handled:
                continue
            if self._capture is not None:
                self._capture.mark_seen(problem)
            logger.info("DOM 兜底发现题目 %s", problem.id)
            await self.enqueue(problem)

    # -- 自动进入课堂 -----------------------------------------------------
    async def _lesson_watcher(self) -> None:
        """轮询「正在进行的课堂」，发现新的就自动跳进去。

        第一轮不等间隔，启动后立刻查一次，之后每 enter_lesson_interval 秒查一次。
        已经进过的课堂不会重复跳转；换成另一节课（lesson id 变了）才会再跳。
        """
        from .browser.driver import goto_lesson, on_lesson_report

        interval = max(5.0, self.config.solver.enter_lesson_interval)
        entered = ""
        last_note = ""
        told_empty = False
        first_round = True
        while not self._stop.is_set():
            if not first_round:
                await asyncio.sleep(interval)
            first_round = False

            try:
                lessons, note = await on_lesson_report(self.page, self.site)
            except Exception as exc:  # pragma: no cover - 兜底
                lessons, note = [], f"查询失败：{exc}"

            if note:
                # 同一个原因只提示一次，免得每轮刷屏
                if note != last_note:
                    last_note = note
                    console.warn(f"自动进入课堂：{note}")
                    self.emit("warn", message=f"自动进入课堂：{note}")
                continue
            last_note = ""

            if not lessons:
                if not told_empty:
                    told_empty = True
                    self.emit("log", level="info", message="自动进入课堂：目前没有进行中的课堂")
                continue
            told_empty = False

            lesson = lessons[0]
            lesson_id = str(lesson.get("id") or "").strip()
            title = str(lesson.get("title") or "")
            if not lesson_id or lesson_id == entered:
                continue
            # 已经停在这个课堂的页面里了：记下来，别再跳一次
            if lesson_id in self.page.url:
                entered = lesson_id
                continue

            console.success(f"检测到进行中的课堂「{title or lesson_id}」，正在进入…")
            self.emit("lesson", lesson_id=lesson_id, title=title, stage="entering")
            try:
                await goto_lesson(
                    self.page, self.site, lesson_id, lesson.get("classroom_id")
                )
            except Exception as exc:
                console.error(f"进入课堂失败：{exc}")
                self.emit(
                    "lesson",
                    lesson_id=lesson_id,
                    title=title,
                    stage="failed",
                    message=str(exc),
                )
                continue
            entered = lesson_id
            self.emit(
                "lesson",
                lesson_id=lesson_id,
                title=title,
                stage="entered",
                url=self.page.url,
            )

    # -- 记录 -------------------------------------------------------------
    def _open_record_file(self) -> None:
        directory = self.config.record_path
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._record_file = directory / f"session-{stamp}.jsonl"
        console.info(f"问答记录将写入 {self._record_file}")

    async def _record(
        self,
        problem: Problem,
        suggestion: Suggestion,
        result: SubmitResult | None,
        *,
        note: str = "",
    ) -> None:
        if self._record_file is None:
            return
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "problem_id": problem.id,
            "type": problem.type.value,
            "prompt": problem.prompt,
            "options": [
                {"letter": option.letter, "text": option.text} for option in problem.options
            ],
            "answer": {
                "letters": suggestion.letters,
                "texts": suggestion.texts,
                "display": suggestion.display(problem),
            },
            "explanation": suggestion.explanation,
            "confidence": suggestion.confidence,
            "model": suggestion.model,
            "elapsed": round(suggestion.elapsed, 2),
            "submit": None
            if result is None
            else {"ok": result.ok, "mode": result.mode, "detail": result.detail},
            "note": note,
        }
        try:
            with self._record_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover
            logger.warning("写入记录失败：%s", exc)


async def dump_current_page(page: Page, config: Config) -> Path:
    """导出当前页面，用于校准选择器。"""
    out_dir = config.project_root / "records" / "page-dump"
    path = await dump_page(page, out_dir, config.answer.question_selectors)
    console.success(f"页面结构已导出到 {path}")
    return path


def _format_duration(seconds: int) -> str:
    """把秒数格式化成 1h02m03s / 05m12s / 42s。"""
    if seconds < 0:
        seconds = 0
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes:02d}m{secs:02d}s"
    return f"{secs}s"


def _problem_payload(problem: Problem) -> dict[str, Any]:
    """题目的可序列化视图。"""
    return {
        "id": problem.id,
        "type": problem.type.value,
        "type_label": problem.type.label,
        "prompt": problem.prompt,
        "options": [
            {"letter": option.letter, "text": option.text} for option in problem.options
        ],
        "blanks": list(problem.blanks),
        "image_url": problem.image_url,
        "source": problem.source,
        "needs_vision": problem.needs_vision,
    }


def _suggestion_payload(suggestion: Suggestion, problem: Problem) -> dict[str, Any]:
    """建议答案的可序列化视图。"""
    return {
        "problem_id": suggestion.problem_id,
        "display": suggestion.display(problem),
        "letters": list(suggestion.letters),
        "texts": list(suggestion.texts),
        "explanation": suggestion.explanation,
        "confidence": suggestion.confidence,
        "failure_reason": suggestion.failure_reason,
        "model": suggestion.model,
        "elapsed": round(suggestion.elapsed, 2),
        "payload": suggestion.submit_payload(problem),
    }
