"""aiohttp 服务：控制台页面 + JSON 接口 + SSE 事件流。"""

from __future__ import annotations

import asyncio
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import console, providers
from ..config import Config
from .runtime import ConsoleError, ConsoleRuntime

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: 用 AppKey 而不是裸字符串，避免 aiohttp 的 NotAppKeyWarning
RUNTIME_KEY: web.AppKey[ConsoleRuntime] = web.AppKey("runtime", ConsoleRuntime)


def create_app(runtime: ConsoleRuntime) -> web.Application:
    app = web.Application()
    app[RUNTIME_KEY] = runtime
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/state", api_state),
            web.get("/api/settings", api_settings_get),
            web.post("/api/settings", api_settings_post),
            web.get("/api/providers", api_providers),
            web.post("/api/apikey", api_apikey),
            web.get("/api/events", api_events),
            web.post("/api/watch/start", api_watch_start),
            web.post("/api/watch/stop", api_watch_stop),
            web.post("/api/browser/attach", api_browser_attach),
            web.post("/api/browser/detach", api_browser_detach),
            web.post("/api/simulate", api_simulate),
            web.post("/api/ask", api_ask),
            web.post("/api/dump", api_dump),
            web.get("/api/probe", api_probe),
            web.get("/api/events.json", api_events_json),
        ]
    )
    # 单页控制台，静态资源只有 index.html 一个
    app.router.add_static("/static/", STATIC_DIR, name="static")
    return app


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
async def index(_request: web.Request) -> web.StreamResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():  # pragma: no cover - 打包异常时
        return web.Response(status=500, text="控制台页面缺失：static/index.html")
    # 明确带上 charset，避免 curl / PowerShell 一类客户端按 Latin-1 解码
    return web.FileResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Type": "text/html; charset=utf-8",
        },
    )


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #
def _runtime(request: web.Request) -> ConsoleRuntime:
    return request.app[RUNTIME_KEY]


def _json(payload: Any, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, dumps=_dumps)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def _body(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
async def api_state(request: web.Request) -> web.Response:
    return _json(_runtime(request).state())


async def api_settings_get(request: web.Request) -> web.Response:
    """控制台设置：当前值 + 可选项 + 落盘路径。"""
    return _json({"ok": True, **_runtime(request).get_settings()})


async def api_providers(request: web.Request) -> web.Response:
    """模型服务商预设，附当前服务商的 Key 是否已配置。"""
    from ..envfile import mask_secret
    from ..providers import key_env_hint, provider_by_id

    runtime = _runtime(request)
    current = runtime.config.deepseek.provider
    key = runtime.config.deepseek.api_key.strip()
    return _json(
        {
            "ok": True,
            "providers": providers.describe_all(),
            "current": current,
            "model": runtime.config.deepseek.model,
            "base_url": runtime.config.deepseek.base_url,
            "key_configured": bool(key),
            # 只回打码结果，真正的 Key 不出后端
            "key_masked": mask_secret(key),
            "key_env": key_env_hint(provider_by_id(current)),
            "generic_key_env": providers.GENERIC_KEY_ENV,
        }
    )


async def api_apikey(request: web.Request) -> web.Response:
    """设置当前服务商的 API Key（写进 .env）。"""
    runtime = _runtime(request)
    body = await _body(request)
    if "key" not in body:
        return _json({"ok": False, "error": "请求里缺少 key 字段。"}, status=400)
    try:
        result = await runtime.set_api_key(str(body.get("key") or ""))
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.exception("写入 API Key 失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
    return _json({"ok": True, **result})


async def api_settings_post(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    body = await _body(request)
    try:
        payload = runtime.update_settings(body)
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    return _json({"ok": True, **payload})


async def api_probe(request: web.Request) -> web.Response:
    return _json(await _runtime(request).probe())


async def api_events_json(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    limit = int(request.query.get("limit", "150"))
    return _json({"events": list(runtime.bus.iter_snapshot(limit))})


async def api_watch_start(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    body = await _body(request)
    try:
        state = await runtime.start_watch(
            mode=body.get("mode"),
            dry_run=body.get("dry_run"),
            duration=body.get("duration"),
        )
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.exception("启动监听失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
    return _json({"ok": True, "state": state})


async def api_watch_stop(request: web.Request) -> web.Response:
    try:
        state = await _runtime(request).stop_watch()
    except Exception as exc:  # pragma: no cover
        logger.exception("停止监听失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
    return _json({"ok": True, "state": state})


async def api_browser_attach(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    try:
        attached = await runtime.ensure_browser()
        title = ""
        try:
            title = await attached.page.title()
        except Exception:  # pragma: no cover
            pass
        return _json(
            {
                "ok": True,
                "url": attached.page.url,
                "title": title,
                "state": runtime.state(),
            }
        )
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover
        logger.exception("接管浏览器失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)


async def api_browser_detach(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    await runtime.detach_browser()
    return _json({"ok": True, "state": runtime.state()})


async def api_simulate(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    body = await _body(request)
    kind = str(body.get("kind") or "offline")
    try:
        report = await runtime.simulate(kind)
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.exception("模拟检测失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
    return _json({"ok": True, "report": report})


async def api_ask(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    body = await _body(request)
    text = str(body.get("text") or "").strip()
    if not text:
        return _json({"ok": False, "error": "请先输入题目文本。"}, status=400)
    try:
        result = await runtime.ask(text)
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover
        logger.exception("手动提问失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
    return _json({"ok": True, "result": result})


async def api_dump(request: web.Request) -> web.Response:
    runtime = _runtime(request)
    try:
        return _json({"ok": True, **(await runtime.dump_page())})
    except ConsoleError as exc:
        return _json({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # pragma: no cover
        logger.exception("导出页面失败")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #
async def api_events(request: web.Request) -> web.StreamResponse:
    runtime = _runtime(request)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    queue = runtime.bus.subscribe()
    try:
        await _send(response, {"type": "hello", "data": runtime.state()})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # 心跳，顺便让浏览器/代理知道连接还活着
                await response.write(b": ping\n\n")
                continue
            await _send(response, event)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        runtime.bus.unsubscribe(queue)
    return response


async def _send(response: web.StreamResponse, event: dict[str, Any]) -> None:
    payload = _dumps(event)
    await response.write(f"data: {payload}\n\n".encode("utf-8"))


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
async def serve(
    config: Config,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """启动控制台服务，直到被 Ctrl+C 中断。"""
    runtime = ConsoleRuntime(config)
    await runtime.start()
    app = create_app(runtime)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        await runner.cleanup()
        await runtime.close()
        raise RuntimeError(
            f"无法监听 {host}:{port}（{exc}）。端口可能已被占用，可用 --port 换一个。"
        ) from exc

    url = f"http://{host}:{port}/"
    console.banner(
        "长江雨课堂 · 本地控制台",
        f"地址 {url}\n"
        f"站点 {runtime.site.label} · 模型 {config.deepseek.effective_model}\n"
        "在网页里可以启停监听、跑模拟检测、查看实时抓题与作答。按 Ctrl+C 退出。",
    )
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - 无桌面环境时忽略
            pass

    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        console.info("正在关闭控制台…")
        await runner.cleanup()
        await runtime.close()
