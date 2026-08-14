"""认证相关路由：注册 / 登录 / 登出 / 当前用户 / 改密 / 用户管理"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import current_user, error, json_body, ok, require_admin, require_user, runtime


async def register(request: Request):
    body = await json_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    rt = runtime(request)
    result = rt.auth.register(username, password)
    if not result.get("ok"):
        return error(result.get("error", "注册失败"))
    login_result = rt.auth.login(username, password)
    return ok(
        {
            "token": login_result.get("token"),
            "username": username,
            "is_admin": result.get("is_admin", False),
        }
    )


async def login(request: Request):
    body = await json_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    rt = runtime(request)
    result = rt.auth.login(username, password)
    if not result.get("ok"):
        return error(result.get("error", "登录失败"), 401)
    user = rt.auth.get_user(username) or {}
    return ok(
        {
            "token": result.get("token"),
            "username": username,
            "is_admin": bool(user.get("is_admin")),
        }
    )


async def logout(request: Request):
    return ok({"message": "已登出"})


async def auth_status(request: Request):
    """无需鉴权。前端据此决定展示「登录」还是「首次注册管理员」。"""
    rt = runtime(request)
    return ok({"initialized": rt.auth.has_users()})


async def get_me(request: Request):
    user = current_user(request)
    if not user:
        return error("未登录", 401)
    return ok(
        {
            "username": user["username"],
            "is_admin": bool(user.get("is_admin", False)),
        }
    )


async def change_password(request: Request):
    user, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    old_password = body.get("old_password") or ""
    new_password = body.get("new_password") or ""
    if len(new_password) < 6:
        return error("新密码至少 6 位")
    rt = runtime(request)
    result = rt.auth.change_password(user["username"], old_password, new_password)
    if not result.get("ok"):
        return error(result.get("error", "修改失败"))
    return ok({"message": "密码已修改"})


async def list_users(request: Request):
    _, err = require_user(request)
    if err:
        return err
    return ok({"users": runtime(request).auth.list_users()})


async def delete_user(request: Request):
    user, err = require_admin(request)
    if err:
        return err
    body = await json_body(request)
    target = (body.get("username") or "").strip()
    if not target:
        return error("缺少 username")
    if target == user["username"]:
        return error("不能删除当前登录用户")
    result = runtime(request).auth.delete_user(target)
    if not result.get("ok"):
        return error(result.get("error", "删除失败"))
    return ok({"message": f"用户已删除: {target}"})


ROUTES = [
    Route("/api/auth/register", register, methods=["POST"]),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/status", auth_status, methods=["GET"]),
    Route("/api/auth/me", get_me, methods=["GET"]),
    Route("/api/auth/change-password", change_password, methods=["POST"]),
    Route("/api/users", list_users, methods=["GET"]),
    Route("/api/users/delete", delete_user, methods=["POST"]),
]
