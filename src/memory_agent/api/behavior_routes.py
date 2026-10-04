"""主动感知·行为推断路由（v0.9.5）。

MA 侧权威行为状态出口：``GET /api/behaviors`` 融合「身份(ma/presence) + 房间 +
canonical 活动」，供管家 v1.7 M4 状态看板消费（需加入 butler 白名单）。
另提供候选序列规则的列表 / 审核 / 导出接口——人工采纳后以结构化 JSON 交付
管家规则库（对应管家 v1.7 M5「LLM 自进化」，实现方为 MA）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from starlette.requests import Request
from starlette.routing import Route

from ..store import now_local
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
    rt = runtime(request)
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
    # DCD R3 红线"审计"：人工确认/驳回是链路上的一环，必须留痕
    if status in ("accepted", "rejected"):
        from ..rule_lifecycle import log_confirmation
        await asyncio.to_thread(
            log_confirmation, rt.store, rule_id, status, "user",
            str(body.get("reason") or ""))
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


async def negative_sample_suggestions(request: Request):
    """手动触发 vMA-1.2.1 §5.1 负样本聚类 → 候选规则建议。

    负样本口径：rejected 候选规则 + feedback_down≥1 记忆；按
    (entity_id, 时间段, 预测标签) 三元组聚类，≥min_count(默认 3) 成簇。
    红线：建议只写 staging/user_confirmed=0，永不直接影响推断。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    try:
        min_count = int(body.get("min_count") or 3)
    except (TypeError, ValueError):
        return error("min_count 必须是整数")
    from ..negative_samples import run_negative_sample_analysis
    res = await asyncio.to_thread(run_negative_sample_analysis, rt.store, max(1, min_count))
    return ok(res)


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


# ── Phase 3.1 主动规则引擎 ──────────────────────────────────────────────

async def behaviors_list_rules(request: Request):
    """列出所有主动规则。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    from ..rule_engine import get_rule_engine
    engine = get_rule_engine(rt.store, rt.alert_dispatcher)
    rules = engine.list_rules()
    return ok({"rules": rules, "count": len(rules)})


async def behaviors_add_rule(request: Request):
    """添加主动规则。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    try:
        body = await request.json()
    except Exception:
        return error("请求体必须是 JSON")
    name = body.get("name")
    condition = body.get("condition")
    action = body.get("action")
    if not name or not condition or not action:
        return error("缺少必填字段: name, condition, action")
    from ..rule_engine import get_rule_engine
    engine = get_rule_engine(rt.store, rt.alert_dispatcher)
    result = engine.add_rule(
        name=name,
        condition=condition,
        action=action,
        description=body.get("description", ""),
        enabled=body.get("enabled", True),
        cooldown_seconds=body.get("cooldown_seconds", 300),
    )
    if not result.get("ok"):
        return error(result.get("error", "添加失败"))
    return ok(result)


async def behaviors_update_rule(request: Request, rule_id: str):
    """更新主动规则。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    try:
        body = await request.json()
    except Exception:
        return error("请求体必须是 JSON")
    from ..rule_engine import get_rule_engine
    engine = get_rule_engine(rt.store, rt.alert_dispatcher)
    result = engine.update_rule(rule_id, **body)
    if not result.get("ok"):
        return error(result.get("error", "更新失败"))
    return ok(result)


async def behaviors_delete_rule(request: Request, rule_id: str):
    """删除主动规则。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    from ..rule_engine import get_rule_engine
    engine = get_rule_engine(rt.store, rt.alert_dispatcher)
    result = engine.delete_rule(rule_id)
    if not result.get("ok"):
        return error(result.get("error", "删除失败"))
    return ok(result)


# ── DCD R3 生效通道：accepted → active_rules（试运行 → 转正 / 撤销）─────────

def _lifecycle(rt):
    from ..rule_engine import get_rule_engine
    from ..rule_lifecycle import get_rule_lifecycle
    return get_rule_lifecycle(rt.store, get_rule_engine(rt.store, rt.alert_dispatcher))


