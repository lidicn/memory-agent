"""第二期审计（`doc/审计报告/2期/` 第三轮、第四轮）数字边界族 MA-07~MA-13 的回归锁。

审计原文给的是**实测读数**，本文件把每一条都换成"真调用 + 真状态字"，并额外锁住第三轮
点出的根因形状：

**第三轮（数字入参：要么落点、要么显式拒）**

| 编号 | 位置（改前） | 改前实测 | 改后 |
|---|---|---|---|
| MA-07 | `api/insight_routes.py:402` | `?limit=abc` → 未捕获 `ValueError` = **HTTP 500**；`?limit=-1` → **全表** | 400；store 层再兜一次 |
| MA-08 | `api/llm_routes.py:631` | `?limit=-1/-999/99999999` → **120 条全表**（有 try、无钳制），且同步读库落在协程里 | 400 + `asyncio.to_thread` |
| MA-09 | `mcp_server.py:2323` | MCP `limit=-1` → 150 条全表，**同一个 store 方法在 API 侧已钳制**（入口不对等） | store 层钳制 ⇒ 两个入口都拉不走全表 |
| MA-10 | `mcp_server.py:2358` | 同上，200 条全表（`store.py:2744` 连 `int()` 都没有） | 同 MA-09 |

核心事实（第三轮 §二）：`LIMIT -1` / `LIMIT -999` 在 SQLite 里是**无上限**，不是报错也不是 0 条。
⇒ 所以"下界"这一侧不能靠"负数自然查不出东西"侥幸，必须显式收敛。

**为什么钳制放在 store 层而不是只在路由层**（第三轮 §五 原文推荐形状）：
「只要 store 层钳住，无论哪个入口（API / MCP / 内部调用）都安全——这正好治 MA-09 那种
"一个入口修了另一个没修"的根因。」本批把 `clamp_limit` 落到五个 `LIMIT ?` 出口，
路由层的 `_num` 继续负责给调用方 400（不静默替调用方改值），两件事分在两处做，
`test_store_chokepoint_is_the_shared_guard` 与 `test_mcp_tools_delegate_to_the_clamped_method`
一上一下把这条分工钉住。

**一处刻意的形状变更**（登记，不是顺手改的）：`llm_cache_list` 的 `?limit=abc` 改前被
`except (TypeError, ValueError): limit = 100` 静默换成默认档——调用方以为拿了 5 条、实际拿了 100 条。
这正是 `behavior_routes.py` 里已经写过判据的那个反例，所以改后走 400。

**第四轮（"有下界 ≠ 有上界"，lesson 87）MA-11/12/13**：`timedelta(minutes=/hours=)` 这一族
只有下界。改前实测：`?minutes=2000000000` → **HTTP 500 OverflowError**（路由侧
`except (TypeError, ValueError)` 兜不住 `OverflowError`，lesson 86）；`since_minutes=1e9` /
`window_min=1e9` **不崩**，窗口回溯到**公元 124 年** = 全量重采 / 全量扫历史（lesson 88「没崩 ≠ 安全」）。
已修范式就在 `day_bounds.clamp_days`，只是当年没铺到分钟/小时档：本批按同一个
10 年天花板换算单位（`MINUTE_WINDOW_MAX = 3650×1440`、`HOURS_WINDOW_MAX = 3650×24`），
不另立新数。

**没并进来的三处，理由写在这里供下一个人复核**：
1. `Store.list_mcp_audit`（`store.py:1736`）的 `max(1, min(int(limit or 100), 1000))` **保持原样**：
   它是第三轮点名的正确范式、本来就双界齐，但它把 `limit=0` 读成「未给 ⇒ 默认 100」，
   而 `clamp_limit` 把 `0` 读成「值，收敛到下界 1」。这两种读法都各自有锁，
   为了统一写法去改一个已经合格的站点，会把「0 = 未给」这层既有语义悄悄换掉。
2. 同族但**外部到不了**的三处不动：`store.py:3133`（`_idem_expires(ttl_hours)` 只有默认 24 的调用点，
   全仓 grep `save_idempotency(`/`reserve_idempotency(` 无人传 ttl）、
   `behavior_predictor.py:199`（`window_min` 唯一调用点 `:240` 传字面量 30）、
   `candidate_promotion.py:1097/1399/1444`（取自 `policy` 字段，运维受控、非 HTTP/MCP 可写）。
   `activity_inference.py:408` 的 `max(1, minutes)` 也无上界，但其唯一入口
   `GET /api/behaviors/current` 在路由层就 `min(minutes, 1440)` 封顶（`behavior_routes.py:34`），
   本批用 `test_current_behaviors_route_keeps_its_own_ceiling` 锁住那道上界不被误删。
3. 全仓其余仍在往 `LIMIT ?` 里塞裸 `int(limit)` 的 store 出口（`store.py:2551 / 2625 / 2763 /
   4113 / 4543 / 4988`）：**外部递不进越界值**，所以不并站。逐条 file:line 依据——
   `list_perception_events`（:2551）全仓无调用点传 `limit`；
   `list_behavior_states`（:2625）的 HTTP 入口自己 `max(1, min(limit, 1000))`
   （`behavior_routes.py:53`），另两处是内部字面量 300 / 3000（`activity_inference.py:414 / :1001`）；
   `list_candidate_rules`（:2763）的三个外部入口（`behavior_routes.py:98 / :137`、
   `mcp_server.py:2233`）与 `rule_lifecycle.pending_promotions` 的路由（`behavior_routes.py:603`）
   都**不传 limit**，走默认档；`query_unified_events`（:4113）在函数头就双界（`:4091`）；
   `list_jobs`（:4543）与 `list_negative_feedback`（:4988）由
   `collect_routes.py:78` / `agent_memory_routes.py:167` 夹紧——前者第三轮 :201 已自查判为
   ✅「有 try + 钳制」，所以不在本批改造清单里。
   这两条的越界读数不靠散文担保：`test_collect_jobs_clamps_before_the_store_sees_it` 与
   `test_negative_feedback_clamps_before_the_store_sees_it` 直接量出
   `?limit=-1 → 1`、`?limit=1000000000 → 100 / 200`，把"钳制在路由层、不在 store 层"这件事
   变成可判红的锁——将来谁把那两行 `max(1, min(…))` 删了，这条会先响。
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import os
import sys
import threading
import textwrap
import types
from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent import intent_inference as ii  # noqa: E402
from memory_agent.api import agent_memory_routes as amr  # noqa: E402
from memory_agent.api import behavior_routes as br  # noqa: E402
from memory_agent.api import collect_routes as cr  # noqa: E402
from memory_agent.api import deps as api_deps  # noqa: E402
from memory_agent.api import insight_routes as ir  # noqa: E402
from memory_agent.api import llm_routes as lr  # noqa: E402
from memory_agent.api import vision_routes as vr  # noqa: E402
from memory_agent.day_bounds import (  # noqa: E402
    DAY_WINDOW_MAX, HOURS_WINDOW_MAX, MINUTE_WINDOW_MAX, clamp_hours, clamp_minutes,
)
from memory_agent.poller import CollectService  # noqa: E402
from memory_agent.store import Store, clamp_limit, now_local  # noqa: E402


# ── harness ────────────────────────────────────────────────────────────────

def _store(tmp_path, name="b2.db") -> Store:
    st = Store(str(tmp_path / name), tz_offset_hours=8.0)
    st.init_schema()
    return st


def _request(*, query="", rt=None, user=None, path="/api/x"):
    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    # ASGI 的 `query_string` 不带前导 `?`；带上会让 Starlette 把 `?limit` 当成键名，
    # 于是 `?limit=abc` 在测试里读成"没给 limit"，用例假绿（本批第一版就踩在这一格）。
    scope = {"type": "http", "method": "GET", "path": path,
             "query_string": query.lstrip("?").encode("utf-8"), "headers": [], "app": app,
             "state": {"user": user} if user is not None else {}}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _call(handler, store, query):
    rt = types.SimpleNamespace(store=store, config=types.SimpleNamespace(tz_offset_hours=8.0))
    req = _request(query=query, rt=rt, user={"sub": "u", "is_admin": True})
    resp = asyncio.run(handler(req))
    return resp, (json.loads(resp.body) if resp.body else None)


def _seed_rule_lifecycle(st, n):
    for i in range(n):
        st.log_rule_lifecycle("ar_1", "advance_live", reason="r%d" % i)


def _seed_answer_cache(st, n):
    for i in range(n):
        st.save_answer_cache("k%d" % i, {"answer": "a%d" % i}, "intent")


def _seed_scene_events(st, n, *, server_ts="2026-10-01T10:00:00"):
    for i in range(n):
        st.insert_behavior_event({
            "server_ts": server_ts, "day": server_ts[:10], "room": "客厅",
            "action": "看电视", "status": "ok",
            "scene_graph_json": {"objects": ["电视"], "relations": [],
                                 "persons": [{"name": "Kevin"}]},
        })


def _seed_runs(st, n):
    for i in range(n):
        st.record_researcher_run({"run_id": "r%d" % i, "job_id": "j1", "token_used": 1})


def _seed_bugs(st, n):
    for i in range(n):
        st.add_bug_report("tool", "desc %d" % i)


def _call_names(func) -> set:
    """函数体里真被调用的名字（`ast` 解析，不用 grep 源码——grep 会把 docstring 也算进去）。

    用 `textwrap.dedent` 而不是 `inspect.cleandoc`：后者按「去掉 docstring 式缩进」处理，
    会把类方法的 `def` 行与函数体一起顶到第 0 列，AST 直接判 `IndentationError`。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


