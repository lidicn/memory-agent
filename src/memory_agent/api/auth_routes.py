"""认证相关路由：注册 / 登录 / 登出 / 当前用户 / 改密 / 用户管理"""

from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .deps import current_user, error, json_body, ok, require_admin, require_user, runtime


def _bearer_or_cookie_token(request: Request) -> str:
    """从 Authorization: Bearer 或 Cookie: token= 提取 JWT。

    /api/auth/register 属于 PUBLIC_PREFIXES，AuthMiddleware 会跳过鉴权、
    不写入 ``state.user``，因此这里自行解析令牌用于管理员校验。
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    cookie_header = request.headers.get("cookie", "")
    if cookie_header:
        from http.cookies import SimpleCookie

        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
        except Exception:
            return ""
        morsel = cookies.get("token")
        if morsel and morsel.value:
            return morsel.value
    return ""


async def register(request: Request):
    body = await json_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    rt = runtime(request)
    # 安全加固（审计 A1）：系统初始化后（已有账号）关闭公开注册，仅管理员可新增用户。
    # 引导期（无任何账号）仍允许匿名注册首个账号并成为管理员，以完成初始化；
    # 可用 INIT_ADMIN_USER / INIT_ADMIN_PASS 预置管理员，进一步关闭初始化窗口。
    if rt.auth.has_users():
        token = _bearer_or_cookie_token(request)
        caller = rt.auth.verify_token(token) if token else None
        if not caller or not caller.get("is_admin"):
            return error("系统已初始化，注册已关闭；如需新增用户请由管理员操作", 403)
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


def _client_ip(request: Request) -> str:
    """取客户端 IP（优先 X-Forwarded-For 首个，兼容反向代理）。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


async def login(request: Request):
    body = await json_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    rt = runtime(request)
    ip = _client_ip(request)
    # 审计 A4：登录爆破防护（按 IP + 用户名双维度限流 / 锁定）
    allowed, retry = rt.auth.login_allowed(ip, username)
    if not allowed:
        return error(f"尝试过于频繁，请在 {max(1, retry // 60 + 1)} 分钟后重试", 429)
    result = rt.auth.login(username, password)
    if not result.get("ok"):
        rt.auth.note_login_failure(ip, username)
        return error(result.get("error", "登录失败"), 401)
    rt.auth.note_login_success(ip, username)
    user = rt.auth.get_user(username) or {}
    return ok(
        {
            "token": result.get("token"),
            "username": username,
            "is_admin": bool(user.get("is_admin")),
        }
    )


async def logout(request: Request):
    # 审计 A2：登出时把当前 Token 的 jti 拉黑，旧 Token 立即失效
    token = _bearer_or_cookie_token(request)
    if token:
        runtime(request).auth.revoke_token(token)
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
