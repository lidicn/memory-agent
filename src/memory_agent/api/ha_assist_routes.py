"""HA Assist 路由（v1.0-2）：OpenAI 兼容会话端点。

契约：docs/HA_Assist接入.md。
鉴权：``Authorization: Bearer <ha_assist_token>``。app.py 的 HA_ENDPOINTS 已把
``/v1/*`` 从全局 JWT 鉴权放行，本路由独立校验令牌；未配置 token 则通道关闭（401）。

提供：
* ``POST /v1/chat/completions`` —— 记忆增强对话（支持 stream=true 的 SSE 分块）。
* ``GET  /v1/models``          —— 模型列表（供客户端探测，返回单个虚拟模型）。
"""
from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .deps import error, json_body, runtime

_MODEL_ID = "ma-family"


def _check_ha_token(request: Request) -> bool:
    expected = (runtime(request).config.ha_assist_token or "").strip()
    if not expected:
        return False
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    try:
        return hmac.compare_digest(auth[7:].strip(), expected)
    except Exception:  # noqa: BLE001 - 非 ASCII 令牌比较异常时判失败
        return False


def _last_user_text(messages) -> str:
    """取最后一条 user 消息的文本（兼容 OpenAI 的 content 字符串 / 多段列表）。"""
    for m in reversed(messages or []):
        if not (isinstance(m, dict) and m.get("role") == "user"):
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return " ".join(t for t in parts if t)
    return ""


async def chat_completions(request: Request):
    """POST /v1/chat/completions —— 记忆增强对话（OpenAI 兼容）。"""
    if not _check_ha_token(request):
        return error("未授权：HA_ASSIST_TOKEN 未配置或令牌无效", 401)
    body = await json_body(request)
    messages = body.get("messages") or []
    user_text = _last_user_text(messages)
    model = body.get("model") or _MODEL_ID
    rt = runtime(request)
    result = await rt.ha_assist.answer(user_text)
    cid = "chatcmpl-" + uuid.uuid4().hex[:20]
    created = int(time.time())

    if body.get("stream"):
        async def _gen():
            head = {
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": result["content"]},
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(head, ensure_ascii=False)}\n\n"
            tail = {
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(tail, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    return JSONResponse({
        "id": cid, "object": "chat.completion", "created": created, "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result["content"]},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


async def list_models(request: Request):
    """GET /v1/models —— 返回单个虚拟模型，供客户端探测。"""
    if not _check_ha_token(request):
        return error("未授权：HA_ASSIST_TOKEN 未配置或令牌无效", 401)
    return JSONResponse({
        "object": "list",
        "data": [{"id": _MODEL_ID, "object": "model", "owned_by": "memory-agent"}],
    })


ROUTES = [
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/v1/models", list_models, methods=["GET"]),
]
