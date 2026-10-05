"""任务表 #63 回归锁：`behavior_routes` 的数字入参口径 + 同步链离开事件循环。

五族缺陷：族 1 与族 5 会把 handler 打成未捕获异常（Starlette 500），族 2/3/4 **不崩，只是算错或算漏**
（`元宝/memory-agent_第十五轮审计报告.md`:200 说「最严重的两个 P1 **都不崩溃，只是算错**」，
族 2–4 正是那一类，所以它们靠审计很难被发现，是覆盖率现读把它们筛出来的）：

1. 同一个 handler 里 `days` 有守卫、其余数字入参没有：`limit=abc` 直接把 handler 打成
   未捕获 ``ValueError``（Starlette 500），`int(x or 3)` 把 0/`""`/``False`` 静默改成默认档，
   还把 config 的 `process_mining_min_variant_support` 顶死在字面量 3 上。
   审计点名的四处之外，顺着"每个入参要么落点要么显式拒"这条口径在本文件又查到三处现行：
   `rule_channel_audit` 的 `except ValueError: limit = 100`（吞掉后照样 200）、
   `negative_sample_suggestions` 的 `int(body.get("min_count") or 3)`、
   `rule_channel_false_positive` 的裸 `int(trigger_id)`。
2. `limit=-1` 下推到 SQLite 的 `LIMIT ?`：**`LIMIT -1` = 无上限**，一个"最多 100 条"的
   接口变成全表返回。
3. 同步 DB / 纯 CPU 落在 MainThread：`AuthMiddleware` 是纯 ASGI 中间件、Store 用全局 RLock，
   一次 50ms 的同步占用就把整个循环钉住（心跳协程 tick 增量为 **0**）。
4. `change_attribution.search_candidate_causes` 里同一个 `lookback_days` 两个出口：
   窗口走 `clamp_days`、衰减半衰期用原值 ⇒ 传极值时 `temporal_proximity` 对所有候选一律
   ≈1.0，`change_attribution.py:397` 的 `candidates.sort(key=lambda x: -x["confidence"])`
   静默失效（排序键全相等，输出顺序退化成输入顺序）。
5. 改前**从未被任何测试调用过**的 handler 里还藏着同族 1 的现行缺陷：`behaviors_run` 把
   `body.get("window_minutes")` 原样交给 `activity_inference.py:260`
   `int(window_minutes or config or 15)`——`"abc"` → 500、`0`/`""`/`False` → 静默换 config 档、
   `10**12` → `now - timedelta(minutes=…)` OverflowError → 500。
   这一族不是审计点名的，是覆盖率现读（改前 371 条语句从未执行）逐块读过去撞出来的：
   未测的七条 handler 里其余六条的入参本来就有 `try/except → 400` + 夹紧，缺陷只在这一条。

取数口径（`元宝/memory-agent_第十五轮审计报告.md`:200 的硬约束：排序类用例必须用与生产一致的数据形状）：
``Store.list_behavior_events`` 是 ``ORDER BY server_ts DESC``，
``mcp_server._fetch_attribution_events`` 是 ``ORDER BY server_ts ASC``——
本文件按各端点自己的真实出口形状喂数据，并在 ``test_production_read_shapes_are_asc_desc``
里把这两个方向钉死，免得哪天有人用"方便断言"的顺序写用例（那种用例测的是顺序不是逻辑）。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import math
import os
import tempfile
import threading
import time
import types
from urllib.parse import quote
from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

import memory_agent.home_profile as home_profile_module
from memory_agent import change_attribution as ca
from memory_agent.api import behavior_routes as br
from memory_agent.day_bounds import DAY_WINDOW_MAX
from memory_agent.store import Store

PERSON = "Member0"
CHANGE_TS = "2026-09-20T00:00:00"


# ── harness ──────────────────────────────────────────────────────────────

def _store(n_bad=7):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    for i in range(n_bad):
        st.insert_behavior_event({
            "server_ts": f"2026-10-01T19:{i:02d}:00",
            "day": "2026-10-01", "room": "客厅", "action": "vlm_failed",
            "status": "vlm_failed", "raw_response": "x" * 300,
            "persons": [{"name": PERSON}],
        })
    return st, path


@pytest.fixture
def store():
    st, path = _store()
    yield st
    st.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def _request(*, method="GET", query="", body=None, rt=None, user=None):
    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {"type": "http", "method": method, "path": "/api/behaviors/x",
             "query_string": query.encode("utf-8"), "headers": [], "app": app,
             "state": {"user": user} if user is not None else {}}

    async def receive():
        return {"type": "http.request",
                "body": json.dumps(body or {}).encode("utf-8"), "more_body": False}

    return Request(scope, receive)


class _StubActivity:
    """只记录"收到的值"：本文件量的是入参转换，不是挖矿算法。"""

    def __init__(self):
        self.calls = []

    def _rec(self, name, a, k):
        # 位置参也要留影：`mine_drift(None, None, days, None, …)` 把承重的那个值走位置传，
        # 只记 kwargs 等于看不见它（族 5 的 `live=1&days=` 用例就靠这一格自证）。
        self.calls.append((name, {"args": a, **k}))
        return {"ok": True}

    def mine_process(self, *a, **k):
        return self._rec("mine_process", a, k)

    def mine_drift(self, *a, **k):
        return self._rec("mine_drift", a, k)

    def audit_rule_recall(self, *a, **k):
        return self._rec("audit_rule_recall", a, k)

    def run(self, *a, **k):
        return self._rec("run", a, k)

    def current_behaviors(self, minutes):
        self.calls.append(("current_behaviors", {"minutes": minutes}))
        return []


def _rt(store, activity=None):
    return types.SimpleNamespace(
        store=store, activity=activity or _StubActivity(), rule_engine=None,
        alert_dispatcher=None,
        config=types.SimpleNamespace(tz_offset_hours=8.0,
                                     vision_snapshot_retention_days=30),
        insights=None,
    )


def _payload(resp):
    return json.loads(resp.body)


def _get(handler, store, query, *, activity=None):
    rt = _rt(store, activity)
    return asyncio.run(handler(_request(query=query, rt=rt, user={"sub": "u"})))


def _post(handler, store, body, *, activity=None):
    rt = _rt(store, activity)
    return asyncio.run(handler(_request(method="POST", body=body, rt=rt,
                                        user={"sub": "u"})))


# ── 0. 生产取数形状（排序类用例的前提，先自证）──────────────────────────

def test_production_read_shapes_are_asc_desc(store):
    """`list_behavior_events` 给 DESC、`_fetch_attribution_events` 给 ASC：两个方向都在这锁住。"""
    rows = store.list_behavior_events(limit=100)
    ts = [r["server_ts"] for r in rows]
    assert ts == sorted(ts, reverse=True), "behavior_events 的读取方向变了，本文件的 DESC 用例前提失效"

    from memory_agent.mcp_server import _fetch_attribution_events
    got = [r["server_ts"] for r in _fetch_attribution_events(store, 365)]
    assert got == sorted(got), "归因取数走的是 ASC，改了方向就不能再拿它当 causal 用例的口径"


# ── 1. `_num`：没给 → 默认档；给了 → 要么收下要么拒 ──────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, 20), ("", 20), ("  ", 20), ("7", 7), ("0", 0),
])
def test_num_absent_falls_back_and_zero_is_a_value(raw, expected):
    """`""`/缺省走默认档，但显式的 `0` 是**值**，不能被 `x or 3` 吞掉。"""
    val, err = br._num(raw, name="limit", default=20)
    assert err is None
    assert val == expected


@pytest.mark.parametrize("raw,msg", [
    ("abc", "limit 必须是整数"),
    ("1.5", "limit 必须是整数"),
    (-1, "limit 不得小于 1"),
    (101, "limit 不得大于 100"),
    (True, "limit 必须是数字"),
])
def test_num_rejects_naming_the_param(raw, msg):
    val, err = br._num(raw, name="limit", default=20, lo=1, hi=100)
    assert val is None and err == msg


def test_num_float_cast_keeps_fraction():
    val, err = br._num("0.25", name="min_score", cast=float, default=None, lo=0.0)
    assert (val, err) == (0.25, None)


# ── 2. bad-cases 的 limit：500 / 全表绕过 / 静默改值 ─────────────────────

def test_bad_cases_limit_rejects_non_numeric(store):
    """改前：`limit=abc` → 未捕获 ValueError → 500（app 没有 exception_handlers）。"""
    resp = _get(br.behaviors_bad_cases_list, store, "limit=abc")
    assert resp.status_code == 400
    assert "limit" in _payload(resp)["error"]


@pytest.mark.parametrize("query", ["limit=-1", "limit=0", "limit=101"])
def test_bad_cases_limit_rejects_out_of_range(store, query):
    """改前 `limit=-1` → 200 且把 7 条全给：SQLite 的 `LIMIT -1` 是无上限，夹紧被绕过。"""
    resp = _get(br.behaviors_bad_cases_list, store, query)
    assert resp.status_code == 400, (query, resp.status_code, _payload(resp))


def test_bad_cases_limit_accepts_in_range_and_defaults(store):
    resp = _get(br.behaviors_bad_cases_list, store, "limit=2")
    assert resp.status_code == 200
    assert _payload(resp)["count"] == 2
    assert len(_payload(resp)["events"]) == 2

    resp = _get(br.behaviors_bad_cases_list, store, "")
    assert resp.status_code == 200
    assert _payload(resp)["count"] == 7          # 表内 7 条 < 默认 20


# ── 3. 挖矿/审计三个 POST 端点的数字入参 ────────────────────────────────

@pytest.mark.parametrize("handler,body,frag", [
    (br.behaviors_mine_process, {"min_variant_support": "abc"}, "min_variant_support"),
    (br.behaviors_mine_process, {"bucket_sec": "abc"}, "bucket_sec"),
    (br.behaviors_mine_process, {"min_cases_per_room": "abc"}, "min_cases_per_room"),
    (br.behaviors_mine_process, {"min_case_events": "abc"}, "min_case_events"),
    (br.behaviors_mine_drift, {"window_size": "abc"}, "window_size"),
    (br.behaviors_mine_drift, {"min_score": "abc"}, "min_score"),
    (br.behaviors_audit_rule_recall, {"min_near_miss": "abc"}, "min_near_miss"),
])
def test_non_numeric_body_param_is_400_not_500(store, handler, body, frag):
    """改前这七条全是未捕获 ValueError → 500；`days` 有守卫、其余没有。"""
    act = _StubActivity()
    resp = _post(handler, store, body, activity=act)
    assert resp.status_code == 400, (body, resp.status_code)
    assert frag in _payload(resp)["error"]
    assert act.calls == [], "参数已被拒，不该再往下推给引擎"


@pytest.mark.parametrize("body,frag", [
    ({"min_variant_support": 0}, "min_variant_support"),
    ({"min_cases_per_room": 0}, "min_cases_per_room"),
    ({"days": 0}, "days"),
    ({"days": 91}, "days"),
])
def test_out_of_range_body_param_is_400(store, body, frag):
    act = _StubActivity()
    resp = _post(br.behaviors_mine_process, store, body, activity=act)
    assert resp.status_code == 400, (body, resp.status_code)
    assert frag in _payload(resp)["error"]
    assert act.calls == []


def test_bucket_sec_zero_is_a_legal_value_and_reaches_the_engine(store):
    """`algo_kernel.py:413`：`bucket_sec=0` = 不聚合（逐事件，调试用）。合法 0 不许被拒，
    也不许被 `int(x or 3)` 之类的写法改成默认档。"""
    act = _StubActivity()
    resp = _post(br.behaviors_mine_process, store, {"days": 3, "bucket_sec": 0},
                 activity=act)
    assert resp.status_code == 200
    assert act.calls[0][1]["bucket_sec"] == 0


def test_drift_bucket_sec_zero_reaches_the_engine(store):
    """同一条口径在 `mine_drift` 那侧也要成立：`activity_inference.py:889` 只在
    `bucket_sec is None` 时才回落到 `cfg.drift_bucket_sec`，传 0 就要原样下去。"""
    act = _StubActivity()
    resp = _post(br.behaviors_mine_drift, store, {"days": 14, "bucket_sec": 0},
                 activity=act)
    assert resp.status_code == 200
    assert act.calls[0][1]["bucket_sec"] == 0


def test_min_score_zero_reaches_the_engine(store):
    act = _StubActivity()
    resp = _post(br.behaviors_mine_drift, store, {"days": 14, "min_score": 0.0},
                 activity=act)
    assert resp.status_code == 200
    assert act.calls[0][1]["min_score"] == 0.0


def test_absent_knob_stays_absent_so_config_governs(store):
    """改前 `int(body.get("min_variant_support") or 3)`：字面量 3 覆盖了
    `activity_inference` 里 `None → 读 config.process_mining_min_variant_support` 的那条路。"""
    act = _StubActivity()
    resp = _post(br.behaviors_mine_process, store, {"days": 3}, activity=act)
    assert resp.status_code == 200
    kw = act.calls[0][1]
    assert kw["min_variant_support"] is None
    assert kw["min_cases_per_room"] is None and kw["min_case_events"] is None


def test_true_is_not_one(store):
    """JSON 的 `true` 不是"1 次"，它是个类型错误。"""
    act = _StubActivity()
    resp = _post(br.behaviors_audit_rule_recall, store, {"min_near_miss": True},
                 activity=act)
    assert resp.status_code == 400
    assert "min_near_miss" in _payload(resp)["error"]
    assert act.calls == []


def test_audit_rule_recall_default_min_near_miss_is_2(store):
    act = _StubActivity()
    resp = _post(br.behaviors_audit_rule_recall, store, {"days": 14}, activity=act)
    assert resp.status_code == 200
    assert act.calls[0][1]["min_near_miss"] == 2


# ── 3b. 同一族的其余三处（顺着落点口径查到的现行，审计没点名）────────────

def test_rule_channel_audit_limit_is_no_longer_swallowed(store):
    """改前：`except ValueError: limit = 100` —— 非数字被**静默改成默认档**再返回 200，
    调用方以为自己要的那一档生效了（仓内门禁 `swallow-and-claim-ok` 的形状）。"""
    for query in ("limit=abc", "limit=0", "limit=-1", "limit=501"):
        resp = _get(br.rule_channel_audit, store, query)
        assert resp.status_code == 400, (query, resp.status_code, _payload(resp))
        assert "limit" in _payload(resp)["error"]
    assert _get(br.rule_channel_audit, store, "").status_code == 200


@pytest.mark.parametrize("body", [{"min_count": "abc"}, {"min_count": 0},
                                  {"min_count": True}])
def test_negative_sample_min_count_rejects_instead_of_swallowing(store, body):
    """改前：`int(body.get("min_count") or 3)` 把 0 静默改成 3、把 ``true`` 读成 1。
    成簇门槛是这条负样本红线的松紧位，替调用方改数就是改判据。"""
    resp = _post(br.negative_sample_suggestions, store, body)
    assert resp.status_code == 400, (body, resp.status_code)
    assert "min_count" in _payload(resp)["error"]


def test_negative_sample_min_count_absent_still_uses_three(store):
    resp = _post(br.negative_sample_suggestions, store, {})
    assert resp.status_code == 200
    assert _payload(resp)["ok"] is True


@pytest.mark.parametrize("body,frag", [
    ({"rule_id": "r", "trigger_id": "abc"}, "trigger_id"),
    ({"rule_id": "r", "trigger_id": True}, "trigger_id"),
    ({"rule_id": "r", "trigger_id": 0}, "不得小于"),
    ({"rule_id": "r"}, "缺少 rule_id / trigger_id"),
    ({"trigger_id": 1}, "缺少 rule_id / trigger_id"),
])
def test_false_positive_trigger_id_is_a_number_or_nothing(store, body, frag):
    """改前：`int(trigger_id)` 无 except ⇒ `trigger_id=abc` 打成 500。
    `rule_triggers.trigger_id INTEGER PRIMARY KEY AUTOINCREMENT` 恒 ≥1，所以 `lo=1`。"""
    resp = _post(br.rule_channel_false_positive, store, body)
    assert resp.status_code == 400, (body, resp.status_code)
    assert frag in _payload(resp)["error"]


# ── 4. intent 的 limit：吞异常 → 点名参数的 400 ──────────────────────────

def test_intent_limit_rejects_non_numeric(store):
    """改前 `try: int(...) except: limit = 3`，把 "abc" 静默吞成默认档。"""
    resp = _get(br.behaviors_intent, store, "limit=abc")
    assert resp.status_code == 400
    assert "limit" in _payload(resp)["error"]


@pytest.mark.parametrize("query", ["limit=0", "limit=6", "limit=-1"])
def test_intent_limit_rejects_out_of_range(store, query):
    """改前 `max(1, min(limit, 5))` 把越界夹进区间，调用方收到 200 却不知道被改了。"""
    resp = _get(br.behaviors_intent, store, query)
    assert resp.status_code == 400, query


def test_intent_limit_accepts_in_range(store):
    resp = _get(br.behaviors_intent, store, "limit=5")
    assert resp.status_code == 200


# ── 5. causal 两端点的窗口口径 ───────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "person=Member0&days=1000000000",
    "person=Member0&lookback_days=1000000000",
    "person=Member0&days=0",
    "person=Member0&lookback_days=-3",
])
def test_causal_analyze_rejects_out_of_window(store, query):
    """改前 `days=10**9` 一路走到取数（窗口被 `clamp_days` 悄悄改成 3650）。
    现在必须**点名参数**地拒掉：只看状态码不够——"数据不足"也是 400。"""
    resp = _get(br.causal_analyze, store, query)
    assert resp.status_code == 400, (query, _payload(resp))
    assert f"必须在 1-{DAY_WINDOW_MAX} 之间" in _payload(resp)["error"], (query, _payload(resp))


def test_causal_analyze_accepts_the_upper_bound(store):
    """上界是 `DAY_WINDOW_MAX` 本身，不许把合法极值也拒了（口径必须与 clamp_days 一致）。"""
    resp = _get(br.causal_analyze, store, f"person={PERSON}&days={DAY_WINDOW_MAX}")
    assert "必须在" not in _payload(resp)["error"]
    assert "不足" in _payload(resp)["error"], _payload(resp)


@pytest.mark.parametrize("query,expect_frag", [
    ("person=Member0&event_type=tv_on&days=1000000000", "days"),
    ("person=Member0&event_type=tv_on&days=0", "days"),
    ("person=Member0&event_type=tv_on&days=abc", "days"),
])
def test_causal_counterfactual_rejects_bad_window(store, query, expect_frag):
    resp = _get(br.causal_counterfactual, store, query)
    assert resp.status_code == 400
    assert expect_frag in _payload(resp)["error"]


def test_causal_counterfactual_in_range_reaches_the_data_gate(store):
    """合法窗口必须走到业务判定（本表 7 条 < 14 天要求），不能被新守卫顺手拦在门口。"""
    resp = _get(br.causal_counterfactual, store,
                f"person={PERSON}&event_type=tv_on&days={DAY_WINDOW_MAX}")
    assert "必须在" not in _payload(resp)["error"]
    assert "不足" in _payload(resp)["error"], _payload(resp)


# ── 6. half_life 与窗口同口径（不崩、只是算错的那一族）───────────────────

def _attr_events():
    """按 `_fetch_attribution_events` 的 ASC 形状造两组同计数、不同年龄的候选事件。

    tv_on 在变化点前 1 天与 1000 天各一次；door_open 在 1000 天与 1000 天各两次。
    两者 c=2/b=0 ⇒ `correlation`/`lift`/`count_score` 完全相同，`proximity` 是唯一的
    区分量。改前半衰期取原值 5e8 天，两个候选的 `temporal_proximity` 都四舍五入成 1.0，
    confidence 相等 ⇒ 排序退化成输入顺序（看起来像答案，其实没排）。
    """
    cdt = datetime.fromisoformat(CHANGE_TS)
    rows = []
    for action, ages in (("tv_on", (1, 1000)), ("door_open", (1000, 1000))):
        for age in ages:
            rows.append({"server_ts": (cdt - timedelta(days=age)).strftime("%Y-%m-%dT21:00:00"),
                         "action": action, "persons_json": json.dumps([{"name": PERSON}]),
                         "room": "客厅"})
    rows.sort(key=lambda r: r["server_ts"])          # ASC = 生产出口形状
    return rows


def _conf_map(cands):
    return {c["event_type"]: c for c in cands if c["event_type"] in ("tv_on", "door_open")}


def test_half_life_uses_the_same_clamped_window_as_the_scan():
    events = _attr_events()
    huge = _conf_map(ca.search_candidate_causes(events, PERSON, CHANGE_TS, 10 ** 9))
    assert huge["tv_on"]["temporal_proximity"] < 1.0, huge
    assert huge["tv_on"]["temporal_proximity"] > huge["door_open"]["temporal_proximity"], huge
    assert huge["tv_on"]["confidence"] > huge["door_open"]["confidence"], huge

    capped = _conf_map(ca.search_candidate_causes(events, PERSON, CHANGE_TS, DAY_WINDOW_MAX))
    assert huge == capped, "传 10**9 与传 3650 必须给同一份判定：窗口、半衰期、描述文案同一个口径"


def test_normal_lookback_is_untouched():
    """收口不许动常规档：7 天窗口的半衰期仍是 3.5 天，1000 天前的点照旧落在窗口外。

    期望值按公式独立算：窗口内唯一那条 tv_on 的时间戳是变化点前 1 天的 21:00，
    距 00:00 的变化点 0.125 天，半衰期 3.5 天 ⇒ `exp(-ln2 · 0.125/3.5) ≈ 0.975`。
    """
    events = _attr_events()
    normal = _conf_map(ca.search_candidate_causes(events, PERSON, CHANGE_TS, 7))
    assert set(normal) == {"tv_on"}, normal
    age_days = 0.125                       # 2026-09-19T21:00 距 2026-09-20T00:00
    expect = math.exp(-math.log(2) * age_days / (7 / 2.0))
    assert normal["tv_on"]["temporal_proximity"] == pytest.approx(expect, abs=0.005), normal
    assert normal["tv_on"]["count"] == 1, normal
    assert normal["tv_on"]["description"].startswith("变化前7天内"), normal


# ── 7. 同步链离开事件循环（runtime 真响 + AST 门）────────────────────────

def _heartbeat_ticks(ms_hold):
    """在同一个 loop 里跑心跳，返回 handler 期间心跳走过的格数。"""
    ticks = {"n": 0}

    async def heartbeat(stop):
        while not stop.is_set():
            ticks["n"] += 1
            await asyncio.sleep(0.001)

    async def run():
        stop = threading.Event()
        task = asyncio.create_task(heartbeat(stop))
        await asyncio.sleep(0.005)
        before = ticks["n"]
        await ms_hold()
        stop.set()
        await task
        return ticks["n"] - before

    return asyncio.run(run())


def test_home_profile_does_not_block_the_loop(store, monkeypatch):
    """改前实测：handler 里 50ms 同步占用期间，同循环心跳 tick 增量 = **0**，
    且 `build_profile` / `list_agent_memories` 都落在 MainThread。"""
    seen = []
    orig = home_profile_module.build_profile

    def slow_build(st, max_chars=4000):
        seen.append(threading.current_thread().name)
        import time
        time.sleep(0.05)
        return orig(st, max_chars=max_chars)

    monkeypatch.setattr(home_profile_module, "build_profile", slow_build)

    async def call():
        rt = _rt(store)
        return await br.behaviors_home_profile(
            _request(query="max_chars=800", rt=rt, user={"sub": "u"}))

    ticks = _heartbeat_ticks(call)
    assert seen and seen[0] != "MainThread", seen
    assert ticks > 0, f"handler 期间心跳一格都没走（同步占用把循环钉住了）：{ticks}"


def test_predictions_and_intent_run_off_the_loop(store, monkeypatch):
    from memory_agent import behavior_predictor as bp
    from memory_agent import intent_inference as ii

    names = []
    monkeypatch.setattr(bp, "predict_arrival_time",
                        lambda *a, **k: names.append(("arrival", threading.current_thread().name)) or {"none": True})
    monkeypatch.setattr(bp, "predict_daily_routine",
                        lambda *a, **k: names.append(("routine", threading.current_thread().name)) or {"none": True})
    monkeypatch.setattr(ii, "infer_intent_sequence",
                        lambda *a, **k: names.append(("intent", threading.current_thread().name)) or [])

    _get(br.behaviors_predictions, store, f"person={PERSON}")
    _get(br.behaviors_intent, store, "")

    assert [n for n, t in names if t == "MainThread"] == [], names
    assert len(names) == 3, names


HEAVY_SYNC_CALLEES = {
    "build_profile", "write_profile_atomic",
    "predict_arrival_time", "predict_daily_routine",
    "infer_intent_sequence", "get_intent_suggestions",
}


def _unguarded_heavy_calls(source: str) -> list[tuple[int, str]]:
    """async handler 里**没有**经 `await` / `asyncio.to_thread` 的重同步调用。

    第五轮量具只认 `rt.store.<m>()` 形状，"把 store 当参数传进同步函数"（以及纯 CPU 的
    预测/推断函数）不进统计——这一格在 HEAD 上一直没人看见，所以这里按函数名点名收。
    """
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        guarded: set[int] = set()
        nested: dict[str, ast.FunctionDef] = {}
        for sub in ast.walk(node):
            if isinstance(sub, ast.FunctionDef):
                nested[sub.name] = sub
            if isinstance(sub, ast.Await):
                for c in ast.walk(sub.value):
                    if isinstance(c, ast.Call):
                        guarded.add(id(c))
            if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "to_thread":
                guarded.add(id(sub))
                for arg in list(sub.args) + [k.value for k in sub.keywords]:
                    if isinstance(arg, ast.Call):
                        guarded.add(id(arg))
                    if isinstance(arg, ast.Name) and arg.id in nested:
                        guarded.update(id(c) for c in ast.walk(nested[arg.id])
                                       if isinstance(c, ast.Call))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or id(sub) in guarded:
                continue
            name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
            if name in HEAVY_SYNC_CALLEES:
                hits.append((sub.lineno, name))
    return hits


@pytest.mark.parametrize("handler", [
    br.behaviors_home_profile, br.behaviors_predictions, br.behaviors_intent,
])
def test_handler_offloads_every_heavy_sync_call(handler):
    """「什么都不改」档：HEAD 上这三个 handler 必须是 0 条。"""
    assert _unguarded_heavy_calls(inspect.getsource(handler)) == []


def test_ast_gate_bites_the_pre_fix_shape():
    """反例档：把改前的写法原样喂给量具，必须判红（否则这条门等于没装）。"""
    before = (
        "async def behaviors_home_profile(request):\n"
        "    from ..home_profile import build_profile, write_profile_atomic\n"
        "    profile_text = build_profile(rt.store, max_chars=4000)\n"
        "    write_profile_atomic('/data/home_profile.md', profile_text)\n"
        "    return profile_text\n"
    )
    hits = _unguarded_heavy_calls(before)
    assert [h[1] for h in hits] == ["build_profile", "write_profile_atomic"], hits

    after = (
        "async def behaviors_home_profile(request):\n"
        "    from ..home_profile import build_profile\n"
        "    profile_text = await asyncio.to_thread(\n"
        "        build_profile, rt.store, max_chars=4000)\n"
        "    return profile_text\n"
    )
    assert _unguarded_heavy_calls(after) == []


# ── 6. 第五族：改前**一次都没被调用过**的 handler ─────────────────────────
#
# 覆盖率现读（本机 Python313 + coverage 7.16.1，全量单轮，成对档见台账 §四十一）在**中间档**
# 点名 behavior_routes 还有 371 条语句从未执行（改前档 HEADpair 是 642 语句 / 506 未执行 = 21%），
# 其中成块的七条是本文件口径该管而没人管过的 handler。
# 顺着「每个入参要么落点要么显式拒」逐条读过去，第一趟就撞到一条现行缺陷：
# `behaviors_run` 把 `body.get("window_minutes")` **原样**交给
# `activity_inference.py:260` 的 `int(window_minutes or config or 15)` ——
# 与族 1 完全同形（"abc" → 未捕获 ValueError → 500；0/""/False → 静默换 config 档；
# 10**12 → `now - timedelta(minutes=…)` OverflowError → 500）。
# 其余六条的 handler 自己有 `try/except → 400` + `max(1, min(…))` 夹紧，
# 那部分本批按「有守卫但会静默改值」登记在案（见 §九 的口径），不在这里重复改。

def _seed_candidate(store, name="seq::客厅到卧室", status="staging"):
    rid, action = store.upsert_candidate_rule(
        name, [{"entity_id": "sensor.a"}, {"entity_id": "sensor.b"}],
        time_window="300", infer="walking", confidence=0.72)
    assert action != "rejected_self_loop", (rid, action)
    if status != "staging":
        assert store.set_candidate_rule_status(rid, status)
    return rid


def test_window_minutes_lands_on_the_engine_or_is_refused(store):
    """改前：`window_minutes=abc` 从没守卫的 `int()` 里打穿 handler（500）。"""
    act = _StubActivity()
    resp = _post(br.behaviors_run, store, {"window_minutes": 45}, activity=act)
    assert resp.status_code == 200
    name, kwargs = act.calls[-1]
    assert name == "run" and kwargs["args"] == (None, None, 45), act.calls

    act2 = _StubActivity()
    resp = _post(br.behaviors_run, store, {}, activity=act2)
    assert resp.status_code == 200
    assert act2.calls[-1][1]["args"] == (None, None, None), "不给时必须交 None，让引擎读自己的 config 档"


@pytest.mark.parametrize("value,frag", [
    ("abc", "window_minutes 必须是整数"),
    (0, "window_minutes 不得小于 1"),
    (-1, "window_minutes 不得小于 1"),
    (True, "window_minutes 必须是数字"),
    ("", "200"),        # 空串 = 没给 → 交给引擎 config 档，不是 0 也不是 400
])
def test_window_minutes_rejects_by_name(store, value, frag):
    act = _StubActivity()
    resp = _post(br.behaviors_run, store, {"window_minutes": value}, activity=act)
    if frag == "200":
        assert resp.status_code == 200, value
        assert act.calls[-1][1]["args"] == (None, None, None), value
        return
    assert resp.status_code == 400, (value, resp.status_code)
    assert _payload(resp)["error"] == frag
    assert act.calls == [], f"{value!r} 被拒了却还是把值传给了引擎"


def test_window_minutes_upper_bound_is_the_day_bounds_ceiling_in_minutes(store):
    """上限不是随手挑的：`DAY_WINDOW_MAX * 1440` 是本仓唯一的窗口上限口径（3650 天）。

    再大就会把窗口起点推到 `datetime` 下界之外——`now - timedelta(minutes=10**12)`
    直接 OverflowError，改前那条路是 500。
    """
    hi = DAY_WINDOW_MAX * 1440
    act = _StubActivity()
    assert _post(br.behaviors_run, store, {"window_minutes": hi},
                 activity=act).status_code == 200
    assert act.calls[-1][1]["args"][2] == hi
    resp = _post(br.behaviors_run, store, {"window_minutes": hi + 1}, activity=act)
    assert resp.status_code == 400
    assert _payload(resp)["error"] == f"window_minutes 不得大于 {hi}"


def test_current_behaviors_minutes_and_states_limit_reach_the_reader(store):
    """两条查询 handler 改前根本没人调过：这里既补落点证据，也把 500 的路堵上。"""
    store.add_behavior_state(PERSON, "客厅", "就寝", 0.9,
                             ts="2026-10-01T19:05:00")
    act = _StubActivity()
    resp = _get(br.behaviors_current, store, "minutes=120", activity=act)
    assert resp.status_code == 200
    assert act.calls[-1] == ("current_behaviors", {"minutes": 120})

    # 越界档：这两条走的是 handler 内的 `max(1, min(…))` 夹紧（在册的"会静默改值"那一族），
    # 本文件只锁"夹紧后的值确实到了读取方"，不改它的口径。
    act2 = _StubActivity()
    assert _get(br.behaviors_current, store, "minutes=99999", activity=act2).status_code == 200
    assert act2.calls[-1][1]["minutes"] == 1440
    act3 = _StubActivity()
    resp = _get(br.behaviors_current, store, "minutes=abc", activity=act3)
    assert resp.status_code == 400
    assert "minutes" in _payload(resp)["error"]
    assert act3.calls == [], "被拒的值不该再传给读取方"

    resp = _get(br.behaviors_states, store, f"member=Member0&room={quote('客厅')}&limit=5")
    assert resp.status_code == 200
    body = _payload(resp)
    assert body["count"] == len(body["states"]) == 1, body
    assert body["states"][0]["member"] == PERSON

    assert _get(br.behaviors_states, store, "limit=abc").status_code == 400


def test_candidate_rule_audit_trail_is_written_only_for_human_decisions(store):
    """R3 红线「人工确认留痕」：accepted/rejected 各留一条，复位 staging 不留。

    最坑的不是被拒，而是"审核过了但没人知道是谁定的"——这条门罩的就是那一步。
    """
    rid = _seed_candidate(store)
    from memory_agent.rule_lifecycle import ACT_CONFIRM
    assert _post(br.candidate_rule_update, store,
                 {"rule_id": rid, "status": "staging"}).status_code == 200
    assert store.list_rule_lifecycle(rid) == []

    resp = _post(br.candidate_rule_update, store,
                 {"rule_id": rid, "status": "accepted", "reason": "看过了"})
    assert resp.status_code == 200
    rows = store.list_rule_lifecycle(rid)
    assert len(rows) == 1, rows
    assert rows[0]["action"] == ACT_CONFIRM and rows[0]["to_state"] == "accepted"
    assert rows[0]["reason"] == "看过了"

    rid2 = _seed_candidate(store, name="seq::厨房喝水")
    assert _post(br.candidate_rule_update, store,
                 {"rule_id": rid2, "status": "rejected"}).status_code == 200
    assert len(store.list_rule_lifecycle(rid2)) == 1


@pytest.mark.parametrize("body", [
    {"rule_id": "", "status": "accepted"},
    {"status": "accepted"},
])
def test_candidate_rule_update_requires_rule_id(store, body):
    resp = _post(br.candidate_rule_update, store, body)
    assert resp.status_code == 400
    assert "缺少 rule_id" in _payload(resp)["error"]


def test_candidate_rule_update_rejects_unknown_status_and_missing_rule(store):
    rid = _seed_candidate(store)
    resp = _post(br.candidate_rule_update, store, {"rule_id": rid, "status": "adopted"})
    assert resp.status_code == 400
    assert "staging/accepted/rejected" in _payload(resp)["error"]
    assert _payload(_post(br.candidate_rule_update, store,
                          {"rule_id": rid, "status": "accepted"}))["ok"] is True
    resp = _post(br.candidate_rule_update, store,
                 {"rule_id": "does-not-exist", "status": "accepted"})
    # 这条 404 在 HEAD 就有实现（`return error("规则不存在", 404)`），但**没有任何用例走过它**
    # ——本批补的是"实现有、锁没有"，不是"改前会回 200"。断言写在这里，下次谁改掉那行就红。
    assert resp.status_code == 404


def test_candidate_rules_list_and_export_land_on_accepted_only(store):
    _seed_candidate(store, name="seq::A")
    _seed_candidate(store, name="seq::B", status="accepted")
    body = _payload(_get(br.candidate_rules_list, store, ""))
    assert body["count"] == 2
    body = _payload(_get(br.candidate_rules_list, store, "status=accepted"))
    assert [r["name"] for r in body["rules"]] == ["seq::B"]
    body = _payload(_get(br.candidate_rules_export, store, ""))
    assert body["count"] == 1
    rule = body["rules"][0]
    # 交付面：管家规则库直接解析这几把键，少一把就是交付失败
    assert rule["order_sensitive"] is True
    assert rule["source"] == "inference"
    assert [s["entity_id"] for s in rule["steps"]] == ["sensor.a", "sensor.b"]
    assert rule["time_window"] == "300" and rule["confidence"] == 0.72


def test_anomaly_list_and_review_round_trip(store):
    aid, _ = store.upsert_behavior_anomaly("case-1", day="2026-10-01", room="客厅",
                                           reasons=["rare_edge"], severity=0.4)
    # 查询串必须百分号编码：Starlette 按 latin-1 解 `query_string`，
    # 直接塞裸 UTF-8 的「客厅」会变成 mojibake，过滤条件命中 0 行——用例会绿在地面上、红在断言上。
    body = _payload(_get(br.behaviors_anomalies, store,
                         f"status=new&day_from=2026-10-01&day_to=2026-10-01&room={quote('客厅')}&limit=10"))
    assert body["count"] == 1 and body["anomalies"][0]["anomaly_id"] == aid
    assert body["anomalies"][0]["reasons"] == ["rare_edge"], "JSON 列没解析就等于把内部列名交给前端"
    assert _get(br.behaviors_anomalies, store, "limit=abc").status_code == 400

    assert _post(br.behavior_anomaly_update, store,
                 {"anomaly_id": aid, "status": "confirmed"}).status_code == 200
    resp = _post(br.behavior_anomaly_update, store, {"anomaly_id": aid, "status": "sure"})
    assert resp.status_code == 400 and "new/confirmed/ignored" in _payload(resp)["error"]
    resp = _post(br.behavior_anomaly_update, store, {"anomaly_id": "ghost", "status": "ignored"})
    assert resp.status_code == 404, "复核一条不存在的异常必须 404（HEAD 已实现，但改前 0 条用例走过）"
    assert _post(br.behavior_anomaly_update, store, {"status": "ignored"}).status_code == 400


def test_drifts_list_and_live_flag_reach_the_engine(store):
    """`live=1` 是唯一会**现算一次**的查询档：不给 days 就交默认 14，给了必须原样到。"""
    store.upsert_behavior_drift("2026-10-01T19:00:00", kind="drift", day="2026-10-01",
                                score=0.8)
    act = _StubActivity()
    body = _payload(_get(br.behaviors_drifts, store, "kind=drift&limit=5", activity=act))
    assert body["count"] == 1 and "live" not in body
    assert act.calls == [], "不带 live 不该触发现算"

    act = _StubActivity()
    body = _payload(_get(br.behaviors_drifts, store, "live=1&days=21", activity=act))
    assert "live" in body
    name, kwargs = act.calls[-1]
    assert name == "mine_drift" and kwargs["args"][2] == 21, act.calls

    act = _StubActivity()
    assert _get(br.behaviors_drifts, store, "live=1&days=abc", activity=act).status_code == 400
    assert act.calls == []


def test_task_records_query_params_land_and_bad_limit_is_named(store):
    from memory_agent.task_record import upsert_task_record
    upsert_task_record(store, "daily_summary", "2026-09-18", {"calls": 3},
                       "2026-09-18T03:00:00")
    upsert_task_record(store, "nightly_mining", "2026-09-19", {"rows": 7},
                       "2026-09-19T03:00:00")

    body = _payload(_get(br.behaviors_task_records, store, ""))
    assert body["count"] == 2
    assert body["records"][0]["period_key"] == "2026-09-19", "ORDER BY period_key DESC 的方向"
    assert body["records"][0]["data"] == {"rows": 7}, "data_json 必须解析成 data 再出境"

    body = _payload(_get(br.behaviors_task_records, store, "task_id=daily_summary"))
    assert [r["task_id"] for r in body["records"]] == ["daily_summary"]
    body = _payload(_get(br.behaviors_task_records, store, "period_key=2026-09-19"))
    assert [r["task_id"] for r in body["records"]] == ["nightly_mining"]

    resp = _get(br.behaviors_task_records, store, "limit=abc")
    assert resp.status_code == 400 and "limit" in _payload(resp)["error"]


def test_return_profile_person_and_days_reach_the_reader(store):
    store.add_behavior_state(PERSON, "客厅", "就寝", 0.9, ts="2026-10-01T22:30:00")
    body = _payload(_get(br.behaviors_return_profile, store, "days=7"))
    assert "profiles" in body and body["count"] == len(body["profiles"])
    body = _payload(_get(br.behaviors_return_profile, store, f"person={PERSON}&days=7"))
    assert "profile" in body
    resp = _get(br.behaviors_return_profile, store, "days=abc")
    assert resp.status_code == 400 and "days" in _payload(resp)["error"]


def test_alerts_stats_fails_loudly_when_dispatcher_absent(store):
    resp = _get(br.alerts_stats, store, "")
    assert resp.status_code == 400
    assert "未初始化" in _payload(resp)["error"]

    seen = []

    class _Dispatcher:
        def get_stats(self, session_id=None):
            seen.append(session_id)
            return {"single_flight": 0}

    rt = _rt(store)
    rt.alert_dispatcher = _Dispatcher()
    resp = asyncio.run(br.alerts_stats(_request(query="session_id=away_mode", rt=rt,
                                                user={"sub": "u"})))
    assert resp.status_code == 200 and seen == ["away_mode"]
    assert _payload(resp)["single_flight"] == 0


def test_rules_view_reports_cooldown_from_the_engine(store):
    """冷却读数来自 `rule_engine._last_triggered`：没有这台引擎时视图必须还能出，
    而且 `in_cooldown`/`seconds_remaining` 不能是硬编码的死值。"""
    from memory_agent.perception_rules import STATIC_RULES
    rt = _rt(store)
    first = STATIC_RULES[0]
    rt.rule_engine = types.SimpleNamespace(
        _last_triggered={first["id"]: time.monotonic() - 10.0})
    body = _payload(asyncio.run(br.behaviors_rules(
        _request(query="", rt=rt, user={"sub": "u"}))))
    assert body["count"] == len(STATIC_RULES)
    by_id = {r["id"]: r for r in body["rules"]}
    hot = by_id[first["id"]]
    assert hot["in_cooldown"] is True
    assert 0 < hot["seconds_remaining"] <= int(first["cooldown_seconds"])
    cold = by_id[STATIC_RULES[1]["id"]]
    assert cold["in_cooldown"] is False and cold["seconds_remaining"] == 0
    assert set(hot) >= {"id", "trigger", "room", "action", "cooldown_seconds",
                        "description", "in_cooldown", "seconds_remaining"}

