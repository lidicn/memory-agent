"""MCP 专用鉴权中间件

与 WebUI 的 JWT ``AuthMiddleware`` 完全隔离：MCP 客户端携带的是
``Authorization: Bearer mcp_xxx``，与浏览器会话不共享任何凭据。

重构前 ``/mcp`` 直接躺在 JWT 白名单里 —— 等同于完全裸奔，
``agent_tokens`` 从未真正生效。
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

_log = logging.getLogger("mcp.auth")


class MCPTokenMiddleware:
    """纯 ASGI 中间件，只保护被包裹的 MCP 子应用。"""

    def __init__(self, app: ASGIApp, token_store_getter):
        self.app = app
        self._get_store = token_store_getter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")

        # 预检请求直接放行，否则浏览器端调试工具无法握手
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        token = self._extract_token(scope)
        store = self._get_store()

        if store is None:
            _log.warning("MCP 鉴权失败: 服务未就绪 (%s %s)", method, path)
            await self._reject(send, "服务未就绪", 503)
            return

        if not store.count() and not getattr(store.config, "mcp_auth_token", ""):
            # 尚未生成任何 Token 时明确提示，而不是让客户端看到一个空洞的 401
            _log.warning("MCP 鉴权失败: 尚未生成 Token (%s %s)", method, path)
            await self._reject(
                send, "尚未生成 MCP Token，请先在 WebUI「MCP 接入」页面生成", 401
            )
            return

        name = store.verify(token) if token else None
        if not name:
            _log.warning("MCP 鉴权失败: Token 无效或缺失 (%s %s)", method, path)
            await self._reject(send, "Token 无效或缺失", 401)
            return

        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["mcp_token_name"] = name

        # Mount("/mcp") 会把子路径剥成空串，而 MCP 子应用的路由注册在 "/"。
        # 不做这一步归一化，访问 /mcp 会先吃到一个 307 重定向到 /mcp/，
        # 对 POST 握手来说是完全没必要的风险。
        if not scope.get("path"):
            scope = dict(scope)
            scope["path"] = "/"
        await self.app(scope, receive, send)

    @staticmethod
    def _extract_token(scope: Scope) -> str:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if headers.get("x-mcp-token"):
            return headers["x-mcp-token"].strip()
        # 兼容部分只支持 URL 参数的客户端
        query = scope.get("query_string", b"").decode("latin-1")
        for part in query.split("&"):
            if part.startswith("token="):
                from urllib.parse import unquote

                return unquote(part[6:])
        return ""

    @staticmethod
    async def _reject(send: Send, message: str, status: int) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": message},
                "id": None,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"www-authenticate", b'Bearer realm="memory-worker"'),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
