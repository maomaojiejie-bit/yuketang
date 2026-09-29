"""交互式一键启动菜单。

`一键启动.bat` 会调用这里。把菜单放在 Python 里而不是批处理里，
是因为批处理对中文和 UTF-8 的支持很糟糕，而这里的输出是安全的。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

from . import console
from .config import Config, ConfigError

MENU_ITEMS: list[tuple[str, str, str]] = [
    ("1", "打开网页控制台", "推荐。图形界面启停监听、跑模拟检测、看实时抓题"),
    ("2", "仅提示答案", "命令行运行，只打印答案与解析，不动页面"),
    ("3", "自动点击作答", "命令行运行，在页面上点击选项并提交"),
    ("4", "自动作答预演", "完整推理但不向页面下发动作，用来先看效果"),
    ("5", "自检", "检查配置、DeepSeek 连通性与调试 Chrome"),
    ("6", "手动输入一道题", "不开浏览器，直接验证 AI 链路"),
    ("7", "初始配置", "设置 API Key、站点"),
    ("8", "打开配置文件", "用记事本编辑 config.yaml"),
    ("0", "退出", ""),
]


# --------------------------------------------------------------------------- #
# 输入辅助
# --------------------------------------------------------------------------- #
def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def _ask_yes(prompt: str, *, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} ({hint})").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "是")


def _ask_secret(prompt: str) -> str:
    try:
        import getpass

        return getpass.getpass(f"{prompt}: ").strip()
    except Exception:
        return _ask(prompt)


def _mask(value: str) -> str:
    if len(value) <= 10:
        return "****"
    return f"{value[:6]}****{value[-4:]}"


# --------------------------------------------------------------------------- #
# 配置写入
# --------------------------------------------------------------------------- #
def upsert_env(path: Path, values: dict[str, str]) -> None:
    """更新 .env 中的若干键，保留其它行（含注释）。"""
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    remaining = dict(values)
    output: list[str] = []
    for line in lines:
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    for key, value in remaining.items():
        output.append(f"{key}={value}")

    text = "\n".join(output).strip("\n") + "\n"
    path.write_text(text, encoding="utf-8")


def ensure_config_file(config: Config, *, site: str, mode: str) -> Path | None:
    """config.yaml 不存在时，从模板复制一份并把站点/模式写进去。"""
    target = config.project_root / "config.yaml"
    if target.exists():
        return None
    template = config.project_root / "config.example.yaml"
    if not template.exists():
        return None
    text = template.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^site:.*$", f"site: {site}", text)
    text = re.sub(r"(?m)^(\s{2}mode:).*$", lambda m: f"{m.group(1)} {mode}", text, count=1)
    target.write_text(text, encoding="utf-8")
    return target


def run_setup(config: Config) -> Config:
    """初始配置向导：收集 API Key 与站点，写进 .env / config.yaml。"""
    console.rule("初始配置")
    console.info(f"配置目录：{config.project_root}")

    # --- API Key ---
    current = config.deepseek.api_key.strip()
    if current:
        console.info(f"当前 API Key：{_mask(current)}")
        change = _ask_yes("是否更换？", default=False)
    else:
        console.warn("尚未配置 DeepSeek API Key。")
        change = True

    key = current
    if change:
        while True:
            key = _ask_secret("请输入 DeepSeek API Key（sk- 开头，直接回车放弃）")
            if not key:
                console.warn("已跳过，Key 保持原样。")
                key = current
                break
            if key.startswith("sk-") and len(key) > 20:
                break
            console.warn("看起来不像有效的 Key（应以 sk- 开头且长度较长），请重试。")

    # --- 站点 ---
    from .yuketang.constants import SITES

    keys = list(SITES)
    console.info("可选站点：" + "  ".join(f"{i + 1}.{SITES[k].label}({k})" for i, k in enumerate(keys)))
    default_index = str(keys.index(config.site) + 1) if config.site in keys else "1"
    choice = _ask("选择站点序号", default_index)
    site = keys[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(keys) else config.site

    # --- 落盘 ---
    env_values: dict[str, str] = {}
    if key:
        env_values["DEEPSEEK_API_KEY"] = key
    if env_values:
        env_path = config.project_root / ".env"
        upsert_env(env_path, env_values)
        console.success(f"已写入 {env_path}")
        # load_dotenv 默认不覆盖已存在的环境变量，这里直接同步到内存
        os.environ.update(env_values)

    config.deepseek.api_key = key
    config.site = site

    created = ensure_config_file(config, site=site, mode=config.answer.mode)
    if created:
        console.success(f"已根据模板生成 {created}")
    else:
        console.info("config.yaml 已存在，保持不动。")

    # --- 验证 ---
    if key and _ask_yes("现在验证一下模型的连通性？", default=True):
        console.info("正在调用 DeepSeek…")
        from .models import Option, Problem, ProblemType
        from .ai.deepseek import AIError, DeepSeekClient

        test = Problem(
            id="self-test",
            type=ProblemType.SINGLE_CHOICE,
            prompt="下列数字中哪一个是质数？",
            options=[Option(0, "4"), Option(1, "6"), Option(2, "7"), Option(3, "9")],
            source="check",
        )

        async def probe() -> None:
            async with DeepSeekClient(config.deepseek) as client:
                suggestion = await client.ask(test)
            console.success(
                f"模型可用：答案 = {suggestion.display(test)}（参考 C. 7），"
                f"耗时 {suggestion.elapsed:.1f}s"
            )

        try:
            asyncio.run(probe())
        except AIError as exc:
            console.error(f"模型调用失败：{exc}")
            console.warn("配置已保存，但连通性验证未通过，请检查 Key 与网络。")
        except Exception as exc:  # pragma: no cover
            console.error(f"验证时出现意外错误：{exc}")

    console.success("初始配置完成。")
    return config


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #
def _run_watch(config: Config, *, mode: str, dry_run: bool) -> None:
    import argparse

    from .cli import command_watch

    config.answer.mode = mode
    config.answer.dry_run = dry_run
    config.validate()
    if mode != "manual" and not dry_run and not _ask_yes(
        f"将以 {mode} 模式自动作答并真实操作页面，确认继续？", default=False
    ):
        console.info("已取消。")
        return
    args = argparse.Namespace(duration=None, yes=True)
    asyncio.run(command_watch(config, args))


def _run_web(config: Config, *, port: int = 8765) -> None:
    from .web.server import serve

    asyncio.run(serve(config, port=port))


def _run_ask(config: Config) -> None:
    console.info("粘贴题目，选项行用 A. / B. 开头；输入空行结束。")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            break
        lines.append(line)
    if not lines:
        console.warn("没有输入内容。")
        return

    from .cli import command_ask

    asyncio.run(command_ask(config, "\n".join(lines)))


def _open_config(config: Config) -> None:
    target = config.config_path or (config.project_root / "config.yaml")
    if not target.exists():
        console.warn(f"{target} 不存在，先执行「初始配置」生成。")
        return
    try:
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        console.info(f"已用系统编辑器打开 {target}")
    except Exception as exc:
        console.warn(f"打开失败：{exc}，路径为 {target}")


def _run_check(config: Config) -> None:
    from .cli import command_check

    asyncio.run(command_check(config))


# --------------------------------------------------------------------------- #
# 菜单
# --------------------------------------------------------------------------- #
def _print_menu(config: Config) -> None:
    from .yuketang.constants import site_by_key

    key_state = "已配置" if config.deepseek.api_key.strip() else "未配置"
    console.print_logo("长江雨课堂 · 自动解题器")
    console.info(
        f"站点 {site_by_key(config.site).label} · 模型 {config.deepseek.model} · "
        f"API Key {key_state}"
    )
    console.console.print()
    for index, title, detail in MENU_ITEMS:
        console.console.print(
            f"  [bold cyan]{index}[/bold cyan]  {title}"
            + (f"  [dim]{detail}[/dim]" if detail else "")
        )
    console.console.print()


def main_menu(config: Config) -> int:
    """主循环。返回进程退出码。"""
    if not config.deepseek.api_key.strip():
        console.warn("检测到尚未配置 API Key，先进入初始配置。")
        config = run_setup(config)

    while True:
        _print_menu(config)
        try:
            choice = input("请输入序号: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        try:
            if choice == "0":
                console.info("再见。")
                return 0
            if choice == "1":
                _run_web(config)
                return 0
            if choice == "2":
                _run_watch(config, mode="manual", dry_run=False)
            elif choice == "3":
                _run_watch(config, mode="dom", dry_run=False)
            elif choice == "4":
                _run_watch(config, mode="dom", dry_run=True)
            elif choice == "5":
                _run_check(config)
            elif choice == "6":
                _run_ask(config)
            elif choice == "7":
                config = run_setup(config)
            elif choice == "8":
                _open_config(config)
            else:
                console.warn("没有这个选项。")
        except ConfigError as exc:
            console.error(str(exc))
        except KeyboardInterrupt:
            console.warn("已中断。")
        except Exception as exc:  # pragma: no cover - 菜单不应因此崩掉
            console.error(f"{type(exc).__name__}: {exc}")

        console.console.print()
        if not _ask_yes("回到菜单？", default=True):
            return 0
