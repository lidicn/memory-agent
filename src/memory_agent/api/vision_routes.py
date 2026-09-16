"""视觉识别路由：配置联调、状态、手动识别、TV 事件上报、行为查询

对应 docs/vision-behavior-spec.md §5。鉴权分两类：
- WebUI 端点沿用 JWT（require_user）；
- `POST /api/events/face` 为设备端点（TV APK / HA 自动化），
  用独立 `vision_device_token` Bearer 鉴权，与 WebUI/MCP 三者隔离。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from starlette.requests import Request
from starlette.routing import Route

import httpx

from ..store import now_local
from ..presence_fusion import fuse_presence
from .deps import error, json_body, ok, require_user, runtime


def _check_device_token(request: Request) -> bool:
    token = (runtime(request).config.vision_device_token or "").strip()
    if not token:
        return False
    auth = request.headers.get("authorization", "")
    return auth == f"Bearer {token}"


def _unmask_or_none(value) -> str | None:
    """前端未改动的密钥是掩码占位（含 ********），透传会拿掩码当真值 → 鉴权必失败。
    返回 None 表示「用已保存配置」。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return None if "********" in value else value


async def vision_status(request: Request):
    """每房间的门槛状态 / 调用计数 / 最近结果，供设置页运行状态块。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    return ok(rt.vision.status())


async def vision_lights(request: Request):
    """列出某房间可用照明实体（含当前灯态），供光线门槛勾选。"""
    _, err = require_user(request)
    if err:
        return err
    room = (request.query_params.get("room") or "").strip()
    if not room:
        return error("缺少 room 参数")
    rt = runtime(request)
    # 候选含 light.* + switch.*（照明可能挂 Switch 域，如米家墙壁开关），
    # 由用户勾选；实际参与门槛判定的以勾选为准（未勾选回退 light.*）。
    entities = rt.vision._light_candidates(room)
    lights = []
    for eid in entities:
        on = await asyncio.to_thread(rt.vision._light_on, eid)
        lights.append({"entity_id": eid, "on": on})
    # 房间注册表为空时，顺带给出提示
    room_cfg = (rt.config.rooms or {}).get(room) or {}
    return ok({
        "room": room,
        "lights": lights,
        "from_registry": bool(room_cfg),
        "camera_override": (rt.vision.camera_for_room(room) or {}).get("light_entities") or [],
    })


async def vision_camera_test(request: Request):
    """测试摄像头取流：拉一帧返回 base64 预览 + 耗时。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    stream = (body.get("stream") or "").strip()
    if not stream:
        return error("缺少 stream 参数")
    rt = runtime(request)
    try:
        result = await asyncio.to_thread(
            rt.vision.test_camera, stream,
            go2rtc_user=(body.get("go2rtc_user") or "").strip() or None,
            go2rtc_pass=_unmask_or_none(body.get("go2rtc_pass")),
        )
    except httpx.TimeoutException as exc:
        return error(
            f"取帧超时：go2rtc 在限定时间内未返回帧（流 '{stream}'）。"
            "请确认 go2rtc 中该流名正确且摄像头在线；米家等摄像头关键帧间隔较长，可稍后重试。",
            extra={"kind": "timeout"},
        )
    except httpx.TransportError as exc:
        return error(
            f"无法连接 go2rtc（{type(exc).__name__}）：请确认 go2rtc 地址 {rt.config.go2rtc_base_url} "
            "可从本服务容器访问（容器↔宿主网络需互通），且端口 1984 已开放。",
            extra={"kind": "transport"},
        )
    except Exception as exc:  # noqa: BLE001
        return error(f"取帧失败: {exc}")
    return ok({"message": f"取帧成功（{result['size']} 字节，{result['latency_ms']}ms）", **result})


async def vision_test_llm(request: Request):
    """端到端体检：取帧 → VLM 简单提问，验证多模态链路与会话存活。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    stream = (body.get("stream") or "").strip() or None
    rt = runtime(request)
    try:
        result = await asyncio.to_thread(
            rt.vision.test_llm, stream,
            go2rtc_user=(body.get("go2rtc_user") or "").strip() or None,
            go2rtc_pass=_unmask_or_none(body.get("go2rtc_pass")),
            vlm_base_url=(body.get("vlm_base_url") or "").strip() or None,
            vlm_api_key=_unmask_or_none(body.get("vlm_api_key")),
            vlm_model=(body.get("vlm_model") or "").strip() or None,
            vlm_endpoint_path=(body.get("vlm_endpoint_path") or "").strip() or None,
        )
    except Exception as exc:  # noqa: BLE001
        return error(
            f"{exc}",
            extra={"hint": "若提示会话失效，请到 doubao2api 管理面板扫码重登"},
        )
    return ok({"message": f"识别成功（取帧 {result['fetch_ms']}ms + 推理 {result['latency_ms']}ms）", **result})


async def vision_analyze(request: Request):
    """手动触发识别（运维/调试）。force 绕过光线门槛与冷却，仍受上限/退避保护。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    room = (body.get("room") or "").strip()
    if not room:
        return error("缺少 room 参数")
    force = bool(body.get("force"))
    wait = bool(body.get("wait"))
    rt = runtime(request)
    if wait:
        result = await asyncio.to_thread(rt.vision.analyze_room, room, force=force, trigger="manual")
        if not result.get("ok"):
            return error(result.get("error") or result.get("reason", "识别失败"),
                         extra=result)
        return ok({"message": result.get("action") or "识别完成", **result})
    asyncio.get_running_loop().create_task(
        asyncio.to_thread(rt.vision.analyze_room, room, force=force, trigger="manual")
    )
    return ok({"message": "已加入识别队列", "room": room}, status_code=202)


