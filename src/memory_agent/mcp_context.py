"""MCP 调用上下文（跨模块传递「当前调用者身份与权限」）

ASGI 中间件（``mcp_auth``）在鉴权通过后把令牌名 / 权限 / 来源写入本 ContextVar，
MCP 服务端（``mcp_server``）在工具分发与审计时读取它 —— 二者分处不同调用栈，
用 ContextVar 传递比层层透传参数更干净，也不引入循环依赖。

注意：ContextVar 在同一个 asyncio 任务内可见；MCP 调用全程在请求所在任务内，
因此可靠。取不到时返回默认值（不应因此拒绝调用，交由权限层判定）。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, List

# 当前 MCP 调用的令牌名（未鉴权 / 未知时为 ""）
current_token_name: ContextVar[str] = ContextVar("ma_mcp_token_name", default="")
# 当前 MCP 调用令牌被授予的权限（未鉴权时为空列表 → 按最严格处理）
current_token_scopes: ContextVar[List[str]] = ContextVar("ma_mcp_scopes", default=[])
# 当前 MCP 调用的客户端来源（Origin/Host，用于审计留痕）
current_origin: ContextVar[str] = ContextVar("ma_mcp_origin", default="")


def set_caller(token_name: str = "", scopes: Any = None, origin: str = "") -> None:
    current_token_name.set(token_name or "")
    current_token_scopes.set(list(scopes) if scopes else [])
    current_origin.set(origin or "")


def reset_caller() -> None:
    set_caller("", [], "")


def caller() -> tuple[str, list, str]:
    """返回 (token_name, scopes, origin)。"""
    return (
        current_token_name.get() or "",
        list(current_token_scopes.get() or []),
        current_origin.get() or "",
    )
