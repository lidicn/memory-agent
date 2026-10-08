"""2 期第十二轮 :201-202 的「声明语义 ↔ 直接断言」第二批（UNVERIFIED 只准减，2026-10-08）。

`scripts/scan_claimed_semantics.py` 的基线里有 23 条"文案声明了某个语义、但没有用例断它"的登记。
本文件收掉其中五格，五格的共同点是**现有用例一次都没碰到那个函数**（`grep -rl` 在 tests 里零命中），
所以"覆盖了"这句话一直是别人替它背的：

| 格 | docstring 的承诺 | 本文件断的那一半 |
| --- | --- | --- |
| `app.py::_is_trusted_source` | 无法解析 IP 时视为不可信（fail-closed） | 空/坏/公网/保留段一律 False，loopback/RFC1918/ULA 才 True —— 缺的是 False 那侧；**补这格时量出 `is_private` 比 docstring 宽，代码按 fail-closed 收紧**（见 §1 第二条用例的 docstring） |
| `auth.py::_trusted_proxy_networks` | 解析不了的条目跳过并记 WARNING（跳过 = fail-closed） | 坏条目被跳过**且不退化成"谁都信"**；配置改动后缓存必须跟着换 |
| `mcp_tokens.py::migrate_legacy` | 幂等，可重复执行 | 第二次执行**返回 0 且不再改动任何条目**（含 legacy 那格的重入） |
| `task_record.py::upsert_task_record` | 幂等写入（UNIQUE(task_id, period_key)） | 同键两次 ⇒ 一行、record_id 相同、data_json 被第二次覆盖 |
| `store.py::make_event_id` | 用原始高精度时间戳做哈希，避免同秒事件相互覆盖 | 同输入必同输出；**只差微秒**必须是两个 id；换实体必须是两个 id |

写法约束（与第一批同一口径）：每条承诺都要有**对偶档**——只断"好的那侧"的门，
把坏那侧改成 fail-open 照样全绿（run29/run30 各档的变异腿已经演示过两次这一族）。
所以 `_is_trusted_source` 的参数表里必须有 `None`/`"garbage"`/`"8.8.8.8"`，
`_trusted_proxy_networks` 必须断"坏条目不会换来 0.0.0.0/0"，`migrate_legacy` 必须断第二次返回 0。
十三条变异腿（run31 档 T01–T13）逐条把这些对偶档退回去，全部必须红；其中 T03 就是"退回
`is_private` 写法"，它证明 §1 那批 False 行不是随手写的期望，而是真在守一段真被收紧过的口径。
"""

import hashlib
import ipaddress
import json
import logging
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import auth as auth_mod
from memory_agent.app import _is_trusted_source  # noqa: E402
from memory_agent.auth import _trusted_proxy_networks  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.mcp_tokens import PREFIX_LEN, MCPTokenStore, _hash  # noqa: E402
from memory_agent.store import Store, make_event_id  # noqa: E402
from memory_agent.task_record import upsert_task_record  # noqa: E402


# ── 1. app._is_trusted_source：dbg_ 令牌那道来源闸 ──────────────────────────

@pytest.mark.parametrize("client_ip", [
    "127.0.0.1",
    "::1",
    "10.1.2.3",
    "172.16.5.6",
    "172.17.0.1",        # Docker bridge 网关：容器化部署时 dbg_ 请求的真实来源
    "192.168.2.100",
    "fc00::1",
    "fd12:3456::1",
    "::ffff:10.1.2.3",   # IPv4-mapped 拆回 v4 后按同一口径
])
def test_is_trusted_source_accepts_only_loopback_rfc1918_and_ula(client_ip):
    assert _is_trusted_source(client_ip) is True


@pytest.mark.parametrize("client_ip", [
    "8.8.8.8",
    "100.64.1.2",        # CGNAT 段：旧口径与新口径都不信，这里钉住不许漂
    "169.254.1.1",       # 链路本地
    "192.0.2.1",         # TEST-NET-1
    "198.51.100.1",      # TEST-NET-2
    "203.0.113.7",       # TEST-NET-3
    "240.0.0.1",         # 保留
    "0.0.0.0",
    "fe80::1",           # IPv6 链路本地
    "2001:db8::1",       # IPv6 文档段
    "::ffff:8.8.8.8",
])
def test_is_trusted_source_rejects_reserved_documented_and_link_local_ranges(client_ip):
    """本批补断言时量出来的那条偏差（结论 = 收紧代码，不是放宽期望）。

    Python 的 `ip_address().is_private` 把上面这些段也返回 True（实测 3.11 与本机 3.13
    同口径：203.0.113.7 / 2001:db8::1 / 192.0.2.1 / 198.51.100.1 / 169.254.1.1 /
    0.0.0.0 / 240.0.0.1 / fe80::1 全 True，只有 100.64.1.2 False）。docstring 写的是
    "内网段"，照 `is_private` 写法放行面比承诺的宽 ⇒ 代码改成 RFC1918 + ULA 实名段，
    方向是 fail-closed。没有用例依赖过旧行为（`grep -rn _is_trusted_source tests` 本批前零命中）。
    """
    assert _is_trusted_source(client_ip) is False


