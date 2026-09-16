"""事件写入路由：butler→MA 富化推送接口（感知层边界决策 §〇）

对应 docs/开发计划_v0.8_主动记忆.md §〇「回归/验收追加」：
豆包管家经 butler_token 把「主动感知富化事件」推给 MA，合并入
``behavior_events``（与 MA 被动日志同表，供 presence / insights 消费）。

设计边界（铁律，见 §〇）：
- 本接口**只收富化事件**（在场 / 动作 / 上下文），即 MA 被动日志会漏掉的
  主动感知产物；**不收设备状态原始事件** —— MA 已有 HA 直连保底，避免双写。
- 鉴权由 ``app.py`` 的 ``AuthMiddleware`` 经 ``BUTLER_ENDPOINTS`` 白名单保证：
  仅 butler_token 可命中本路径，越权（非白名单路径）一律 403。
  WebUI 管理员（JWT）亦可调用本接口，用于调试 / 手动补录；设备 / APP 令牌
  被各自白名单拦截，无法触达。
"""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from ..store import now_local
from .deps import error, json_body, ok, require_user, runtime


async def butler_events_push(request: Request):
    """豆包管家推送富化事件（POST /api/events）。

    请求体：

        {"events": [
            {"room": "客厅", "action": "出现", "persons": ["爸爸"],
             "confidence": 0.92, "scene": "沙发", "trigger": "butler",
             "camera_src": "butler", "server_ts": "2026-09-13T21:05:00",
             "status": "ok"},
            ...
        ]}

    - ``persons`` 为名字列表，缺省空列表（未在/未识别则不填，presence 查询会跳过）。
    - ``camera_src`` 缺省 ``"butler"``；``trigger`` 缺省 ``"butler"``；``status`` 缺省 ``"ok"``。
    - ``server_ts`` 缺省取服务端接收时刻（本地时区）；``day`` 由 store 自动推导。
    - 兼容裸数组：body 直接为事件列表亦接受。
    """
    user, err = require_user(request)
    if err:
        return err
    try:
        body = await json_body(request)
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return error("请求体需为 JSON 对象")
    raw = body.get("events")
    if not isinstance(raw, list):
        # 兼容裸数组
        raw = body if isinstance(body, list) else None
    if not isinstance(raw, list) or not raw:
        return error("events 须为非空数组")

    items: list[dict] = []
    for ev in raw:
        if not isinstance(ev, dict):
            return error("events 元素须为对象")
        items.append({
            "room": str(ev.get("room") or "").strip(),
            "action": ev.get("action"),
            "scene": ev.get("scene"),
            "confidence": ev.get("confidence"),
            "persons": ev.get("persons") or [],
            "camera_src": str(ev.get("camera_src") or "butler").strip(),
            "trigger": str(ev.get("trigger") or "butler").strip(),
            "status": str(ev.get("status") or "ok").strip(),
            "server_ts": ev.get("server_ts"),
        })

    rt = runtime(request)
    inserted = 0
    for it in items:
        try:
            rt.store.insert_behavior_event(it)
            inserted += 1
        except Exception as exc:  # 单条失败不中断整批
            print(f"[Events] 写入失败 {it.get('room')}/{it.get('action')}: {exc}")
    return ok({"inserted": inserted, "total": len(items)})


ROUTES = [
    Route("/api/events", butler_events_push, methods=["POST"]),
]