async def rule_channel_status(request: Request):
    """通道全景：试运行中 / 已转正 / 已撤销的晋升规则与各自观察读数。"""
    _, err = require_user(request)
    if err:
        return err
    return ok(await asyncio.to_thread(_lifecycle(runtime(request)).channel_status))


async def rule_channel_pending(request: Request):
    """accepted 候选的晋升预演清单（只读，逐条给四红线判据）。"""
    _, err = require_user(request)
    if err:
        return err
    rows = await asyncio.to_thread(_lifecycle(runtime(request)).pending_promotions)
    return ok({"items": rows, "count": len(rows)})


async def rule_channel_eligibility(request: Request):
    """单条候选的门槛判定（只读）。"""
    _, err = require_user(request)
    if err:
        return err
    candidate_id = (request.query_params.get("candidate_id") or "").strip()
    if not candidate_id:
        return error("缺少 candidate_id")
    return ok(await asyncio.to_thread(
        _lifecycle(runtime(request)).eligibility, candidate_id))


async def rule_channel_promote(request: Request):
    """晋升一条 accepted 候选进引擎（一律先落 dry_run）。

    ``cooldown_seconds``（DCD 20261004 MA-裁1 Q1）是这条规则对外的吵人上限：
    这里不传就用候选行里已设定的值，两者都没有则 409 拒绝——不替调用方默认一个数。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    candidate_id = (body.get("candidate_id") or "").strip()
    if not candidate_id:
        return error("缺少 candidate_id")
    res = await asyncio.to_thread(
        _lifecycle(rt).promote, candidate_id, "user",
        str(body.get("reason") or ""), body.get("cooldown_seconds"))
    if not res.get("ok"):
        return error(res.get("error", "晋升失败"), 409)
    return ok(res)


async def rule_channel_advance(request: Request):
    """试运行满观察期且零误报 → 转正为 live。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    rule_id = (body.get("rule_id") or "").strip()
    if not rule_id:
        return error("缺少 rule_id")
    res = await asyncio.to_thread(
        _lifecycle(rt).advance_to_live, rule_id, "user", str(body.get("reason") or ""))
    if not res.get("ok"):
        return error(res.get("error", "转正失败"), 409)
    return ok(res)


async def rule_channel_revoke(request: Request):
    """撤销生效规则，并连同它产生的推断一起回滚。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    rule_id = (body.get("rule_id") or "").strip()
    if not rule_id:
        return error("缺少 rule_id")
    res = await asyncio.to_thread(
        _lifecycle(rt).revoke, rule_id, "user", str(body.get("reason") or ""),
        bool(body.get("rollback_inferences", True)))
    if not res.get("ok"):
        return error(res.get("error", "撤销失败"), 409)
    return ok(res)


async def rule_channel_false_positive(request: Request):
    """把一条触发记录判为误报（观察期的红判据）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    rule_id = (body.get("rule_id") or "").strip()
    trigger_id = body.get("trigger_id")
    if not rule_id or trigger_id in (None, ""):
        return error("缺少 rule_id / trigger_id")
    res = await asyncio.to_thread(
        _lifecycle(rt).flag_false_positive, rule_id, int(trigger_id), "user",
        str(body.get("reason") or ""))
    if not res.get("ok"):
        return error(res.get("error", "标记失败"), 404)
    return ok(res)


