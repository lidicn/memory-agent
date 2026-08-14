#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""忆家管家 · 家庭行为记忆中枢 —— ASGI 入口

重构要点
--------
* 用标准 ``lifespan`` 在启动阶段构建 ``AppRuntime``，不再依赖「用户访问首页」
  才惰性创建后台服务（原来的 ``lazy_startup`` 正是采集与 API 割裂的根源）。
* ``AuthMiddleware`` 改为**纯 ASGI 中间件**：``BaseHTTPMiddleware`` 会给 SSE
  流式响应带来缓冲与生命周期问题，而本项目的 AI 助手强依赖流式输出。
* ``/mcp`` 由独立的 Token 中间件保护，与 WebUI 的 JWT 体系完全隔离。

⚠️ Basic Auth 分支必须保留：``nodered/water_purifier_flow.json`` 线上流
就是靠它通过鉴权的，删掉即断流。
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import traceback
from http.cookies import SimpleCookie

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from . import api, mcp_server
from .acp_auth import ACPTokenMiddleware
from .acp_server import acp_dispatcher
from .auth import AuthManager
from .config import get_config
from .mcp_auth import MCPTokenMiddleware
from .runtime import get_runtime, start_runtime, stop_runtime

BASE_DIR = os.path.dirname(__file__)
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")

# 无需登录即可访问的路径
PUBLIC_PREFIXES = (
    "/static",
    "/mcp",
    "/acp",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/status",
    "/favicon.ico",
)
PUBLIC_EXACT = {"/", "/index.html", "/health", "/sw.js", "/manifest.webmanifest"}


class AuthMiddleware:
    """纯 ASGI 鉴权中间件，支持 Bearer JWT / Basic / Cookie 三种凭据。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        user = self._authenticate(headers)

        if not user:
            await self._reject(send, path)
            return

        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["user"] = user
        await self.app(scope, receive, send)

    def _authenticate(self, headers: dict) -> dict | None:
        auth_manager = AuthManager(get_config())
        authorization = headers.get("authorization", "")

        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            user = auth_manager.verify_token(token)
            if user:
                return user
            # 调试令牌（dbg_）回退校验：经运行时 Token 库识别 kind=debug 后授权
            # 访问 /api 调试接口。mcp_ 令牌仅用于 /mcp，不在此放行（保持隔离）。
            try:
                store = getattr(get_runtime(), "tokens", None)
            except Exception:  # noqa: BLE001
                store = None
            if store is not None:
                name = store.verify(token)
                if name and store.kind(name) == "debug":
                    return {"username": name, "is_admin": False, "debug": True}
            return None

        if authorization.startswith("Basic "):
            # Node-RED 依赖此分支，不可删除
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except Exception:
                return None
            result = auth_manager.login(username, password)
            if result.get("ok"):
                return {
                    "username": result["username"],
                    "is_admin": result.get("is_admin", False),
                }
            return None

        cookie_header = headers.get("cookie", "")
        if cookie_header:
            cookies = SimpleCookie()
            try:
                cookies.load(cookie_header)
            except Exception:
                return None
            morsel = cookies.get("token")
            if morsel and morsel.value:
                return auth_manager.verify_token(morsel.value)
        return None

    @staticmethod
    async def _reject(send: Send, path: str) -> None:
        body = json.dumps({"ok": False, "error": "未登录"}, ensure_ascii=False).encode()
        status = 401
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ]
        if not path.startswith("/api"):
            # 非 API 请求交还给前端外壳，由前端弹出登录视图，避免重定向循环
            status = 401
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class NoCacheStaticMiddleware:
    """给 /static 资源注入 no-cache 头。

    避免浏览器长期缓存旧的 main.js / members.js 等，导致后端重启、
    前端代码更新后页面仍加载旧 JS（表现为组件未注册、页面白屏）。
    仅对 /static 路径生效，不影响 API 响应。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or not scope.get("path", "").startswith("/static"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [
                    h for h in headers
                    if h[0].lower() not in (b"cache-control", b"pragma", b"expires")
                ]
                headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def homepage(request: Request):
    if not os.path.exists(INDEX_FILE):
        return PlainTextResponse("前端资源缺失：static/index.html 未找到", status_code=500)
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    response = HTMLResponse(content)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


async def liveness(request: Request):
    """无需鉴权的存活探针。"""
    return JSONResponse({"ok": True, "service": "memory-worker"})


async def mcp_unavailable(request: Request):
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32000,
                "message": mcp_server.MCP_IMPORT_ERROR or "MCP 服务不可用",
            },
        },
        status_code=503,
    )


_log = logging.getLogger("mcp.dispatch")


