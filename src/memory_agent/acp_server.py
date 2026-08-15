"""ACP server：会话管理 + 复用 _execute_run + 内部事件→ACP content block 映射。

设计要点
--------
* 复用 ``api.debug_routes._execute_run``（既有的 agent 推理循环），零重写。
  仅做两处向后兼容的可选参数注入：``tools`` 与 ``run_tool``，使 ACP agent
  能使用 builtin 工具集 + ``delegate_to_autoflow``。
* ACP session 直接复用 ``_CONV``（以 sessionId 作为 conversation_id），天然多轮连续。
* 内部 ``tool_call/done/aborted/error`` 事件经订阅队列翻译为 ACP content blocks，
  由 ``prompt`` 的 SSE 流（event: message）下发。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from . import tool_schema
from .acp_protocol import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    ERR_SESSION_NOT_FOUND,
    M_CANCEL,
    M_INITIALIZE,
    M_PROMPT,
    M_SESSION_DELETE,
    M_SESSION_HISTORY,
    M_SESSION_LIST,
    M_SESSION_NEW,
    M_SESSION_UPDATE,
    make_error,
    make_notification,
    make_response,
    sse_message,
    status_block,
    text_block,
    tool_call_block,
)
from .api.debug_routes import (
    DebugRun,
    _CONV,
    _execute_run,
    _register,
    _RUNS,
    _TERMINAL,
)
from .api.llm_routes import _run_memory_tool
from .runtime import get_runtime

AGENT_VERSION = "1.0.0"
DEFAULT_MAX_ROUNDS = 6

# SSE 防缓冲头（镜像 MCP 的处理，避免反向代理攒批）
NO_BUFFER_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


# ── 会话存储 ───────────────────────────────────────────────────────────
class SessionStore:
    """轻量内存会话表（单进程，无单点；上限镜像 _MAX_RUNS 思想）。"""

    def __init__(self, max_sessions: int = 200) -> None:
        self._sessions: dict[str, dict] = {}
        self._max = max_sessions

    def new(self, session_id: Optional[str] = None) -> str:
        sid = session_id or f"acp_{uuid.uuid4().hex}"
        self._sessions[sid] = {"run_id": None, "created_at": time.time()}
        self._trim()
        return sid

    def bind(self, sid: str, run_id: str) -> None:
        self._sessions.setdefault(sid, {"run_id": None, "created_at": time.time()})
        self._sessions[sid]["run_id"] = run_id

    def get(self, sid: str) -> Optional[dict]:
        return self._sessions.get(sid)

    def delete(self, sid: str) -> None:
        self._sessions.pop(sid, None)
        _CONV.pop(sid, None)

    def list(self) -> list[dict]:
        return [
            {"sessionId": s, **meta}
            for s, meta in sorted(
                self._sessions.items(), key=lambda kv: kv[1].get("created_at", 0)
            )
        ]

    def _trim(self) -> None:
        if len(self._sessions) <= self._max:
            return
        oldest = sorted(
            self._sessions.items(), key=lambda kv: kv[1].get("created_at", 0)
        )
        for s, _ in oldest[: len(self._sessions) - self._max]:
            self._sessions.pop(s, None)


_STORE = SessionStore()


# ── 工具目录 ───────────────────────────────────────────────────────────
def build_acp_tools() -> list[dict]:
    """ACP initialize 暴露的工具：builtin 集 + delegate_to_autoflow。"""
    tools: list[dict] = []
    for t in tool_schema.build_openai_tools(("builtin",)):
        fn = t["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn["description"],
                "inputSchema": fn["parameters"],
            }
        )
    tools.append(
        {
            "name": "delegate_to_autoflow",
            "description": (
                "将当前任务委派给 autoflow agent 处理（peer-to-peer 拓扑 X）。"
                "当任务更适合由 autoflow 执行时调用，返回 autoflow 的最终文本结果。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "用自然语言描述要交给 autoflow 的任务",
                    },
                    "context": {
                        "type": "object",
                        "description": "可选的补充上下文（任意 JSON）",
                    },
                },
                "required": ["task"],
            },
        }
    )
    return tools


# ACP 循环用的 OpenAI 工具表：builtin 集 + delegate
ACP_TOOLS: list[dict] = tool_schema.build_openai_tools(("builtin",)) + [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_autoflow",
            "description": (
                "将当前任务委派给 autoflow agent 处理（peer-to-peer 拓扑 X）。"
                "当任务更适合由 autoflow 执行时调用，返回 autoflow 的最终文本结果。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "context": {"type": "object"},
                },
                "required": ["task"],
            },
        },
    }
]


async def _run_acp_tool(rt: Any, name: str, args: dict) -> dict:
    """ACP 循环的自定义工具 runner：内置工具走 dispatch，delegate 走 ACP client。"""
    if name == "delegate_to_autoflow":
        from .acp_client import delegate_to_autoflow as _delegate

        return await _delegate(rt, args or {})
    return await _run_memory_tool(rt, name, args)


# ── 内部事件 → ACP 通知 映射 ───────────────────────────────────────────
def _parse_packed(packed: str) -> tuple[str, dict]:
    ev = None
    data_str = None
    for line in packed.split("\n"):
        if line.startswith("event: "):
            ev = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data_str = line[len("data: "):].strip()
    data = json.loads(data_str) if data_str else {}
    return ev, data


def _event_to_notification(ev: str, data: dict, session_id: str, req_id: Any):
    base = {"sessionId": session_id, "id": req_id}
    if ev == "backend":
        base["status"] = "running"
        base["content"] = [
            status_block("running", f"后端: {data.get('model')} ({data.get('provider')})")
        ]
        return make_notification(M_SESSION_UPDATE, base)
    if ev == "tool_call":
        base["status"] = "running"
        base["content"] = [
            tool_call_block(
                data.get("name", ""),
                data.get("arguments", {}),
                data.get("result"),
            )
        ]
        return make_notification(M_SESSION_UPDATE, base)
    if ev == "done":
        base["status"] = "completed"
        base["content"] = [text_block(data.get("answer") or "")]
        return make_notification(M_SESSION_UPDATE, base)
    if ev == "aborted":
        base["status"] = "aborted"
        base["content"] = [status_block("aborted", data.get("message", ""))]
        return make_notification(M_SESSION_UPDATE, base)
    if ev == "error":
        base["status"] = "error"
        base["content"] = [status_block("error", data.get("message", ""))]
        return make_notification(M_SESSION_UPDATE, base)
    return None


# ── 指令提取 ───────────────────────────────────────────────────────────
def _extract_instruction(params: dict) -> str:
    if params.get("prompt"):
        return str(params["prompt"]).strip()
    msgs = params.get("messages") or []
    for m in reversed(msgs):
        if m.get("role") == "user":
            return str(m.get("content") or "").strip()
    if msgs:
        return str(msgs[-1].get("content") or "").strip()
    return ""


# ── 分发 ───────────────────────────────────────────────────────────────
async def acp_handle(
    rt: Any, payload: dict
) -> tuple[Optional[dict], Optional[AsyncIterator]]:
    """解析 JSON-RPC，返回 (json 响应 dict | None, SSE 生成器 | None)。"""
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    if method == M_INITIALIZE:
        return make_response(
            req_id,
            {
                "agent": {
                    "name": "memory-agent",
                    "version": AGENT_VERSION,
                    "vendor": {"name": "memory-agent"},
                },
                "capabilities": {
                    "streaming": True,
                    "cancellation": True,
                    "sessions": True,
                    "tools": True,
                },
                "tools": build_acp_tools(),
            },
        ), None

    if method == M_SESSION_NEW:
        sid = _STORE.new(params.get("sessionId"))
        return make_response(req_id, {"sessionId": sid}), None

    if method == M_SESSION_LIST:
        return make_response(req_id, {"sessions": _STORE.list()}), None

    if method == M_SESSION_HISTORY:
        sid = params.get("sessionId")
        if not sid or sid not in _CONV:
            return make_error(req_id, ERR_SESSION_NOT_FOUND, "session 不存在"), None
        return (
            make_response(req_id, {"sessionId": sid, "messages": _CONV.get(sid, [])}),
            None,
        )

    if method == M_SESSION_DELETE:
        sid = params.get("sessionId")
        if not sid:
            return make_error(req_id, ERR_INVALID_PARAMS, "缺少 sessionId"), None
        _STORE.delete(sid)
        return make_response(req_id, {"sessionId": sid, "deleted": True}), None

    if method == M_CANCEL:
        sid = params.get("sessionId")
        meta = _STORE.get(sid) if sid else None
        if not meta or not meta.get("run_id"):
            return (
                make_error(req_id, ERR_SESSION_NOT_FOUND, "session 无进行中的任务"),
                None,
            )
        run = _RUNS.get(meta["run_id"])
        if run is not None:
            run.abort()
        return make_response(req_id, {"sessionId": sid, "status": "cancelling"}), None

    if method == M_PROMPT:
        session_id = params.get("sessionId") or _STORE.new()
        instruction = _extract_instruction(params)
        if not instruction:
            return (
                make_error(
                    req_id,
                    ERR_INVALID_PARAMS,
                    "prompt 缺少指令（messages 或 prompt）",
                ),
                None,
            )
        try:
            max_rounds = int(params.get("maxRounds") or DEFAULT_MAX_ROUNDS)
        except (TypeError, ValueError):
            max_rounds = DEFAULT_MAX_ROUNDS
        max_rounds = max(1, min(max_rounds, 20))
        model = params.get("model")
        run = DebugRun(
            run_id=uuid.uuid4().hex,
            instruction=instruction,
            model=model,
            mode="run",
            max_rounds=max_rounds,
            conversation_id=session_id,
            temperature=None,
        )
        _register(run)
        _STORE.bind(session_id, run.run_id)
        asyncio.create_task(
            _execute_run(rt, run, tools=ACP_TOOLS, run_tool=_run_acp_tool)
        )

        async def gen() -> AsyncIterator[str]:
            # 订阅范式与 debug_stream 对齐：先追加队列、回放历史、再消费实时，
            # 以 _TERMINAL 哨兵结束。
            own: asyncio.Queue = asyncio.Queue()
            run.subscribers.append(own)
            snapshot = list(run.history)
            try:
                for packed in snapshot:
                    ev, data = _parse_packed(packed)
                    notif = _event_to_notification(ev, data, session_id, req_id)
                    if notif is not None:
                        yield sse_message(notif)
                if run.terminal:
                    return
                while True:
                    item = await own.get()
                    if item is _TERMINAL:
                        break
                    ev, data = _parse_packed(item)
                    notif = _event_to_notification(ev, data, session_id, req_id)
                    if notif is not None:
                        yield sse_message(notif)
                    if ev in ("done", "aborted", "error"):
                        break
            except asyncio.CancelledError:
                run.abort()
                raise
            finally:
                try:
                    run.subscribers.remove(own)
                except ValueError:
                    pass

        return None, gen()

    return make_error(req_id, ERR_METHOD_NOT_FOUND, f"未知方法: {method}"), None


# ── 原始 ASGI 分发器（供 Mount 包裹） ──────────────────────────────────
async def acp_dispatcher(scope, receive, send) -> None:
    """/acp 的原始 ASGI 入口：解析 JSON-RPC，prompt 走 SSE，其余走 JSON。"""
    if scope.get("type") != "http":
        await send(
            {
                "type": "http.response.start",
                "status": 405,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b""})
        return

    request = Request(scope, receive=receive)
    if request.method == "GET":
        resp = JSONResponse(
            {
                "service": "memory-agent-acp",
                "transport": "json-rpc-2.0-over-http+sse",
                "note": "ACP 仅接受 JSON-RPC POST；详见 docs/acp-integration.md",
            }
        )
        await resp(scope, receive, send)
        return

    rt = get_runtime()
    try:
        body = await request.json()
    except Exception:
        resp = JSONResponse(make_error(None, -32700, "无效的 JSON"), status_code=400)
        await resp(scope, receive, send)
        return

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        resp = JSONResponse(
            make_error(
                body.get("id") if isinstance(body, dict) else None,
                -32600,
                "非 JSON-RPC 2.0 请求",
            ),
            status_code=400,
        )
        await resp(scope, receive, send)
        return

    json_resp, sse_gen = await acp_handle(rt, body)
    if sse_gen is not None:
        resp: Response = StreamingResponse(
            sse_gen, media_type="text/event-stream", headers=NO_BUFFER_HEADERS
        )
    else:
        resp = JSONResponse(json_resp, status_code=200)
    await resp(scope, receive, send)
