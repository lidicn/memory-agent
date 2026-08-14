"""ACP（Agent Client Protocol）JSON-RPC 2.0 信封与内容块契约。

memory-worker 对外暴露的 ACP 协议子集，作为 server 实现与对接文档
（docs/acp-integration.md）的单一真源。传输：HTTP + SSE。

内容块（content blocks）
-----------------------
* text      : {"type":"text","text":str}
* tool_call : {"type":"tool_call","name":str,"arguments":dict,"result":str|null}
* status    : {"type":"status","status":"running|completed|aborted|error","message":str}

流式通知统一经 SSE 下发：``event: message\\ndata: <JSON-RPC 通知>``。
"""
from __future__ import annotations

import json
from typing import Any

JSONRPC_VERSION = "2.0"

# ── 方法名 ─────────────────────────────────────────────────────────────
M_INITIALIZE = "initialize"
M_PROMPT = "prompt"
M_CANCEL = "cancel"
M_SESSION_NEW = "session.new"
M_SESSION_LIST = "session.list"
M_SESSION_HISTORY = "session.history"
M_SESSION_DELETE = "session.delete"
M_SESSION_UPDATE = "session_update"

# ── 标准 JSON-RPC 错误码 ──────────────────────────────────────────────
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# ── ACP 自定义错误码 ──────────────────────────────────────────────────
ERR_SESSION_NOT_FOUND = -32001
ERR_AGENT_BUSY = -32002
ERR_DELEGATE_FAILED = -32003


def make_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": err}


def make_notification(method: str, params: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}


# ── 内容块构造 ─────────────────────────────────────────────────────────
def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def tool_call_block(name: str, arguments: dict, result: str | None = None) -> dict:
    return {
        "type": "tool_call",
        "name": name,
        "arguments": arguments,
        "result": result,
    }


def status_block(status: str, message: str = "") -> dict:
    return {"type": "status", "status": status, "message": message}


def sse_message(payload: dict) -> str:
    """把一条 JSON-RPC 通知/响应包成 ACP 规定的 SSE 行（event: message）。"""
    return "event: message\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
