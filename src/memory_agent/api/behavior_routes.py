"""主动感知·行为推断路由（v0.9.5）。

MA 侧权威行为状态出口：``GET /api/behaviors`` 融合「身份(ma/presence) + 房间 +
canonical 活动」，供管家 v1.7 M4 状态看板消费（需加入 butler 白名单）。
另提供候选序列规则的列表 / 审核 / 导出接口——人工采纳后以结构化 JSON 交付
管家规则库（对应管家 v1.7 M5「LLM 自进化」，实现方为 MA）。
"""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


async def behaviors_current(request: Request):
    """当前 canonical 行为状态（谁·在哪·做什么）。管家实时上下文引擎数据源。

    查询参数 ``minutes``（默认 30）限定身份新鲜度窗口。鉴权走 AuthMiddleware
    （WebUI JWT 或 butler_token；本路径在 butler 白名单内）。
    """
    rt = runtime(request)
    try:
        minutes = int(request.query_params.get("minutes") or 30)
    except (TypeError, ValueError):
        return error("minutes 必须是整数")
    items = await asyncio.to_thread(rt.activity.current_behaviors, max(1, min(minutes, 1440)))
    return ok({"behaviors": items, "count": len(items)})


async def behaviors_states(request: Request):
    """历史 canonical 行为状态（按 ts 倒序）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    member = (request.query_params.get("member") or "").strip()
    room = (request.query_params.get("room") or "").strip()
    since = (request.query_params.get("since") or "").strip()
    try:
        limit = int(request.query_params.get("limit") or 100)
    except (TypeError, ValueError):
        return error("limit 必须是整数")
    rows = await asyncio.to_thread(
        rt.store.list_behavior_states, member or None, room or None, since or None,
        max(1, min(limit, 1000)),
    )
    return ok({"states": rows, "count": len(rows)})


async def behaviors_run(request: Request):
    """手动触发一次行为推断（调试/补算用）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    res = await asyncio.to_thread(
        rt.activity.run,
        (body.get("start") or "").strip() or None,
        (body.get("end") or "").strip() or None,
        body.get("window_minutes"),
    )
    return ok(res)


async def candidate_rules_list(request: Request):
    """列出候选序列规则（可按 status 过滤 staging/accepted/rejected）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    status = (request.query_params.get("status") or "").strip()
    rows = await asyncio.to_thread(rt.store.list_candidate_rules, status or None)
    return ok({"rules": rows, "count": len(rows)})


async def candidate_rule_update(request: Request):
    """审核候选规则：采纳(accepted) / 驳回(rejected) / 复位(staging)。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rule_id = (body.get("rule_id") or "").strip()
    status = (body.get("status") or "").strip()
    if not rule_id:
        return error("缺少 rule_id")
    if status not in ("staging", "accepted", "rejected"):
        return error("status 必须是 staging/accepted/rejected")
    changed = await asyncio.to_thread(rt.store.set_candidate_rule_status, rule_id, status)
    if not changed:
        return error("规则不存在", 404)
    return ok({"rule_id": rule_id, "status": status})


async def candidate_rules_export(request: Request):
    """导出**已采纳**的候选规则为结构化 JSON（交付管家规则库）。

    返回 ``{rules:[{name, order_sensitive, steps, time_window, infer, confidence}]}``，
    管家可直接解析回灌其规则引擎（对应管家 v1.7 M5）。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    rows = await asyncio.to_thread(rt.store.list_candidate_rules, "accepted")
    rules = [
        {
            "name": r.get("name"),
            "order_sensitive": True,
            "steps": r.get("steps") or [],
            "time_window": r.get("time_window") or "",
            "infer": r.get("infer") or "",
            "confidence": r.get("confidence") or 0.0,
            "source": r.get("source") or "inference",
        }
        for r in rows
    ]
    return ok({"rules": rules, "count": len(rules)})


ROUTES = [
    Route("/api/behaviors", behaviors_current, methods=["GET"]),
    Route("/api/behaviors/states", behaviors_states, methods=["GET"]),
    Route("/api/behaviors/run", behaviors_run, methods=["POST"]),
    Route("/api/behaviors/candidate-rules", candidate_rules_list, methods=["GET"]),
    Route("/api/behaviors/candidate-rules/update", candidate_rule_update, methods=["POST"]),
    Route("/api/behaviors/candidate-rules/export", candidate_rules_export, methods=["GET"]),
]
