#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户认证系统"""
import json
import ipaddress
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
_LOGIN_MAX_FAILS = 5           # 窗口内连续失败阈值（IP 键）
_LOGIN_USER_MAX_FAILS = 20     # A4 P3-8：用户名键阈值更高，防止跨 IP 各错 1 次即锁定账号（账号级 DoS）
_LOGIN_WINDOW_SECONDS = 300    # 失败计数窗口（5 分钟）
_LOGIN_LOCK_SECONDS = 1800     # 触发阈值后锁定（30 分钟）
_login_fails: Dict[str, list] = {}    # key -> [失败时间戳]
_login_locked: Dict[str, float] = {}  # key -> 解锁时间戳
_login_guard = threading.Lock()

# 全局失败预算（DCD 20261007 §五 裁丙）：与 IP 无关的一条兜底闸。
# 为什么单加一条：PWA 经 Caddy 时全家人**共一个源 IP 桶**，而"每个桶各算各的"意味着
# 换 IP（或被伪造的 XFF）就能无限重试——桶越多越松。预算数取 MA 给的 30/60/120 中值。
# 超预算只让**登录这一件事**退避一分钟并记 WARNING，不锁账号：账号级 DoS 正是
# A4 P3-8 特意避开的那个形状，不能在这里重新引入。
_GLOBAL_FAIL_BUDGET = 60
_GLOBAL_WINDOW_SECONDS = 60
_GLOBAL_BACKOFF_SECONDS = 60
_login_global: list = []                # 窗口内的全局失败时间戳
_login_global_until: float = 0.0        # 退避到期时间戳


def _prune_revoked(now: float) -> None:
    """清理已过期的黑名单条目（调用方需持有 _revoked_lock）。"""
    for jti in [j for j, exp in _revoked_jtis.items() if exp <= now]:
        _revoked_jtis.pop(jti, None)


def _prune_login(now: float) -> None:
    """清理所有已经失去意义的登录计数 / 锁定键（调用方需持有 _login_guard）。

    与同文件 `_prune_revoked` 同形状 —— 第九轮 MA-21 报的就是「同文件有对照，
    这一族没铺」：改前只在**当前键**上过滤过期时间戳，键本身永不移除（全仓 `pop`
    0 次、`clear` 0 次于过期路径），实测 1000/10000/50000 次失败 → 1250/10250/**50250**
    条常驻，单条约 228 B。

    保留条件写反过来说更清楚：窗口 `_LOGIN_WINDOW_SECONDS` 内的计数还要用来凑阈值，
    锁定未到期的键还要继续拦人 —— 两者都不成立的键才是纯负担。
    """
    for k, fails in list(_login_fails.items()):
        if not any(now - t < _LOGIN_WINDOW_SECONDS for t in fails):
            _login_fails.pop(k, None)
    for k, until in list(_login_locked.items()):
        if until <= now:
            _login_locked.pop(k, None)


#: `trusted_proxy_cidrs` 原始串 -> 解析后的网段元组。判 IP 是登录路径每个请求都要
#: 做的一步，而配置串几乎不变；不缓存就等于把 CPU 白送给打 /login 的人。
#: 只留最近一份（配置改动后下一个请求自然重解析）。
_trusted_nets_cache: tuple = ("", ())


def _trusted_proxy_networks(raw: str) -> tuple:
    """解析登记的可信代理网段；**解析不了的条目跳过并记 WARNING**。

    跳过 = fail-closed：写坏一条 CIDR 只会让它不参与"是否可信"的判断，
    绝不会因为配置出错而退化成"谁都信"。
    """
    global _trusted_nets_cache
    if _trusted_nets_cache[0] == raw:
        return _trusted_nets_cache[1]
    nets = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            _LOG.warning("trusted_proxy_cidrs 有一条无法解析，已跳过（不视为可信代理）：%r", item)
    out = tuple(nets)
    _trusted_nets_cache = (raw, out)
    return out


def resolve_client_ip(peer_ip: str, forwarded_for: str, *, trust_proxy: bool,
                      trusted_proxy_cidrs: str) -> str:
    """客户端 IP 的唯一口径（DCD 20261007 §五 裁乙）——**默认完全不看 X-Forwarded-For**。

    这条 IP 是登录限速的桶键，所以"谁能决定它算什么"就等于"谁能决定自己不被限速"。
    `/api/auth/login`（auth_routes._client_ip）和 Basic Auth（app.AuthMiddleware）两条
    入口共用本函数，就是为了避免"同一个客户端在两个入口落进不同桶"。

    改前的口径是 WO-MA-004 ⑤b：无条件取 XFF **最后一个**元素（首元素客户端可伪造，
    所以不取首位）。取末位挡住了"自己编首位"，却没挡住更根本的一条：**末位可信的前提
    是这条连接确实只经过我们那台反代**。而 MA 的 8086 是直曝的（同批裁定：本轮不收口，
    因为 DB 直连 `http://192.168.2.200:8086` 的依赖真实存在，收口 = 断 DB→MA），
    直连请求自带任意末位就能自造 IP 桶 ⇒ 限速器反向失效。

    现在的口径（默认拒绝，三层）：
      1. `trust_proxy` 关（默认）→ 一律 TCP 对端；
      2. 开着，但请求方 IP ∉ `trusted_proxy_cidrs` → 仍按 TCP 对端；
      3. 开着且请求方可信 → 取 XFF 末位（反代把真实客户端 IP 追加在末尾）。

    网段填 Caddy 服务名解析出的**单 IP**（如 `172.18.0.7/32`），不用 `172.16.0.0/12`
    整段：信任面越大，能伪造 XFF 的邻居越多。这里只收 IP/CIDR 字面量、**不做服务名解析**
    ——把信任决定交给 DNS，等于让"谁能占住这个名字"决定谁能伪造客户端 IP。
    对端 IP 自身解析不出（unix socket / 未知）时按不可信处理。
    """
    if not trust_proxy:
        return peer_ip
    nets = _trusted_proxy_networks(trusted_proxy_cidrs or "")
    if not nets:
        return peer_ip
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return peer_ip
    if not any(addr in net for net in nets):
        return peer_ip
    parts = [p.strip() for p in (forwarded_for or "").split(",") if p.strip()]
    return parts[-1] if parts else peer_ip


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

