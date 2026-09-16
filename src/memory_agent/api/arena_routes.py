"""AutoFlow 竞技场快照路由。

鉴权由 app.py AuthMiddleware 统一把关：竞技场令牌（kind=arena）仅能访问
ARENA_ENDPOINTS 白名单，管理员 JWT 亦可；本路由不再二次鉴权。
竞技场运行时调用的 3 个工具走 ACP（见 acp_server.py），不在此处。

契约：docs/交接单_AutoFlow竞技场对接.md
"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, runtime


async def create_snapshot(request: Request):
    """POST /api/arena/snapshot —— 从真实库抽取指定房间设备+历史，脱敏后存为版本化快照。"""
    body = await json_body(request)
    arena_id = (body.get("arena_id") or "").strip()
    if not arena_id:
        return error("缺少 arena_id")
    room = body.get("room") or ""
    devices = body.get("devices") or []
    try:
        history_days = int(body.get("history_days") or 30)
    except (TypeError, ValueError):
        history_days = 30
    rt = runtime(request)
    if rt.arena is None:
        return error("竞技场服务未就绪", 500)
    try:
        snap = await asyncio.to_thread(
            rt.arena.build_snapshot, arena_id, room, devices, history_days
        )
        return ok(snap)
    except Exception as exc:  # noqa: BLE001
        return error(f"快照生成失败: {exc}", 500)


async def list_snapshots(request: Request):
    """GET /api/arena/snapshots —— 列出所有竞技场分区的快照（含最新版本）。"""
    rt = runtime(request)
    if rt.arena is None:
        return error("竞技场服务未就绪", 500)
    data = await asyncio.to_thread(rt.store.list_arena_snapshots)
    return ok({"snapshots": data})


async def get_snapshot(request: Request):
    """GET /api/arena/snapshots/{arena_id}?version=1 —— 获取某分区快照（默认最新版本）。"""
    arena_id = request.path_params.get("arena_id", "")
    version = request.query_params.get("version")
    rt = runtime(request)
    if rt.arena is None:
        return error("竞技场服务未就绪", 500)
    try:
        ver = int(version) if version else None
    except (TypeError, ValueError):
        ver = None
    snap = await asyncio.to_thread(rt.store.get_arena_snapshot, arena_id, ver)
    if not snap:
        return error("快照不存在", 404)
    return ok({"snapshot": snap})


async def get_analytics(request: Request):
    """GET /api/arena/analytics —— 竞技场闭环分析：用了洞察的 Agent vs 没用的成功率/token 对比。

    仅对管理员 JWT 开放（不在 ARENA_ENDPOINTS 白名单，arena_ 令牌访问会被 403 拦截）。
    """
    arena_id = request.query_params.get("arena_id")
    rt = runtime(request)
    if rt.arena is None:
        return error("竞技场服务未就绪", 500)
    try:
        data = await asyncio.to_thread(rt.store.get_arena_analytics, arena_id)
        return ok(data)
    except Exception as exc:  # noqa: BLE001
        return error(f"分析失败: {exc}", 500)


ROUTES = [
    Route("/api/arena/snapshot", create_snapshot, methods=["POST"]),
    Route("/api/arena/snapshots", list_snapshots, methods=["GET"]),
    Route("/api/arena/snapshots/{arena_id}", get_snapshot, methods=["GET"]),
    Route("/api/arena/analytics", get_analytics, methods=["GET"]),
]
