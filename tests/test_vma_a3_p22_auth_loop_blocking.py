"""A3 §八「少量未卸载的同步调用」：真身在鉴权链，卸载/不卸载各有理由（2026-10-05，#51）。

审计 A3 的结论是「事件循环卸载做得系统，但**少量未卸载的同步 I/O 在高并发下会造成 P99 劣化**」，
没有点名位置。本仓用 AST 扫 `src/memory_agent` 的全部 `async def`（`scripts/scan_unloaded_async_io.py`，
正/反例自证：正例目录 `HITS=2`、`to_thread` 包裹的那条不响）⇒ 走 store 的 DB 调用**全库 HITS=0**，
所以 A3 那句话在 DB/HTTP 层已经不成立；不经 store 的那几条全在**鉴权链**上：

| 位置 | 改前 | 为什么是缺陷 | 本批 |
|---|---|---|---|
| **`AuthMiddleware.__call__` → `_authenticate`**（每个非公开请求一次） | 协程内直调同步函数 | 里面两处慢：JWT 分支 `verify_token`→`_load_users()` 每请求 `open()`+`json.load()` 读账号文件；Basic 分支 `login`→`bcrypt.checkpw`。**这是 A3 那一格的真身**，且量具看不见（见本节开头） | 卸载 |
| `/api/auth/login` → `auth.login()` → `bcrypt.checkpw` | 协程内直调 | bcrypt 是**刻意慢**函数（百毫秒级），端点还免鉴权 ⇒ 任何人都能用登录请求冻住整条事件循环 | 卸载 |
| `/api/auth/status` → `auth.has_users()` → 账号文件 `json.load` | 协程内直调 | 免鉴权热路径；A3 自己的读数就是它：`/api/auth/status` P99 **49.8ms** vs `/health` 1.1ms | 卸载 |
| `/api/auth/register` 全条 | 协程内直调 | 冷路径，且「看有没有账号 → 建号」不能被 await 打断 | **故意不卸载**，见最后一条测试 |
| `acp_auth.ACPTokenMiddleware` → `store.verify` | 协程内直调 | 已看过、**判定不改**：`verify` 是内存里的 `compare_digest` 循环（微秒级），写盘只有 `_touch` 的**节流**写且自带 `self._lock`；ACP 是窄面调试通道，不是热路径 | 不改 |

`register` 不卸载的理由是现读的，不是推测：把前置 `has_users()` 挪进线程之后，
引导期两个匿名并发注册从"第一个成号、第二个 403"变成**两个都建号**（两次检查都在空表上返回 False），
等于给攻击者留出"在管理员初始化那一刻顺手塞一个账号"的窗口。
`test_two_concurrent_bootstrap_registrations_only_create_one_account` 锁的就是这个语义，
它同时挡住"以后有人图省事把 register 也卸载了"。
"""

import asyncio
import base64
import json
import time

import pytest
from starlette.requests import Request

from memory_agent import app as app_module
from memory_agent.api import auth_routes as ar

BLOCK_SECONDS = 0.25
HEARTBEAT = 0.01
# 卸载后循环照常 ~25 次心跳（本机空载）；没卸载则 0-1 次。阈值不写死在这个数上，
# 改成同轮空闲对照的比例（见 `assert_offloaded`）：心跳计数是墙钟量，全量档单轮
# （1600+ 条用例，本机 18 分钟）能把卸载后的那一格压到 7，固定的 10 于是假红
# （2026-10-06 #64 after-档实测 `1 failed` = `assert 7 >= 10`，同一条用例单独跑与
# 容器 SUITE 两次都是绿的）。空闲对照与被测格在同一台机器、同一段负载里量，
# 整体变慢时两个数一起降；真没卸载时 handler 冻满 BLOCK_SECONDS ⇒ 0-1 跳，够不到任何一档。
MAX_TICKS_WHEN_BLOCKED = 1
OFFLOAD_CONTROL_FRACTION = 0.5


class _SlowAuth:
    """把 bcrypt / 文件读这类同步慢调用换成可测量的替身（sleep 而不是真哈希）。"""

    def __init__(self, delay=BLOCK_SECONDS):
        self.delay = delay
        self.notes = []
        self.login_calls = 0
        self.status_reads = 0

    def login_allowed(self, ip, username):
        return True, 0

    def login(self, username, password):
        self.login_calls += 1
        time.sleep(self.delay)
        return {"ok": True, "token": "t-" + username,
                "username": username, "is_admin": False}

    def get_user(self, username):
        time.sleep(self.delay)
        return {"username": username, "is_admin": False}

    def note_login_success(self, ip, username):
        self.notes.append(("ok", username))

    def note_login_failure(self, ip, username):
        self.notes.append(("bad", username))

    def has_users(self):
        self.status_reads += 1
        time.sleep(self.delay)
        return True


class _RT:
    def __init__(self, auth):
        self.auth = auth


