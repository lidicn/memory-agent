"""DCD 20261007 §五 登录限速 裁乙 + 裁丙（#81，2026-10-07）。

裁定原文（`关键决策部/decisions/20261007-MA五件与AF一件-裁定.md` §五）采纳 MA 推荐的
「乙 + 丙」，并明确 **8086 暂不收口**：DB 直连 `http://192.168.2.200:8086` 的依赖真实存在
（`doubao-butler/butler/config.py:134-135`），收口 = 断 DB→MA，必须等 DB 的 service_token
收敛同一批。**软件层因此必须先自证：直连面不能成为限速的旁路。**

| 格 | 改前 | 为什么是缺陷 | 本批 |
|---|---|---|---|
| `/api/auth/login` 的桶键 | 无条件取 XFF **末位**（WO-MA-004 ⑤b） | 末位可信的前提是「这条连接只经过我们那台反代」。8086 直曝时直连请求自带任意末位 = 自造 IP 桶 ⇒ A4 的双桶限速反向失效 | `resolve_client_ip`：开关关 / 对端不在登记网段 → 一律 TCP 对端（三层默认拒绝） |
| Basic Auth 的桶键 | `client_ip or "unknown"`（TCP 对端） | 与 `/login` **两套口径**：⑤a 声称两条入口汇到同一份计数，同一个客户端却落进不同的桶 | 两条入口共用同一个函数 |
| 全局兜底 | 无 | 桶是「各算各的」：换 IP（或编一个头）就能无限重试，桶越多越松 | 60 次/分钟全局预算（三档里取中值），超了统一退避 + WARNING |
| 退避读数 | `int(until - now) + 1` / 文案 `retry // 60 + 1` | 裁丙的退避恰好 60 秒，按原式算出 61 再显示成「2 分钟」——给用户一个当场可证伪的读数 | 秒数向下取整且至少 1 秒；文案向上取整 |

丙刻意**不锁账号**：A4 P3-8 特意避开「跨 IP 各错一次就锁定全局账号」那个账号级 DoS 形状，
预算只让「登录这件事」退避一分钟。`test_budget_breach_locks_nobody_but_the_login_action`
把这条钉住，防止后来人"顺手"把预算改成锁账号。

变异自证（22 条注入腿）反过来抓出本文件一开始漏的一格：把 `if not trust_proxy` 整块删掉，
全套用例照样全绿——「网段为空」和「对端不在网段」两道兜底把行为遮住了。补上的
`test_the_switch_is_the_master_gate_even_with_a_registered_peer` 是唯一能区分「开关」与
「网段」两个条件的格子，也正是运维最容易落进的中间态（CIDR 填了、开关忘了拨）。
"""

import asyncio
import base64
import json
import logging
import types

import pytest
from starlette.requests import Request

from memory_agent import app as app_module
from memory_agent import auth as auth_module
from memory_agent.api import auth_routes as ar
from memory_agent.auth import resolve_client_ip
from memory_agent.config import Config

CADDY_IP = "172.18.0.7"
CADDY_NET = CADDY_IP + "/32"
NEIGHBOR_IP = "172.18.0.8"          # 同一网段、未登记的邻居：不该被信任
VICTIM = "203.0.113.9"              # 真实客户端 IP（反代写进 XFF 末位的那个）


def _cfg(trust_proxy=False, cidrs=""):
    cfg = Config()
    cfg.trust_proxy = trust_proxy
    cfg.trusted_proxy_cidrs = cidrs
    return cfg


def _mgr():
    return auth_module.AuthManager.__new__(auth_module.AuthManager)


@pytest.fixture
def clean_login_state():
    """进程内限速状态跨用例不复位，必须手动清（含裁丙新增的两个全局变量）。"""
    saved = (dict(auth_module._login_fails), dict(auth_module._login_locked),
             list(auth_module._login_global), auth_module._login_global_until)
    auth_module._login_fails.clear()
    auth_module._login_locked.clear()
    auth_module._login_global.clear()
    auth_module._login_global_until = 0.0
    yield
    auth_module._login_fails.clear()
    auth_module._login_fails.update(saved[0])
    auth_module._login_locked.clear()
    auth_module._login_locked.update(saved[1])
    auth_module._login_global[:] = saved[2]
    auth_module._login_global_until = saved[3]