async def rule_channel_audit(request: Request):
    """规则生命周期审计（全链路留痕）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    rule_id = (request.query_params.get("rule_id") or "").strip()
    try:
        limit = max(1, min(500, int(request.query_params.get("limit") or "100")))
    except ValueError:
        limit = 100
    rows = await asyncio.to_thread(rt.store.list_rule_lifecycle, rule_id, limit)
    return ok({"items": rows, "count": len(rows)})


def _label_rosters(rt) -> tuple[list, list]:
    """label 白名单要的两份名册：房间名（config 的区域 ∪ 库里出现过的房间）+ 成员姓名。

    同步函数，调用点用 ``asyncio.to_thread`` 包住（后两句要碰 SQLite）。
    这里**不吞异常**：``insights.room_names`` 自身已带降级（读失败回空表），剩下的
    ``distinct_rooms`` / ``list_members`` 一旦出错就该让请求报错——拿着空名册继续走，
    等于在"认不出姓名"的状态下把校验放行。
    """
    rooms = list(rt.insights.room_names() or []) + list(rt.store.distinct_rooms() or [])
    names = [m.get("name") or "" for m in rt.store.list_members()]
    return sorted({r for r in rooms if r}, key=len, reverse=True), [n for n in names if n]


async def behaviors_feedback_pack(request: Request):
    """Phase 4.3 反馈闭环：导出 VLM 误识别 bad-case 包（tar.gz）。

    Body: ``snapshot_path``（可选，VLM 快照路径）、``trace``（可选，trace 文本）、
    ``label``（可选，默认 bad_case）。
    返回打包后的文件路径。脱敏失败宁可丢 trace（fail-closed）。

    DCD 20261004 MA-裁3（分层）：包里同时有 ``trace.txt``（S3 出境口径，姓名可读）
    与 ``trace_anon.txt``（再叠 S1 入库口径，姓名→成员N），取哪份出境由出境动作决定；
    ``label`` 会成为文件名，因此走白名单校验（姓名进不来）。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    from ..feedback_pack import build_feedback_pack, validate_label
    rt = runtime(request)
    output_dir = "/data/feedback_packs"
    os.makedirs(output_dir, exist_ok=True)
    label = body.get("label", "bad_case")
    try:
        rooms, names = await asyncio.to_thread(_label_rosters, rt)
    except Exception as exc:
        # 名册读不到就拒绝导出，而不是拿着空名册悄悄放行校验：缺的那一份恰好是成员名册，
        # 而这一层要保证的正是「姓名不会长在文件名上」。
        return error(f"label 名册读取失败，已拒绝导出：{exc}")
    passed, reason, _safe = validate_label(label, known_rooms=rooms, member_names=names)
    if not passed:
        return error(f"label 不符合出境面白名单：{reason}")
    try:
        result = await asyncio.to_thread(
            build_feedback_pack,
            snapshot_path=body.get("snapshot_path", ""),
            trace=body.get("trace", ""),
            output_dir=output_dir,
            label=label,
            known_rooms=rooms,
            member_names=names,
            anon_sanitizer=rt.store.sanitize_feedback_text,
        )
    except Exception as exc:
        return error(f"反馈包打包异常：{exc}")
    if result is None:
        return error("反馈包打包失败（label 不过白名单/脱敏异常，已 fail-closed）")
    size = os.path.getsize(result) if os.path.exists(result) else 0
    return ok({"path": result, "size_bytes": size, "label": label})


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
        events = await asyncio.to_thread(
            rt.store.list_behavior_events,
            room=room or None, status="vlm_failed", limit=limit,
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

    DCD 20261004 MA-裁3：与 ``/feedback-pack`` 同一口径——包里两份 trace（出境/入库），
    label 过白名单（默认值 ``bad_case_{event_id}`` 本身就在白名单结构内）。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    event_id = body.get("event_id")
    if not event_id:
        return error("缺少 event_id")
    rt = runtime(request)
    from ..feedback_pack import build_feedback_pack, validate_label
    output_dir = "/data/feedback_packs"
    os.makedirs(output_dir, exist_ok=True)
    # 从数据库读取事件
    def _find_event():
        event = None
        if hasattr(rt.store, "get_behavior_event"):
            event = rt.store.get_behavior_event(event_id)
        if event is not None:
            return event
        # 回退：从最近事件中找
        for e in rt.store.list_behavior_events(limit=500):
            if str(e.get("id")) == str(event_id):
                return e
        return None

    try:
        event = await asyncio.to_thread(_find_event)
    except Exception as exc:
        return error(f"查询事件失败: {exc}")
    if event is None:
        return error(f"事件 {event_id} 不存在")
    label = body.get("label") or f"bad_case_{event_id}"
    try:
        rooms, names = await asyncio.to_thread(_label_rosters, rt)
    except Exception as exc:
        return error(f"label 名册读取失败，已拒绝导出：{exc}")
    # 事件自己所在的房间一定算"已知房间"：`distinct_rooms` 读的是 events 表，
    # 只出现在 behavior_events 里的房间不该因为名册少一行就过不了白名单。
    event_room = str(event.get("room") or "").strip()
    rooms = sorted({r for r in rooms if r} | ({event_room} if event_room else set()),
                   key=len, reverse=True)
    passed, reason, _safe = validate_label(label, known_rooms=rooms, member_names=names)
    if not passed:
        return error(f"label 不符合出境面白名单：{reason}")
    result = await asyncio.to_thread(
        build_feedback_pack,
        snapshot_path=event.get("snapshot_path", ""),
        trace=event.get("raw_response", ""),
        output_dir=output_dir,
        label=label,
        known_rooms=rooms,
        member_names=names,
        anon_sanitizer=rt.store.sanitize_feedback_text,
    )
    if result is None:
        return error("反馈包打包失败（label 不过白名单/脱敏异常，已 fail-closed）")
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
    def _fetch_rows():
        conn = rt.store.connect()
        with rt.store._lock:
            return conn.execute(sql, args).fetchall()

    rows = await asyncio.to_thread(_fetch_rows)
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


async def behaviors_predictions(request: Request):
    """P4a 行为预测：基于历史事件预测家人的行为模式。

    查询参数：
    - person: 人名（如 "Kevin"），必填
    - weekday: 0=周一, 6=周日，可选（默认所有日期）
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    person = (request.query_params.get("person") or "").strip()
    if not person:
        return error("缺少 person 参数")
    weekday_str = (request.query_params.get("weekday") or "").strip()
    weekday = None
    if weekday_str:
        try:
            weekday = int(weekday_str)
            if not (0 <= weekday <= 6):
                return error("weekday 必须是 0-6（周一到周日）")
        except ValueError:
            return error("weekday 必须是整数")

    events = await asyncio.to_thread(rt.store.list_behavior_events, limit=5000)
    if not events:
        return ok({"person": person, "predictions": None, "note": "无历史事件数据"})

    from ..behavior_predictor import predict_daily_routine, predict_arrival_time

    arrival = predict_arrival_time(events, person, weekday=weekday,
                                   tz_offset_hours=rt.config.tz_offset_hours)
    routine = predict_daily_routine(events, person, tz_offset_hours=rt.config.tz_offset_hours)

    return ok({
        "person": person,
        "weekday": weekday,
        "arrival_prediction": arrival,
        "daily_routine": routine,
    })


async def behaviors_intent(request: Request):
    """P4b 意图推断：从最近的行为事件推断用户意图。

    查询参数：
    - person: 人名（可选，限定某人）
    - limit: 返回意图数量（默认 3）
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    person = (request.query_params.get("person") or "").strip() or None
    try:
        limit = int(request.query_params.get("limit") or 3)
    except (TypeError, ValueError):
        limit = 3

    events = await asyncio.to_thread(rt.store.list_behavior_events, limit=5000)
    if not events:
        return ok({"intents": [], "note": "无历史行为事件数据"})

    from ..intent_inference import infer_intent_sequence, get_intent_suggestions

    intents = infer_intent_sequence(events, person=person, max_intents=max(1, min(limit, 5)))
    for intent in intents:
        intent["suggestions"] = get_intent_suggestions(intent)

    return ok({"intents": intents, "count": len(intents)})


async def behaviors_intent_execute(request: Request):
    """P4c 意图→动作执行：推断意图后执行（或预览）建议动作。

    Body: {"intent": "watch_tv", "dry_run": true, "person": "Kevin"}
    - dry_run=true（默认）：只预览不执行
    - dry_run=false：执行 auto=true 的动作，auto=false 的动作需要用户确认
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    intent = (body.get("intent") or "").strip()
    if not intent:
        return error("intent 必填")
    dry_run = body.get("dry_run", True)
    person = (body.get("person") or "").strip() or None

    from ..intent_action import execute_intent_actions

    result = await execute_intent_actions(intent, rt, dry_run=dry_run, person=person)
    return ok(result)



async def causal_analyze(request: Request):
    """行为变化因果归因（P5a+P5b）：GET /api/behaviors/causal/analyze?person=X&metric=Y&days=30"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    person = (request.query_params.get("person") or "").strip()
    metric = (request.query_params.get("metric") or "arrival_time").strip()
    room = (request.query_params.get("room") or "").strip()
    try:
        days = int(request.query_params.get("days") or 30)
        lookback = int(request.query_params.get("lookback_days") or 7)
    except (TypeError, ValueError):
        return error("days / lookback_days 必须是整数")
    if not person:
        return error("person 必填")
    from ..mcp_server import _fetch_attribution_events
    from ..change_attribution import attribute_with_conditional
    events = await asyncio.to_thread(_fetch_attribution_events, rt.store, max(14, days))
    if len(events) < 14:
        return error(f"事件数据不足（{len(events)} 条 < 14 天最低要求）")
    result = await asyncio.to_thread(
        attribute_with_conditional, events, person, metric,
        0.5, lookback, max(30, days), room or None
    )
    return ok({"person": person, "metric": metric, "event_count": len(events), **result})


async def causal_counterfactual(request: Request):
    """反事实查询（P5c）：GET /api/behaviors/causal/counterfactual?person=X&event_type=Y&metric=Z&days=30"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    person = (request.query_params.get("person") or "").strip()
    event_type = (request.query_params.get("event_type") or "").strip()
    metric = (request.query_params.get("metric") or "arrival_time").strip()
    room = (request.query_params.get("room") or "").strip()
    try:
        days = int(request.query_params.get("days") or 30)
    except (TypeError, ValueError):
        return error("days 必须是整数")
    if not person or not event_type:
        return error("person 和 event_type 必填")
    from ..mcp_server import _fetch_attribution_events
    from ..change_attribution import counterfactual_query as _cfq
    events = await asyncio.to_thread(_fetch_attribution_events, rt.store, max(14, days))
    if len(events) < 14:
        return error(f"事件数据不足（{len(events)} 条 < 14 天最低要求）")
    # 与事件行的墙钟口径一致（counterfactual 拿 change_ts 与 server_ts 比较）
    change_ts = (now_local(rt.config.tz_offset_hours)
                 - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    result = await asyncio.to_thread(
        _cfq, events, person, metric, event_type, change_ts, max(30, days), room or None
    )
    return ok(result)


ROUTES = [
    Route("/api/behaviors", behaviors_current, methods=["GET"]),
    Route("/api/behaviors/states", behaviors_states, methods=["GET"]),
    Route("/api/behaviors/run", behaviors_run, methods=["POST"]),
    Route("/api/behaviors/predictions", behaviors_predictions, methods=["GET"]),
    Route("/api/behaviors/intent", behaviors_intent, methods=["GET"]),
    Route("/api/behaviors/intent/execute", behaviors_intent_execute, methods=["POST"]),
    Route("/api/behaviors/candidate-rules", candidate_rules_list, methods=["GET"]),
    Route("/api/behaviors/candidate-rules/update", candidate_rule_update, methods=["POST"]),
    Route("/api/behaviors/candidate-rules/export", candidate_rules_export, methods=["GET"]),
    Route("/api/behaviors/negative-samples/suggestions", negative_sample_suggestions, methods=["POST"]),
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
    Route("/api/behaviors/rule-channel", rule_channel_status, methods=["GET"]),
    Route("/api/behaviors/rule-channel/pending", rule_channel_pending, methods=["GET"]),
    Route("/api/behaviors/rule-channel/eligibility", rule_channel_eligibility, methods=["GET"]),
    Route("/api/behaviors/rule-channel/promote", rule_channel_promote, methods=["POST"]),
    Route("/api/behaviors/rule-channel/advance", rule_channel_advance, methods=["POST"]),
    Route("/api/behaviors/rule-channel/revoke", rule_channel_revoke, methods=["POST"]),
    Route("/api/behaviors/rule-channel/false-positive", rule_channel_false_positive, methods=["POST"]),
    Route("/api/behaviors/rule-channel/audit", rule_channel_audit, methods=["GET"]),
    Route("/api/alerts/stats", alerts_stats, methods=["GET"]),
    Route("/api/behaviors/causal/analyze", causal_analyze, methods=["GET"]),
    Route("/api/behaviors/causal/counterfactual", causal_counterfactual, methods=["GET"]),
]
