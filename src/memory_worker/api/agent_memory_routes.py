"""Agent 记忆（参与式写回向量库）管理路由。

暴露给 Web UI 的 CRUD / 操作端点；MCP 工具是同一服务层的另一入口。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


async def add_memory(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    text = (body.get("text") or "").strip()
    if not text:
        return error("缺少 text")
    rt = runtime(request)
    # add_semantic_memory 是同步方法（内部含 chroma 检索），不可 await
    result = rt.agent_memory.add_semantic_memory(
        text=text,
        topic_key=(body.get("topic_key") or "").strip(),
        session_id=(body.get("session_id") or "web-ui").strip(),
        source_refs=body.get("source_refs") or [],
        tags=body.get("tags") or [],
        dry_run=bool(body.get("dry_run", False)),
        ttl_days=body.get("ttl_days"),
    )
    if not result.get("ok"):
        return error(result.get("error", "写入失败"), result.get("code", 400), result)
    return ok(result)


async def list_memories(request: Request):
    _, err = require_user(request)
    if err:
        return err
    state = request.query_params.get("state", "all")
    topic_key = request.query_params.get("topic_key", "").strip()
    rt = runtime(request)
    result = rt.agent_memory.list_agent_memories(state)
    rows = result.get("memories", [])
    if topic_key:
        rows = [r for r in rows if r.get("topic_key") == topic_key]
    return ok({"state": state, "topic_key": topic_key, "count": len(rows), "memories": rows})


async def memory_health(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    return ok(rt.agent_memory.health())


async def promote(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    memory_id = (body.get("memory_id") or "").strip()
    if not memory_id:
        return error("缺少 memory_id")
    rt = runtime(request)
    # promote_memory 是同步方法；Web 端由人工复核触发,允许 force 晋升
    result = rt.agent_memory.promote_memory(
        memory_id,
        session_id=(body.get("session_id") or "").strip(),
        force=bool(body.get("force", False)),
        corroborating_insight_id=(body.get("corroborating_insight_id") or "").strip(),
        human_override=True,
    )
    if not result.get("ok"):
        return error(result.get("error", "晋升失败"), result.get("code", 400), result)
    return ok(result)


async def revoke(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    memory_id = (body.get("memory_id") or "").strip()
    if not memory_id:
        return error("缺少 memory_id")
    rt = runtime(request)
    result = rt.agent_memory.revoke_memory(memory_id)
    if not result.get("ok"):
        return error(result.get("error", "撤销失败"))
    return ok(result)


async def rollback(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return error("缺少 session_id")
    rt = runtime(request)
    result = rt.agent_memory.rollback_agent_memory(session_id)
    if not result.get("ok"):
        return error(result.get("error", "回滚失败"))
    return ok(result)


async def feedback(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    memory_id = (body.get("memory_id") or "").strip()
    if not memory_id:
        return error("缺少 memory_id")
    useful = bool(body.get("useful", True))
    rt = runtime(request)
    result = rt.agent_memory.feedback_memory(memory_id, useful)
    if not result.get("ok"):
        return error(result.get("error", "反馈失败"))
    return ok(result)


async def sweep(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    result = rt.agent_memory.sweep_and_reconcile()
    return ok(result)


async def retrieve(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    question = (body.get("question") or "").strip()
    if not question:
        return error("缺少 question")
    rt = runtime(request)
    trust_min = body.get("trust_min")
    top_k = int(body.get("top_k", 5))
    hits = rt.agent_memory.retrieve(
        question,
        trust_min=float(trust_min) if trust_min not in (None, "") else None,
        top_k=top_k,
    )
    return ok({"count": len(hits), "memories": hits})


ROUTES = [
    Route("/api/agent/memories", add_memory, methods=["POST"]),
    Route("/api/agent/memories", list_memories, methods=["GET"]),
    Route("/api/agent/memories/health", memory_health, methods=["GET"]),
    Route("/api/agent/memories/promote", promote, methods=["POST"]),
    Route("/api/agent/memories/revoke", revoke, methods=["POST"]),
    Route("/api/agent/memories/rollback", rollback, methods=["POST"]),
    Route("/api/agent/memories/feedback", feedback, methods=["POST"]),
    Route("/api/agent/memories/sweep", sweep, methods=["POST"]),
    Route("/api/agent/memories/retrieve", retrieve, methods=["POST"]),
]

__all__ = ["ROUTES"]