# ── 1. `_num` 是一把尺，不是每路由各刻一把（MA-07/08 的根因之一）────────────

def test_num_is_one_shared_implementation():
    assert api_deps._num is br._num is ir._num is lr._num
    src = inspect.cleandoc(inspect.getsource(br))
    tree = ast.parse(src)
    local_defs = {n.name for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_num" not in local_defs, "behavior_routes 又留了一份本地 `_num`，两把尺会各自漂移"


@pytest.mark.parametrize("raw,msg", [
    ("abc", "limit 必须是整数"),
    ("1.5", "limit 必须是整数"),
    ("-1", "limit 不得小于 1"),
    ("99999999", "limit 不得大于 500"),
    (True, "limit 必须是数字"),        # JSON 的 true 不该被读成 1
])
def test_num_rejects_every_shape_the_audit_measured(raw, msg):
    """改前这四类里三类**不打断**：`abc`→500、`-1`/`99999999`→全表。"""
    val, err = api_deps._num(raw, name="limit", default=50, lo=1, hi=500)
    assert val is None and err == msg


def test_num_absent_still_falls_back_and_zero_is_a_value():
    assert api_deps._num(None, name="limit", default=50, lo=1) == (50, None)
    assert api_deps._num("", name="limit", default=50, lo=1) == (50, None)
    # 显式 0 是「值」，交给 lo 判它越界，而不是被 `x or 3` 静默换成默认档
    assert api_deps._num("0", name="limit", default=50, lo=1) == (None, "limit 不得小于 1")


# ── 2. `clamp_limit`：store 层的兜底，坏值不静默 ────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (None, 50), (50, 50), (1, 1), (0, 1), (-1, 1), (-999, 1), (10 ** 400, 500),
    (float("inf"), 500), (float("nan"), 50), ("abc", 50), ({}, 50),
])
def test_clamp_limit_unit_extremes(value, expected):
    assert clamp_limit(value, 50, 500, label="probe") == expected


