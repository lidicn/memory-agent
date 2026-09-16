"""实体身份 / 设备健康 只读接口（v0.3）

供 WebUI「逻辑设备 / 失效清单」页使用，落实 A3：让失效设备**对用户可见**，
而不是让模板静默产出错误洞察。

鉴权：仅 WebUI 的 JWT。``app_token`` 不放行这两个端点——它只被允许访问
``/api/insights/query``（见 ``APP_ENDPOINTS``），避免应用层消费者拿到设备清单。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


async def device_list(request: Request):
    """逻辑设备清单：一个物理设备 ↔ 多个可能漂移的 HA 实体。"""
    _, err = require_user(request)
    if err:
        return err
    identity = getattr(runtime(request), "identity", None)
    if identity is None:
        return error("身份层未启用")
    devices = identity.list_devices()
    return ok({"devices": devices, "total": len(devices)})


async def health_list(request: Request):
    """实体健康 / 失效清单。

    ``state`` 可填 ``active``（确认在线）/ ``unknown``（短暂失联）/ ``stale``（长期失效），
    留空返回全部。``referenced=1`` 表示该实体仍被模板引用，失效后应优先处理。
    """
    _, err = require_user(request)
    if err:
        return err
    state = (request.query_params.get("state") or "").strip()
    identity = getattr(runtime(request), "identity", None)
    if identity is None:
        return error("身份层未启用")
    rows = identity.store.list_device_health(state)
    return ok({"health": rows, "total": len(rows), "state": state or "all"})


async def merge_audit(request: Request):
    """合并审计日志（v0.6 #1）：列出所有 A2 自动合并的逻辑设备及其候选实体。"""
    _, err = require_user(request)
    if err:
        return err
    identity = getattr(runtime(request), "identity", None)
    if identity is None:
        return error("身份层未启用")
    rows = identity.store.list_merged_logical_devices()
    return ok({"merges": rows, "total": len(rows)})


async def merge_split(request: Request):
    """拆分 / 回滚一个 A2 自动合并设备：候选实体还原为独立 user-pinned 设备。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    stable_id = (body.get("stable_id") or "").strip()
    if not stable_id:
        return error("缺少 stable_id")
    identity = getattr(runtime(request), "identity", None)
    if identity is None:
        return error("身份层未启用")
    result = identity.store.split_logical_device(stable_id)
    if not result.get("ok"):
        return error(result.get("error", "拆分失败"), result.get("code", 400), result)
    identity.invalidate_cache()
    return ok(result)


ROUTES = [
    Route("/api/identity/devices", device_list, methods=["GET"]),
    Route("/api/identity/health", health_list, methods=["GET"]),
    Route("/api/identity/merges", merge_audit, methods=["GET"]),
    Route("/api/identity/merges/split", merge_split, methods=["POST"]),
]