@pytest.fixture
def fake_clock():
    clock = {"t": 2_000_000.0}
    original = auth_module.time
    auth_module.time = types.SimpleNamespace(time=lambda: clock["t"])
    try:
        yield clock
    finally:
        auth_module.time = original


@pytest.fixture
def clean_proxy_cache():
    """可信网段的解析结果按配置串缓存；不清就会读到上一条用例的解析产物。"""
    saved = auth_module._trusted_nets_cache
    auth_module._trusted_nets_cache = ("", ())
    yield
    auth_module._trusted_nets_cache = saved


# ── 裁乙：口径本身 ────────────────────────────────────────────────────────────

def test_switch_off_ignores_forwarded_for_entirely():
    """默认档（生产默认）：XFF 写什么都没用，一律 TCP 对端。

    末位法在改前是"谁都信"：任何人都能在直连 8086 时自带 `X-Forwarded-For`，
    让限速器把每一次尝试记成不同的客户端。
    """
    forged = ",".join(f"10.{i}.0.1" for i in range(100))
    assert resolve_client_ip(VICTIM, forged, trust_proxy=False,
                             trusted_proxy_cidrs=CADDY_NET) == VICTIM


def test_the_switch_is_the_master_gate_even_with_a_registered_peer():
    """开关是**主闸**：网段登记了、对端就是那台代理，只要 `trust_proxy` 关着就不看 XFF。

    这条是变异档 M01 逼出来的实测盲区：把 `if not trust_proxy: return peer_ip` 整块删掉，
    其余用例照样全绿——因为别的用例要么开关开着，要么对端压根不在登记网段里，
    「网段为空」和「对端核对」顺带把行为兜住了。只有这一格能把"开关"和"网段"两个
    条件区分开，而它恰好是运维最可能落进的中间态：CIDR 填了、开关忘了拨（或反过来
    想先关着观察）。口径上这时必须按 TCP 对端算。
    """
    assert resolve_client_ip(CADDY_IP, VICTIM, trust_proxy=False,
                             trusted_proxy_cidrs=CADDY_NET) == CADDY_IP
    assert resolve_client_ip(CADDY_IP, f"{VICTIM}, 198.51.100.2", trust_proxy=False,
                             trusted_proxy_cidrs=CADDY_IP) == CADDY_IP


