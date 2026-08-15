"""Node-RED 兼容层

⚠️ 回归红线
-----------
``nodered/water_purifier_flow.json`` 是线上运行流，其 HTTP 节点固定为
``POST http://<host>/api/analyze/water_purifier`` + Basic Auth。
本文件内所有端点的**路径与响应结构一律不得变更**，只允许修内部 Bug。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime

WATER_PURIFIER_ENTITY = "event.chunmi_cn_334432105_600f2_water_out_finish_e_7_1"


def _patterns_or_error(request: Request):
    pm = runtime(request).patterns
    if pm is None:
        return None, error("行为模式库（向量库）不可用", 503)
    return pm, None


async def nr_match_pattern(request: Request):
    body = await json_body(request)
    pm, err = _patterns_or_error(request)
    if err:
        return err
    entity_id = body.get("entity_id", "")
    result = await asyncio.to_thread(pm.list_patterns)
    # list_patterns() 返回的是 {"patterns": [...], "total": n}。
    # 重构前直接把它当 list 遍历，实际遍历到的是 key 字符串，必然 AttributeError。
    patterns = result.get("patterns", []) if isinstance(result, dict) else (result or [])
    matched = [
        p
        for p in patterns
        if isinstance(p, dict)
        and entity_id
        and entity_id in json.dumps(p.get("entities", {}), ensure_ascii=False)
    ]
    return ok({"patterns": matched})


async def nr_execute_action(request: Request):
    body = await json_body(request)
    action_type = body.get("type", "")
    target = body.get("target", "")
    return ok({"message": f"动作已执行: {action_type} -> {target}"})


async def nr_record_feedback(request: Request):
    body = await json_body(request)
    pattern_id = body.get("pattern_id", "")
    feedback = body.get("feedback", "")
    pm = runtime(request).patterns
    if pm is not None and pattern_id:
        try:
            await asyncio.to_thread(pm.record_feedback, pattern_id, feedback)
        except Exception as exc:
            print(f"[NR] 记录反馈失败: {exc}")
    return ok({"message": f"反馈已记录: {pattern_id} = {feedback}"})


async def nr_get_pending_patterns(request: Request):
    pm, err = _patterns_or_error(request)
    if err:
        return err
    result = await asyncio.to_thread(pm.list_patterns, None, None, "pending")
    patterns = result.get("patterns", []) if isinstance(result, dict) else (result or [])
    pending = [p for p in patterns if isinstance(p, dict) and p.get("status") == "pending"]
    return ok({"patterns": pending})


async def analyze_water_purifier(request: Request):
    """净水器出水统计。响应结构被 Node-RED 流直接消费，不可改。"""
    _, err = require_user(request)
    if err:
        return err
    try:
        rt = runtime(request)
        start = (datetime.now() - timedelta(days=7)).isoformat()
        history_dict = await asyncio.to_thread(
            rt.ha.get_history, [WATER_PURIFIER_ENTITY], start
        )
        records = (
            history_dict.get(WATER_PURIFIER_ENTITY, [])
            if isinstance(history_dict, dict)
            else []
        )
        if not records:
            return ok({"message": "无历史数据", "data": []})

        daily_stats: dict[str, dict] = {}
        for record in records:
            if record.get("state") != "on":
                continue
            attrs = record.get("attributes", {}) or {}
            out_data = attrs.get("out_data", "")
            parts = str(out_data).split("-")
            if len(parts) < 4:
                continue
            try:
                volume_ml = int(parts[2]) if parts[2] else 0
                tds_parts = parts[3].split(",") if parts[3] else []
                tds_in = int(tds_parts[0]) if len(tds_parts) > 0 and tds_parts[0] else 0
                tds_out = int(tds_parts[1]) if len(tds_parts) > 1 and tds_parts[1] else 0
                ts = datetime.fromisoformat(
                    str(record["last_changed"]).replace("Z", "+00:00")
                )
            except (ValueError, KeyError, IndexError):
                continue  # 单条脏数据不应让整个统计失败
            date_key = ts.strftime("%Y-%m-%d")
            stats = daily_stats.setdefault(
                date_key,
                {
                    "date": date_key,
                    "count": 0,
                    "total_volume_ml": 0,
                    "tds_in_sum": 0,
                    "tds_out_sum": 0,
                },
            )
            stats["count"] += 1
            stats["total_volume_ml"] += volume_ml
            stats["tds_in_sum"] += tds_in
            stats["tds_out_sum"] += tds_out

        result = []
        for date_key in sorted(daily_stats.keys()):
            stats = daily_stats[date_key]
            count = stats["count"]
            result.append(
                {
                    "date": stats["date"],
                    "count": count,
                    "total_volume_l": round(stats["total_volume_ml"] / 1000, 2),
                    "avg_tds_in": round(stats["tds_in_sum"] / count) if count > 0 else 0,
                    "avg_tds_out": round(stats["tds_out_sum"] / count) if count > 0 else 0,
                    "tds_reduction_pct": round(
                        (1 - stats["tds_out_sum"] / stats["tds_in_sum"]) * 100, 1
                    )
                    if stats["tds_in_sum"] > 0
                    else 0,
                }
            )
        return ok({"message": "分析完成", "data": result})
    except Exception as exc:
        return error(str(exc), 500)


ROUTES = [
    Route("/api/nr/match-pattern", nr_match_pattern, methods=["POST"]),
    Route("/api/nr/execute-action", nr_execute_action, methods=["POST"]),
    Route("/api/nr/record-feedback", nr_record_feedback, methods=["POST"]),
    Route("/api/nr/pending-patterns", nr_get_pending_patterns, methods=["GET"]),
    Route("/api/analyze/water_purifier", analyze_water_purifier, methods=["POST", "GET"]),
]