def _no_buffer_send(send: Send) -> Send:
    """为 MCP 响应注入防缓冲头，避免反向代理缓冲 SSE 流导致 endpoint 事件延迟。"""

    async def wrapped(message):
        if message.get("type") == "http.response.start":
            headers = list(message.get("headers", []))
            names = {h[0].lower() for h in headers}
            if b"cache-control" not in names:
                headers.append((b"cache-control", b"no-cache, no-transform"))
            if b"x-accel-buffering" not in names:
                headers.append((b"x-accel-buffering", b"no"))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


async def _mcp_dispatcher(scope: Scope, receive: Receive, send: Send):
    """在 /mcp 下同时兼容 Streamable HTTP 与旧版 SSE 协议。

    路由规则
    --------
    * ``POST /mcp``                    → Streamable HTTP（initialize / 工具调用）
    * ``GET  /mcp/sse`` 或 ``GET /mcp``（根路径）
                                       → SSE 传输：建立流并立即发送 ``endpoint`` 事件，
                                         兼容只连 base URL（不追加 /sse）的客户端
    * ``POST /mcp/messages/..``        → SSE 传输的消息上行通道

    Starlette>=0.33 的 Mount 不再从 scope["path"] 剥离挂载前缀，而是保留完整路径
    （如 /mcp/sse）并把前缀写入 scope["root_path"]。这里手动把 /mcp 前缀剥掉，让子
    应用收到它们期望的路径（/、/sse、/messages/...）；同时保证 root_path 以 /mcp 结尾，
    使 SSE 构建的 messages 端点 URL 带前缀，客户端 POST 回 /mcp/messages/.. 能正确路由。
    """
    if scope.get("type") != "http":
        await mcp_server.mcp_app(scope, receive, send)
        return

    path = scope.get("path", "")
    method = scope.get("method", "GET")
    if path.startswith("/mcp"):
        sub = path[len("/mcp"):]
        if not sub:
            sub = "/"
        elif not sub.startswith("/"):
            sub = "/" + sub
    else:
        sub = path

    scope = dict(scope)
    scope["path"] = sub

    # 判定传输类型并记录日志，便于排查客户端握手问题
    if method == "GET" and (sub == "/sse" or sub == "/"):
        transport = "sse"
    elif sub.startswith("/messages/"):
        transport = "sse-msg"
    else:
        transport = "streamable"
    _log.info("MCP dispatch %s %s -> %s", method, path, transport)

    if transport == "sse":
        # 兼容连 base URL（/mcp）而非 /mcp/sse 的客户端：把它当作 SSE 端点。
        # 确保 SSE 生成的 messages 端点 URL 带 /mcp 前缀，使回传路由一致。
        rp = scope.get("root_path", "") or ""
        if not rp.endswith("/mcp"):
            rp = rp.rstrip("/") + "/mcp"
        scope["root_path"] = rp
        scope["path"] = "/sse"
        target = mcp_server.mcp_sse_app
    elif transport == "sse-msg":
        target = mcp_server.mcp_sse_app
    else:
        target = mcp_server.mcp_app

    # 注入防缓冲头，避免任何反向代理缓冲 SSE 流，导致 endpoint 事件/响应延迟到达。
    await target(scope, receive, _no_buffer_send(send))