def test_clamp_limit_leaves_a_warning_for_junk_and_underflow(caplog):
    """静默是这一族的病根：兜底可以，但不许无声。"""
    with caplog.at_level(logging.WARNING, logger="memory_agent.store"):
        clamp_limit("abc", 50, 500, label="list_probe")
        clamp_limit(-1, 50, 500, label="list_probe")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "list_probe" in text and "非数字" in text and "下界收敛" in text


def test_clamp_limit_control_good_value_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="memory_agent.store"):
        assert clamp_limit(30, 50, 500, label="list_probe") == 30
    assert [r for r in caplog.records if "clamp_limit" in r.getMessage()] == []


# ── 3. store 层钳制：两个入口共用的那道闸（MA-07~MA-10）─────────────────────

NEGATIVE_LIMITS = [-1, -999, 0]


@pytest.mark.parametrize("limit", NEGATIVE_LIMITS)
def test_negative_or_zero_limit_reads_one_row_not_the_table(tmp_path, limit):
    """`LIMIT -1` = 无上限是第三轮的核心事实；0 也不能当成"不限"。"""
    st = _store(tmp_path)
    _seed_rule_lifecycle(st, 60)
    _seed_answer_cache(st, 120)
    _seed_scene_events(st, 40)
    _seed_runs(st, 30)
    _seed_bugs(st, 30)
    try:
        assert len(st.list_rule_lifecycle("", limit)) == 1
        assert len(st.list_answer_cache(limit)) == 1
        assert len(st.list_scene_graphs(limit=limit)) == 1
        assert len(st.list_researcher_runs(None, limit)) == 1
        assert len(st.list_bug_reports("open", limit)) == 1
    finally:
        st.close()