def _request(body=None, method="POST"):
    payload = json.dumps(body or {}).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {"type": "http", "http_version": "1.1", "method": method,
         "path": "/api/auth/x", "raw_path": b"/api/auth/x", "query_string": b"",
         "headers": [(b"host", b"test"), (b"x-forwarded-for", b"1.2.3.4")],
         "client": ("10.0.0.1", 1234), "scheme": "http", "server": ("test", 80)},
        receive=receive)


async def _heartbeat(counter):
    while True:
        await asyncio.sleep(HEARTBEAT)
        counter[0] += 1


def _ticks_during(factory):
    """跑一次 handler，数这段时间里事件循环还能被调度几次。"""
    async def main():
        counter = [0]
        hb = asyncio.ensure_future(_heartbeat(counter))
        try:
            await factory()
        finally:
            hb.cancel()
        return counter[0]

    return asyncio.run(main())


def _idle_control_ticks():
    """同轮空闲对照：同样长的时间里，循环什么都不挡时实际跳了几次。"""
    async def idle():
        await asyncio.sleep(BLOCK_SECONDS)

    return _ticks_during(idle)


def assert_offloaded(ticks, label):
    """卸载档的判据：既要在绝对下界之上，也要达到同轮空闲对照的一半。"""
    control = _idle_control_ticks()
    floor = max(MAX_TICKS_WHEN_BLOCKED + 1, int(control * OFFLOAD_CONTROL_FRACTION))
    assert ticks >= floor, (
        f"{label}：事件循环被同步调用冻住。ticks={ticks}, 同轮空闲对照={control}, 阈值={floor}")
    return control


def test_the_heartbeat_can_actually_see_a_stall():
    """门自证：协程里直接 `time.sleep` 时心跳必须几乎不涨，否则后面那些
    "心跳 ≥ 对照的一半" 的断言就是恒真（量具没牙，等于没锁）。"""
    control = _idle_control_ticks()
    async def direct_blocking():
        time.sleep(BLOCK_SECONDS)

    ticks = _ticks_during(direct_blocking)
    assert ticks <= MAX_TICKS_WHEN_BLOCKED
    assert ticks < control * OFFLOAD_CONTROL_FRACTION, (
        f"量具本身没牙：冻循环档 ticks={ticks} 与空闲对照 {control} 分不开")


def test_login_handler_does_not_freeze_the_event_loop(monkeypatch):
    auth = _SlowAuth()
    monkeypatch.setattr(ar, "runtime", lambda request=None: _RT(auth))

    async def call():
        return await ar.login(_request({"username": "u1", "password": "pw"}))

    ticks = _ticks_during(call)
    assert auth.login_calls == 1
    assert_offloaded(ticks, "/api/auth/login 的 bcrypt 没卸载")
    assert auth.notes == [("ok", "u1")]


def test_auth_status_handler_does_not_freeze_the_event_loop(monkeypatch):
    auth = _SlowAuth()
    monkeypatch.setattr(ar, "runtime", lambda request=None: _RT(auth))

    async def call():
        return await ar.auth_status(_request(method="GET"))

    ticks = _ticks_during(call)
    assert auth.status_reads == 1
    assert_offloaded(ticks, "/api/auth/status 的账号文件读没卸载")


def test_two_concurrent_bootstrap_registrations_only_create_one_account(monkeypatch):
    """`register` 里那条「has_users 检查 → 建号」留在同一线程内跑完，是有语义后果的：
    引导期（无任何账号）两个匿名并发注册，HEAD 只让第一个成号、第二个拿 403。
    若把前置检查卸载进线程，两次检查会同时在空表上返回 False ⇒ 攻击者能在管理员初始化那一刻
    顺手多塞一个账号。这条锁防的就是"顺手把 register 也卸载了"。"""
    class _RmwAuth:
        def __init__(self):
            self.users = {}

        def has_users(self):
            return bool(self.users)

        def register(self, username, password):
            snapshot = dict(self.users)      # 读
            time.sleep(0.05)                 # 真实实现里是文件 IO，放大窗口用
            snapshot[username] = {"password_hash": "h"}
            self.users = snapshot            # 整份写回（覆盖）
            return {"ok": True, "is_admin": len(snapshot) == 1}

        def login(self, username, password):
            return {"ok": True, "token": "t"}

    auth = _RmwAuth()
    monkeypatch.setattr(ar, "runtime", lambda request=None: _RT(auth))

    async def main():
        return await asyncio.gather(
            ar.register(_request({"username": "a", "password": "pw"})),
            ar.register(_request({"username": "b", "password": "pw"})),
        )

    responses = asyncio.run(main())
    assert sorted(auth.users) == ["a"]
    assert sorted(r.status_code for r in responses) == [200, 403]


