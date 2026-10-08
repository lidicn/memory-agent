#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户认证系统"""
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import bcrypt
from jose import jwt, JWTError

_LOG = logging.getLogger(__name__)

# ── JWT 撤销黑名单 + 登录爆破防护（审计 A2/A4）──────────────────────────
# 注意：app.AuthMiddleware._authenticate 每次请求都 new AuthManager，因此
# 撤销 / 限流状态必须是「模块级」才能跨请求共享，不能挂在实例上。
# 均为进程内状态，重启清零（Token 最长 7 天；如需强一致可迁移 DB/Redis）。
_revoked_jtis: Dict[str, float] = {}   # jti -> 过期时间戳（到期即清理）
_revoked_lock = threading.Lock()

# 登录爆破防护（审计 A4）：按 IP 与用户名双维度计数 + 锁定。
_LOGIN_MAX_FAILS = 5           # 窗口内连续失败阈值
_LOGIN_WINDOW_SECONDS = 300    # 失败计数窗口（5 分钟）
_LOGIN_LOCK_SECONDS = 1800     # 触发阈值后锁定（30 分钟）
_login_fails: Dict[str, list] = {}    # key -> [失败时间戳]
_login_locked: Dict[str, float] = {}  # key -> 解锁时间戳
_login_guard = threading.Lock()


def _prune_revoked(now: float) -> None:
    """清理已过期的黑名单条目（调用方需持有 _revoked_lock）。"""
    for jti in [j for j, exp in _revoked_jtis.items() if exp <= now]:
        _revoked_jtis.pop(jti, None)

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

