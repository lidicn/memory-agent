"""第二期审计第三批的回归锁：MA-05 / MA-06 / MA-17 / MA-18 / MA-19 / MA-20 / MA-21 / MA-23，
外加本批**自己撞出来的一个缺口**（`_num` 接不住非有限浮点，见 §1）。

审计原文（`doc/审计报告/2期/` 第二、七、八、九、十轮）给的是失效描述，本文件把每一条换成
"真调用 + 真状态字"。几条关键的改前实测：

| 编号 | 位置 | 改前实测 | 本文件的锁 |
|---|---|---|---|
| MA-18 | `api/config_routes.py` | `{"tz_offset_hours": -999}` **类型收敛顺利通过**并落盘，之后 `now_local()` 全线 `ValueError`；±24h 的限制藏在 `datetime.timezone` 内部（lesson 108：判据要问"消费函数拿到它会做什么"） | §3：400 + 点名参数 + config 未变 + `reload_config` 未被调 |
| MA-19 | 同输入两个结果 | `polling_interval=0` 走 `/api/config` 存成 0（忙循环），走采集端点被钳到 900；采集端点**只有下界**，`interval_minutes=1e9` → 6e10 秒 = 采集再也不跑（lesson 87） | §2 唯一真源表 + §3 / §4 两入口读同一张表 |
| MA-17 | `app.py` `af_metrics.json` | 20 线程各写不同 key → 落盘 **5** 条；文件截断后一次**完全正常**的 ingest → 原 3 条永久消失且返回 `ok: True`（第一环是 `except Exception: store = {}`） | §7：并发 20 条齐全 / 损坏时拒写且**磁盘字节不变** / 查询侧把"损坏"与"没有"分成两种读数 |
| MA-20 | `api/debug_routes.py` 淘汰路径 | 同一条级联清理漏了 `_CONV_LOCKS`：N=500/2000/10000 个 `conversation_id` 时两个有上限的容器精确停在 200，这个容器线性同增 | §5：跑 1000 个会话后 `len(_CONV_LOCKS) == len(_RUNS)` |
| MA-21 | `auth.py note_login_failure` | 只在**当前键**上过滤时间戳，键本身永不移除（过期路径全仓 `pop` 0 次）⇒ 1000/10000/50000 次失败 → 1250/10250/**50250** 条常驻 | §6：窗口过期后的一次写入顺带把 10000 个陈旧键扫干净 |
| MA-23 | 四处 `rt.reload_config()` | 同步重建 HAClient / 关 MariaDB / 重置 chroma 缓存，单次把事件循环钉住数秒（60 并发实测 tick 1 vs 1003） | §3 / §4 的卸载证据（工作线程名）+ §9 AST：路由层不许再出现**直接调用** |
| MA-05 | `mcp_server.py` admin 审计通道 | docstring 与留痕都写"全量"，实际恒传 `exact_member=True` ⇒ 只返回公共记忆。**假证据**比功能缺失更重，故本批选**乙**：改声明与留痕，不给 admin 扩权（扩权要走 DCD） | §8：公共/成员库实测收窄读数 + 三处谎言行消失 |
| MA-06 | `read_self_diary` / `generate_self_diary` | 不传 `exact_member` ⇒ 绕过收窄层读全量；当前日记恒公共所以未泄露，**将来加成员归属就立刻变成跨成员读取** | §8：两处 store 级调用必须带 `exact_member=True` |

**§1 是本批自己发现的**（不在审计清单里）：`_num` 的 `except (TypeError, ValueError)`
接不住 `int(float('inf'))` 抛的 `OverflowError`（第八轮 lesson 86 量过同一形状），而
`json.loads` 默认接受 `Infinity` / `NaN` 字面量 ⇒ body 能把**真** `float('inf')` 递进来；
`NaN` 更阴险——`nan < lo` 和 `nan > hi` 恒为 False，上下界校验对它形同不存在。
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import json
import logging
import os
import pathlib
import sys
import threading
import time
import types

import pytest
from starlette.requests import Request

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent import app as app_module  # noqa: E402
from memory_agent import auth as auth_module  # noqa: E402
from memory_agent.api import collect_routes as cr  # noqa: E402
from memory_agent.api import config_routes as cfr  # noqa: E402
from memory_agent.api import debug_routes as dr  # noqa: E402
from memory_agent.api import deps as api_deps  # noqa: E402
from memory_agent.api.config_routes import WRITABLE_FIELDS  # noqa: E402
from memory_agent.config import NUMERIC_BOUNDS, Config, bounded  # noqa: E402
from memory_agent.store import Store  # noqa: E402

_ADMIN = {"sub": "u", "is_admin": True}
_MAIN_THREAD = threading.current_thread().name


def _src_text(*rel) -> str:
    return pathlib.Path(os.path.join(_SRC, *rel)).read_text(encoding="utf-8")


# ── 1. `_num` 接不住非有限值（本批自撞的缺口）────────────────────────────────

def test_json_literal_infinity_is_a_real_float_object():
    """前提要自己量：body 里的 `Infinity` 不是字符串，是**真** `float('inf')`。

    `json_body` 走 `json.loads`，而它默认认 `Infinity` / `-Infinity` / `NaN` 三个字面量
    （严格 JSON 不认）。这一步不成立，下面两组用例就是空转。
    """
    parsed = json.loads('{"v": Infinity}')
    assert isinstance(parsed["v"], float) and parsed["v"] == float("inf")
    nan = json.loads('{"v": NaN}')["v"]
    assert isinstance(nan, float) and nan != nan          # NaN != NaN


@pytest.mark.parametrize("raw,cast,msg", [
    (float("inf"), int, "x 必须是整数"),          # int(inf) → OverflowError，改前直接逃出去
    (float("-inf"), int, "x 必须是整数"),
    (float("inf"), float, "x 必须是有限数字"),     # 过了 cast，交给有限性这一档
    (float("-inf"), float, "x 必须是有限数字"),
    (float("nan"), float, "x 必须是有限数字"),     # 改前：`nan<lo` 与 `nan>hi` 恒 False ⇒ 收下
    (float("nan"), int, "x 必须是整数"),
    ("inf", int, "x 必须是整数"),
    ("nan", float, "x 必须是有限数字"),
])
def test_num_turns_every_non_finite_shape_into_a_rejection(raw, cast, msg):
    """改前三条读数：`inf`+int → **未捕获 OverflowError**（=HTTP 500）；
    `nan`+float 与 `"nan"`+float → `(nan, None)`，一路写进 config。"""
    assert api_deps._num(raw, name="x", cast=cast, lo=1, hi=100) == (None, msg)


@pytest.mark.parametrize("raw,expected", [
    (5, 5), ("5", 5), (0, 0), (100, 100), (10 ** 6, 10 ** 6),
])
def test_num_still_treats_ordinary_numbers_as_values(raw, expected):
    """收紧非有限值不许顺手把普通值也改掉：这条是 §1 那组的对照组。"""
    assert api_deps._num(raw, name="x", cast=int, lo=0, hi=10 ** 9) == (expected, None)
    assert api_deps._num(raw, name="x", cast=float, lo=0, hi=10 ** 9) == (float(expected), None)


def test_num_bounds_still_fire_for_finite_values():
    assert api_deps._num(-1, name="x", cast=int, lo=0) == (None, "x 不得小于 0")
    assert api_deps._num(101, name="x", cast=int, hi=100) == (None, "x 不得大于 100")
    assert api_deps._num(14.0, name="x", cast=float, lo=-12.0, hi=14.0) == (14.0, None)


# ── 2. `NUMERIC_BOUNDS`：两入口的唯一真源 ───────────────────────────────────

def _writable_numeric_keys() -> set:
    cfg = Config()
    return {
        k for k in WRITABLE_FIELDS
        if isinstance(getattr(cfg, k), (int, float)) and not isinstance(getattr(cfg, k), bool)
    }


def test_bounds_table_is_exactly_the_writable_numeric_surface():
    """表 = HTTP 写得动的数值键全集，不多不少（实测 24 个；`Config` 共 72 个数值字段）。

    新增一个可写数值键却忘了配区间 ⇒ 这条先响，而不是留到下一个审计轮。
    """
    assert set(NUMERIC_BOUNDS) == _writable_numeric_keys()


@pytest.mark.parametrize("key", sorted(NUMERIC_BOUNDS))
def test_every_shipped_default_sits_inside_its_own_band(key):
    """区间表若把自己的出厂默认值判成越界，就是表写错了——24 个键逐个量。"""
    default = getattr(Config(), key)
    lo, hi = NUMERIC_BOUNDS[key]
    assert lo <= default <= hi, f"{key} 默认值 {default!r} 不在 {(lo, hi)}"


@pytest.mark.parametrize("key", sorted(NUMERIC_BOUNDS))
def test_bands_are_closed_intervals_with_the_right_shape(key):
    lo, hi = NUMERIC_BOUNDS[key]
    assert isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo < hi
    assert bounded(key, lo) == lo and bounded(key, hi) == hi
    assert bounded(key, lo - 1) == lo and bounded(key, hi + 1) == hi


def test_bounded_leaves_unknown_keys_alone():
    """没配区间的键（只能经 config.json / 环境变量进来）不在这张表的责任范围内。"""
    assert bounded("no_such_key", 10 ** 30) == 10 ** 30


def test_tz_band_matches_the_consumer_that_hidden_the_limit():
    """MA-18 的判据形状：消费方是 `datetime.timezone(timedelta(hours=…))`，标准库内部
    限制 ±24h；`insights/models.py` 早就按这个数在钳 ⇒ 本表与它取同一数，不自立新数。"""
    assert NUMERIC_BOUNDS["tz_offset_hours"] == (-12.0, 14.0)
    for hours in (-12.0, 14.0):
        datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=hours)))
    with pytest.raises(ValueError):
        datetime.timezone(datetime.timedelta(hours=999999))


# ── harness：路由级 POST ────────────────────────────────────────────────────

def _post(handler, *, rt=None, raw=b"{}", user=None, path="/api/x"):
    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {"type": "http", "method": "POST", "path": path, "query_string": b"",
             "headers": [(b"content-type", b"application/json")], "app": app,
             "state": {"user": user} if user is not None else {}}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return asyncio.run(handler(Request(scope, receive)))


def _fake_rt(**attrs):
    """只带被测键的 config 桩：`update_config_api` 只对 body 里出现的键做 `getattr`。"""
    cfg = types.SimpleNamespace(**attrs)
    cfg.save = lambda: None
    threads = []

    def _reload():
        threads.append(threading.current_thread().name)
        return cfg

    return types.SimpleNamespace(config=cfg, reload_config=_reload, threads=threads), cfg


# ── 3. `/api/config`：越界 → 400，且什么都不改 ──────────────────────────────

@pytest.mark.parametrize("key,current,sent", [
    ("tz_offset_hours", 8.0, -999),          # MA-18 的原始用例
    ("tz_offset_hours", 8.0, 1e5),
    ("tz_offset_hours", 8.0, float("inf")),  # §1 那条缺口在端点上的落点
    ("tz_offset_hours", 8.0, float("nan")),
    ("polling_interval", 3600, 0),           # MA-19：这里改前**存成 0**
    ("polling_interval", 3600, -1),
    ("polling_interval", 3600, 10 ** 12),
    ("llm_temperature", 0.7, 1e9),
    ("scene_graph_sample_rate", 0.5, 1.5),
    ("ha_assist_memory_top_k", 5, 9999),
    ("redis_port", 6379, 70000),
    ("vision_cooldown_s", 120, -5),
])
def test_config_api_rejects_out_of_band_numbers_by_name(key, current, sent):
    rt, cfg = _fake_rt(**{key: current})
    resp = _post(cfr.update_config_api, rt=rt, raw=json.dumps({key: sent}).encode(), user=_ADMIN)
    body = json.loads(resp.body)
    assert resp.status_code == 400, body
    assert key in body["error"], body            # 点名参数，不静默替调用方改值
    assert getattr(cfg, key) == current          # 越界值一个字节都没落进 config
    assert rt.threads == []                      # 也没触发那次最重的 reload


@pytest.mark.parametrize("key,current,accepted", [
    ("tz_offset_hours", 8.0, 12.0),
    ("tz_offset_hours", 8.0, -12.0),
    ("polling_interval", 3600, 900),
    ("polling_interval", 3600, 86400),
    ("redis_port", 6379, 6380),
])
def test_config_api_still_accepts_legal_numbers_and_reloads_off_the_loop(key, current, accepted):
    rt, cfg = _fake_rt(**{key: current})
    resp = _post(cfr.update_config_api, rt=rt, raw=json.dumps({key: accepted}).encode(), user=_ADMIN)
    body = json.loads(resp.body)
    assert resp.status_code == 200, body
    assert getattr(cfg, key) == accepted
    assert key in body["changed"]
    assert rt.threads and rt.threads[0] != _MAIN_THREAD          # MA-23 的卸载证据


def test_config_api_rejects_a_bool_and_an_explicit_null():
    rt, cfg = _fake_rt(polling_interval=3600)
    resp = _post(cfr.update_config_api, rt=rt, raw=b'{"polling_interval": true}', user=_ADMIN)
    assert resp.status_code == 400 and "polling_interval" in json.loads(resp.body)["error"]
    assert cfg.polling_interval == 3600

    rt2, cfg2 = _fake_rt(polling_interval=3600)
    resp2 = _post(cfr.update_config_api, rt=rt2, raw=b'{"polling_interval": null}', user=_ADMIN)
    assert resp2.status_code == 400, json.loads(resp2.body)
    assert cfg2.polling_interval == 3600


def test_config_api_never_returns_500_for_a_numeric_band_violation():
    """把 §1 的前提走完：客户端真的能发 `Infinity` 这个词，端点必须给 400 而不是炸。"""
    rt, _ = _fake_rt(polling_interval=3600)
    resp = _post(cfr.update_config_api, rt=rt, raw=b'{"polling_interval": Infinity}', user=_ADMIN)
    assert resp.status_code == 400, json.loads(resp.body)


# ── 4. 采集端点：保留"夹紧"语义，数字换成同一张表 ───────────────────────────

def _collect_rt():
    return _fake_rt(
        polling_enabled=True, polling_mode="interval", polling_time="03:00",
        polling_interval=3600, data_retention_days=30, rooms={}, excluded_entities=[],
    )


@pytest.mark.parametrize("body,expected", [
    ({"interval_minutes": 1e9}, NUMERIC_BOUNDS["polling_interval"][1]),   # 改前：6e10 秒照存
    ({"interval_minutes": 1}, NUMERIC_BOUNDS["polling_interval"][0]),     # 下界语义保持
    ({"polling_interval": -5}, 900),
    ({"polling_interval": 10 ** 12}, 86400),
    ({"data_retention_days": -1}, 0),
    ({"data_retention_days": 10 ** 9}, 3650),
])
def test_collect_config_clamps_to_the_shared_table(body, expected):
    rt, cfg = _collect_rt()
    resp = _post(cr.collect_config, rt=rt, raw=json.dumps(body).encode(), user=_ADMIN,
                 path="/api/collect/config")
    assert resp.status_code == 200, json.loads(resp.body)
    key = "data_retention_days" if "data_retention_days" in body else "polling_interval"
    assert getattr(cfg, key) == expected


@pytest.mark.parametrize("body", [
    {"interval_minutes": "abc"},
    {"interval_minutes": float("inf")},        # §1：改前 OverflowError 逃出 → 500
    {"polling_interval": float("inf")},
    {"data_retention_days": {}},
])
def test_collect_config_returns_400_for_junk_instead_of_crashing(body):
    rt, cfg = _collect_rt()
    resp = _post(cr.collect_config, rt=rt, raw=json.dumps(body).encode(), user=_ADMIN,
                 path="/api/collect/config")
    assert resp.status_code == 400, json.loads(resp.body)
    assert cfg.polling_interval == 3600
    assert rt.threads == []                    # 被拒的配置不该顺带重建一遍客户端


def test_collect_config_reload_runs_in_a_worker_thread():
    rt, _ = _collect_rt()
    resp = _post(cr.collect_config, rt=rt, raw=json.dumps({"polling_interval": 1800}).encode(),
                 user=_ADMIN, path="/api/collect/config")
    assert resp.status_code == 200
    assert rt.threads and rt.threads[0] != _MAIN_THREAD


def test_collect_enable_offloads_its_reload_too():
    rt, cfg = _fake_rt(polling_interval=3600)
    resp = _post(cr.collect_enable, rt=rt, raw=json.dumps({"enabled": True}).encode(),
                 user=_ADMIN, path="/api/collect/enable")
    assert resp.status_code == 200, json.loads(resp.body)
    assert cfg.polling_enabled is True
    assert rt.threads and rt.threads[0] != _MAIN_THREAD


# ── 5. MA-20：`_CONV_LOCKS` 跟着 `_CONV` 一起被驱逐 ─────────────────────────

@pytest.fixture
def clean_debug_state():
    saved = {
        "_RUNS": dict(dr._RUNS),
        "_CONV": dict(dr._CONV),
        "_CONV_LOCKS": dict(dr._CONV_LOCKS),
    }
    saved_order = list(dr._CONV_ORDER)
    for d in (dr._RUNS, dr._CONV, dr._CONV_LOCKS):
        d.clear()
    dr._CONV_ORDER.clear()
    yield
    for name, snap in saved.items():
        target = getattr(dr, name)
        target.clear()
        target.update(snap)
    dr._CONV_ORDER.clear()
    dr._CONV_ORDER.extend(saved_order)


def _debug_run(i: int, conv: str) -> dr.DebugRun:
    return dr.DebugRun(run_id=f"r{i}", instruction="x", model="m", mode="tools",
                       max_rounds=1, conversation_id=conv, temperature=0.2)


def test_conv_locks_do_not_outlive_the_runs_that_created_them(clean_debug_state):
    """改前实测：N=500/2000/10000 个 `conversation_id`（键直接来自请求体）时，
    `_RUNS` / `_CONV` 精确停在 200，`_CONV_LOCKS` 却与 N 同增。"""
    n = 1000

    async def churn():
        for i in range(n):
            conv = f"c{i}"
            await dr._get_conv_lock(conv)         # 路由层的真实入口：懒创建锁
            dr._CONV[conv] = [{"role": "user", "content": "x"}]
            dr._CONV_ORDER.append(conv)
            run = _debug_run(i, conv)
            dr._register(run)
            run.finish()                          # 让它在下一轮淘汰时成为可驱逐对象

    asyncio.run(churn())
    assert len(dr._RUNS) == dr._MAX_RUNS
    assert len(dr._CONV_LOCKS) == len(dr._RUNS), \
        f"锁容器 {len(dr._CONV_LOCKS)} 条而 run 只有 {len(dr._RUNS)} 条 ⇒ 有孤儿锁"
    assert set(dr._CONV_LOCKS) == {r.conversation_id for r in dr._RUNS.values()}
    assert set(dr._CONV_LOCKS) == set(dr._CONV)


def test_register_cascades_conv_locks_without_awaiting(clean_debug_state):
    """把"这里不需要 `_CONV_LOCKS_GUARD`"那句论断变成可判红的结构锁：
    `_register` 全程无 await ⇒ 单线程协作式调度下驱逐与取锁不会交错。"""
    tree = ast.parse(_src_text("memory_agent", "api", "debug_routes.py"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_register")
    assert not any(isinstance(n, ast.Await) for n in ast.walk(fn))
    popped = {(n.func.value.id if isinstance(n.func.value, ast.Name) else n.func.value.attr)
              for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "pop"}
    assert {"_CONV", "_CONV_LOCKS"} <= popped, popped


# ── 6. MA-21：登录限速容器的全局清理 ────────────────────────────────────────

@pytest.fixture
def clean_login_state():
    """隔离登录限速的**全部**进程内状态。

    加进 `_login_global` / `_login_global_until`（DCD 20261007 §五 裁丙）是必须的，
    不是装饰：本文件下面那条 5000 次失败的清扫用例会把全局预算打穿，而预算一旦打穿
    就把 `_login_global_until` 推到未来一分钟 —— 不清它就会顺着模块状态漏到后面的
    用例，让别处"还能登录"的断言随机判红（限速状态是模块级的，跨用例不自动复位）。
    """
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
    """只替换 `auth` 模块命名空间里的 `time`（该模块内 `time.` 只有 `time.time()`）。"""
    clock = {"t": 2_000_000.0}
    original = auth_module.time
    auth_module.time = types.SimpleNamespace(time=lambda: clock["t"])
    try:
        yield clock
    finally:
        auth_module.time = original


def _mgr():
    return auth_module.AuthManager.__new__(auth_module.AuthManager)


def test_prune_login_keeps_exactly_what_still_has_to_be_kept(clean_login_state):
    """保留条件写反过来说更清楚：窗口内还要凑阈值的计数、还没到点的锁定必须留下。"""
    now = 1_000_000.0
    w = auth_module._LOGIN_WINDOW_SECONDS
    auth_module._login_fails.update({
        "ip:fresh": [now - 1], "ip:stale": [now - w - 1], "ip:empty": [],
    })
    auth_module._login_locked.update({
        "user:locked": now + 600, "user:expired": now - 1, "user:right_now": now,
    })
    auth_module._prune_login(now)
    assert set(auth_module._login_fails) == {"ip:fresh"}
    assert set(auth_module._login_locked) == {"user:locked"}


def test_failure_sweep_bounds_the_key_set_after_the_window_passes(clean_login_state, fake_clock):
    """改前实测：1000/10000/50000 次失败 → 1250/10250/**50250** 条常驻（键永不移除）。

    这里用一个假时钟把"窗口已过"在几十毫秒内演完：5000 个用户各失败一次（各自 IP，
    免得 IP 键先到阈值变成锁定测试），窗口翻过去之后再失败一次 —— 那一次顺带把
    10000 个陈旧键扫干净。
    """
    mgr = _mgr()
    for i in range(5000):
        mgr.note_login_failure(f"10.0.{i // 256}.{i % 256}", f"u{i}")
    assert len(auth_module._login_fails) == 10000        # 窗口内：一个都不该清

    fake_clock["t"] += auth_module._LOGIN_WINDOW_SECONDS + 1
    mgr.note_login_failure("9.9.9.9", "u_late")
    assert set(auth_module._login_fails) == {"ip:9.9.9.9", "user:u_late"}, \
        f"陈旧键没被顺带扫掉：剩 {len(auth_module._login_fails)} 条"


def test_expired_lockout_stops_blocking_and_is_pruned(clean_login_state, fake_clock):
    """锁定键只在"到期"后清；未到期必须继续拦人，否则限速就成了摆设。"""
    mgr = _mgr()
    for i in range(auth_module._LOGIN_MAX_FAILS):
        mgr.note_login_failure("1.2.3.4", f"attacker{i}")
    assert "ip:1.2.3.4" in auth_module._login_locked
    allowed, secs = mgr.login_allowed("1.2.3.4", "attacker0")
    assert allowed is False and secs > 0

    fake_clock["t"] += auth_module._LOGIN_LOCK_SECONDS + 1
    mgr.note_login_failure("5.5.5.5", "latecomer")
    assert auth_module._login_locked == {}
    assert mgr.login_allowed("1.2.3.4", "attacker0")[0] is True


def test_a_live_counter_is_never_swept_away(clean_login_state, fake_clock):
    """清理只碰失去意义的键：还在窗口内的失败必须原样留着，否则阈值永远凑不满。

    离阈值还差一次（3/5）时推进到窗口的第 290 秒再失败一次 —— 四次都还在窗口内，
    一条都不该被清；此时也不该出现锁定键。
    """
    mgr = _mgr()
    for i in range(auth_module._LOGIN_MAX_FAILS - 2):
        mgr.note_login_failure("8.8.8.8", f"u{i}")
    assert len(auth_module._login_fails["ip:8.8.8.8"]) == 3

    fake_clock["t"] += auth_module._LOGIN_WINDOW_SECONDS - 10
    mgr.note_login_failure("8.8.8.8", "u_late")
    assert len(auth_module._login_fails["ip:8.8.8.8"]) == 4
    assert set(auth_module._login_fails) == {
        "ip:8.8.8.8", "user:u0", "user:u1", "user:u2", "user:u_late"}
    assert auth_module._login_locked == {}
    assert mgr.login_allowed("8.8.8.8", "u0")[0] is True


# ── 7. MA-17：`af_metrics.json` 的读→改→写 ──────────────────────────────────

@pytest.fixture
def metrics_file(tmp_path, monkeypatch):
    path = str(tmp_path / "af_metrics.json")
    monkeypatch.setattr(app_module, "_METRICS_PATH", path)
    return path


def _slow_reads(monkeypatch, delay=0.02):
    """把 读→改→写 的窗口拉宽：不锁住就**必然**丢更新，而不是碰运气。"""
    orig = pathlib.Path.read_text

    def slow(self, *a, **k):
        text = orig(self, *a, **k)
        time.sleep(delay)
        return text

    monkeypatch.setattr(pathlib.Path, "read_text", slow)


def test_twenty_concurrent_ingests_all_land(metrics_file, monkeypatch):
    """改前实测：20 线程各写不同 key → 落盘 **5** 条（丢 15 条）。"""
    _slow_reads(monkeypatch)

    def worker(i):
        app_module._metrics_ingest_sync(f"k{i}", {"automation_id": f"a{i}", "runs": i})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store = json.loads(pathlib.Path(metrics_file).read_text(encoding="utf-8"))
    assert len(store) == 20, f"落盘 {len(store)} 条，应 20 条"
    assert set(store) == {f"k{i}" for i in range(20)}


def test_ingest_replaces_the_same_dedupe_key_instead_of_doubling(metrics_file):
    """`dedupe_key` 的幂等语义在改造后必须原样保留。"""
    for run in (1, 2, 3):
        resp = _post(app_module.metrics_ingest_endpoint,
                     raw=json.dumps({"automation_id": "a1", "dedupe_key": "k1", "runs": run}).encode())
        assert json.loads(resp.body) == {"ok": True, "dedupe_key": "k1"}
    store = json.loads(pathlib.Path(metrics_file).read_text(encoding="utf-8"))
    assert list(store) == ["k1"] and store["k1"]["runs"] == 3


def test_ingest_runs_off_the_event_loop(metrics_file, monkeypatch):
    seen = []

    def spy(key, payload):
        seen.append(threading.current_thread().name)

    monkeypatch.setattr(app_module, "_metrics_ingest_sync", spy)
    resp = _post(app_module.metrics_ingest_endpoint, raw=b'{"automation_id": "a1"}')
    assert resp.status_code == 200
    assert seen and seen[0] != _MAIN_THREAD


def test_query_returns_the_stored_metrics(metrics_file):
    pathlib.Path(metrics_file).write_text('{"k1": {"automation_id": "a1"}}', encoding="utf-8")
    body = json.loads(_post(app_module.metrics_query_endpoint, raw=b"").body)
    assert body == {"ok": True, "metrics": {"k1": {"automation_id": "a1"}}}


@pytest.mark.parametrize("junk", [b'{"a": 1', b"not json", b"[]", b'{"a": 1}}', b"\xef\xbb"])
def test_a_corrupt_history_is_never_overwritten_by_a_healthy_write(metrics_file, junk):
    """改前实测：文件截断后一次**完全正常**的 ingest → 落盘 1 条，原 3 条永久消失，
    返回体还写着 `ok: True`。第一环就是 `except Exception: store = {}`。
    """
    pathlib.Path(metrics_file).write_bytes(junk)
    before = pathlib.Path(metrics_file).read_bytes()
    resp = _post(app_module.metrics_ingest_endpoint,
                 raw=json.dumps({"automation_id": "new", "dedupe_key": "new"}).encode())
    assert resp.status_code == 500, json.loads(resp.body)
    assert json.loads(resp.body)["error"] == "metrics_file_unreadable"
    assert pathlib.Path(metrics_file).read_bytes() == before   # 磁盘字节一个没动


def test_query_distinguishes_corrupt_from_absent(metrics_file, caplog):
    """两种"空"必须读得出区别：改前它们返回**完全相同**的形状。"""
    body_missing = json.loads(_post(app_module.metrics_query_endpoint, raw=b"").body)
    assert body_missing == {"ok": True, "metrics": {}}        # 没有文件：契约不变，无 error 键

    pathlib.Path(metrics_file).write_text("{\"a\": 1", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="metrics.ingest"):
        body_corrupt = json.loads(_post(app_module.metrics_query_endpoint, raw=b"").body)
    assert body_corrupt["metrics"] == {}
    assert body_corrupt["error"] == "metrics_file_unreadable"  # 损坏：多一个可判红的键
    assert "读不出来" in " ".join(r.getMessage() for r in caplog.records)


def test_metrics_load_raises_instead_of_authorising_erasure(metrics_file):
    """`_metrics_load` 不许返回空表——返回空表就等于授权"用一次正常写入把历史抹掉"。"""
    pathlib.Path(metrics_file).write_text("{\"a\": 1", encoding="utf-8")
    with pytest.raises(Exception):
        app_module._metrics_load()
    pathlib.Path(metrics_file).write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        app_module._metrics_load()
    pathlib.Path(metrics_file).write_text("{\"a\": 1}", encoding="utf-8")
    assert app_module._metrics_load() == {"a": 1}


def test_metrics_write_is_atomic_via_the_repo_helper():
    """落盘范式对齐同仓已做对的 `home_profile.write_profile_atomic`（mkstemp+fsync+replace）。"""
    tree = ast.parse(_src_text("memory_agent", "app.py"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_metrics_ingest_sync")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    names = {getattr(c.func, "attr", None) or getattr(c.func, "id", None) for c in calls}
    assert "write_profile_atomic" in names, names
    withs = {(getattr(item.context_expr, "id", None) or getattr(item.context_expr, "attr", None))
             for n in ast.walk(fn) if isinstance(n, ast.With) for item in n.items}
    assert "_METRICS_LOCK" in withs, withs        # 读→改→写全程持锁
    # 旧形状不许回来：`Path(...).write_text(...)`
    assert not any(isinstance(c.func, ast.Attribute) and c.func.attr == "write_text" for c in calls)


# ── 8. MA-05 / MA-06：MCP 面的声明、留痕与收窄 ──────────────────────────────

def _agent_store(tmp_path) -> Store:
    st = Store(str(tmp_path / "ma05.db"), tz_offset_hours=8.0)
    st.init_schema()
    for tag, member in (("pub", ""), ("alice", "member:alice"), ("bob", "member:bob")):
        st.add_agent_memory("s1", f"diary {tag}", "self_diary", "[]", "[]", 365,
                            state="live", member_id=member)
    return st


def test_member_id_less_narrowed_read_returns_only_public_rows(tmp_path):
    """MA-05 选**乙**的事实依据：admin 走的是公共档，不是全量 ⇒ 留痕必须与之一致。"""
    st = _agent_store(tmp_path)
    try:
        narrowed = [m["text"] for m in
                    st.list_agent_memories("all", "", 500, "", exact_member=True)]
        everything = [m["text"] for m in st.list_agent_memories("all", "", 500, "")]
        named = [m["text"] for m in
                 st.list_agent_memories("all", "", 500, "member:bob", exact_member=True)]
        assert narrowed == ["diary pub"]
        assert len(everything) == 3                      # 对照：不传才是全量（内部 sweep 档）
        assert named == ["diary bob"]                    # 点名才是逐成员通道
    finally:
        st.close()


def test_mcp_audit_trail_no_longer_claims_a_capability_it_does_not_have():
    """假证据比功能缺失更重：留痕写 "full / cross-member"，实际只返回公共记忆。"""
    src = _src_text("memory_agent", "mcp_server.py")
    for lie in ("AUDIT: full member_id-less listing", "AUDIT: cross-member recall",
                "全量查询需 admin scope"):
        assert lie not in src, f"仍留着：{lie}"
    assert "AUDIT: public-only listing (member_id omitted)" in src
    assert "AUDIT: public-scope recall (member_id omitted)" in src


def test_tool_spec_does_not_advertise_a_full_member_view():
    """ToolSpec 是 butler/agent 读的能力契约，谎言行同样不能留（MA-05 同一族）。"""
    src = _src_text("memory_agent", "tool_schema.py")
    assert "可查全量" not in src
    assert "admin 缺省也只返回公共记忆" in src


def test_diary_reads_go_through_the_narrowing_layer():
    """MA-06：`store.list_agent_memories` 的两处调用必须显式收窄。

    当前日记恒公共（`write_self_diary` 不带 member_id），所以这两行的**读数不变**；
    锁住的是"将来给日记加成员归属"那一刻——那时未收窄就是跨成员读取。
    """
    tree = ast.parse(_src_text("memory_agent", "mcp_server.py"))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or getattr(node.func, "attr", None) != "to_thread":
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Attribute) and arg.attr == "list_agent_memories"
                    and isinstance(arg.value, ast.Attribute) and arg.value.attr == "store"):
                sites.append(node)
    assert len(sites) == 2, f"store 级日记读取应有 2 处，实测 {len(sites)}"
    for node in sites:
        assert any(k.arg == "exact_member"
                   and isinstance(k.value, ast.Constant) and k.value.value is True
                   for k in node.keywords), \
            f"mcp_server.py:{node.lineno} 绕过收窄层"


# ── 9. MA-23：路由层不许再直接调用 reload_config ───────────────────────────

def _api_sources():
    api_dir = os.path.join(_SRC, "memory_agent", "api")
    return {name: pathlib.Path(os.path.join(api_dir, name)).read_text(encoding="utf-8")
            for name in sorted(os.listdir(api_dir)) if name.endswith(".py")}


def _to_thread_calls(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Attribute) and fn.attr == "to_thread":
                out.append(n)
    return out


def test_no_route_touches_reload_config_outside_a_worker_thread():
    """`reload_config` 是同步重活（重建 HAClient / 关 MariaDB / 重置 chroma 缓存）。

    在 async handler 里直接调用它 = 把事件循环钉住数秒（60 并发实测 tick 1 vs 1003）。
    判据口径：`rt.reload_config` 这个属性访问**只能**出现在 `asyncio.to_thread(...)` 的
    参数位。写回 `rt.reload_config()` 会多出一个 Call.func 节点 ⇒ 红；写成
    `await rt.reload_config()` 同样红（它不阻塞循环，但会当场 TypeError）。
    """
    offenders = []
    for name, src in _api_sources().items():
        tree = ast.parse(src)
        legal = set()
        for call in _to_thread_calls(tree):
            for arg in list(call.args) + [kw.value for kw in call.keywords]:
                legal.add(id(arg))
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr == "reload_config" and id(n) not in legal:
                offenders.append(f"{name}:{n.lineno}")
    assert offenders == [], offenders


def test_the_reload_offload_sites_are_all_still_there():
    """MA-23 点到四处：`config_routes` / `collect_enable` / `collect_config` / `ha_save_rooms`。
    删掉其中一处的卸载会让这条先响（而不是等心跳 tick 归 0 才发现）。"""
    counts = {}
    for name, src in _api_sources().items():
        tree = ast.parse(src)
        hits = sum(1 for call in _to_thread_calls(tree)
                   for arg in call.args
                   if isinstance(arg, ast.Attribute) and arg.attr == "reload_config")
        if hits:
            counts[name] = hits
    assert counts == {"collect_routes.py": 2, "config_routes.py": 1, "ha_routes.py": 1}, counts
