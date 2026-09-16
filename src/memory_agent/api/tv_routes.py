"""电视截屏多模态识别路由（按需调用）

对应 docs/电视截屏多模态识别功能_交接单.md。提供三个接口：
- GET  /api/tv/state      当前电视应用信息（不含截图，轻量）
- GET  /api/tv/screenshot 当前电视画面截图（image/jpeg，每次重新取）
- POST /api/tv/analyze    截屏 + 多模态识别，返回结构化结果

鉴权：WebUI 走 JWT（require_user）；豆包管家（外部服务）走 butler_token，
令牌仅在 BUTLER_ENDPOINTS 白名单内生效（见 app.py）。
"""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..tv_service import TVError
from .deps import error, json_body, ok, require_user, runtime


def _tv_error(exc) -> Response:
    """把 TVError 转成 503 + 可执行的排查提示；其余异常转 500。"""
    if isinstance(exc, TVError):
        return error(exc.message, code=503, extra={"code": exc.code, **exc.extra})
    return error(f"{type(exc).__name__}: {exc}", 500)


async def tv_state(request: Request):
    """当前电视打开的 App 信息（不抓截图，轻量，可高频查询）。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    try:
        include_mqtt = (request.query_params.get("include_tv_state") or "").lower() in ("1", "true", "yes")
        info = await asyncio.to_thread(rt.tv.state, include_mqtt)
        # _capture_url 是带签名的一次性 URL，不应回传给调用方
        info.pop("_capture_url", None)
        return ok({"state": info})
    except Exception as exc:  # noqa: BLE001
        return _tv_error(exc)


async def tv_screenshot(request: Request):
    """当前电视画面截图，直接返回 JPEG。

    关键：截图 URL 有时效，每次都重新读 HA 实体拿新 URL，绝不缓存。
    `?t=` 仅作缓存破绽用，服务端忽略其取值。
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    try:
        frame, _info = await asyncio.to_thread(rt.tv.capture)
        return Response(
            frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    except Exception as exc:  # noqa: BLE001
        return _tv_error(exc)


async def tv_analyze(request: Request):
    """截屏 + 多模态识别，返回结构化结果。

    Body（可空）：
    - include_tv_state: bool（默认 true）是否结合 TV Cam 的 MQTT 状态广播
    - detail_level: "brief" | "normal" | "detailed"（默认 brief）
    - save_snapshot: bool（默认 true）是否落盘快照
    """
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    body = await json_body(request)
    include_tv_state = body.get("include_tv_state", True)
    detail_level = body.get("detail_level", "brief")
    save_snapshot = body.get("save_snapshot", True)
    try:
        result = await asyncio.to_thread(
            rt.tv.analyze,
            include_tv_state=bool(include_tv_state),
            detail_level=detail_level,
            save_snapshot=bool(save_snapshot),
        )
        # 截图本身是二进制，已存为快照/可经 /api/tv/screenshot 取，不在 JSON 里再塞 base64
        result.pop("raw_response", None)
        return ok(result)
    except Exception as exc:  # noqa: BLE001
        return _tv_error(exc)


ROUTES = [
    Route("/api/tv/state", tv_state, methods=["GET"]),
    Route("/api/tv/screenshot", tv_screenshot, methods=["GET"]),
    Route("/api/tv/analyze", tv_analyze, methods=["POST"]),
]
