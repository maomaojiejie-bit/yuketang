"""统一的 rich 控制台输出。"""

from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(highlight=False, soft_wrap=True)
error_console = Console(stderr=True, highlight=False, soft_wrap=True)

_TAG_STYLES = {
    "题": "bold cyan",
    "答": "bold green",
    "警": "bold yellow",
    "错": "bold red",
    "网": "dim",
    "AI": "bold magenta",
}


def setup_logging(level: str = "INFO") -> None:
    """把 logging 输出收敛到 rich，避免与表格输出交织。"""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 这两家的 INFO 日志会把每条 HTTP 请求都打出来，太吵
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(numeric, logging.WARNING))


def info(message: str) -> None:
    console.print(f"[dim]·[/dim] {message}")


def success(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def warn(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def error(message: str) -> None:
    error_console.print(f"[red]✗[/red] {message}")


def rule(title: str = "") -> None:
    console.rule(f"[bold]{title}" if title else "")


def banner(title: str, subtitle: str = "") -> None:
    body = Text(title, style="bold white")
    if subtitle:
        body.append(f"\n{subtitle}", style="dim")
    console.print(Panel(body, border_style="cyan", expand=False))


def heartbeat_panel(data: dict) -> None:
    """心跳日志：监听期间每隔一段时间打印一次，带边框。"""
    stats = data.get("stats") or {}
    capture = data.get("capture") or {}
    page = data.get("page") or {}

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("监听时长", str(data.get("uptime_text") or "—"))
    table.add_row(
        "题目",
        f"遇到 {stats.get('problems', 0)} · 作答 {stats.get('answered', 0)} · "
        f"跳过 {stats.get('skipped', 0)} · 失败 {stats.get('failed', 0)}",
    )
    table.add_row(
        "流量",
        f"接口响应 {capture.get('response', 0)} · WebSocket 帧 {capture.get('ws_frame', 0)} · "
        f"待处理 {data.get('queue', 0)}",
    )
    if page.get("alive"):
        title = page.get("title") or "(无标题)"
        table.add_row("页面", f"{title}\n[dim]{_shorten_url(str(page.get('url', '')))}[/dim]")
    else:
        table.add_row("页面", "[yellow]已关闭或不可用[/yellow]")

    console.print(Panel(table, title="[bold]心跳[/bold]", border_style="blue", expand=False))


def _shorten_url(url: str, limit: int = 72) -> str:
    return url if len(url) <= limit else url[: limit - 1] + "…"


def logo_text(subtitle: str = "") -> Text:
    """把 "YKT" 立体字渲染成带配色的 rich Text（正面亮、侧面暗，做出层次）。

    输出编码不支持方块字符时自动降级成 ASCII 版，避免满屏问号。
    """
    from .logo import LOGO_ASCII_LINES, LOGO_LINES, fits

    encoding = getattr(console.file, "encoding", None) or ""
    unicode_ok = fits(encoding)
    lines = LOGO_LINES if unicode_ok else LOGO_ASCII_LINES

    text = Text()
    for index, line in enumerate(lines):
        if index:
            text.append("\n")
        for char in line:
            if char == "█":
                text.append(char, style="bold bright_cyan")
            elif char == "#":
                text.append(char, style="bold bright_cyan")
            elif char in "▓+":
                text.append(char, style="cyan")
            elif char in "░":
                text.append(char, style="dim cyan")
            else:
                text.append(char)
    if subtitle:
        text.append(f"\n\n{subtitle}", style="dim")
    return text


def print_logo(subtitle: str = "") -> None:
    """打印立体 YKT 标题。"""
    console.print(
        Panel(logo_text(subtitle), border_style="cyan", expand=False, padding=(1, 3))
    )


def problem_panel(header: str, problem: Any) -> None:
    """打印题目内容。"""
    lines = [f"[bold]{problem.type.label}[/bold]  [dim]id={problem.id}[/dim]"]
    lines.append("")
    lines.append(problem.prompt or "[dim](题干在课件图片中)[/dim]")
    if problem.options:
        lines.append("")
        for option in problem.options:
            lines.append(f"  [cyan]{option.letter}[/cyan]. {option.text}")
    if problem.blanks:
        lines.append("")
        for index, blank in enumerate(problem.blanks, start=1):
            lines.append(f"  [cyan]第{index}空[/cyan] {blank}")
    if problem.image_url:
        lines.append("")
        lines.append(f"[dim]图片：{problem.image_url}[/dim]")
    console.print(
        Panel("\n".join(lines), title=f"[bold]{header}[/bold]", border_style="blue", expand=False)
    )


def answer_panel(suggestion: Any, problem: Any, *, mode: str, applied: bool) -> None:
    """打印 AI 给出的答案。"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold green", no_wrap=True)
    table.add_column()
    table.add_row("答案", suggestion.display(problem))
    if suggestion.explanation:
        table.add_row("解析", suggestion.explanation)
    confidence = (
        "—" if suggestion.confidence is None else f"{suggestion.confidence:.0%}"
    )
    table.add_row("置信度", confidence)
    if suggestion.failure_reason:
        table.add_row("失败原因", f"[red]{suggestion.failure_reason}[/red]")
    table.add_row("模型", f"{suggestion.model}  [dim]{suggestion.elapsed:.1f}s[/dim]")
    table.add_row("处置", _mode_text(mode, applied))
    console.print(Panel(table, border_style="green" if applied else "yellow", expand=False))


def _mode_text(mode: str, applied: bool) -> str:
    if mode == "manual":
        return "[yellow]仅提示，请在浏览器中自行作答[/yellow]"
    if mode == "api":
        return "[green]已通过接口提交[/green]" if applied else "[red]接口提交失败[/red]"
    return "[green]已在页面点击作答[/green]" if applied else "[red]页面点击失败[/red]"