def test_upper_cap_measured_on_bigger_tables(tmp_path):
    """上界必须是**真数**：表比天花板大时，读数停在天花板而不是整张表。"""
    st = _store(tmp_path)
    _seed_rule_lifecycle(st, 501)          # hi=500
    _seed_answer_cache(st, 1001)           # hi=1000
    _seed_scene_events(st, 250)            # hi=200
    try:
        assert len(st.list_rule_lifecycle("", 999999)) == 500
        assert len(st.list_rule_lifecycle("", 500)) == 500       # 控制档：天花板本身照常拿满
        assert len(st.list_answer_cache(99999)) == 1000
        assert len(st.list_scene_graphs(limit=999999)) == 200
    finally:
        st.close()


def test_mcp_audit_precedent_still_clamps_both_ends(tmp_path):
    """第三轮点名的正确范式（`store.py:1736`）不能被这批改双界口径时顺手弄坏。

    它把 `limit=0` 读成「未给 ⇒ 默认 100」，本批**保留**该读法（见模块 docstring）。
    """
    st = _store(tmp_path)
    for i in range(20):
        st.log_mcp_audit("tok", "tool_%d" % i)
    try:
        assert len(st.list_mcp_audit(-1)) == 1
        assert len(st.list_mcp_audit(0)) == 20        # 0 → 默认 100，表小就全给
        assert len(st.list_mcp_audit(99999)) == 20    # 上界 1000 未被改小
    finally:
        st.close()


def test_store_chokepoint_is_the_shared_guard():
    """五个 `LIMIT ?` 出口体内必须真的调 `clamp_limit`（AST，不看注释）。"""
    for fn in (Store.list_rule_lifecycle, Store.list_bug_reports, Store.list_answer_cache,
               Store.list_researcher_runs, Store.list_scene_graphs):
        assert "clamp_limit" in _call_names(fn), fn.__name__


def _mcp_tool_source(name: str) -> ast.FunctionDef:
    from memory_agent import mcp_server
    tree = ast.parse(inspect.getsource(mcp_server))
    found = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert found, "MCP 工具 %s 不在了，本锁的落点需重新登记" % name
    return found[0]


