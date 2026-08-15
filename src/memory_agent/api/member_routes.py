"""家庭成员（生活习惯档案）路由

成员 CRUD、关联房间/设备、生活习惯标签写回。
所有写操作需登录（``require_user``）；读操作对 WebUI 开放。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


async def member_list(request: Request):
    """成员列表（含 rooms / devices / tags 聚合）。"""
    members = runtime(request).store.list_members()
    return ok({"members": members, "total": len(members)})


async def member_create(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    if not name:
        return error("缺少成员姓名 name")
    member = runtime(request).store.create_member(
        name=name,
        avatar_emoji=body.get("avatar_emoji", "") or "",
        avatar_bg=body.get("avatar_bg", "") or "#0EA5E9",
        avatar_url=body.get("avatar_url", "") or "",
        note=body.get("note", "") or "",
    )
    return ok({"message": f"成员已创建: {member['name']}", "member": member})


async def member_detail(request: Request):
    member_id = request.path_params.get("member_id", "")
    member = runtime(request).store.get_member(member_id)
    if not member:
        return error("成员不存在", 404)
    return ok({"member": member})


async def member_update(request: Request):
    _, err = require_user(request)
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    body = await json_body(request)
    member = runtime(request).store.update_member(member_id, **body)
    if not member:
        return error("成员不存在", 404)
    return ok({"message": "成员已更新", "member": member})


async def member_delete(request: Request):
    _, err = require_user(request)
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    runtime(request).store.delete_member(member_id)
    return ok({"message": "成员已删除"})


async def member_rooms(request: Request):
    _, err = require_user(request)
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    body = await json_body(request)
    rooms = body.get("rooms")
    if not isinstance(rooms, list):
        return error("rooms 必须是数组")
    runtime(request).store.set_member_rooms(member_id, rooms)
    return ok({"message": "关联房间已更新"})


async def member_devices(request: Request):
    _, err = require_user(request)
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    body = await json_body(request)
    entity_ids = body.get("entity_ids")
    if not isinstance(entity_ids, list):
        return error("entity_ids 必须是数组")
    runtime(request).store.set_member_devices(member_id, entity_ids)
    return ok({"message": "专属设备已更新"})


async def member_tag_add(request: Request):
    _, err = require_user(request)
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    body = await json_body(request)
    tag = (body.get("tag") or "").strip()
    if not tag:
        return error("缺少标签 tag")
    member = runtime(request).store.add_member_tag(
        member_id=member_id,
        tag=tag,
        category=body.get("category", "other") or "other",
        emoji=body.get("emoji", "") or "",
        confidence=float(body.get("confidence", 0.0) or 0.0),
        evidence=body.get("evidence") if isinstance(body.get("evidence"), list) else None,
        source=body.get("source", "agent") or "agent",
    )
    return ok({"message": f"标签已写回: {tag}", "tag": member})


async def member_tag_delete(request: Request):
    _, err = require_user(request)
    if err:
        return err
    member_id = request.path_params.get("member_id", "")
    tag = request.path_params.get("tag", "")
    runtime(request).store.delete_member_tag(member_id, tag)
    return ok({"message": "标签已删除"})


ROUTES = [
    Route("/api/members", member_list, methods=["GET"]),
    Route("/api/members", member_create, methods=["POST"]),
    Route("/api/members/{member_id}", member_detail, methods=["GET"]),
    Route("/api/members/{member_id}", member_update, methods=["PUT"]),
    Route("/api/members/{member_id}", member_delete, methods=["DELETE"]),
    Route("/api/members/{member_id}/rooms", member_rooms, methods=["PUT"]),
    Route("/api/members/{member_id}/devices", member_devices, methods=["PUT"]),
    Route("/api/members/{member_id}/tags", member_tag_add, methods=["POST"]),
    Route("/api/members/{member_id}/tags/{tag}", member_tag_delete, methods=["DELETE"]),
]
