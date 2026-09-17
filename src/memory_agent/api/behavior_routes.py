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


async def behaviors_mine_process(request: Request):
    """手动触发过程挖掘（P1.1）：挖行为过程模型 + 一致性检验 → 行为异常 / 候选规则。

    body 可选：``days``（默认 7）、``start``/``end``、``rooms``(list)、
    ``persist``、``emit_rules``、``min_variant_support``。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    try:
        days = int(body.get("days") or 7)
    except (TypeError, ValueError):
        return error("days 必须是整数")
    rooms = body.get("rooms")
    res = await asyncio.to_thread(
        rt.activity.mine_process,
        (body.get("start") or "").strip() or None,
        (body.get("end") or "").strip() or None,
        max(1, min(days, 90)),
        [str(r) for r in rooms] if isinstance(rooms, list) and rooms else None,
        persist=bool(body.get("persist", True)),
        emit_rules=bool(body.get("emit_rules", True)),
        min_variant_support=int(body.get("min_variant_support") or 3),
        bucket_sec=(int(body["bucket_sec"]) if body.get("bucket_sec") is not None else None),
        min_cases_per_room=(
            int(body["min_cases_per_room"])
            if body.get("min_cases_per_room") is not None else None),
        min_case_events=(
            int(body["min_case_events"])
            if body.get("min_case_events") is not None else None),
    )
    return ok(res)


async def behaviors_anomalies(request: Request):
    """列出行为异常（P1.1 过程挖掘产出，偏离已学过程模型的 case）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    try:
        limit = int(request.query_params.get("limit") or 100)
    except (TypeError, ValueError):
        return error("limit 必须是整数")
    rows = await asyncio.to_thread(
        rt.store.list_behavior_anomalies,
        (request.query_params.get("status") or "").strip() or None,
        (request.query_params.get("day_from") or "").strip() or None,
        (request.query_params.get("day_to") or "").strip() or None,
        (request.query_params.get("room") or "").strip() or None,
        max(1, min(limit, 1000)),
    )
    return ok({"anomalies": rows, "count": len(rows)})


async def behavior_anomaly_update(request: Request):
    """复核行为异常：confirmed（确属异常）/ ignored（误报）/ new（复位）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    anomaly_id = (body.get("anomaly_id") or "").strip()
    status = (body.get("status") or "").strip()
    if not anomaly_id:
        return error("缺少 anomaly_id")
    if status not in ("new", "confirmed", "ignored"):
        return error("status 必须是 new/confirmed/ignored")
    changed = await asyncio.to_thread(
        rt.store.set_behavior_anomaly_status, anomaly_id, status
    )
    if not changed:
        return error("异常不存在", 404)
    return ok({"anomaly_id": anomaly_id, "status": status})


async def behaviors_mine_drift(request: Request):
    """手动触发在线异常 / 概念漂移检测（P1.2）。

    body 可选：``days``（默认 14）、``bucket_sec``、``window_size``、``persist``。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    try:
        days = int(body.get("days") or 14)
    except (TypeError, ValueError):
        return error("days 必须是整数")
    res = await asyncio.to_thread(
        rt.activity.mine_drift, None, None, max(1, min(days, 180)), None,
        bucket_sec=(int(body["bucket_sec"]) if body.get("bucket_sec") is not None else None),
        window_size=(int(body["window_size"]) if body.get("window_size") is not None else None),
        min_score=(float(body["min_score"]) if body.get("min_score") is not None else None),
        persist=bool(body.get("persist", True)),
    )
    return ok(res)


async def behaviors_drifts(request: Request):
    """列出漂移点 / 异常时段；``live=1`` 时顺带现算一次（不落库）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    kind = (request.query_params.get("kind") or "").strip() or None
    try:
        limit = int(request.query_params.get("limit") or 100)
    except (TypeError, ValueError):
        return error("limit 必须是整数")
    payload: dict = {}
    if (request.query_params.get("live") or "").strip() in ("1", "true", "yes"):
        try:
            days = int(request.query_params.get("days") or 14)
        except (TypeError, ValueError):
            return error("days 必须是整数")
        payload["live"] = await asyncio.to_thread(
            rt.activity.mine_drift, None, None, max(1, min(days, 180)), None,
            persist=False,
        )
    rows = await asyncio.to_thread(
        rt.store.list_behavior_drifts, kind,
        (request.query_params.get("day_from") or "").strip() or None,
        (request.query_params.get("day_to") or "").strip() or None,
        max(1, min(limit, 1000)),
    )
    payload.update({"drifts": rows, "count": len(rows)})
    return ok(payload)


async def behaviors_audit_rule_recall(request: Request):
    """规则召回审计（P1.4）：eligible/matched/near-miss + 卡点诊断（+ 可选产出放宽建议）。

    body 可选：``days``（默认 14）、``rooms``(list)、``persist``、``min_near_miss``。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    try:
        days = int(body.get("days") or 14)
    except (TypeError, ValueError):
        return error("days 必须是整数")
    rooms = body.get("rooms")
    res = await asyncio.to_thread(
        rt.activity.audit_rule_recall,
        (body.get("start") or "").strip() or None,
        (body.get("end") or "").strip() or None,
        max(1, min(days, 180)),
        [str(r) for r in rooms] if isinstance(rooms, list) and rooms else None,
        persist=bool(body.get("persist", True)),
        min_near_miss=int(body.get("min_near_miss") or 2),
    )
    return ok(res)


ROUTES = [
    Route("/api/behaviors", behaviors_current, methods=["GET"]),
    Route("/api/behaviors/states", behaviors_states, methods=["GET"]),
    Route("/api/behaviors/run", behaviors_run, methods=["POST"]),
    Route("/api/behaviors/candidate-rules", candidate_rules_list, methods=["GET"]),
    Route("/api/behaviors/candidate-rules/update", candidate_rule_update, methods=["POST"]),
    Route("/api/behaviors/candidate-rules/export", candidate_rules_export, methods=["GET"]),
    Route("/api/behaviors/mine-process", behaviors_mine_process, methods=["POST"]),
    Route("/api/behaviors/anomalies", behaviors_anomalies, methods=["GET"]),
    Route("/api/behaviors/anomalies/update", behavior_anomaly_update, methods=["POST"]),
    Route("/api/behaviors/mine-drift", behaviors_mine_drift, methods=["POST"]),
    Route("/api/behaviors/drifts", behaviors_drifts, methods=["GET"]),
    Route("/api/behaviors/audit-rule-recall", behaviors_audit_rule_recall,
          methods=["POST"]),
]
