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


async def behaviors_rules(request: Request):
    """Phase 3 主动规则引擎：查看 STATIC 规则列表和冷却状态。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    from ..perception_rules import STATIC_RULES
    rules = []
    for r in STATIC_RULES:
        rid = r["id"]
        last = rt.rule_engine._last_triggered.get(rid, 0.0) if hasattr(rt, "rule_engine") else 0.0
        cooldown = float(r.get("cooldown_seconds", 0))
        import time
        elapsed = time.monotonic() - last if last > 0 else None
        in_cd = elapsed is not None and elapsed < cooldown
        rules.append({
            "id": rid,
            "trigger": r.get("trigger"),
            "room": r.get("room", ""),
            "action": r.get("action"),
            "cooldown_seconds": cooldown,
            "description": r.get("description", ""),
            "in_cooldown": in_cd,
            "seconds_remaining": max(0, int(cooldown - elapsed)) if in_cd else 0,
        })
    return ok({"rules": rules, "count": len(rules)})


async def behaviors_home_profile(request: Request):
    """Phase 2.3 家庭画像：从 live 记忆生成 profile.md（原子写+权重截断）。

    查询参数：``max_chars``（默认 4000）、``write``（是否写入 /data/home_profile.md，默认 false）。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    try:
        max_chars = int(request.query_params.get("max_chars") or 4000)
    except (TypeError, ValueError):
        return error("max_chars 必须是整数")
    write = request.query_params.get("write", "").lower() in ("1", "true", "yes")
    from ..home_profile import build_profile, write_profile_atomic
    profile_text = build_profile(rt.store, max_chars=max(100, min(max_chars, 20000)))
    written_path = None
    if write:
        out_path = "/data/home_profile.md"
        write_profile_atomic(out_path, profile_text)
        written_path = out_path
    return ok({"profile": profile_text, "chars": len(profile_text), "written": written_path})


async def behaviors_feedback_pack(request: Request):
    """Phase 4.3 反馈闭环：导出 VLM 误识别 bad-case 包（tar.gz）。

    Body: ``snapshot_path``（可选，VLM 快照路径）、``trace``（可选，trace 文本）、
    ``label``（可选，默认 bad_case）。
    返回打包后的文件路径。脱敏失败宁可丢 trace（fail-closed）。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    from ..feedback_pack import build_feedback_pack
    output_dir = "/data/feedback_packs"
    import os
    os.makedirs(output_dir, exist_ok=True)
    result = build_feedback_pack(
        snapshot_path=body.get("snapshot_path", ""),
        trace=body.get("trace", ""),
        output_dir=output_dir,
        label=body.get("label", "bad_case"),
    )
    if result is None:
        return error("反馈包打包失败（脱敏/打包异常，已 fail-closed）")
    size = os.path.getsize(result) if os.path.exists(result) else 0
    return ok({"path": result, "size_bytes": size, "label": body.get("label", "bad_case")})


async def behaviors_bad_cases_list(request: Request):
    """Phase 4.3 反馈闭环：列出最近的 VLM 失败 bad-case 事件。

    查询参数：``limit``（可选，默认 20）、``room``（可选，按房间过滤）。
    返回 vlm_failed 状态的行为事件列表（含 snapshot_path、raw_response 摘要）。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    limit = min(int(request.query_params.get("limit", 20) or 20), 100)
    room = (request.query_params.get("room") or "").strip()
    try:
        events = rt.store.query_events(
            status="vlm_failed", room=room or None, limit=limit,
        )
    except Exception as exc:
        return error(f"查询失败: {exc}")
    # 脱敏：raw_response 只返回前 200 字符
    out = []
    for e in events:
        raw = e.get("raw_response") or ""
        out.append({
            "id": e.get("id"), "ts": e.get("ts"), "room": e.get("room"),
            "camera_src": e.get("camera_src"), "trigger": e.get("trigger"),
            "snapshot_path": e.get("snapshot_path"),
            "error_summary": raw[:200] + ("..." if len(raw) > 200 else ""),
            "device_ts": e.get("device_ts"),
        })
    return ok({"count": len(out), "events": out})