@pytest.mark.parametrize("bad", [None, "", "   ", "garbage", "192.168.2.999", "1.2.3.4/24"])
def test_is_trusted_source_fails_closed_when_the_ip_is_absent_or_unparsable(bad):
    # 对偶档：解析不了 / 没给 ⇒ False。把这条改成 True 就是"外网拿 dbg_ 令牌"的门拆了。
    assert _is_trusted_source(bad) is False


# ── 2. auth._trusted_proxy_networks：坏 CIDR 的口径 ─────────────────────────

def test_trusted_proxy_networks_keeps_the_valid_items():
    nets = _trusted_proxy_networks("192.168.1.0/24, 10.0.0.0/8")
    assert len(nets) == 2
    assert ipaddress.ip_address("192.168.1.7") in nets[0]
    assert ipaddress.ip_address("10.9.9.9") in nets[1]


def test_trusted_proxy_networks_skips_unparsable_items_without_trusting_everyone(caplog):
    # 对偶档：跳过 = 少一条网段，绝不是"谁都信"。把 except 分支换成 0.0.0.0/0 必须红在这里。
    with caplog.at_level(logging.WARNING, logger="memory_agent.auth"):
        nets = _trusted_proxy_networks("not-a-cidr, 10.0.0.0/8")
    assert len(nets) == 1
    assert nets[0] == ipaddress.ip_network("10.0.0.0/8")
    assert not any(n.prefixlen == 0 for n in nets)
    assert ipaddress.ip_address("8.8.8.8") not in nets
    assert "无法解析" in caplog.text


@pytest.mark.parametrize("raw", ["", "   ", ",,,"])
def test_trusted_proxy_networks_treats_blank_config_as_no_trusted_proxy(raw):
    assert _trusted_proxy_networks(raw) == ()


def test_trusted_proxy_networks_cache_follows_the_config_string():
    first = _trusted_proxy_networks("10.0.0.0/8")
    assert _trusted_proxy_networks("10.0.0.0/8") is first      # 同串复用缓存
    changed = _trusted_proxy_networks("172.16.0.0/12")
    assert changed != first
    assert auth_mod._trusted_nets_cache[0] == "172.16.0.0/12"  # 改完配置缓存必须跟着换


# ── 3. mcp_tokens.migrate_legacy：重复执行 ─────────────────────────────────

def _token_cfg(**kw):
    cfg = Config()
    cfg.save = lambda: None
    cfg.agent_tokens = kw.get("agent_tokens", {})
    cfg.mcp_auth_token = kw.get("mcp_auth_token", "")
    cfg.tz_offset_hours = 8
    return cfg


def test_migrate_legacy_replaces_plaintext_with_hash_and_never_keeps_the_secret():
    plain = "mcp-plain-secret-0123456789"
    cfg = _token_cfg(agent_tokens={"butler": plain})
    assert MCPTokenStore(cfg).migrate_legacy() == 1
    rec = cfg.agent_tokens["butler"]
    assert isinstance(rec, dict) and rec["hash"] == _hash(plain)
    assert rec["prefix"] == plain[:PREFIX_LEN] and rec["migrated"] is True
    assert plain not in json.dumps(cfg.agent_tokens)


def test_migrate_legacy_second_run_migrates_nothing_and_changes_nothing():
    plain = "mcp-plain-secret-9876543210"
    cfg = _token_cfg(agent_tokens={"butler": plain})
    store = MCPTokenStore(cfg)
    store.migrate_legacy()
    after_first = json.dumps(cfg.agent_tokens, sort_keys=True)
    assert store.migrate_legacy() == 0
    assert json.dumps(cfg.agent_tokens, sort_keys=True) == after_first