def _attr_chains(node) -> set:
    """函数里出现过的属性链（`rt.store.list_bug_reports` 一类）。

    `asyncio.to_thread(rt.store.list_bug_reports, …)` 把方法当**参数**传，不是 Call 节点，
    只扫 `Call.func` 会把它看成"没有落点"（本批第一版判红在这里）。
    """
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            out.add(".".join(_names(n)))
    return out


@pytest.mark.parametrize("tool,store_method", [
    ("list_rule_lifecycle_audit", "list_rule_lifecycle"),
    ("list_bug_reports", "list_bug_reports"),
])
def test_mcp_tools_delegate_to_the_clamped_method(tool, store_method):
    """MA-09 的根因锁：MCP 侧不自己写 SQL、把 limit 交给同一个（已钳制的）store 方法。"""
    node = _mcp_tool_source(tool)
    chains = _attr_chains(node)
    assert any(s.endswith("store." + store_method) for s in chains), chains
    limit_passed = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    assert "limit" in limit_passed, "limit 不再透传，本锁的落点需重新登记"
    sql_literals = [n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and "LIMIT" in n.value.upper()]
    assert sql_literals == [], "MCP 工具里出现了本地 SQL，绕过了 store 层这道闸"


def _names(node):
    """把 `rt.store.list_bug_reports` 这类属性链拉平成字符串片段。"""
    out = []
    while isinstance(node, ast.Attribute):
        out.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        out.append(node.id)
    return list(reversed(out))


# ── 4. 路由侧：400 而不是 500，也不再把整张表交出去 ─────────────────────────

def test_researcher_runs_route_answers_400_instead_of_500(tmp_path):
    st = _store(tmp_path)
    _seed_runs(st, 30)
    try:
        for q in ("?limit=abc", "?limit=-1", "?limit=999999"):
            resp, body = _call(ir.researcher_runs_list, st, q)
            assert resp.status_code == 400, q
            assert body["ok"] is False and "limit" in body["error"], q
        # 控制档：合法入参照常工作，且真的按 limit 截断
        resp, body = _call(ir.researcher_runs_list, st, "?limit=5")
        assert resp.status_code == 200 and len(body["runs"]) == 5
    finally:
        st.close()


def test_answer_cache_route_rejects_and_moves_off_the_loop(tmp_path):
    st = _store(tmp_path)
    _seed_answer_cache(st, 120)
    try:
        for q in ("?limit=abc", "?limit=-1", "?limit=99999999"):
            resp, body = _call(lr.llm_cache_list, st, q)
            assert resp.status_code == 400, q
            assert "limit" in body["error"], q
        resp, body = _call(lr.llm_cache_list, st, "?limit=7")
        assert resp.status_code == 200 and body["count"] == 7
    finally:
        st.close()


def test_answer_cache_read_runs_off_the_event_loop(tmp_path):
    """MA-08 的第二半：改前在协程里同步读库，一次读就把事件循环钉住（心跳 tick 归 0）。"""
    st = _store(tmp_path)
    _seed_answer_cache(st, 5)
    seen = []
    original = Store.list_answer_cache

    def spy(self, limit=100):
        seen.append(threading.current_thread().name)
        return original(self, limit)

    try:
        Store.list_answer_cache = spy
        resp, _ = _call(lr.llm_cache_list, st, "?limit=5")
    finally:
        Store.list_answer_cache = original
    assert resp.status_code == 200
    assert seen and seen[0] != "MainThread", seen


@pytest.mark.parametrize("raw,expected", [
    ("?limit=-1", 1),            # SQLite 里 -1 = 不限行数，必须由路由收掉
    ("?limit=0", 1),
    ("?limit=1000000000", 100),  # 上限在那行 `min(limit, 100)`，不在 store
])
def test_collect_jobs_clamps_before_the_store_sees_it(tmp_path, raw, expected):
    st = _store(tmp_path)
    seen = []
    original = Store.list_jobs

    def spy(self, limit=20):
        seen.append(limit)
        return []

    try:
        Store.list_jobs = spy
        resp, _ = _call(cr.collect_jobs, st, raw)
    finally:
        Store.list_jobs = original
        st.close()
    assert resp.status_code == 200, raw
    assert seen == [expected], raw


