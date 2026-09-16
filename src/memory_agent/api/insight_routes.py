"""行为洞察（模板）路由

``GET /api/templates`` 保持重构前的「裸数组」响应契约不变；
新 WebUI 使用 ``/api/insights`` 系列获取完整字段。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..template_validate import (
    apply_fix,
    check_executable,
    get_cached,
    set_disabled,
    validate_all,
    validate_template,
)
from ..templates import BehaviorInsight, EntityQuery, run_query, run_template
from ..store import now_local
from .deps import error, json_body, ok, require_user, runtime


def _brief(t: BehaviorInsight) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "category": t.category,
        "confidence": t.confidence,
        "sample_days": t.sample_days,
        "entities_count": len(t.entities),
    }


def build_insight(body: dict) -> BehaviorInsight:
    """从请求体构造 BehaviorInsight，缺失字段给出安全默认值。"""
    entities = []
    for e in body.get("entities", []) or []:
        if not isinstance(e, dict) or not e.get("entity_id"):
            continue
        entities.append(
            EntityQuery(
                entity_id=e["entity_id"],
                attribute=e.get("attribute", "state"),
                pattern=e.get("pattern", "equals"),
                value=e.get("value", ""),
                time_range=e.get("time_range", ""),
                metric=e.get("metric", "duration"),
            )
        )
    return BehaviorInsight(
        id=body["id"],
        name=body["name"],
        description=body.get("description", ""),
        category=body.get("category", "other"),
        entities=entities,
        pattern=body.get("pattern", ""),
        confidence=float(body.get("confidence", 0.0) or 0.0),
        sample_days=int(body.get("sample_days", 0) or 0),
        nr_condition=body.get("nr_condition", ""),
        nr_action=body.get("nr_action", ""),
        default_days=int(body.get("default_days", 0) or 0),
        interpretation=body.get("interpretation", ""),
    )


async def template_list(request: Request):
    """兼容端点：返回裸数组（契约不变，仅新增校验状态字段）。"""
    rt = runtime(request)
    category = request.query_params.get("category")
    templates = rt.templates.list_all()
    if category:
        templates = [t for t in templates if t.category == category]
    out = []
    for t in templates:
        item = _brief(t)
        cached = get_cached(rt, t.id) or {}
        item["validation_status"] = cached.get("status")
        item["disabled"] = cached.get("status") == "disabled"
        out.append(item)
    return JSONResponse(out)


async def insight_list(request: Request):
    rt = runtime(request)
    category = request.query_params.get("category")
    keyword = (request.query_params.get("q") or "").strip().lower()
    templates = rt.templates.list_all()
    if category and category != "all":
        templates = [t for t in templates if t.category == category]
    if keyword:
        templates = [
            t
            for t in templates
            if keyword in t.name.lower()
            or keyword in t.description.lower()
            or keyword in t.id.lower()
        ]
    items = []
    summary: dict[str, int] = {}
    for t in templates:
        data = t.to_dict()
        data["builtin"] = rt.templates.is_builtin(t.id)
        cached = get_cached(rt, t.id) or {}
        data["validation"] = cached or None
        data["validation_status"] = cached.get("status")
        data["disabled"] = cached.get("status") == "disabled"
        items.append(data)
        if cached.get("status"):
            summary[cached["status"]] = summary.get(cached["status"], 0) + 1
    categories = sorted({t.category for t in rt.templates.list_all() if t.category})
    return ok({"templates": items, "total": len(items),
               "categories": categories, "validation_summary": summary})


async def member_schedule(request: Request):
    """成员作息实测摘要（豆包管家需求 3，可选能力）。

    ``GET /api/insights/member-schedule?name=Kevin&days=14[&start=YYYY-MM-DD][&end=YYYY-MM-DD]``

    输出成员每天首次/末次被识别到的时间，管家可用实测数据校准作息档
    （例如发现实际常 19:30 到家，就把问候基准调早）。还返回 ``segments``：按该成员
    习惯记忆（``habit:`` 前缀、带 valid_from/valid_to）的有效窗口分段，给出每段中位
    作息，实现「作息演变可回溯」（任务 1 时间维度）。传 ``start``/``end`` 可拉取历史窗口。
    ``appearances`` 是当天命中该成员的事件条数，受巡检频率与冷却限制，
    **不等于真实进出次数**，只作趋势参考。
    """
    params = request.query_params
    name = (params.get("name") or "").strip()
    if not name:
        return error("缺少成员姓名 name")
    try:
        days = int(params.get("days") or 14)
    except (TypeError, ValueError):
        return error("days 必须是整数")
    days = max(1, min(days, 90))
    start = (params.get("start") or "").strip() or None
    end = (params.get("end") or "").strip() or None
    for _v, _lbl in ((start, "start"), (end, "end")):
        if _v:
            try:
                datetime.strptime(_v, "%Y-%m-%d")
            except Exception:
                return error(f"{_lbl} 必须是 YYYY-MM-DD")
    rt = runtime(request)
    data = await asyncio.to_thread(rt.store.member_schedule, name, days, start, end)
    if not data.get("samples"):
        data["hint"] = f"指定窗口内没有 {name} 的识别记录（可能未在成员库或摄像头未覆盖）"
    return ok(data)


async def template_save(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    if not body.get("id") or not body.get("name"):
        return error("缺少 id 或 name")
    try:
        insight = build_insight(body)
    except Exception as exc:
        return error(f"模板字段非法: {exc}")
    saved = runtime(request).templates.save(insight)
    return ok({"message": f"模板已保存: {saved.name}", "id": saved.id})


async def template_delete(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    template_id = body.get("template_id") or body.get("id") or ""
    if not template_id:
        return error("缺少 template_id")
    if not runtime(request).templates.delete(template_id):
        return error("模板不存在或为内置模板，无法删除", 404)
    return ok({"message": f"模板已删除: {template_id}"})


async def template_export(request: Request):
    body = await json_body(request)
    template_id = body.get("template_id") or request.query_params.get("template_id") or ""
    insight = runtime(request).templates.export_insight(template_id)
    if not insight:
        return error("模板不存在", 404)
    return JSONResponse(insight)


async def insight_query(request: Request):
    """结构化洞察查询（v0.3 对外接口，供 TVPilot / DeskPilot 免 MCP 拉数据）。

    两种模式二选一：

    1. ``template_id``：按已保存模板计算（语义最稳定，推荐）
       ``{"template_id": "xbox_daily_usage", "start": "...", "end": "..."}``
    2. ``logical_id`` / ``entity_id``：即时查询单个设备
       ``{"logical_id": "客厅电视", "attribute": "source", "value": "HDMI 3",
          "metric": "duration", "days": 2}``

    鉴权：WebUI 的 JWT，或独立 ``app_token``（仅放行本路径，见 APP_ENDPOINTS）。
    """
    body = await json_body(request)
    if not isinstance(body, dict):
        body = {}
    rt = runtime(request)
    template_id = (body.get("template_id") or "").strip()

    if template_id:
        # 执行闸门（v0.7）：失效模板硬阻止，避免静默返回误导性的 0
        gate = await asyncio.to_thread(check_executable, rt, template_id)
        if not gate.get("allowed"):
            return error(
                f"模板已失效（{gate.get('status')}）："
                f"{gate.get('reason') or '引用实体不可用'}",
                409,
            )
        res = await asyncio.to_thread(
            run_template,
            rt,
            template_id,
            int(body.get("days") or 0),
            (body.get("start") or "").strip(),
            (body.get("end") or "").strip(),
            bool(body.get("include_timeline", True)),
        )
        if not res.get("ok"):
            return error(res.get("error") or "查询失败", 404)
        if gate.get("warning"):
            res["warning"] = gate["warning"]
        return ok(res)

    res = await asyncio.to_thread(
        run_query,
        rt,
        entity_id=(body.get("entity_id") or "").strip(),
        logical_id=(body.get("logical_id") or body.get("logical_device") or "").strip(),
        attribute=body.get("attribute") or "state",
        pattern=body.get("pattern") or "equals",
        value=body.get("value") or "",
        metric=body.get("metric") or "duration",
        days=int(body.get("days") or 7),
        start=(body.get("start") or "").strip(),
        end=(body.get("end") or "").strip(),
        include_timeline=bool(body.get("include_timeline", True)),
    )
    if not res.get("ok"):
        return error(res.get("error") or "查询失败")
    return ok(res)


async def template_validate_api(request: Request):
    """校验模板：不传 template_id 则全量校验（供 WebUI「重新校验」）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    body = body if isinstance(body, dict) else {}
    rt = runtime(request)
    template_id = (body.get("template_id") or "").strip()
    if template_id:
        tpl = rt.templates.get(template_id)
        if tpl is None:
            return error(f"模板不存在: {template_id}", 404)
        return ok(await asyncio.to_thread(validate_template, rt, tpl))
    return ok(await asyncio.to_thread(validate_all, rt, True))