class AuthManager:
    """用户认证管理器"""
    
    def __init__(self, config):
        self.config = config
        self.users_file = config.users_file
        self._ensure_file()
        self._seed_initial_admin()
    
    def _ensure_file(self):
        """确保用户文件存在"""
        os.makedirs(os.path.dirname(self.users_file), exist_ok=True)
        if not os.path.exists(self.users_file):
            self._save_users({})
    
    def _seed_initial_admin(self) -> None:
        """引导期种子化管理员（审计 A1 加固）。

        若尚无任何账号且设置了 ``INIT_ADMIN_USER`` / ``INIT_ADMIN_PASS``，
        则直接创建该管理员。用于关闭「服务暴露但尚无人注册时，首个访问者
        抢注为管理员」的初始化窗口。未配置环境变量时不做任何事。
        """
        username = (os.getenv("INIT_ADMIN_USER") or "").strip()
        password = os.getenv("INIT_ADMIN_PASS") or ""
        if len(username) < 3 or len(password) < 6:
            return
        if self._load_users():
            return
        self._save_users({
            username: {
                "password_hash": _hash_password(password),
                "created_at": datetime.now().isoformat(),
                "is_admin": True,
            }
        })
        _LOG.warning("已根据 INIT_ADMIN_USER 种子化初始管理员账号: %s", username)

    def _load_users(self) -> Dict[str, Any]:
        """加载用户数据"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:  # 审计 A3：裸 except 会连 KeyboardInterrupt 一起吞
            _LOG.warning("读取用户文件失败 %s: %s", self.users_file, exc)
            return {}
    
    def _save_users(self, users: Dict[str, Any]):
        """保存用户数据（原子写，避免中断损坏账号文件）"""
        directory = os.path.dirname(self.users_file) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".users-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.users_file)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise
    
    def register(self, username: str, password: str) -> Dict[str, Any]:
        """注册用户"""
        users = self._load_users()
        
        if username in users:
            return {"ok": False, "error": "用户名已存在"}
        
        if not username or len(username) < 3:
            return {"ok": False, "error": "用户名至少3个字符"}
        
        if not password or len(password) < 6:
            return {"ok": False, "error": "密码至少6个字符"}
        
        # 引导期（无任何账号）首个注册者成为管理员；系统初始化后注册端点已在路由层
        # 收紧为「仅管理员可新增用户」（见 api/auth_routes.register，审计 A1）。
        users[username] = {
            "password_hash": _hash_password(password),
            "created_at": datetime.now().isoformat(),
            "is_admin": len(users) == 0
        }
        
        self._save_users(users)
        
        return {
            "ok": True,
            "message": "注册成功",
            "is_admin": users[username]["is_admin"]
        }
    
    def login(self, username: str, password: str) -> Dict[str, Any]:
        """登录"""
        users = self._load_users()
        
        if username not in users:
            return {"ok": False, "error": "用户名或密码错误"}
        
        user = users[username]
        
        if not _verify_password(password, user["password_hash"]):
            return {"ok": False, "error": "用户名或密码错误"}
        
        token = self._create_token(username, user.get("is_admin", False))
        
        return {
            "ok": True,
            "token": token,
            "username": username,
            "is_admin": user.get("is_admin", False)
        }
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证JWT Token"""
        try:
            payload = jwt.decode(
                token,
                self.config.jwt_secret,
                algorithms=["HS256"]
            )
            username = payload.get("sub")
            if username is None:
                return None

            # 审计 A2：命中撤销黑名单的 Token 一律拒绝（登出后旧 Token 立即失效）
            jti = payload.get("jti")
            if jti:
                now = time.time()
                with _revoked_lock:
                    exp = _revoked_jtis.get(jti)
                    if exp is not None:
                        if exp > now:
                            return None
                        _revoked_jtis.pop(jti, None)

            users = self._load_users()
            if username not in users:
                return None
            
            return {
                "username": username,
                "is_admin": users[username].get("is_admin", False)
            }
        except JWTError:
            return None
    
    def _create_token(self, username: str, is_admin: bool) -> str:
        """创建JWT Token（含 jti 便于登出后撤销，审计 A2）"""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": username,
            "is_admin": is_admin,
            "exp": now + timedelta(days=7),
            "iat": int(now.timestamp()),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, self.config.jwt_secret, algorithm="HS256")
    
    # ── JWT 撤销（审计 A2）──────────────────────────────────────────────
    def revoke_token(self, token: str) -> bool:
        """把 Token 的 jti 加入撤销黑名单（登出时调用）。

        即使 Token 未过期，之后 ``verify_token`` 也会拒绝它。返回是否成功拉黑。
        """
        try:
            payload = jwt.decode(
                token, self.config.jwt_secret, algorithms=["HS256"],
                options={"verify_exp": False},
            )
        except JWTError:
            return False
        jti = payload.get("jti")
        if not jti:
            return False
        now = time.time()
        exp = float(payload.get("exp") or (now + 7 * 86400))
        with _revoked_lock:
            _prune_revoked(now)
            _revoked_jtis[jti] = exp
        return True

    # ── 登录爆破防护（审计 A4）─────────────────────────────────────────
    @staticmethod
    def _login_keys(ip: str, username: str) -> list:
        return [f"ip:{ip}", f"user:{username}"]

    def login_allowed(self, ip: str, username: str) -> tuple:
        """返回 (是否允许登录, 剩余锁定秒数)。"""
        now = time.time()
        with _login_guard:
            for k in self._login_keys(ip, username):
                until = _login_locked.get(k)
                if until and until > now:
                    return False, int(until - now) + 1
            return True, 0

    def note_login_failure(self, ip: str, username: str) -> None:
        """记录一次失败；达到阈值则锁定对应 IP 与用户名。"""
        now = time.time()
        with _login_guard:
            for k in self._login_keys(ip, username):
                fails = [t for t in _login_fails.get(k, []) if now - t < _LOGIN_WINDOW_SECONDS]
                fails.append(now)
                if len(fails) >= _LOGIN_MAX_FAILS:
                    _login_locked[k] = now + _LOGIN_LOCK_SECONDS
                    _login_fails[k] = []
                else:
                    _login_fails[k] = fails

    def note_login_success(self, ip: str, username: str) -> None:
        """登录成功后清零失败计数与锁定。"""
        with _login_guard:
            for k in self._login_keys(ip, username):
                _login_fails.pop(k, None)
                _login_locked.pop(k, None)

    def change_password(self, username: str, old_password: str, new_password: str) -> Dict[str, Any]:
        """修改密码"""
        users = self._load_users()
        
        if username not in users:
            return {"ok": False, "error": "用户不存在"}
        
        user = users[username]
        
        if not _verify_password(old_password, user["password_hash"]):
            return {"ok": False, "error": "原密码错误"}
        
        if len(new_password) < 6:
            return {"ok": False, "error": "新密码至少6个字符"}
        
        users[username]["password_hash"] = _hash_password(new_password)
        self._save_users(users)
        
        return {"ok": True, "message": "密码修改成功"}
    
    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """获取单个用户的公开信息（不含密码哈希）"""
        user = self._load_users().get(username)
        if not user:
            return None
        return {
            "username": username,
            "is_admin": user.get("is_admin", False),
            "created_at": user.get("created_at", ""),
        }

    def has_users(self) -> bool:
        """是否已存在账号。用于前端决定展示登录还是首次注册。"""
        return bool(self._load_users())

    def list_users(self) -> list:
        """列出所有用户"""
        users = self._load_users()
        return [
            {
                "username": username,
                "is_admin": user.get("is_admin", False),
                "created_at": user.get("created_at", "")
            }
            for username, user in users.items()
        ]
    
    def delete_user(self, username: str) -> Dict[str, Any]:
        """删除用户"""
        users = self._load_users()
        
        if username not in users:
            return {"ok": False, "error": "用户不存在"}
        
        if users[username].get("is_admin"):
            admin_count = sum(1 for u in users.values() if u.get("is_admin"))
            if admin_count <= 1:
                return {"ok": False, "error": "不能删除最后一个管理员"}
        
        del users[username]
        self._save_users(users)
        
        return {"ok": True, "message": "用户删除成功"}