@pytest.mark.parametrize("raw,expected", [
    ("?limit=-1", 1),
    ("?limit=0", 1),
    ("?limit=1000000000", 200),
])
def test_negative_feedback_clamps_before_the_store_sees_it(tmp_path, raw, expected):
    st = _store(tmp_path)
    seen = []

    class _FakeAgentMemory:
        def list_negative_feedback(self, limit):
            seen.append(limit)
            return []

    rt = types.SimpleNamespace(
        store=st, config=types.SimpleNamespace(tz_offset_hours=8.0),
        agent_memory=_FakeAgentMemory())
    try:
        resp = asyncio.run(amr.negative_feedback(
            _request(query=raw, rt=rt, user={"sub": "u", "is_admin": True})))
    finally:
        st.close()
    assert resp.status_code == 200, raw
    assert seen == [expected], raw


# ── 5. MA-11：`minutes` 只有下界的那一族 ────────────────────────────────────

def test_scene_graph_minutes_extreme_no_longer_500s(tmp_path):
    st = _store(tmp_path)
    _seed_scene_events(st, 3)
    try:
        # 改前：路由 `except (TypeError, ValueError)` 兜不住 OverflowError（lesson 86）
        rows = st.list_scene_graphs(minutes=2_000_000_000)
        assert isinstance(rows, list)
        resp, body = _call(vr.scene_graph_query, st, "?minutes=2000000000")
        assert resp.status_code == 200
        # 回显的就是真正生效的窗口，不许"报 20 亿、查 10 年"
        assert body["minutes"] == MINUTE_WINDOW_MAX
        assert body["count"] == 3
        resp, body = _call(vr.scene_graph_query, st, "?minutes=60")
        assert resp.status_code == 200 and body["minutes"] == 60    # 控制档
    finally:
        st.close()


def test_zero_and_negative_minutes_stay_the_explicit_no_window(tmp_path):
    """下界收进 0 而不是 1：`0` 在这一档是「不加时间窗」的合法语义。"""
    st = _store(tmp_path)
    # 「近期」必须相对 store 的家庭墙钟取，写死字面量会让 30 分钟窗口按真实时钟判空
    recent = now_local(8.0).isoformat(timespec="seconds")
    _seed_scene_events(st, 2, server_ts=recent)
    _seed_scene_events(st, 1, server_ts="2020-01-01T10:00:00")
    try:
        assert len(st.list_scene_graphs(minutes=0)) == 3
        assert len(st.list_scene_graphs(minutes=None)) == 3
        assert len(st.list_scene_graphs(minutes=-5)) == 3      # 负数不能把窗口算反
        for q in ("?minutes=0", "?minutes=-5"):
            resp, body = _call(vr.scene_graph_query, st, q)
            assert resp.status_code == 200 and body["minutes"] == 0, q
            assert body["count"] == 3, q
        # 真给窗口时旧事件要被排除（收紧的不是这条腿）
        resp, body = _call(vr.scene_graph_query, st, "?minutes=30")
        assert body["count"] == 2
    finally:
        st.close()


def test_scene_graph_route_still_rejects_non_numeric_minutes(tmp_path):
    st = _store(tmp_path)
    _seed_scene_events(st, 1)
    try:
        resp, body = _call(vr.scene_graph_query, st, "?minutes=abc")
        assert resp.status_code == 400 and "minutes" in body["error"]
    finally:
        st.close()


