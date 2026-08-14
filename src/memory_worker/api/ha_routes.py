"""Home Assistant 相关路由：实体发现、房间启停配置"""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


def _merge_rooms(discovered: dict, configured: dict) -> dict:
    """把新发现的实体并入既有配置，保留用户已设置的启停状态。

    重新发现不应该把用户勾选过的开关全部重置 —— 这是采集配置最容易被
    悄悄破坏的地方。
    """
    merged: dict = {}
    for room, payload in discovered.items():
        old_room = configured.get(room, {}) if isinstance(configured, dict) else {}
        old_entities = old_room.get("entities", {}) if isinstance(old_room, dict) else {}
        entities = {}
        for entity_id, info in payload.get("entities", {}).items():
            old = old_entities.get(entity_id, {})
            entities[entity_id] = {
                **info,
                "enabled": bool(old.get("enabled", True)),
            }
        merged[room] = {
            "enabled": bool(old_room.get("enabled", True)),
            "entities": entities,
        }
    # 保留已配置但本次未发现的房间（HA 临时离线时不丢配置）
    for room, payload in (configured or {}).items():
        if room not in merged and isinstance(payload, dict):
            merged[room] = {**payload, "stale": True}
    return merged


async def ha_discover_entities(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    result = await asyncio.to_thread(rt.ha.discover_entities)
    if not result.get("ok"):
        return error(result.get("error", "发现实体失败"))
    merged = _merge_rooms(result.get("rooms", {}), rt.config.rooms or {})
    return ok(
        {
            "rooms": merged,
            "total_entities": result.get("total_entities", 0),
            "saved": False,
        }
    )


async def ha_rooms(request: Request):
    """返回当前已保存的房间/实体启停配置。"""
    _, err = require_user(request)
    if err:
        return err
    cfg = runtime(request).config
    rooms = cfg.rooms or {}
    enabled_entities = 0
    total_entities = 0
    for room in rooms.values():
        entities = room.get("entities", {}) if isinstance(room, dict) else {}
        total_entities += len(entities)
        if room.get("enabled", True):
            enabled_entities += sum(
                1 for e in entities.values() if e.get("enabled", True)
            )
    return ok(
        {
            "rooms": rooms,
            "excluded_entities": cfg.excluded_entities,
            "total_entities": total_entities,
            "enabled_entities": enabled_entities,
        }
    )


async def ha_save_rooms(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rooms = body.get("rooms")
    if not isinstance(rooms, dict):
        return error("rooms 必须是对象")
    rt = runtime(request)
    rt.config.rooms = rooms
    if isinstance(body.get("excluded_entities"), list):
        rt.config.excluded_entities = body["excluded_entities"]
    rt.config.save()
    rt.reload_config()
    enabled = sum(
        1
        for room in rooms.values()
        if room.get("enabled", True)
        for e in room.get("entities", {}).values()
        if e.get("enabled", True)
    )
    return ok({"message": f"已保存，启用 {enabled} 个实体", "enabled_entities": enabled})


async def ha_state(request: Request):
    _, err = require_user(request)
    if err:
        return err
    entity_id = request.query_params.get("entity_id", "")
    if not entity_id:
        return error("缺少 entity_id")
    state = await asyncio.to_thread(runtime(request).ha.get_state, entity_id)
    if state is None:
        return error("实体不存在或 HA 不可达", 404)
    return ok({"state": state})


ROUTES = [
    Route("/api/ha/discover", ha_discover_entities, methods=["GET", "POST"]),
    Route("/api/ha/rooms", ha_rooms, methods=["GET"]),
    Route("/api/ha/rooms", ha_save_rooms, methods=["POST"]),
    Route("/api/ha/state", ha_state, methods=["GET"]),
]
