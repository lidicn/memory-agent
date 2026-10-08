"""信号规则（学习策略）管理路由。

暴露给 Web UI 的 CRUD / 操作端点；MCP 工具（teach_signal / list_signal_rules）是同一服务层的另一入口。
软记忆（topic_key=signal_trust）的晋升/撤销/反馈复用 ``agent_memory_routes``，本模块只负责
硬排除的 list/teach/revoke 与混合列表。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


async def list_rules(request: Request):
    """列出已学会的信号规则：硬排除 + 软记忆。"""
    _, err = require_user(request)
    if err:
        return err
    include_revoked = str(request.query_params.get("include_revoked", "")).lower() in (
        "1", "true", "yes",
    )
    rt = runtime(request)
    return ok(rt.signal_learning.list_rules(include_revoked=include_revoked))


async def teach(request: Request):
    """教一条信号规则（硬排 / 软记忆两条路径）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    entity_id = (body.get("entity_id") or "").strip()
    if not entity_id:
        return error("缺少 entity_id")
    kind = (body.get("kind") or "hard").strip().lower()
    rt = runtime(request)
    result = rt.signal_learning.teach_signal(
        entity_id=entity_id,
        scope=(body.get("scope") or "all").strip(),
        kind=kind,
        reason=(body.get("reason") or "").strip(),
        text=(body.get("text") or "").strip(),
        source_refs=body.get("source_refs") or [],
        session_id=(body.get("session_id") or "web-ui").strip(),
        exclusion_type=(body.get("exclusion_type") or "exclude").strip(),
    )
    if not result.get("ok"):
        return error(result.get("error", "teach_signal 失败"), result.get("code", 400))
    return ok(result)


async def revoke(request: Request):
    """撤销一条硬排除（墓碑，保留审计）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    exclusion_id = (body.get("exclusion_id") or "").strip()
    if not exclusion_id:
        return error("缺少 exclusion_id")
    rt = runtime(request)
    result = rt.signal_learning.revoke_exclusion(exclusion_id)
    if not result.get("ok"):
        return error(result.get("error", "撤销失败"), 404)
    return ok(result)


ROUTES = [
    Route("/api/signal-rules", list_rules, methods=["GET"]),
    Route("/api/signal-rules/teach", teach, methods=["POST"]),
    Route("/api/signal-rules/revoke", revoke, methods=["POST"]),
]
