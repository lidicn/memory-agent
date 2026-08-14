"""API 公共依赖：响应约定、鉴权助手、SSE 流式响应

响应约定沿用重构前的 ``{"ok": bool, ...}``，避免破坏 Node-RED
与既有前端的解析逻辑。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from ..runtime import AppRuntime, get_runtime

__all__ = [
    "ok",
    "error",
    "_ok",
    "_error",
    "current_user",
    "require_user",
    "require_admin",
    "json_body",
    "runtime",
    "sse_response",
    "sse_pack",
    "mask_secret",
]


# ── 响应 ───────────────────────────────────────────────────────────────────

def error(msg: str, code: int = 400, extra: dict | None = None) -> JSONResponse:
    """失败响应。``extra`` 用于携带诊断明细（如自检各步骤），
    让前端在报错时也能展示「到底卡在哪一步」，而不是只有一句红字。"""
    payload: dict[str, Any] = {"ok": False, "error": msg}
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=code)


def ok(data: Any = None, status_code: int = 200) -> JSONResponse:
    if data is None:
        return JSONResponse({"ok": True}, status_code=status_code)
    if isinstance(data, dict):
        return JSONResponse({"ok": True, **data}, status_code=status_code)
    return JSONResponse({"ok": True, "data": data}, status_code=status_code)


# 兼容旧命名
_ok = ok
_error = error


# ── 鉴权 ───────────────────────────────────────────────────────────────────

def current_user(request: Request) -> dict | None:
    """读取鉴权中间件写入的用户。

    不同 Starlette 版本对 ``request.state`` 与 ``scope['state']`` 的绑定方式
    不一致，这里两条路径都兜住。
    """
    state = request.scope.get("state")
    if isinstance(state, dict) and state.get("user"):
        return state["user"]
    return getattr(request.state, "user", None)


def require_user(request: Request) -> tuple[dict | None, JSONResponse | None]:
    user = current_user(request)
    if not user:
        return None, error("未登录", 401)
    return user, None


def require_admin(request: Request) -> tuple[dict | None, JSONResponse | None]:
    user = current_user(request)
    if not user:
        return None, error("未登录", 401)
    if not user.get("is_admin"):
        return None, error("需要管理员权限", 403)
    return user, None


# ── 运行时 ─────────────────────────────────────────────────────────────────

def runtime(request: Request | None = None) -> AppRuntime:
    if request is not None:
        rt = getattr(request.app.state, "runtime", None)
        if rt is not None:
            return rt
    return get_runtime()


# ── 请求体 ─────────────────────────────────────────────────────────────────

async def json_body(request: Request) -> dict:
    """安全解析 JSON body。空体或非法 JSON 一律返回空字典，不抛异常。"""
    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {"data": data}


# ── SSE ────────────────────────────────────────────────────────────────────

def sse_pack(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def sse_response(generator: Callable[[], AsyncIterator[str]] | AsyncIterator[str]):
    """构造 SSE 响应。

    必须显式关闭缓冲，否则经过反向代理时会「攒够一批才吐」，
    前端表现为「不流式」。
    """
    iterator = generator() if callable(generator) else generator
    return StreamingResponse(
        iterator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 其他 ───────────────────────────────────────────────────────────────────

def mask_secret(value: str, keep: int = 4) -> str:
    """密钥掩码。GET /api/config 一律返回掩码值，避免密钥随页面泄露。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}{value[-keep:]}"
