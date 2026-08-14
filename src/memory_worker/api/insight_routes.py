"""行为洞察（模板）路由

``GET /api/templates`` 保持重构前的「裸数组」响应契约不变；
新 WebUI 使用 ``/api/insights`` 系列获取完整字段。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..templates import BehaviorInsight, EntityQuery
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
    """兼容端点：返回裸数组。"""
    category = request.query_params.get("category")
    templates = runtime(request).templates.list_all()
    if category:
        templates = [t for t in templates if t.category == category]
    return JSONResponse([_brief(t) for t in templates])


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
    for t in templates:
        data = t.to_dict()
        data["builtin"] = rt.templates.is_builtin(t.id)
        items.append(data)
    categories = sorted({t.category for t in rt.templates.list_all() if t.category})
    return ok({"templates": items, "total": len(items), "categories": categories})


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


ROUTES = [
    Route("/api/templates", template_list, methods=["GET"]),
    Route("/api/templates", template_save, methods=["POST"]),
    Route("/api/templates/delete", template_delete, methods=["POST"]),
    Route("/api/templates/export", template_export, methods=["POST"]),
    Route("/api/insights", insight_list, methods=["GET"]),
]