def test_presence_route_upper_bound_precedent_intact(tmp_path):
    """同文件 `:204` 一直是双界齐的对照样本，这批改 minutes 口径不能把它削掉。"""
    st = _store(tmp_path)
    try:
        resp, body = _call(vr.vision_presence, st, "?minutes=1000000000")
        assert resp.status_code == 200
        assert body["window_minutes"] == 24 * 60
        gap = datetime.fromisoformat(body["now"]) - datetime.fromisoformat(body["since"])
        assert gap == timedelta(minutes=24 * 60)
    finally:
        st.close()


def test_current_behaviors_route_keeps_its_own_ceiling():
    """`activity_inference.py:408` 只有下界，靠入口封顶 ⇒ 入口那层 `min(...,1440)` 是承重的。"""
    src = textwrap.dedent(inspect.getsource(br.behaviors_current))
    assert "min(minutes, 1440)" in src
    # `to_thread(rt.activity.current_behaviors, …)` 把方法当参数传，不是 Call 节点
    assert any(s.endswith("activity.current_behaviors")
               for s in _attr_chains(ast.parse(src))), src


# ── 6. MA-12：增量采集的 `since_minutes` / 首跑的 `first_run_lookback_hours` ──

def _collect_service(tmp_path, *, name="b2c.db", **cfg):
    config = types.SimpleNamespace(
        tz_offset_hours=8.0,
        last_poll_time=cfg.get("last_poll_time", ""),
        first_run_lookback_hours=cfg.get("first_run_lookback_hours", 24),
    )
    svc = CollectService(config, None, None, _store(tmp_path, name), None)
    captured = []

    async def fake_execute(job_id, source, windows, rooms_filter=None, on_success=None):
        captured.append(windows)

    svc._execute = fake_execute
    return svc, captured


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("since", [2_000_000_000, 1_000_000_000, 10 ** 12])
def test_incremental_collection_window_has_an_upper_bound(tmp_path, since, capsys):
    """改前 `2e9` → OverflowError 打成 MCP 500；`1e9` 不崩但窗口回溯到公元 124 年。"""
    svc, captured = _collect_service(tmp_path)
    try:
        _run(svc._run_realtime("j1", "mcp", since_minutes=since))
    finally:
        svc.store.close()
    assert len(captured) == 1
    start, end = captured[0][0]
    assert start.year >= 1 and start < end
    span = (end - start) - timedelta(minutes=1)      # 实现里固定留 1 分钟重叠
    assert span == timedelta(minutes=MINUTE_WINDOW_MAX)
    # 打印的是收口后的窗口，不是入参原值
    assert "回溯最近 %d 分钟" % MINUTE_WINDOW_MAX in capsys.readouterr().out


@pytest.mark.parametrize("since", [None, 0, -5])
def test_since_minutes_zero_still_falls_back_to_last_poll_time(tmp_path, since):
    """下界收进 0，`0` 的"按上次水位续采"语义不能被抬成 1 分钟。"""
    svc, captured = _collect_service(tmp_path, last_poll_time="2026-10-05T08:00:00")
    try:
        _run(svc._run_realtime("j1", "mcp", since_minutes=since))
    finally:
        svc.store.close()
    start, _ = captured[0][0]
    assert start == datetime(2026, 10, 5, 8, 0) - timedelta(minutes=1)


def test_first_run_lookback_hours_extreme(tmp_path, capsys):
    """没有 `last_poll_time` 时走小时档：`10**12` 也不能崩，窗口 = 10 年。"""
    svc, captured = _collect_service(tmp_path, last_poll_time="",
                                     first_run_lookback_hours=10 ** 12)
    try:
        _run(svc._run_realtime("j1", "mcp", since_minutes=None))
    finally:
        svc.store.close()
    start, end = captured[0][0]
    span = (end - start) - timedelta(minutes=1)
    assert span == timedelta(hours=HOURS_WINDOW_MAX)
    assert "回溯 %d 小时" % HOURS_WINDOW_MAX in capsys.readouterr().out


