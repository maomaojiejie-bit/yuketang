"""命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from . import console
from .adhoc import parse_adhoc_problem
from .ai.deepseek import AIError, DeepSeekClient
from .browser import attach, ensure_chrome
from .browser.launcher import is_cdp_ready
from .config import Config, ConfigError, load_config
from .models import Option, Problem, ProblemType
from .solver import Solver, dump_current_page
from .yuketang.constants import SITES, site_by_key

RESPONSIBLE_NOTE = (
    "本工具仅用于个人学习辅助。请遵守学校规定与雨课堂服务条款，"
    "不要用它替代自己的学习与诚信作答。"
)


def project_root() -> Path:
    """源码目录下取仓库根，安装为包后退回当前工作目录。"""
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "run.py").exists() or (candidate / "pyproject.toml").exists():
        return candidate
    return Path.cwd()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cjsolver",
        description="通过 Chrome 自动化获取长江雨课堂随堂习题，并用 DeepSeek 给出答案。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=RESPONSIBLE_NOTE,
    )
    parser.add_argument("--config", help="配置文件路径，默认查找 ./config.yaml")
    parser.add_argument("--site", choices=sorted(SITES), help="站点：changjiang / standard / pro")
    parser.add_argument(
        "--mode",
        choices=("manual", "dom", "api"),
        help="manual=只提示 | dom=页面上点击选项 | api=直接调作答接口",
    )
    parser.add_argument("--dry-run", action="store_true", help="只推理不提交")
    parser.add_argument("--duration", type=float, help="监听多少秒后自动退出，默认一直运行")
    parser.add_argument("--yes", action="store_true", help="自动作答时跳过二次确认")

    action = parser.add_mutually_exclusive_group()
    action.add_argument("--web", action="store_true", help="启动本地网页控制台")
    action.add_argument("--menu", action="store_true", help="交互式一键启动菜单")
    action.add_argument("--check", action="store_true", help="自检配置、模型连通性与 Chrome")
    action.add_argument(
        "--ask", nargs="?", const="-", help="直接对一段题目文本作答（- 表示读标准输入）"
    )
    action.add_argument("--dump", action="store_true", help="导出当前页面 HTML 与扫描结果")
    action.add_argument("--simulate", choices=("offline", "browser", "live"),
                        help="跑一次模拟检测：offline=离线抓题 browser=页面抓题 live=端到端投喂")
    action.add_argument("--list-sites", action="store_true", help="列出支持的站点")

    parser.add_argument("--host", default="127.0.0.1", help="网页控制台监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="网页控制台端口，默认 8765")
    parser.add_argument("--no-open", action="store_true", help="启动控制台时不自动打开浏览器")

    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser


def build_config(args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {}
    if args.site:
        overrides["site"] = args.site
    if args.mode:
        overrides["answer.mode"] = args.mode
    if args.dry_run:
        overrides["answer.dry_run"] = True
    if args.verbose:
        overrides["solver.log_level"] = "DEBUG"
    return load_config(args.config, project_root=project_root(), overrides=overrides)


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def command_list_sites() -> int:
    console.rule("支持的站点")
    for key, site in SITES.items():
        console.info(f"{key:<11} {site.label:<8} {site.start_url}")
    return 0


async def command_check(config: Config) -> int:
    console.rule("自检")
    console.info(f"配置文件：{config.config_path or '（未找到，使用内置默认值）'}")
    console.info(f"站点：{site_by_key(config.site)}")
    console.info(f"作答模式：{config.answer.mode}")
    console.info(f"模型：{config.deepseek.effective_model} @ {config.deepseek.base_url}")

    try:
        config.validate()
        console.success("配置校验通过，API Key 已填写。")
    except ConfigError as exc:
        console.error(str(exc))
        return 2

    console.info("正在测试模型连通性…")
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
        console.error(f"模型调用失败：{exc}")
        return 3
    console.success(
        f"模型可用，自测题答案 = {suggestion.display(test_problem)}"
        f"（参考答案 C. 7，耗时 {suggestion.elapsed:.1f}s）"
    )

    if await is_cdp_ready(config.browser.cdp_url):
        console.success(f"调试 Chrome 已就绪：{config.browser.cdp_url}")
    else:
        hint = (
            "启动时会自动拉起 Chrome。"
            if config.browser.auto_launch
            else "且 auto_launch=false，需要手动启动。"
        )
        console.warn(f"{config.browser.cdp_url} 未监听；{hint}")
    return 0


async def command_ask(config: Config, text: str) -> int:
    config.validate()
    if text == "-":
        text = sys.stdin.read()
    problem = parse_adhoc_problem(text)
    if not problem.prompt and not problem.options:
        console.error("没能从输入里解析出题目。")
        return 2

    console.problem_panel("手动输入", problem)
    try:
        async with DeepSeekClient(config.deepseek) as client:
            suggestion = await client.ask(problem)
    except AIError as exc:
        console.error(f"模型调用失败：{exc}")
        return 3
    console.answer_panel(suggestion, problem, mode="manual", applied=False)
    return 0


async def command_simulate(config: Config, kind: str) -> int:
    """命令行下跑一次模拟检测。"""
    from .simulate import Simulator

    site = site_by_key(config.site)
    client = None
    if config.deepseek.api_key.strip():
        client = DeepSeekClient(config.deepseek)
        await client.__aenter__()
    try:
        simulator = Simulator(config, site, client=client, emit=_print_sim_event)
        if kind == "offline":
            report = await simulator.run_offline(test_model=client is not None)
        elif kind == "browser":
            handle = await ensure_chrome(config.browser, config.user_data_path)
            attached = await attach(handle.cdp_url, site)
            try:
                report = await simulator.run_browser(
                    attached.page, test_model=client is not None
                )
            finally:
                await attached.close()
        else:
            console.error("命令行下不支持 live 模拟，请在网页控制台里、监听运行中执行。")
            return 2
    finally:
        if client is not None:
            await client.aclose()

    console.rule(report.title)
    for check in report.checks:
        mark = "○" if check.skipped else ("✓" if check.ok else "✗")
        line = f"  {mark} {check.label}"
        if check.detail:
            line += f"  [dim]{check.detail}[/dim]"
        console.console.print(line)
    if report.error:
        console.error(report.error)
    console.console.print()
    if report.ok:
        console.success(f"模拟检测通过（{report.elapsed:.1f}s）")
        return 0
    console.error(f"模拟检测存在未通过项（{report.elapsed:.1f}s）")
    return 1


def _print_sim_event(event: dict[str, object]) -> None:
    if event.get("type") == "sim-log":
        data = event.get("data") or {}
        console.info(str(data.get("message", "")))


async def command_dump(config: Config) -> int:
    handle = await ensure_chrome(config.browser, config.user_data_path)
    attached = await attach(handle.cdp_url, site_by_key(config.site))
    try:
        await dump_current_page(attached.page, config)
        console.info(
            "把 page.html 里的关键 class 填进 config.yaml 的 answer.option_selectors 即可精确定位。"
        )
    finally:
        await attached.close()
    return 0


async def command_watch(config: Config, args: argparse.Namespace) -> int:
    config.validate()
    site = site_by_key(config.site)
    console.banner(
        "长江雨课堂自动解题器",
        f"站点 {site.label} · 模式 {config.answer.mode}"
        f"{' · dry-run' if config.answer.dry_run else ''}\n{RESPONSIBLE_NOTE}",
    )

    handle = await ensure_chrome(config.browser, config.user_data_path)
    attached = await attach(handle.cdp_url, site)
    console.info(f"当前页面：{attached.page.url}")

    auto = config.answer.mode != "manual" and not config.answer.dry_run
    if auto and not args.yes:
        console.warn(f"即将以 {config.answer.mode} 模式自动作答，请确认已登录且处于正确课堂。")
        answer = await asyncio.to_thread(input, "继续？(y/N) ")
        if answer.strip().lower() not in ("y", "yes"):
            console.info("已取消。")
            await attached.close()
            return 0

    try:
        async with DeepSeekClient(config.deepseek) as client:
            solver = Solver(config, attached.page, site, client)
            previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda *_: solver.request_stop())
            try:
                await solver.run(duration=args.duration)
            finally:
                signal.signal(signal.SIGINT, previous)
    finally:
        await attached.close()
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_sites:
        return command_list_sites()

    try:
        config = build_config(args)
    except ConfigError as exc:
        console.error(str(exc))
        return 2

    console.setup_logging("DEBUG" if args.verbose else config.solver.log_level)

    try:
        if args.menu:
            from .launcher import main_menu

            return main_menu(config)
        if args.web:
            from .web.server import serve

            asyncio.run(
                serve(
                    config,
                    host=args.host,
                    port=args.port,
                    open_browser=not args.no_open,
                )
            )
            return 0
        if args.check:
            return asyncio.run(command_check(config))
        if args.ask is not None:
            return asyncio.run(command_ask(config, args.ask))
        if args.simulate:
            return asyncio.run(command_simulate(config, args.simulate))
        if args.dump:
            return asyncio.run(command_dump(config))
        return asyncio.run(command_watch(config, args))
    except ConfigError as exc:
        console.error(str(exc))
        return 2
    except KeyboardInterrupt:
        console.warn("已中断。")
        return 130
    except RuntimeError as exc:
        console.error(str(exc))
        return 1