async def template_disable(request: Request):
    """停用/恢复模板（内置模板也可停用，仅影响列表与调用过滤）。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    template_id = (body.get("template_id") or "").strip()
    if not template_id:
        return error("缺少 template_id")
    res = await asyncio.to_thread(
        set_disabled, runtime(request), template_id, bool(body.get("disabled", True))
    )
    if not res.get("ok"):
        return error(res.get("error") or "操作失败", res.get("code", 400))
    return ok(res)


async def template_fix(request: Request):
    """应用修复建议（换用可用实体）；auto=true 时采用首选高置信候选。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    template_id = (body.get("template_id") or "").strip()
    if not template_id:
        return error("缺少 template_id")
    try:
        entity_index = int(body.get("entity_index"))
    except (TypeError, ValueError):
        return error("entity_index 必须是整数")
    res = await asyncio.to_thread(
        apply_fix,
        runtime(request),
        template_id,
        entity_index,
        (body.get("new_ref") or "").strip(),
        bool(body.get("auto", False)),
    )
    if not res.get("ok"):
        return error(res.get("error") or "修复失败", res.get("code", 400))
    return ok(res)


# ── 记忆研究员（v0.8 定向洞察）─────────────────────────────────────
async def researcher_job_list(request: Request):
    """列出定向洞察任务（Insight Job）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    jobs = rt.store.list_insight_jobs()
    return ok({"jobs": jobs, "gates": {
        "daily_token_budget": rt.researcher.gates.daily_token_budget,
        "unit_cap": rt.researcher.gates.unit_cap,
        "enabled": rt.researcher.gates.enabled,
    }})


async def researcher_job_save(request: Request):
    """新增/更新一个定向洞察任务。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    if not (body.get("name") or "").strip():
        return error("name 必填")
    rt = runtime(request)
    payload = rt.store.save_insight_job(body)
    return ok(payload)