class _MCPRootApp(ASGIApp):
    """精确路径 /mcp（无尾斜杠）的 ASGI 入口。

    Starlette>=0.33 的 ``Mount`` 只对带子路径的请求（如 /mcp/sse、/mcp/messages）
    命中，对精确的 ``/mcp``（无尾斜杠）不会命中——而 Streamable HTTP 客户端把
    所有请求（含 initialize 握手）都发往 ``/mcp``。这里用一条精确 ``Route`` 兜底，
    复用与 Mount 完全相同的鉴权与分发逻辑。
    """

    def __init__(self) -> None:
        self._mw = MCPTokenMiddleware(_mcp_dispatcher, lambda: get_runtime().tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._mw(scope, receive, send)


class _ACPRootApp(ASGIApp):
    """精确路径 /acp（无尾斜杠）的 ASGI 入口，复用 ACP 鉴权与分发逻辑。"""

    def __init__(self) -> None:
        self._mw = ACPTokenMiddleware(acp_dispatcher, lambda: get_runtime().tokens)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._mw(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    runtime = await start_runtime()
    app.state.runtime = runtime

    mcp_lifespan = mcp_server.get_lifespan_context()
    try:
        if mcp_lifespan is not None and mcp_server.mcp_app is not None:
            # Streamable HTTP 的会话管理器必须在 lifespan 中启动，
            # 否则第一次 initialize 就会因 task group 未就绪而失败
            async with mcp_lifespan(mcp_server.mcp_app):
                print("[App] MCP 子应用已挂载于 /mcp")
                yield
        else:
            if mcp_server.MCP_IMPORT_ERROR:
                print(f"[App] MCP 未启用: {mcp_server.MCP_IMPORT_ERROR}")
            yield
    finally:
        await stop_runtime()


async def _service_worker(request: Request):
    """以根作用域提供 Service Worker，供 PWA 离线壳使用。"""
    p = os.path.join(STATIC_DIR, "sw.js")
    if not os.path.exists(p):
        return Response("not found", status_code=404)
    with open(p, "rb") as f:
        body = f.read()
    return Response(
        body,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


async def _manifest(request: Request):
    """以正确 MIME 提供 PWA manifest。"""
    p = os.path.join(STATIC_DIR, "manifest.webmanifest")
    if not os.path.exists(p):
        return Response("not found", status_code=404)
    with open(p, "rb") as f:
        body = f.read()
    return Response(
        body,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


def _build_routes() -> list:
    routes: list = list(api.get_routes())
    routes.append(Route("/health", liveness, methods=["GET"]))

    if mcp_server.mcp_app is not None:
        mcp_mw = MCPTokenMiddleware(_mcp_dispatcher, lambda: get_runtime().tokens)
        routes.append(Mount("/mcp", app=mcp_mw))
        # Starlette>=0.33 的 Mount 对精确的 "/mcp"（无尾斜杠）不会命中，
        # 只有 /mcp/sse、/mcp/messages 等带子路径请求才命中。
        # Streamable HTTP 客户端把所有请求发往 /mcp，需单独兜底。
        routes.append(
            Route(
                "/mcp",
                endpoint=_MCPRootApp(),
                methods=["GET", "POST", "DELETE", "OPTIONS"],
            )
        )
    else:
        routes.append(Route("/mcp", mcp_unavailable, methods=["GET", "POST", "DELETE"]))

    # ACP（Agent Client Protocol，拓扑 X peer-to-peer）：与 MCP 同构的挂载方式。
    acp_mw = ACPTokenMiddleware(acp_dispatcher, lambda: get_runtime().tokens)
    routes.append(Mount("/acp", app=acp_mw))
    # Starlette>=0.33 的 Mount 对精确的 "/acp"（无尾斜杠）不会命中，用精确 Route 兜底。
    routes.append(
        Route(
            "/acp",
            endpoint=_ACPRootApp(),
            methods=["GET", "POST", "OPTIONS"],
        )
    )

    if os.path.isdir(STATIC_DIR):
        routes.append(
            Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static")
        )
    else:
        print(f"[App] 警告：静态目录不存在 {STATIC_DIR}")

    routes.append(Route("/sw.js", _service_worker, methods=["GET"]))
    routes.append(Route("/manifest.webmanifest", _manifest, methods=["GET"]))
    routes.append(Route("/", homepage, methods=["GET"]))
    return routes


combined_app = Starlette(
    routes=_build_routes(),
    middleware=[Middleware(AuthMiddleware), Middleware(NoCacheStaticMiddleware)],
    lifespan=lifespan,
)
# 避免 /mcp 被 307 重定向到 /mcp/，导致部分 MCP 客户端解析失败。
# Starlette>=0.33 已从 Starlette.__init__ 移除 redirect_slashes，
# 改为设置底层 Router 的属性。
combined_app.router.redirect_slashes = False


# 候选落盘路径：容器内 data 卷的挂载点在不同部署下可能不同，逐个尝试。
_TRACEBACK_PATHS = (
    "/app/data/last_traceback.txt",
    "/data/last_traceback.txt",
    "/tmp/last_traceback.txt",
)


async def _dump_traceback_handler(request: Request, exc: Exception) -> Response:
    """全局异常兜底。

    未捕获异常默认只会得到 uvicorn 的裸 ``Internal Server Error``（21 字节），
    无法定位根因。这里把真实栈同时写入容器日志与挂载目录，便于在宿主机直接读取。
    """
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logging.getLogger("app.error").error(
        "未捕获异常 %s %s\n%s", request.method, request.url.path, text
    )
    for path in _TRACEBACK_PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(text)
            break
        except OSError:
            continue
    return JSONResponse(
        {"ok": False, "error": "internal_server_error", "traceback": text},
        status_code=500,
    )


# 必须在 _mount_slash_fix 包装之前注册：包装后 combined_app 是普通 ASGI 可调用对象，
# 不再具备 Starlette 的 add_exception_handler 方法。
combined_app.add_exception_handler(Exception, _dump_traceback_handler)


def _mount_slash_fix(app: ASGIApp) -> ASGIApp:
    """Starlette>=0.33 的 ``Mount`` 对精确的 ``/mcp``（无尾斜杠）不会命中，
    只有带子路径的 ``/mcp/sse``、``/mcp/messages`` 才命中；而 Streamable HTTP
    客户端把所有请求（含 initialize 握手）都发往 ``/mcp``。这里在入口处把
    精确的 ``/mcp`` 重写为 ``/mcp/``，让 ``Mount`` 能正常匹配并完成前缀剥离。
    """

    async def _wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
        await app(scope, receive, send)

    return _wrapped


combined_app = _mount_slash_fix(combined_app)

# 兼容旧引用
app = combined_app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(combined_app, host="0.0.0.0", port=8000)