async def events_face(request: Request):
    """TV 人脸事件上报（spec §5.1）。异步处理，不同步等 VLM。"""
    if not _check_device_token(request):
        return error("无效的设备令牌", 401)
    body = await json_body(request)
    room = (body.get("room") or "").strip()
    if not room:
        return error("缺少 room 参数")
    persons = body.get("persons") if isinstance(body.get("persons"), list) else []
    trigger = (body.get("trigger") or "heartbeat").strip()
    device_ts = body.get("ts")
    rt = runtime(request)
    result = rt.vision.record_face_event(
        room, persons, trigger, device_ts, camera=body.get("camera")
    )
    return ok(result, status_code=202)


async def vision_presence(request: Request):
    """在场查询：最近 N 分钟内，各成员最后一次被识别到的时间与来源。

    ``GET /api/vision/presence?room=客厅&minutes=10``

    豆包管家每 30~60s 轮询一次，发现「某成员新出现」即触发个性化问候。
    数据来源是 ``behavior_events.persons_json``（TV 人脸事件 + 视觉巡检都写这里），
    不额外建表。``via`` 归一化：TV 端上报的 ``face`` 与服务端补认的 ``arcface``
    统一为 ``arcface``，外观匹配为 ``appearance_matched``；原始值在 ``via_raw``。
    未识别 / 陌生人（``未识别成员`` 等）不出现在结果里。
    """
    params = request.query_params
    room = (params.get("room") or "").strip() or None
    raw_minutes = params.get("minutes")
    try:
        minutes = float(raw_minutes) if raw_minutes not in (None, "") else 10.0
    except (TypeError, ValueError):
        return error("minutes 必须是数字")
    minutes = max(0.5, min(minutes, 24 * 60))
    rt = runtime(request)
    now = now_local(rt.config.tz_offset_hours)
    since = (now - timedelta(minutes=minutes)).isoformat(sep="T")
    items = await asyncio.to_thread(rt.store.recent_presence, since, room)
    # 在场融合（v0.7.5）：跨房间占用 + 名册消除法身份推断。
    # 消除法需要全房子视图，故 occupancy 始终取全房间（不受 room 过滤影响），
    # 而 items 仍按 room 过滤返回，保持对管家的既有契约不变。
    members = await asyncio.to_thread(rt.store.list_members)
    occupancy = await asyncio.to_thread(rt.store.recent_occupancy, since)
    fusion = fuse_presence(members, occupancy)
    return ok({
        "room": room or "",
        "window_minutes": minutes,
        "since": since,
        "now": now.isoformat(sep="T"),
        "count": len(items),
        "items": items,
        "fusion": fusion,
    })


async def behaviors_query(request: Request):
    """行为查询（spec §5.3）：?member=&room=&from=&to=&limit="""
    _, err = require_user(request)
    if err:
        return err
    params = request.query_params
    rt = runtime(request)
    events = await asyncio.to_thread(
        rt.store.list_behavior_events,
        (params.get("room") or "").strip() or None,
        (params.get("member") or "").strip() or None,
        (params.get("from") or "").strip()[:10] or None,
        (params.get("to") or "").strip()[:10] or None,
        min(int(params.get("limit") or 100), 500),
    )
    return ok({"count": len(events), "events": events})


async def behaviors_label(request: Request):
    """人工标注闭环（spec §3 P2）：把一条未识别事件归到某成员/新建成员。

    body: {"member_id": "<id>"} 或 {"new_member": "Kevin"}。
    - 将该事件 persons[].name 改写为目标成员名、via 标 "labeled"；
    - 将该事件 appearance 并入成员 appearance_json（学习/补全）；
    - 若 new_member 则先创建成员再关联。
    """
    _, err = require_user(request)
    if err:
        return err
    raw_id = request.path_params.get("event_id")
    try:
        event_id = int(raw_id)
    except (TypeError, ValueError):
        return error("event_id 非法")
    body = await json_body(request)
    member_id = body.get("member_id")
    new_member = (body.get("new_member") or "").strip()
    rt = runtime(request)
    event = await asyncio.to_thread(rt.store.get_behavior_event, event_id)
    if not event:
        return error("行为事件不存在", 404)
    if new_member:
        member = await asyncio.to_thread(
            rt.store.create_member, new_member
        )
        member_id = member["id"]
    if not member_id:
        return error("缺少 member_id 或 new_member")
    member = await asyncio.to_thread(rt.store.get_member, member_id)
    if not member:
        return error("成员不存在", 404)
    persons = event.get("persons") or []
    for p in persons:
        if isinstance(p, dict):
            p["name"] = member["name"]
            p["via"] = "labeled"
    await asyncio.to_thread(
        rt.store.update_behavior_event_persons, event_id, persons, via="labeled"
    )
    # 把该事件的外观并入成员档案（学习）
    if event.get("appearance"):
        await asyncio.to_thread(
            rt.store.merge_member_appearance, member_id, event["appearance"]
        )
    member = await asyncio.to_thread(rt.store.get_member, member_id)
    return ok({
        "message": f"已标注为 {member['name']}",
        "event_id": event_id,
        "member": member,
    })


ROUTES = [
    Route("/api/vision/status", vision_status, methods=["GET"]),
    Route("/api/vision/lights", vision_lights, methods=["GET"]),
    Route("/api/vision/cameras/test", vision_camera_test, methods=["POST"]),
    Route("/api/vision/test-llm", vision_test_llm, methods=["POST"]),
    Route("/api/vision/analyze", vision_analyze, methods=["POST"]),
    Route("/api/events/face", events_face, methods=["POST"]),
    Route("/api/vision/presence", vision_presence, methods=["GET"]),
    Route("/api/behaviors", behaviors_query, methods=["GET"]),
    Route("/api/behaviors/{event_id}/label", behaviors_label, methods=["POST"]),
]