def test_migrate_legacy_does_not_duplicate_the_default_token_on_rerun():
    legacy = "mcp-legacy-default-000000"
    cfg = _token_cfg(agent_tokens={}, mcp_auth_token=legacy)
    store = MCPTokenStore(cfg)
    assert store.migrate_legacy() == 1
    assert list(cfg.agent_tokens) == ["legacy-default"]
    assert cfg.agent_tokens["legacy-default"]["hash"] == _hash(legacy)
    assert store.migrate_legacy() == 0                  # 拆掉 already 判重就红在这条
    assert list(cfg.agent_tokens) == ["legacy-default"]
    assert store.verify(legacy) == "legacy-default"     # 迁完老令牌照样能用，且只命中这一格


# ── 4. task_record.upsert_task_record：同键两次 ───────────────────────────

@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    yield st
    st.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def test_upsert_task_record_same_key_twice_stays_one_row_and_takes_the_new_data(store):
    first = upsert_task_record(store, "daily_summary", "2026-10-08", {"calls": 1},
                               "2026-10-08T03:00:00")
    second = upsert_task_record(store, "daily_summary", "2026-10-08", {"calls": 9},
                                "2026-10-08T03:00:00")
    rows = store.db_query(
        "SELECT record_id, data_json FROM task_records WHERE task_id=? AND period_key=?",
        ("daily_summary", "2026-10-08"))
    assert first == second                              # 同键不换身份
    assert len(rows) == 1                               # 同键不长第二行
    assert json.loads(rows[0]["data_json"]) == {"calls": 9}


def test_upsert_task_record_other_period_key_is_a_new_row(store):
    upsert_task_record(store, "daily_summary", "2026-10-08", {"calls": 1}, "2026-10-08T03:00:00")
    upsert_task_record(store, "daily_summary", "2026-10-09", {"calls": 2}, "2026-10-09T03:00:00")
    rows = store.db_query(
        "SELECT period_key FROM task_records WHERE task_id=? ORDER BY period_key",
        ("daily_summary",))
    assert [r["period_key"] for r in rows] == ["2026-10-08", "2026-10-09"]


# ── 5. store.make_event_id：确定性 + 高精度不相互覆盖 ──────────────────────

def test_make_event_id_is_a_stable_sha1_for_the_same_input():
    a = make_event_id("sensor.living_room_motion", "2026-10-01T19:00:00.123456")
    b = make_event_id("sensor.living_room_motion", "2026-10-01T19:00:00.123456")
    assert a == b
    assert a == hashlib.sha1(b"sensor.living_room_motion|2026-10-01T19:00:00.123456").hexdigest()
    assert len(a) == 40 and all(c in "0123456789abcdef" for c in a)


def test_make_event_id_keeps_events_that_differ_only_by_subsecond_timestamp():
    # 对偶档：payload 里去掉 raw_ts（只剩 entity）时这两条会撞成一个 id ⇒ 同秒事件相互覆盖。
    hi = make_event_id("sensor.tv_state", "2026-10-01T19:00:00.100000")
    lo = make_event_id("sensor.tv_state", "2026-10-01T19:00:00.900000")
    assert hi != lo
    other = make_event_id("sensor.tv_volume", "2026-10-01T19:00:00.100000")
    assert other != hi


def test_make_event_id_survives_non_string_timestamps_without_raising():
    import datetime as dt
    made = [make_event_id("sensor.x", v) for v in
            (None, 1727780400, 1727780400.123, dt.datetime(2026, 10, 1, 19, 0, 0))]
    assert len(set(made)) == 4
    assert all(len(x) == 40 for x in made)


# ── 6. 这批登记自身的形状（防止"用例名字对上了但断的不是那条语义"）──────────

def test_the_five_reductions_are_registered_with_both_directions():
    import importlib.util as _u
    spec = _u.spec_from_file_location("g4", os.path.join(
        os.path.dirname(__file__), "..", "scripts", "scan_claimed_semantics.py"))
    gauge = _u.module_from_spec(spec)
    spec.loader.exec_module(gauge)
    keys = [("app.py", "_is_trusted_source"), ("auth.py", "_trusted_proxy_networks"),
            ("mcp_tokens.py", "migrate_legacy"), ("task_record.py", "upsert_task_record"),
            ("store.py", "make_event_id")]
    for key in keys:
        assert key in gauge.REGISTRY, key
        assert key not in gauge.UNVERIFIED, key
        entry = gauge.REGISTRY[key]
        assert "fail-closed" in entry["claims"] or "幂等" in entry["claims"], key
        assert len(entry["cases"]) >= 2, key
        for tfile, tname in entry["cases"]:
            assert tfile and tname, (key, tfile, tname)

