"""人脸识别节点池路由（face_node_pool）

端点
----
* ``POST /api/face/node/register``   设备端点（vision_device_token）：节点注册
* ``POST /api/face/node/heartbeat``  设备端点：续活心跳
* ``GET  /api/face/node/lib``        设备端点：节点下行人脸库（取成员特征）
* ``POST /api/face/node/lib``        设备端点：节点上行回写成员特征
* ``GET  /api/face/lib``             WebUI（JWT）：人脸库
* ``POST /api/face/lib``             WebUI（JWT）：回写成员特征
* ``POST /api/face/recognize``       WebUI（JWT）：统一识别路由（选路 → Arcface 节点）

识别/降级语义见 face_node_registry 与 vision_service.recognize_face。
节点侧设备端点统一放 ``/api/face/node/*``，与 WebUI 的 ``/api/face/*`` 区分，
避免设备令牌与 JWT 在中间件层冲突。
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import error, json_body, ok, require_user, runtime


def _check_device_token(request: Request) -> bool:
    """复用视觉设备令牌（与 /api/events/face 同源）。空令牌视为未启用 → 拒绝。"""
    token = (runtime(request).config.vision_device_token or "").strip()
    if not token:
        return False
    auth = request.headers.get("authorization", "")
    return auth == f"Bearer {token}"


def _require_device(request: Request):
    if not _check_device_token(request):
        return error("无效的设备令牌", 401)
    return None


def _resolve_member_id(rt, member_id: str, name: str) -> str:
    member_id = (member_id or "").strip()
    if member_id:
        return member_id
    name = (name or "").strip()
    if name:
        member = next(
            (m for m in rt.store.list_members() if m.get("name") == name), None
        )
        if member:
            return member.get("id") or ""
    return ""


async def face_node_register(request: Request):
    """Arcface 节点注册：{node_id, node_type(tv|phone), url, room?}。"""
    deny = _require_device(request)
    if deny:
        return deny
    body = await json_body(request)
    node_id = (body.get("node_id") or "").strip()
    node_type = (body.get("node_type") or "").strip() or "default"
    url = (body.get("url") or "").strip()
    room = (body.get("room") or "").strip()
    if not node_id or not url:
        return error("node_id 与 url 必填")
    reg = runtime(request).face
    res = reg.register(node_id, node_type, url, room)
    if not res.get("ok"):
        return error(res.get("error", "注册失败"))
    return ok({"message": f"节点已注册: {node_id}（{node_type}）", **res})


async def face_node_heartbeat(request: Request):
    """节点心跳续活：{node_id}。"""
    deny = _require_device(request)
    if deny:
        return deny
    body = await json_body(request)
    node_id = (body.get("node_id") or "").strip()
    if not node_id:
        return error("node_id 必填")
    res = runtime(request).face.heartbeat(node_id)
    if not res.get("ok"):
        return error(res.get("error", "心跳失败"))
    return ok(res)


async def face_node_lib_get(request: Request):
    """节点下行人脸库：返回已注册特征的成员清单（含 face_feature）。设备端点。"""
    deny = _require_device(request)
    if deny:
        return deny
    return ok({"members": runtime(request).store.get_face_lib()})


async def face_node_lib_post(request: Request):
    """节点上行回写成员特征：{member_id | name, face_feature}。设备端点。"""
    deny = _require_device(request)
    if deny:
        return deny
    body = await json_body(request)
    face_feature = body.get("face_feature")
    if face_feature is None:
        return error("face_feature 必填")
    rt = runtime(request)
    member_id = _resolve_member_id(rt, body.get("member_id"), body.get("name"))
    if not member_id:
        return error("需提供 member_id 或可识别的 name")
    if not rt.store.set_member_face_feature(member_id, face_feature):
        return error("成员不存在", 404)
    return ok({"message": "人脸特征已回写", "member_id": member_id})


async def face_node_list(request: Request):
    """列出全部节点及其在线状态（调试/状态用，需登录）。"""
    _, err = require_user(request)
    if err:
        return err
    return ok({"nodes": runtime(request).face.list_nodes()})


async def face_recognize(request: Request):
    """统一识别路由（WebUI/测试用）。

    入参 {image(base64/data URL 或 URL), room?, min_conf?} → 选权重最高在线节点转发。
    命中返回 {ok, via:"arcface", name, confidence, node}；
    无在线节点/失败 → {ok:False, via:"vlm", degraded:True}（由调用方走 VLM 降级，不报错）。
    """
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    image = body.get("image") or ""
    if not image:
        return error("image 必填")
    room = (body.get("room") or "").strip() or None
    min_conf = body.get("min_conf")
    res = runtime(request).vision.recognize_face(image, room, min_conf)
    if res is None:
        return ok({
            "ok": False, "via": "vlm", "degraded": True,
            "reason": "no_online_node_or_failed",
            "message": "无在线 Arcface 节点或识别失败，已降级（请走 VLM 流程）",
        })
    return ok({"ok": True, **res})


async def face_lib_get(request: Request):
    """下行人脸库：返回已注册特征的成员清单（含 face_feature）。需登录。"""
    _, err = require_user(request)
    if err:
        return err
    return ok({"members": runtime(request).store.get_face_lib()})


async def face_lib_post(request: Request):
    """回写成员特征：{member_id | name, face_feature}。需登录。"""
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    face_feature = body.get("face_feature")
    if face_feature is None:
        return error("face_feature 必填")
    rt = runtime(request)
    member_id = _resolve_member_id(rt, body.get("member_id"), body.get("name"))
    if not member_id:
        return error("需提供 member_id 或可识别的 name")
    if not rt.store.set_member_face_feature(member_id, face_feature):
        return error("成员不存在", 404)
    return ok({"message": "人脸特征已回写", "member_id": member_id})


ROUTES = [
    Route("/api/face/node/register", face_node_register, methods=["POST"]),
    Route("/api/face/node/heartbeat", face_node_heartbeat, methods=["POST"]),
    Route("/api/face/node/lib", face_node_lib_get, methods=["GET"]),
    Route("/api/face/node/lib", face_node_lib_post, methods=["POST"]),
    Route("/api/face/node/list", face_node_list, methods=["GET"]),
    Route("/api/face/lib", face_lib_get, methods=["GET"]),
    Route("/api/face/lib", face_lib_post, methods=["POST"]),
    Route("/api/face/recognize", face_recognize, methods=["POST"]),
]