async def behaviors_bad_case_export(request: Request):
    """Phase 4.3 反馈闭环：根据事件 ID 一键导出 bad-case 包。

    Body: ``event_id``（必填，行为事件 ID）、``label``（可选，默认 bad_case_{id}）。
    从数据库读取事件的 snapshot_path 和 raw_response，打包成 tar.gz。
    fail-closed：脱敏失败宁可丢 trace。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    event_id = body.get("event_id")
    if not event_id:
        return error("缺少 event_id")
    rt = runtime(request)
    from ..feedback_pack import build_feedback_pack
    output_dir = "/data/feedback_packs"
    os.makedirs(output_dir, exist_ok=True)
    # 从数据库读取事件
    try:
        events = rt.store.query_events(limit=1)
        # query_events 不支持按 id 过滤，用 get_event 或直接查
        event = None
        if hasattr(rt.store, "get_behavior_event"):
            event = rt.store.get_behavior_event(event_id)
        if event is None:
            # 回退：从最近事件中找
            all_events = rt.store.query_events(limit=500)
            for e in all_events:
                if str(e.get("id")) == str(event_id):
                    event = e
                    break
    except Exception as exc:
        return error(f"查询事件失败: {exc}")
    if event is None:
        return error(f"事件 {event_id} 不存在")
    label = body.get("label") or f"bad_case_{event_id}"
    result = build_feedback_pack(
        snapshot_path=event.get("snapshot_path", ""),
        trace=event.get("raw_response", ""),
        output_dir=output_dir,
        label=label,
    )
    if result is None:
        return error("反馈包打包失败（脱敏/打包异常，已 fail-closed）")
    size = os.path.getsize(result) if os.path.exists(result) else 0
    return ok({"path": result, "size_bytes": size, "label": label, "event_id": event_id})


async def behaviors_task_records(request: Request):
    """Phase 5.3 持久意图 + 周期归档：查询任务记录。

    查询参数：``task_id``（可选）、``period_key``（可选，如 2026-09-18）。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    task_id = (request.query_params.get("task_id") or "").strip()
    period_key = (request.query_params.get("period_key") or "").strip()
    try:
        limit = int(request.query_params.get("limit") or 100)
    except (TypeError, ValueError):
        return error("limit 必须是整数")
    conn = rt.store.connect()
    sql = "SELECT * FROM task_records"
    conds, args = [], []
    if task_id:
        conds.append("task_id = ?")
        args.append(task_id)
    if period_key:
        conds.append("period_key = ?")
        args.append(period_key)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY period_key DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    with rt.store._lock:
        rows = conn.execute(sql, args).fetchall()
    import json as _json
    records = []
    for r in rows:
        d = dict(r)
        try:
            d["data"] = _json.loads(d.pop("data_json", "{}"))
        except Exception:
            d["data"] = {}
        records.append(d)
    return ok({"records": records, "count": len(records)})


async def behaviors_return_profile(request: Request):
    """Phase 5.2 家庭日常画像：每人回家时间基线 + 异常检测。

    查询参数：``person``（可选，指定某人）、``days``（默认 14）。
    不指定 person 时返回所有人的画像。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    person = (request.query_params.get("person") or "").strip()
    try:
        days = int(request.query_params.get("days") or 14)
    except (TypeError, ValueError):
        return error("days 必须是整数")
    from ..daily_profile import get_return_time_profile, list_all_return_profiles
    if person:
        profile = await asyncio.to_thread(
            get_return_time_profile, rt.store, person, max(1, min(days, 60))
        )
        return ok({"profile": profile})
    profiles = await asyncio.to_thread(
        list_all_return_profiles, rt.store, None, max(1, min(days, 60))
    )
    return ok({"profiles": profiles, "count": len(profiles)})


async def alerts_stats(request: Request):
    """Phase 4.1 告警分发统计：查看 AlertDispatcher 的单飞/合并/优先级淘汰状态。

    查询参数 ``session_id``（可选）限定某个会话（如 away_mode / vision:客厅）。
    鉴权走 require_user（WebUI JWT）。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    dispatcher = getattr(rt, "alert_dispatcher", None)
    if dispatcher is None:
        return error("alert_dispatcher 未初始化")
    session_id = (request.query_params.get("session_id") or "").strip()
    stats = dispatcher.get_stats(session_id=session_id or None)
    return ok(stats)

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
    Route("/api/behaviors/return-profile", behaviors_return_profile, methods=["GET"]),
    Route("/api/behaviors/task-records", behaviors_task_records, methods=["GET"]),
    Route("/api/behaviors/feedback-pack", behaviors_feedback_pack, methods=["POST"]),
    Route("/api/behaviors/bad-cases", behaviors_bad_cases_list, methods=["GET"]),
    Route("/api/behaviors/bad-cases/export", behaviors_bad_case_export, methods=["POST"]),
    Route("/api/behaviors/home-profile", behaviors_home_profile, methods=["GET"]),
    Route("/api/behaviors/rules", behaviors_rules, methods=["GET"]),
    Route("/api/alerts/stats", alerts_stats, methods=["GET"]),
]
