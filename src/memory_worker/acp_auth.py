"""ACP 端点鉴权中间件：Bearer acp_xxx（kind=acp），与 WebUI JWT / mcp_ 令牌隔离。

镜像 ``mcp_auth.MCPTokenMiddleware``，但只放行 kind=="acp" 的令牌，
并在拒绝时返回 ACP 标准的 JSON-RPC 错误体。
"""
from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from .acp_protocol import make_error

_log = logging.getLogger("acp.auth")


class ACPTokenMiddleware:
    """纯 ASGI 中间件，只保护被包裹的 ACP 子应用。"""

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
            _log.warning("ACP 鉴权失败: 服务未就绪 (%s %s)", method, path)
            await self._reject(send, "服务未就绪", 503)
            return

        name = store.verify(token) if token else None
        if not name:
            _log.warning("ACP 鉴权失败: Token 无效或缺失 (%s %s)", method, path)
            await self._reject(
                send, "Token 无效或缺失（需 acp_ 令牌，kind=acp）", 401
            )
            return
        if store.kind(name) != "acp":
            _log.warning("ACP 鉴权失败: Token 用途非 acp (%s, %s)", name, path)
            await self._reject(send, "该 Token 非 ACP 用途（kind!=acp）", 403)
            return

        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["acp_token_name"] = name

        # Mount("/acp") 会把子路径剥成空串，归一化为 "/"
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
        if headers.get("x-acp-token"):
            return headers["x-acp-token"].strip()
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
            make_error(None, -32000, message), ensure_ascii=False
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"www-authenticate", b'Bearer realm="memory-worker-acp"'),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
