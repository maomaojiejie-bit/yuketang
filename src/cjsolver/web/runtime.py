"""控制台运行时：持有浏览器、模型客户端与监听会话的生命周期。

所有耗时操作都在同一个事件循环里，对外暴露的方法都可以被 HTTP 处理器直接 await。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from typing import Any

from .. import console
from ..adhoc import parse_adhoc_problem
from ..ai.deepseek import AIError, DeepSeekClient
from ..browser import AttachedBrowser, attach, ensure_chrome, resize_window
from ..browser.launcher import ChromeHandle, is_cdp_ready, kill_chrome
from ..config import Config, ConfigError
from ..simulate import SimulationReport, Simulator
from ..solver import Solver, _problem_payload  # noqa: PLC2701 - 复用同一份题目摘要结构
from ..yuketang.constants import site_by_key
from . import settings
from .bus import EventBus

logger = logging.getLogger(__name__)


class ConsoleError(RuntimeError):
    """面向用户的操作失败，消息可直接展示在控制台上。"""


class ConsoleRuntime:
    """网页控制台的唯一状态持有者。"""

    def __init__(self, config: Config) -> None:
        # 控制台里存过的设置优先级高于 config.yaml
        self.config = settings.load(config)
        self.bus = EventBus()
        self.site = site_by_key(config.site)

        self._handle: ChromeHandle | None = None
        self._attached: AttachedBrowser | None = None
        self._client: DeepSeekClient | None = None
        self._solver: Solver | None = None
        self._watch_task: asyncio.Task[Any] | None = None
        self._last_report: SimulationReport | None = None
        self._browser_lock = asyncio.Lock()
        self._busy = False

    # -- 生命周期 ---------------------------------------------------------
    async def start(self) -> None:
        """进程启动时调用：有 Key 就先把模型客户端建好。"""
        if self.config.deepseek.api_key.strip():
            self._client = DeepSeekClient(self.config.deepseek)
            await self._client.__aenter__()
            self.bus.log(f"模型客户端已就绪：{self.config.deepseek.effective_model}")
        else:
            from ..providers import key_env_hint, provider_by_id

            provider = provider_by_id(self.config.deepseek.provider)
            self.bus.log(
                f"未配置 {provider.label} 的 API Key（.env 里填 "
                f"{key_env_hint(provider)}），模型相关功能不可用。",
                level="warn",
            )

    async def close(self) -> None:
        await self.stop_watch()
        await self.detach_browser()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- 设置 -------------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        """当前可调设置，供控制台回填表单。"""
        return settings.describe(self.config)

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """应用一份设置并落盘；后续启动的监听会用新配置。"""
        try:
            config, values = settings.apply(self.config, payload)
        except settings.SettingsError as exc:
            raise ConsoleError(str(exc)) from exc
        self.config = config
        path = settings.save(config, values)
        self.bus.log(f"设置已更新（保存到 {path.name}）")
        # 换服务商后 Key 要重新解析，并把新配置推给已在跑的客户端
        if self._client is not None:
            self._client.update_config(self.config.deepseek)
        return settings.describe(config)

    # -- API Key ----------------------------------------------------------
    async def set_api_key(self, key: str) -> dict[str, Any]:
        """把 API Key 写进 .env 并立即生效。

        写 .env 而不是 console-settings.json，是因为后者**已被提交进仓库**，
        密钥写进去等于推到 GitHub。
        """
        from ..envfile import mask_secret, upsert_env
        from ..providers import key_env_hint, provider_by_id

        provider = provider_by_id(self.config.deepseek.provider)
        env_name = key_env_hint(provider)
        cleaned = (key or "").strip()
        if cleaned and any(char.isspace() for char in cleaned):
            raise ConsoleError("API Key 里不应有空格或换行，请重新粘贴。")

        env_path = self.config.project_root / ".env"
        try:
            upsert_env(env_path, {env_name: cleaned})
        except OSError as exc:
            raise ConsoleError(f"写入 {env_path.name} 失败：{exc}") from exc

        # 同步进程环境，后续 apply_provider_key / 重启解析都拿得到
        if cleaned:
            os.environ[env_name] = cleaned
        else:
            os.environ.pop(env_name, None)

        self.config = dataclasses.replace(
            self.config,
            deepseek=dataclasses.replace(self.config.deepseek, api_key=cleaned),
        )

        if cleaned:
            if self._client is None:
                self._client = DeepSeekClient(self.config.deepseek)
                await self._client.__aenter__()
            else:
                self._client.update_config(self.config.deepseek)
            self.bus.log(
                f"{provider.label} 的 API Key 已更新（{mask_secret(cleaned)}），立即生效。"
            )
        else:
            self.bus.log(f"已清空 {env_name}，模型相关功能暂时不可用。", level="warn")

        return {
            "env_name": env_name,
            "provider": provider.id,
            "provider_label": provider.label,
            "configured": bool(cleaned),
            "masked": mask_secret(cleaned),
            "path": str(env_path),
        }

    # -- 浏览器 -----------------------------------------------------------
    async def ensure_browser(self) -> AttachedBrowser:
        """确保 Chrome 已启动并接管，返回可用的页面。"""
        if self._attached is not None:
            try:
                if not self._attached.page.is_closed():
                    return self._attached
            except Exception:  # pragma: no cover - 浏览器可能已被关掉
                pass
            await self.detach_browser()

        async with self._browser_lock:
            if self._attached is not None:
                return self._attached
            self.bus.log("正在启动 / 接管调试 Chrome…")
            try:
                self._handle = await ensure_chrome(
                    self.config.browser, self.config.user_data_path
                )
                attached = await attach(self._handle.cdp_url, self.site)
            except (ConfigError, RuntimeError) as exc:
                await self.detach_browser()
                raise ConsoleError(str(exc)) from exc
            self._attached = attached
            title = ""
            try:
                title = await attached.page.title()
            except Exception:  # pragma: no cover
                pass
            self.bus.publish(
                "browser",
                data={
                    "attached": True,
                    "url": attached.page.url,
                    "title": title,
                    "cdp_url": self._handle.cdp_url,
                },
            )
            self.bus.log(f"已接管页面：{attached.page.url}")
            return attached

    async def detach_browser(self) -> None:
        if self._attached is not None:
            await self._attached.close()
            self._attached = None
            self.bus.publish("browser", data={"attached": False})
        if self._handle is not None:
            if self._handle.launched:
                kill_chrome(self._handle)
            self._handle = None

    # -- 监听会话 ---------------------------------------------------------
    async def start_watch(
        self,
        *,
        mode: str | None = None,
        dry_run: bool | None = None,
        duration: float | None = None,
    ) -> dict[str, Any]:
        if self._watch_task is not None and not self._watch_task.done():
            raise ConsoleError("监听已经在运行中。")

        # 配置问题（缺 Key、模式非法）应当是可控的 400，而不是 500。
        # 放在客户端检查之前：config.validate() 的措辞会指名道姓说缺哪个
        # 环境变量（切到千问后就不该再喊 DeepSeek）。
        try:
            config = self._with_overrides(mode=mode, dry_run=dry_run)
        except ConfigError as exc:
            raise ConsoleError(str(exc)) from exc

        if self._client is None:
            raise ConsoleError("模型客户端未就绪，请在控制台里检查 API Key 后重新打开控制台。")

        attached = await self.ensure_browser()

        # 监听期间把浏览器窗口缩到配置里的尺寸，免得挡住自己的窗口
        browser = self.config.browser
        if browser.window_width > 0 and browser.window_height > 0:
            resized = await resize_window(
                attached.page, browser.window_width, browser.window_height
            )
            if resized:
                self.bus.log(
                    f"浏览器窗口已调整为 {browser.window_width}×{browser.window_height}"
                )

        solver = Solver(
            config,
            attached.page,
            self.site,
            self._client,
            on_event=self.bus.publish,
        )
        self._solver = solver
        self._watch_task = asyncio.create_task(solver.run(duration=duration))
        # run() 是异步任务，必须等它真正挂上抓题器，否则紧接着的投喂会落空
        if not await solver.wait_ready(timeout=20):
            await self.stop_watch()
            raise ConsoleError("监听启动超时，请查看终端日志。")
        self.bus.log(
            f"监听已启动：模式={config.answer.mode}"
            f"{'（dry-run）' if config.answer.dry_run else ''}"
        )
        return self.state()

    async def stop_watch(self) -> dict[str, Any]:
        if self._solver is not None:
            self._solver.request_stop()
        if self._watch_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._watch_task), timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._watch_task.cancel()
            except Exception:  # pragma: no cover - 会话内部异常已记录
                logger.debug("监听任务结束时抛出异常", exc_info=True)
            self._watch_task = None
        self._solver = None
        return self.state()

    # -- 模拟检测 ---------------------------------------------------------
    async def simulate(self, kind: str) -> dict[str, Any]:
        if self._busy:
            raise ConsoleError("已有诊断任务在运行，请稍候。")
        self._busy = True
        self.bus.log(f"开始模拟检测：{kind}")
        try:
            simulator = Simulator(
                self.config, self.site, client=self._client, emit=self.bus.publish
            )
            if kind == "offline":
                report = await simulator.run_offline(test_model=self._client is not None)
            elif kind == "browser":
                attached = await self.ensure_browser()
                report = await simulator.run_browser(
                    attached.page, test_model=self._client is not None
                )
            elif kind == "live":
                capture = self._solver.capture if self._solver is not None else None
                if capture is None:
                    raise ConsoleError("请先启动监听，再执行端到端投喂检测。")
                report = await simulator.feed_live(capture)
            else:
                raise ConsoleError(f"未知的模拟类型：{kind}")
        finally:
            self._busy = False

        self._last_report = report
        payload = report.to_dict()
        self.bus.publish("simulation", data=payload)
        level = "info" if report.ok else "warn"
        summary = "全部通过" if report.ok else "存在未通过项"
        self.bus.log(f"模拟检测结束（{report.title}）：{summary}", level=level)
        return payload

    # -- 手动提问 ---------------------------------------------------------
    async def ask(self, text: str) -> dict[str, Any]:
        if self._client is None:
            raise ConsoleError("未配置 DeepSeek API Key。")
        problem = parse_adhoc_problem(text)
        if not problem.prompt and not problem.options:
            raise ConsoleError("没能从输入里解析出题目，请至少给出一行题干。")
        self.bus.publish("problem", data={"index": 0, "problem": _problem_payload(problem)})
        suggestion = await self._client.ask(problem)
        payload = {
            "problem_id": suggestion.problem_id,
            "display": suggestion.display(problem),
            "explanation": suggestion.explanation,
            "confidence": suggestion.confidence,
            "failure_reason": suggestion.failure_reason,
            "model": suggestion.model,
            "elapsed": round(suggestion.elapsed, 2),
            "payload": suggestion.submit_payload(problem),
        }
        self.bus.publish("answer", data={"problem_id": problem.id, "ok": suggestion.answerable, "suggestion": payload})
        return payload

    async def dump_page(self) -> dict[str, Any]:
        """导出当前页面结构，便于校准选择器。"""
        from ..solver import dump_current_page

        attached = await self.ensure_browser()
        path = await dump_current_page(attached.page, self.config)
        return {"path": str(path)}

    # -- 状态 -------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        browser: dict[str, Any] = {"attached": False}
        if self._attached is not None:
            try:
                browser = {
                    "attached": not self._attached.page.is_closed(),
                    "url": self._attached.page.url,
                    "cdp_url": self._handle.cdp_url if self._handle else self.config.browser.cdp_url,
                }
            except Exception:  # pragma: no cover
                browser = {"attached": False}

        watching = self._watch_task is not None and not self._watch_task.done()
        stats = self._solver.stats.to_dict() if (watching and self._solver) else None
        capture_stats = None
        if watching and self._solver and self._solver.capture is not None:
            capture_stats = dict(self._solver.capture.stats)

        return {
            "site": {"key": self.site.key, "label": self.site.label, "host": self.site.host},
            "answer": {
                "mode": self.config.answer.mode,
                "dry_run": self.config.answer.dry_run,
                "min_confidence": self.config.answer.min_confidence,
                "auto_submit": self.config.answer.auto_submit,
            },
            "solver": {
                "auto_enter_lesson": self.config.solver.auto_enter_lesson,
                "dom_fallback": self.config.solver.dom_fallback,
            },
            "model": {
                "name": self.config.deepseek.effective_model,
                "base_url": self.config.deepseek.base_url,
                "configured": bool(self.config.deepseek.api_key.strip()),
                "vision": self.config.deepseek.vision_enabled,
            },
            "browser": browser,
            "watching": watching,
            "stats": stats,
            "capture_stats": capture_stats,
            "busy": self._busy,
            "last_simulation": self._last_report.to_dict() if self._last_report else None,
            "subscribers": self.bus.subscriber_count,
        }

    async def probe(self) -> dict[str, Any]:
        """自检：配置文件、API Key、调试端口。"""
        cdp_ready = await is_cdp_ready(self.config.browser.cdp_url)
        return {
            "config_path": str(self.config.config_path) if self.config.config_path else None,
            "project_root": str(self.config.project_root),
            "api_key_configured": bool(self.config.deepseek.api_key.strip()),
            "cdp_url": self.config.browser.cdp_url,
            "cdp_ready": cdp_ready,
            "auto_launch": self.config.browser.auto_launch,
            "chrome_found": bool(self.config.browser.chrome_path) or True,
            "record_dir": str(self.config.record_path),
        }

    # -- 内部 -------------------------------------------------------------
    def _with_overrides(self, *, mode: str | None, dry_run: bool | None) -> Config:
        answer = self.config.answer
        if mode is not None or dry_run is not None:
            answer = dataclasses.replace(
                answer,
                mode=mode if mode is not None else answer.mode,
                dry_run=dry_run if dry_run is not None else answer.dry_run,
            )
        config = dataclasses.replace(self.config, answer=answer)
        config.validate()
        return config


async def check_ai(config: Config) -> dict[str, Any]:
    """独立的自检入口，供 CLI 的 --check 与网页控制台共用。"""
    from ..models import Option, Problem, ProblemType

    test_problem = Problem(
        id="self-test",
        type=ProblemType.SINGLE_CHOICE,
        prompt="下列数字中哪一个是质数？",
        options=[Option(0, "4"), Option(1, "6"), Option(2, "7"), Option(3, "9")],
        source="check",
    )
    try:
        async with DeepSeekClient(config.deepseek) as client:
            suggestion = await client.ask(test_problem)
    except AIError as exc:
        return {"ok": False, "detail": str(exc)}
    return {
        "ok": suggestion.answerable,
        "detail": f"答案={suggestion.display(test_problem)}（参考 C. 7），耗时 {suggestion.elapsed:.1f}s",
    }


__all__ = ["ConsoleError", "ConsoleRuntime", "check_ai"]