async def researcher_job_delete(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    job_id = (body.get("job_id") or "").strip()
    if not job_id:
        return error("job_id 必填")
    rt = runtime(request)
    rt.store.delete_insight_job(job_id)
    return ok({"job_id": job_id})


async def researcher_job_toggle(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    job_id = (body.get("job_id") or "").strip()
    enabled = bool(body.get("enabled"))
    if not job_id:
        return error("job_id 必填")
    rt = runtime(request)
    rt.store.set_insight_job_enabled(job_id, enabled)
    return ok({"job_id": job_id, "enabled": enabled})


async def researcher_run_now(request: Request):
    """立即运行（绕过总开关，但受日预算约束）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    job_id = (body.get("job_id") or "").strip()
    today = now_local(rt.config.tz_offset_hours).strftime("%Y-%m-%d")
    if job_id:
        job = rt.store.get_insight_job(job_id)
        if not job:
            return error("任务不存在")
        asyncio.create_task(rt.researcher.run_job(job, {
            "budget_left": rt.researcher.gates.daily_token_budget - rt.store.researcher_daily_token_used(today),
            "date": today,
        }))
    else:
        asyncio.create_task(rt.researcher.run_all(force=True))
    return ok({"triggered": True})


async def researcher_runs_list(request: Request):
    """查询运行/成本记录。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    limit = int(request.query_params.get("limit", 50) or 50)
    job_id = (request.query_params.get("job_id") or "").strip()
    runs = rt.store.list_researcher_runs(job_id=job_id or None, limit=limit)
    return ok({"runs": runs})


async def researcher_direction_feedback(request: Request):
    """v0.8-3 按方向聚合研究员洞察的 👍/👎（方向/模板质量反哺）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    return ok({"directions": rt.store.researcher_direction_feedback()})


ROUTES = [
    Route("/api/researcher/jobs", researcher_job_list, methods=["GET"]),
    Route("/api/researcher/jobs", researcher_job_save, methods=["POST"]),
    Route("/api/researcher/jobs/delete", researcher_job_delete, methods=["POST"]),
    Route("/api/researcher/jobs/toggle", researcher_job_toggle, methods=["POST"]),
    Route("/api/researcher/run", researcher_run_now, methods=["POST"]),
    Route("/api/researcher/runs", researcher_runs_list, methods=["GET"]),
    Route("/api/researcher/direction-feedback", researcher_direction_feedback, methods=["GET"]),
    Route("/api/templates", template_list, methods=["GET"]),
    Route("/api/templates", template_save, methods=["POST"]),
    Route("/api/templates/delete", template_delete, methods=["POST"]),
    Route("/api/templates/export", template_export, methods=["POST"]),
    Route("/api/insights", insight_list, methods=["GET"]),
    Route("/api/insights/member-schedule", member_schedule, methods=["GET"]),
    # v0.7 模板校验 / 停用 / 修复
    Route("/api/templates/validate", template_validate_api, methods=["POST"]),
    Route("/api/templates/disable", template_disable, methods=["POST"]),
    Route("/api/templates/fix", template_fix, methods=["POST"]),
    # v0.3 对外查询接口（TVPilot / DeskPilot 走 app_token）
    Route("/api/insights/query", insight_query, methods=["POST"]),
]