def test_first_run_lookback_hours_normal_and_negative(tmp_path):
    svc, cap6 = _collect_service(tmp_path, name="b2h6.db",
                                 last_poll_time="", first_run_lookback_hours=6)
    try:
        _run(svc._run_realtime("j1", "mcp", since_minutes=None))
    finally:
        svc.store.close()
    start, end = cap6[0][0]
    assert (end - start) - timedelta(minutes=1) == timedelta(hours=6)   # 控制档

    svc2, cap2 = _collect_service(tmp_path, name="b2hneg.db",
                                  last_poll_time="", first_run_lookback_hours=-3)
    try:
        _run(svc2._run_realtime("j2", "mcp", since_minutes=None))
    finally:
        svc2.store.close()
    start, end = cap2[0][0]
    assert (end - start) - timedelta(minutes=1) == timedelta(hours=1)   # 下界 1 小时


# ── 7. MA-13：`window_min` 进引擎前必须收口 ─────────────────────────────────

@pytest.mark.parametrize("window_min,expected", [
    (60, 60), (0, 1), (-5, 1), (1_000_000_000, MINUTE_WINDOW_MAX),
    (2_000_000_000, MINUTE_WINDOW_MAX), (10 ** 12, MINUTE_WINDOW_MAX),
])
def test_infer_intent_sequence_window_reaches_the_engine_clamped(monkeypatch, window_min, expected):
    """量"落点"：引擎收到的窗口就是收口后的那个，不是入参原值。"""
    seen = []

    def fake_infer(events, *, window_min=None, person=None):
        seen.append(window_min)
        return None

    monkeypatch.setattr(ii, "infer_intent", fake_infer)
    ii.infer_intent_sequence([{"server_ts": "2026-10-01T10:00:00"}], window_min=window_min)
    assert seen == [expected]


def test_infer_intent_sequence_default_windows_untouched(monkeypatch):
    seen = []

    def fake_infer(events, *, window_min=None, person=None):
        seen.append(window_min)
        return None

    monkeypatch.setattr(ii, "infer_intent", fake_infer)
    ii.infer_intent_sequence([{"server_ts": "2026-10-01T10:00:00"}], window_min=None)
    assert seen == [5, 15, 30]      # 不给窗口仍是三档各试一遍，没有被钳成一档


# ── 8. 天花板本身必须在 `datetime` 域内（单位换算的自证）────────────────────

def test_ceilings_stay_inside_the_datetime_domain():
    assert MINUTE_WINDOW_MAX == DAY_WINDOW_MAX * 1440
    assert HOURS_WINDOW_MAX == DAY_WINDOW_MAX * 24
    now = datetime(2026, 10, 6, 12, 0)
    for delta in (timedelta(days=DAY_WINDOW_MAX), timedelta(hours=HOURS_WINDOW_MAX),
                  timedelta(minutes=MINUTE_WINDOW_MAX)):
        assert now - delta > datetime.min
        assert now + delta < datetime.max
    # 天花板是「已裁定的 10 年口径换成单位」，不是 `datetime` 的物理边界：
    # 比它更大仍被收回同一档，而 10 年前的日期稳稳落在域内（换算不产生新的崩点）。
    assert clamp_minutes(MINUTE_WINDOW_MAX + 1) == MINUTE_WINDOW_MAX
    assert clamp_hours(HOURS_WINDOW_MAX + 1) == HOURS_WINDOW_MAX
    assert (now - timedelta(minutes=MINUTE_WINDOW_MAX)).year == 2016
    # 真正的物理越界只发生在"没被钳住"的那一侧——这行证明不钳会怎样（lesson 88，
    # 也就是审计实测的 `?minutes=2000000000` 打成 HTTP 500 的那一步）
    with pytest.raises(OverflowError):
        now - timedelta(minutes=2_000_000_000)
    # 而 `1e9` 分钟**不崩**，只是把窗口起点推到公元 125 年（审计在它的参照日量到 124 年，
    # 同一个数量级、同一个结论）：没崩 ≠ 安全
    assert (now - timedelta(minutes=1_000_000_000)).year == 125
