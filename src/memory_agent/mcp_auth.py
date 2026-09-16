"""MCP 专用鉴权中间件

与 WebUI 的 JWT ``AuthMiddleware`` 完全隔离：MCP 客户端携带的是
``Authorization: Bearer mcp_xxx``，与浏览器会话不共享任何凭据。

重构前 ``/mcp`` 直接躺在 JWT 白名单里 —— 等同于完全裸奔，
``agent_tokens`` 从未真正生效。

v0.7.5 加固（路线图 §三）
------------------------
1. **工具级 scope**：解析 ``tools/call`` 的工具名，写工具需令牌持 ``write``；
   新令牌默认只读，存量令牌视作全权（见 ``mcp_tokens.LEGACY_SCOPES``）。
2. **kind 身份隔离**：只放行 ``mcp`` / ``debug``；``acp_`` / ``arena`` 令牌访问 /mcp 一律 403。
3. **Origin/Host 纵深防御**：``MCP_ALLOWED_ORIGINS`` 非空时校验来源（默认关闭，不破坏现有客户端）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from .mcp_context import set_caller

_log = logging.getLogger("mcp.auth")

# 允许访问 /mcp 的令牌用途（acp / arena 属另一协议，禁止互用）
MCP_KINDS = ("mcp", "debug")
# 请求体上限：工具参数不会太大，超限直接拒绝，避免被当缓冲区打
MAX_BODY_BYTES = 1024 * 1024


def _allowed_origins() -> list[str]:
    raw = os.getenv("MCP_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return []
    return [x.strip().lower().rstrip("/") for x in raw.split(",") if x.strip()]


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

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        # ── 3) Origin/Host 纵深防御（可选开启）──────────────────────────
        allowed = _allowed_origins()
        if allowed:
            origin = (
                headers.get("origin") or headers.get("host") or ""
            ).strip().lower().rstrip("/")
            if origin not in allowed:
                _log.warning("MCP 鉴权失败: 来源不在白名单 (%s) %s %s", origin, method, path)
                await self._reject(send, f"来源未被允许: {origin or '(空)'}", 403)
                return

        token = self._extract_token(headers, scope)
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

        # ── 2) kind 身份隔离：acp_ / arena 令牌不得访问 /mcp ────────────
        kind = store.kind(name) if hasattr(store, "kind") else "mcp"
        if kind not in MCP_KINDS:
            _log.warning("MCP 鉴权失败: 令牌用途非 MCP (%s, kind=%s, %s)", name, kind, path)
            await self._reject(send, f"该 Token 非 MCP 用途（kind={kind}，须为 mcp/debug）", 403)
            return

        # ── 1) 工具级 scope 授权 ───────────────────────────────────────
        # 注意：**不要在中间件里读 body**。ASGI 请求体只能消费一次，
        # 而 /mcp 同时挂在 Mount 与精确 Route 上，读完再重放会让子应用拿不到
        # 完整请求而挂起（实测：所有 tools/call 超时）。
        # 真正的权限判定下沉到工具分发层（mcp_server._tracked_call_tool），
        # 那里既拿得到工具名，也不碰 ASGI 流。这里只负责把身份与权限透传下去。
        granted = store.scopes(name) if hasattr(store, "scopes") else None

        scope.setdefault("state", {})
        if isinstance(scope["state"], dict):
            scope["state"]["mcp_token_name"] = name
            scope["state"]["mcp_token_scopes"] = granted or []
        set_caller(
            name,
            granted or [],
            headers.get("origin") or headers.get("host") or "",
        )

        # Mount("/mcp") 会把子路径剥成空串，而 MCP 子应用的路由注册在 "/"。
        # 不做这一步归一化，访问 /mcp 会先吃到一个 307 重定向到 /mcp/，
        # 对 POST 握手来说是完全没必要的风险。
        if not scope.get("path"):
            scope = dict(scope)
            scope["path"] = "/"
        await self.app(scope, receive, send)

    @staticmethod
    def _extract_token(headers: dict, scope: Scope) -> str:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if headers.get("x-mcp-token"):
            return headers.get("x-mcp-token", "").strip()
        # 安全（审计 M6）：默认不再从 URL 参数取 Token（会进浏览器历史 / 访问日志）。
        # 如需兼容只支持 URL 的旧客户端，显式设 MCP_ALLOW_URL_TOKEN=1 才开启。
        if os.getenv("MCP_ALLOW_URL_TOKEN", "").strip().lower() in ("1", "true", "yes", "on"):
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
                "error": {
                    "code": -32003 if status == 403 else -32001,
                    "message": message,
                },
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
                    (b"www-authenticate", b'Bearer realm="memory-agent"'),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