class StateUnreadable(Exception):
    """状态文件已损坏，拒绝后续覆盖写入（M6/M21 护栏）。"""


class AuthManager:
    """用户认证管理器"""

    def __init__(self, config):
        self.config = config
        self.users_file = config.users_file
        # M6/M21：状态损坏护栏。_state_poisoned=True 时写侧拒绝覆盖；
        # 读侧用 _cached_users（最后一次成功加载的快照）降级为只读。
        self._state_poisoned = False
        self._cached_users: Dict[str, Any] = {}
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
        """加载用户数据。成功时缓存快照；失败时置位 poison 并返回缓存降级只读。"""
        try:
            with open(self.users_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._cached_users = data  # M6: 缓存最后一次成功加载的快照
            self._state_poisoned = False
            return data
        except Exception as exc:
            _LOG.warning("读取用户文件失败 %s: %s", self.users_file, exc)
            self._state_poisoned = True
            if self._cached_users:
                _LOG.warning("状态文件不可读，降级为只读缓存（%d 个账号）", len(self._cached_users))
                return self._cached_users
            return {}

    def _save_users(self, users: Dict[str, Any]):
        """保存用户数据（原子写，避免中断损坏账号文件）。
        M6：状态文件已损坏时拒绝覆盖写入，保留损坏原件待人工恢复。"""
        if self._state_poisoned:
            raise StateUnreadable(
                f"用户文件 {self.users_file} 已损坏，拒绝覆盖写入。"
                "请人工修复后重启服务。")
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
        """返回 (是否允许登录, 剩余锁定秒数)。

        先查全局失败预算（裁丙），再查 IP/用户名两个桶（A4）。顺序有意为之：预算是
        "整个入口此刻正在被打"这一层读数，它比单个桶更该先拦住请求。

        剩余秒数取整到「至少 1 秒」：`int()` 向下取整在只剩不到 1 秒时会给出 0，
        而 0 会被路由侧的文案算成"0 分钟后重试"，等于告诉对方现在就能再试。
        """
        now = time.time()
        with _login_guard:
            if _login_global_until > now:
                return False, max(1, int(_login_global_until - now))
            for k in self._login_keys(ip, username):
                until = _login_locked.get(k)
                if until and until > now:
                    return False, max(1, int(until - now))
            return True, 0

    def note_login_failure(self, ip: str, username: str) -> None:
        """记录一次失败；达到阈值则锁定对应 IP 与用户名。

        A4 P3-8：IP 键阈值 5 次（防爆破），用户名键阈值 20 次（防账号级 DoS）。
        跨 IP 各错 1 次不再能轻易锁定全局账号。

        `global _login_global_until` 不是风格问题：赋值会把这个名字变成本地变量，
        少了这一行就是 `UnboundLocalError`（写第一版时漏了，被
        `tests/test_vma_login_rate_limit.py` 的打穿用例当场抓住）。`_login_global`
        走的是切片原地写（`[:]`），所以只有退避到期时间这一个需要 `global`。
        """
        global _login_global_until
        now = time.time()
        global_breach = 0
        with _login_guard:
            _prune_login(now)  # MA-21：每次写入顺带做全局清理（对齐 _prune_revoked）
            for k in self._login_keys(ip, username):
                threshold = _LOGIN_USER_MAX_FAILS if k.startswith("user:") else _LOGIN_MAX_FAILS
                fails = [t for t in _login_fails.get(k, []) if now - t < _LOGIN_WINDOW_SECONDS]
                fails.append(now)
                if len(fails) >= threshold:
                    _login_locked[k] = now + _LOGIN_LOCK_SECONDS
                    _login_fails[k] = []
                else:
                    _login_fails[k] = fails
            # 全局预算：换 IP / 伪造代理头都绕不过这一条（桶再多也共用了同一段墙钟）。
            global_fails = [t for t in _login_global if now - t < _GLOBAL_WINDOW_SECONDS]
            global_fails.append(now)
            if len(global_fails) >= _GLOBAL_FAIL_BUDGET:
                if _login_global_until <= now:
                    global_breach = len(global_fails)
                    _login_global_until = now + _GLOBAL_BACKOFF_SECONDS
                global_fails = []
            _login_global[:] = global_fails
        if global_breach:
            # 可见性：预算被打穿是一次需要人知道的事件，不是悄悄多等一分钟
            _LOG.warning("登录全局失败预算打穿（窗口 %d 秒内 %d 次），入口退避 %d 秒；"
                         "本次计数不含 IP/用户名桶的锁定，两者各自照旧",
                         _GLOBAL_WINDOW_SECONDS, global_breach, _GLOBAL_BACKOFF_SECONDS)

    def note_login_success(self, ip: str, username: str) -> None:
        """登录成功后清零该 IP 与该用户名的失败计数与锁定。

        **不动全局预算**：预算记的是"这一分钟整个入口被打得多狠"，一次正常登录成功
        不该把别人的爆破记录洗掉（否则边登录边被打等于免费清账）。
        """
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