# ── 鉴权中间件：A3 那一格的真身在这里，不在上面那两条路由里 ─────────────────────
#
# `app.py` 的 `AuthMiddleware.__call__` 是**每个非公开请求**都要走一遍的同步函数调用，
# 而它调的 `_authenticate` 里有两处慢：
#   - JWT/设备令牌分支 → `AuthManager.verify_token` → `_load_users()` → `open()` + `json.load()`
#     读账号文件（无缓存，每次请求一整轮）；
#   - Basic 分支 → `AuthManager.login` → `bcrypt.checkpw`（刻意慢，百毫秒级）。
# 这条**量具扫不出来**：`_authenticate` 是普通 `def`，一层 AST 只看协程体内的直调，
# 传递性阻塞看不见；账号文件读写与 bcrypt 也不在 `_is_blocking` 的表里。
# 所以这一格靠人读定位 + 下面的心跳锁，HITS=0 绝不能当成"循环没被冻住"的证据。


class _SlowAuthManager:
    """替身：只把"慢"和"读盘"这两件事做成可测量的 sleep，接口形状照 AuthManager。"""

    instances = []

    def __init__(self, config=None):
        self.calls = []
        type(self).instances.append(self)

    def verify_token(self, token):
        self.calls.append("verify_token")
        time.sleep(BLOCK_SECONDS)          # 真实实现里这一步是 _load_users() 读账号文件
        return {"username": "u1", "is_admin": False}

    def login_allowed(self, ip, username):
        return True, 0

    def login(self, username, password):
        self.calls.append("login")
        time.sleep(BLOCK_SECONDS)          # 真实实现里这一步是 bcrypt.checkpw
        return {"ok": True, "token": "t-" + username, "username": username,
                "is_admin": False}

    def note_login_success(self, ip, username):
        self.calls.append("ok")

    def note_login_failure(self, ip, username):
        self.calls.append("bad")


def _scope(path="/api/agent/memories", authorization="", method="GET"):
    return {
        "type": "http", "http_version": "1.1", "method": method, "path": path,
        "raw_path": path.encode(), "query_string": b"", "scheme": "http",
        "headers": [(b"host", b"test"), (b"authorization", authorization.encode())],
        "client": ("10.0.0.9", 5555),
    }


async def _noop_receive():
    return {"type": "http.request"}


async def _noop_send(message):
    pass


async def _downstream(scope, receive, send):
    pass


@pytest.fixture
def slow_auth(monkeypatch):
    from memory_agent.config import Config
    monkeypatch.setattr(app_module, "get_config", lambda: Config())
    monkeypatch.setattr(app_module, "AuthManager", _SlowAuthManager)
    _SlowAuthManager.instances = []
    return _SlowAuthManager


def test_jwt_authentication_does_not_freeze_the_event_loop(slow_auth):
    """Bearer 令牌走 `verify_token` → 账号文件读：改前这条在协程里直调，
    每个已鉴权请求冻循环一次；卸载后循环照常调度，且鉴权结果照常传到下游。"""
    async def main():
        counter = [0]
        hb = asyncio.ensure_future(_heartbeat(counter))
        try:
            await app_module.AuthMiddleware(_downstream)(
                _scope(authorization="Bearer some.jwt.token"), _noop_receive, _noop_send)
        finally:
            hb.cancel()
        return counter[0]

    ticks = asyncio.run(main())
    assert_offloaded(ticks, "JWT 分支的 verify_token 没卸载")
    assert [i.calls for i in slow_auth.instances] == [["verify_token"]]


def test_basic_auth_does_not_freeze_the_event_loop(slow_auth):
    """Basic 分支比 JWT 更贵：`auth_manager.login()` = bcrypt.checkpw。
    Node-RED 依赖这条通道，所以它的每一次请求在改前都在冻整条循环。"""
    cred = base64.b64encode(b"u1:pw").decode()

    async def main():
        counter = [0]
        hb = asyncio.ensure_future(_heartbeat(counter))
        try:
            await app_module.AuthMiddleware(_downstream)(
                _scope(authorization="Basic " + cred), _noop_receive, _noop_send)
        finally:
            hb.cancel()
        return counter[0]

    ticks = asyncio.run(main())
    assert_offloaded(ticks, "Basic 分支的 auth_manager.login 没卸载")
    assert [i.calls for i in slow_auth.instances] == [["login", "ok"]]


def test_offloading_keeps_the_authentication_result_intact(slow_auth):
    """对偶档（防修过头）：卸载只该改变"在哪个线程跑"，不该改变鉴权语义——
    下游必须照常拿到 `state.user`，且 401/放行判定不变。"""
    async def pass_through(sc, receive, send):
        sc.setdefault("__seen__", []).append(sc.get("state", {}).get("user"))

    scope_ok = _scope(authorization="Bearer some.jwt.token")

    async def main():
        await app_module.AuthMiddleware(pass_through)(scope_ok, _noop_receive, _noop_send)
        # 坏令牌（替身的 verify_token 认所有串）之外，再验一条"没有凭据必须被拒"的路径
        rejected = {}

        async def send(message):
            if message.get("type") == "http.response.start":
                rejected["status"] = message.get("status")

        await app_module.AuthMiddleware(pass_through)(
            _scope(path="/api/config"), _noop_receive, send)
        return rejected

    rejected = asyncio.run(main())
    assert scope_ok["__seen__"] == [{"username": "u1", "is_admin": False}]
    assert rejected.get("status") == 401