def test_switch_on_still_ignores_xff_when_peer_is_not_registered():
    """开了开关 ≠ 信任所有人：只有请求方 IP 落在登记网段里才看 XFF。"""
    assert resolve_client_ip(NEIGHBOR_IP, f"1.1.1.1, {VICTIM}", trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == NEIGHBOR_IP


def test_trusted_peer_gets_the_last_xff_element_not_the_first():
    """WO-MA-004 ⑤b 的那半条仍然成立：可信代理之后追加的真实客户端 IP 在**末位**。"""
    assert resolve_client_ip(CADDY_IP, f"{VICTIM}, 198.51.100.2", trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == "198.51.100.2"
    assert resolve_client_ip(CADDY_IP, VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == VICTIM


def test_bare_ip_and_cidr_both_register_and_the_rest_of_the_subnet_does_not():
    """`172.18.0.7` 与 `172.18.0.7/32` 等价（单 IP 最小信任面）；同段其他地址不沾光。"""
    for raw in (CADDY_IP, CADDY_NET):
        assert resolve_client_ip(CADDY_IP, VICTIM, trust_proxy=True,
                                 trusted_proxy_cidrs=raw) == VICTIM, raw
    assert resolve_client_ip(NEIGHBOR_IP, VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == NEIGHBOR_IP


def test_switch_on_with_no_registered_network_is_fail_closed():
    """开关开了却忘了登记网段：退回 TCP 对端，绝不退化成"谁都信"。"""
    assert resolve_client_ip(NEIGHBOR_IP, VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs="") == NEIGHBOR_IP


def test_unparseable_peer_is_never_treated_as_trusted():
    """对端读数拿不到（unix socket / 未知）时按不可信处理，仍然不读 XFF。"""
    assert resolve_client_ip("unknown", VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == "unknown"


def test_bad_cidr_entry_is_skipped_with_a_warning_and_the_good_one_survives(clean_proxy_cache,
                                                                            caplog):
    """坏条目跳过而不是"全放行"，并且必须看得见——静默吞掉配置错误等于运维以为配好了。"""
    with caplog.at_level(logging.WARNING, logger="memory_agent.auth"):
        got = resolve_client_ip(CADDY_IP, VICTIM, trust_proxy=True,
                                trusted_proxy_cidrs="not-a-cidr, " + CADDY_NET)
    assert got == VICTIM                       # 好条目照常生效
    assert any("无法解析" in r.getMessage() for r in caplog.records), caplog.text


def test_cache_is_keyed_on_the_config_string_so_a_new_value_takes_effect(clean_proxy_cache):
    """按原始串缓存 ≠ 永久缓存：改配置后下一个请求必须用新口径。"""
    assert resolve_client_ip(NEIGHBOR_IP, VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == NEIGHBOR_IP
    assert resolve_client_ip(NEIGHBOR_IP, VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs=NEIGHBOR_IP) == VICTIM
    assert resolve_client_ip(NEIGHBOR_IP, VICTIM, trust_proxy=True,
                             trusted_proxy_cidrs=CADDY_NET) == NEIGHBOR_IP


# ── 裁乙：两个入口 + 配置面 ────────────────────────────────────────────────────

def _login_request(peer, xff):
    headers = [(b"host", b"test")]
    if xff:
        headers.append((b"x-forwarded-for", xff.encode()))

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(
        {"type": "http", "http_version": "1.1", "method": "POST",
         "path": "/api/auth/login", "raw_path": b"/api/auth/login",
         "query_string": b"", "scheme": "http", "headers": headers,
         "client": (peer, 45678), "server": ("test", 80)},
        receive=receive)


def test_route_reads_the_switch_from_runtime_config(monkeypatch):
    """`/login` 的桶键走 `_client_ip` → 同一个口径，且开关读的是运行时 config。

    四档：开关关且网段空 / 开关关但网段已登记（对端恰好就是那台代理——只有这一档能证明
    开关是主闸而不是网段在兜底）/ 开关开但对端未登记 / 开关开且对端登记。
    """
    for cfg, expected in ((_cfg(), CADDY_IP),
                          (_cfg(False, CADDY_NET), CADDY_IP),
                          (_cfg(True, NEIGHBOR_IP), CADDY_IP),
                          (_cfg(True, CADDY_NET), VICTIM)):
        monkeypatch.setattr(ar, "runtime",
                            lambda request=None, _c=cfg: types.SimpleNamespace(config=_c))
        assert ar._client_ip(_login_request(CADDY_IP, VICTIM)) == expected


def _basic_login_ip(monkeypatch, cfg, peer, xff):
    """跑一次真实的 Basic Auth 鉴权入口，带回它递给 `login_allowed` 的桶键。"""
    captured = {}

    class _RecordingAuth:
        def __init__(self, config=None):
            pass

        def login_allowed(self, ip, username):
            captured["ip"] = ip
            return True, 0

        def login(self, username, password):
            return {"ok": False}

        def note_login_failure(self, ip, username):
            captured["failed"] = ip

        def note_login_success(self, ip, username):
            captured["succeeded"] = ip

    monkeypatch.setattr(app_module, "get_config", lambda: cfg)
    monkeypatch.setattr(app_module, "AuthManager", _RecordingAuth)
    headers = {"authorization": "Basic " + base64.b64encode(b"u1:wrong").decode(),
               "x-forwarded-for": xff}
    assert app_module.AuthMiddleware(app=None)._authenticate(headers, peer) is None
    # 判定与记账必须是同一个键，否则桶永远凑不满阈值
    assert captured["failed"] == captured["ip"]
    return captured["ip"]


def test_both_login_paths_use_the_same_bucket_key(monkeypatch):
    """⑤a 的承诺是「Basic Auth 与 JWT 登录路径汇到同一份爆破计数」。

    改前两条入口各算各的 IP（Basic 用 TCP 对端、/login 用 XFF 末位），同一个客户端
    在两边落进不同桶 —— "同一份计数"只剩下一句注释。这里让两条路径喂同一份
    (对端, XFF, 配置)，判红条件就是两个读数不相等。
    """
    cfg = _cfg(True, NEIGHBOR_IP)          # 对端可信 → 两条都该取 XFF 末位
    monkeypatch.setattr(ar, "runtime", lambda request=None: types.SimpleNamespace(config=cfg))
    assert _basic_login_ip(monkeypatch, cfg, NEIGHBOR_IP, VICTIM) \
        == ar._client_ip(_login_request(NEIGHBOR_IP, VICTIM)) == VICTIM


def test_basic_path_with_switch_off_uses_tcp_peer(monkeypatch):
    """生产默认档下 Basic 那条入口也不给伪造的头开后门。

    第二手是主闸门那一格：网段登记了、对端就在里面，开关关着 ⇒ 仍按 TCP 对端。
    """
    assert _basic_login_ip(monkeypatch, _cfg(), "10.0.0.5", "1.2.3.4") == "10.0.0.5"
    assert _basic_login_ip(monkeypatch, _cfg(False, CADDY_NET), CADDY_IP, VICTIM) == CADDY_IP


def test_switch_defaults_off_and_env_can_open_it_both_ways(monkeypatch):
    """默认关（不开口信任代理），且 env 能双向拨：只能单向收紧的开关不是开关。

    键名与 AF 的 `AUTOFORGE_TRUST_PROXY` 对齐（裁定：便于跨仓对表）。
    """
    from memory_agent.config import get_config

    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    for key in ("MA_TRUST_PROXY", "MA_TRUSTED_PROXY_CIDRS"):
        monkeypatch.delenv(key, raising=False)
    base = Config()
    assert (base.trust_proxy, base.trusted_proxy_cidrs) == (False, "")

    monkeypatch.setenv("MA_TRUST_PROXY", "1")
    monkeypatch.setenv("MA_TRUSTED_PROXY_CIDRS", CADDY_NET)
    cfg = get_config()
    assert cfg.trust_proxy is True
    assert cfg.trusted_proxy_cidrs == CADDY_NET

    monkeypatch.setenv("MA_TRUST_PROXY", "false")
    assert get_config().trust_proxy is False


# ── 裁丙：全局失败预算 ────────────────────────────────────────────────────────

def test_ruling_fixed_the_numbers_and_they_are_locked():
    """30/60/120 三档里 DCD 取中值 60 次/分钟；退避一分钟。写死是要求"改数必须过裁定"。"""
    assert auth_module._GLOBAL_FAIL_BUDGET == 60
    assert auth_module._GLOBAL_WINDOW_SECONDS == 60
    assert auth_module._GLOBAL_BACKOFF_SECONDS == 60


def test_spray_across_many_ips_and_users_hits_one_wall(clean_login_state, fake_clock):
    """预算的意义就在这条：每个桶各只失败一次，双桶谁也到不了阈值，但整个入口已被打了 60 次。"""
    mgr = _mgr()
    budget = auth_module._GLOBAL_FAIL_BUDGET
    for i in range(budget - 1):
        mgr.note_login_failure(f"10.{i // 256}.{i % 256}", f"u{i}")
        assert mgr.login_allowed(f"10.{i // 256}.{i % 256}", f"u{i}")[0] is True
    # 还没打穿：第 60 次之前一切照常
    assert mgr.login_allowed("198.51.100.99", "fresh-user")[0] is True

    mgr.note_login_failure("10.0.0.255", "u-last")
    allowed, retry = mgr.login_allowed("198.51.100.99", "fresh-user")
    assert allowed is False
    assert 0 < retry <= auth_module._GLOBAL_BACKOFF_SECONDS


def test_budget_breach_locks_nobody_but_the_login_action(clean_login_state, fake_clock):
    """丙**不产生账号锁定**：A4 P3-8 避开的那个形状不能从这里重新引进。"""
    mgr = _mgr()
    for i in range(auth_module._GLOBAL_FAIL_BUDGET):
        mgr.note_login_failure(f"10.0.0.{i % 251}", f"u{i}")
    assert mgr.login_allowed("10.0.0.1", "u0")[0] is False      # 退避中
    assert auth_module._login_locked == {}, \
        f"预算不该产生任何锁定键，实际 {sorted(auth_module._login_locked)}"
    # 用户名桶各自只失败 1 次，远未到 _LOGIN_USER_MAX_FAILS
    assert all(len(v) == 1 for v in auth_module._login_fails.values())


def test_breach_is_visible_in_the_log(clean_login_state, fake_clock, caplog):
    """「悄悄多等一分钟」和「入口正在被打穿」是两种读数，后者需要有人知道。"""
    mgr = _mgr()
    with caplog.at_level(logging.WARNING, logger="memory_agent.auth"):
        for i in range(auth_module._GLOBAL_FAIL_BUDGET):
            mgr.note_login_failure(f"10.0.0.{i % 251}", f"u{i}")
    assert any("预算打穿" in r.getMessage() for r in caplog.records), caplog.text


def test_backoff_expires_and_the_window_is_not_cumulative(clean_login_state, fake_clock):
    """预算是**每分钟**的：窗口翻过去就不该继续拦人，否则等于永久限速。"""
    mgr = _mgr()
    for i in range(auth_module._GLOBAL_FAIL_BUDGET - 1):
        mgr.note_login_failure(f"10.0.0.{i % 251}", f"u{i}")
    assert len(auth_module._login_global) == auth_module._GLOBAL_FAIL_BUDGET - 1

    fake_clock["t"] += auth_module._GLOBAL_WINDOW_SECONDS + 1
    mgr.note_login_failure("10.0.0.250", "u-next-window")       # 第 60 次落在新窗口
    assert mgr.login_allowed("198.51.100.7", "whoever")[0] is True
    assert auth_module._login_global_until <= fake_clock["t"]

    # 打穿一次 → 拦人；退避期过后 → 照常放行
    for i in range(auth_module._GLOBAL_FAIL_BUDGET):
        mgr.note_login_failure(f"10.1.0.{i % 251}", f"v{i}")
    assert mgr.login_allowed("198.51.100.7", "whoever")[0] is False
    fake_clock["t"] += auth_module._GLOBAL_BACKOFF_SECONDS + 1
    assert mgr.login_allowed("198.51.100.7", "whoever")[0] is True


def test_a_successful_login_does_not_clear_the_global_budget(clean_login_state, fake_clock):
    """成功登录只清自己那两个桶。

    否则"边被打边正常登录"等于给防守方开了一个免费清账的口子，预算永不过效。
    """
    mgr = _mgr()
    for i in range(auth_module._GLOBAL_FAIL_BUDGET - 1):
        mgr.note_login_failure(f"10.0.0.{i % 251}", f"u{i}")
    before = len(auth_module._login_global)
    mgr.note_login_success("10.0.0.1", "u0")
    assert len(auth_module._login_global) == before
    assert "ip:10.0.0.1" not in auth_module._login_fails         # 自己的桶照清

    mgr.note_login_failure("10.0.0.2", "u1")                     # 补上第 60 次
    assert mgr.login_allowed("198.51.100.7", "whoever")[0] is False


def test_per_ip_bucket_still_locks_at_five(clean_login_state, fake_clock):
    """对偶档（防修过头）：加了全局兜底不能把 A4 的双桶换掉——单 IP 5 次仍是 30 分钟锁定。"""
    mgr = _mgr()
    for i in range(auth_module._LOGIN_MAX_FAILS):
        mgr.note_login_failure("1.2.3.4", f"attacker{i}")
    assert "ip:1.2.3.4" in auth_module._login_locked
    allowed, retry = mgr.login_allowed("1.2.3.4", "attacker0")
    assert allowed is False
    assert retry > auth_module._GLOBAL_BACKOFF_SECONDS            # 来自 IP 锁定，不是预算


def test_remaining_seconds_never_read_zero(clean_login_state, fake_clock):
    """还剩不到 1 秒时 `int()` 会给 0，路由侧的文案于是显示"0 分钟后重试"= 现在就能再试。"""
    mgr = _mgr()
    for i in range(auth_module._GLOBAL_FAIL_BUDGET):
        mgr.note_login_failure(f"10.0.0.{i % 251}", f"u{i}")
    fake_clock["t"] += auth_module._GLOBAL_BACKOFF_SECONDS - 0.5
    allowed, retry = mgr.login_allowed("198.51.100.7", "whoever")
    assert allowed is False and retry >= 1


def test_login_handler_reports_the_backoff_in_minutes_not_seconds(monkeypatch):
    """429 的文案是给用户看的：60 秒退避显示成「2 分钟」是一个当场可证伪的读数。"""
    class _Auth:
        def __init__(self, retry):
            self.retry = retry

        def login_allowed(self, ip, username):
            return False, self.retry

    rt = types.SimpleNamespace(config=_cfg())
    monkeypatch.setattr(ar, "runtime", lambda request=None: rt)

    async def call():
        return await ar.login(_login_request(NEIGHBOR_IP, VICTIM))

    for retry, phrase in ((60, "1 分钟"), (61, "2 分钟"), (1800, "30 分钟")):
        rt.auth = _Auth(retry)
        response = asyncio.run(call())
        body = json.loads(response.body)
        assert response.status_code == 429 and body["ok"] is False
        assert phrase in body["error"], body
