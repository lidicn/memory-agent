"""run14 前置探针：behavior_routes 四组候选缺陷的现读（本机 Python 3.13）。

只贴输出，不做解释；每条都要有"改前真的响"的证据。
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import types

# 探针的取数根必须**可指**：这里原来无条件插本仓 `src`，于是 `PYTHONPATH=.qoder/head63/src`
# 这类"指向 HEAD 快照"的跑法会被本行覆盖掉——量具报的是它自己看到的那棵树，不是我以为的那棵。
# 现在用 `MA_PROBE_SRC` 指定，缺省仍是工作树；跑改前面板请显式指到 `git archive HEAD` 的快照。
sys.path.insert(0, os.environ.get("MA_PROBE_SRC")
                or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from starlette.requests import Request  # noqa: E402

from memory_agent.api import behavior_routes as br  # noqa: E402
from memory_agent.store import Store  # noqa: E402


def _request(*, method="GET", query="", body=None, rt=None, user=None):
    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {"type": "http", "method": method, "path": "/api/behaviors/x",
             "query_string": query.encode("utf-8"), "headers": [], "app": app,
             "state": {"user": user} if user is not None else {}}

    async def receive():
        return {"type": "http.request",
                "body": json.dumps(body or {}).encode("utf-8"), "more_body": False}

    return Request(scope, receive)


def _store(n_events=6):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    for i in range(n_events):
        st.insert_behavior_event({
            "server_ts": f"2026-10-01T19:{i:02d}:00",
            "day": "2026-10-01",
            "room": "客厅",
            "status": "vlm_failed",
            "raw_response": "x" * 300,
            "persons": [{"name": "Member0"}],
        })
    return st, path


class _StubActivity:
    """只记录"收到的值"，不真的挖矿——本探针要量的是入参转换，不是算法。"""

    def __init__(self):
        self.calls = []

    def mine_process(self, *a, **k):
        self.calls.append(("mine_process", a, k))
        return {"ok": True}

    def mine_drift(self, *a, **k):
        self.calls.append(("mine_drift", a, k))
        return {"ok": True}

    def audit_rule_recall(self, *a, **k):
        self.calls.append(("audit_rule_recall", a, k))
        return {"ok": True}

    def current_behaviors(self, minutes):
        return []

    def run(self, *a, **k):
        return {"ok": True}


def _rt(store, activity=None):
    return types.SimpleNamespace(
        store=store, activity=activity, rule_engine=None, alert_dispatcher=None,
        config=types.SimpleNamespace(tz_offset_hours=8.0,
                                     vision_snapshot_retention_days=30),
        insights=None,
    )


async def p1_bad_cases_limit():
    print("--- P1 /api/behaviors/bad-cases limit 口径（表内 7 条）")
    st, path = _store(7)
    rt = _rt(st)
    try:
        r = await br.behaviors_bad_cases_list(
            _request(query="limit=abc", rt=rt, user={"sub": "u"}))
        print("P1a limit=abc ->", r.status_code, r.body[:120])
    except Exception as exc:
        print(f"P1a limit=abc -> 未捕获 {type(exc).__name__}: {exc}")
    r = await br.behaviors_bad_cases_list(
        _request(query="limit=2", rt=rt, user={"sub": "u"}))
    print("P1b 对照 limit=2 -> count=", json.loads(r.body).get("count"))
    r = await br.behaviors_bad_cases_list(
        _request(query="limit=-1", rt=rt, user={"sub": "u"}))
    print(f"P1c limit=-1 -> {r.status_code} count={json.loads(r.body).get('count')} "
          f"（SQLite 的 LIMIT -1 = 无上限：比 limit=2 给得多、又不受 100 夹紧约束）")
    r = await br.behaviors_bad_cases_list(
        _request(query="limit=0", rt=rt, user={"sub": "u"}))
    print("P1d limit=0 ->", r.status_code, r.body[:80])
    st.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


async def p2_loop_blocking():
    print("--- P2 事件循环：DB / CPU 是否离开主线程")
    st, path = _store(0)
    rt = _rt(st)
    seen = []
    orig_list = st.list_agent_memories

    def spy_list(*a, **k):
        seen.append(("list_agent_memories", threading.current_thread().name))
        return orig_list(*a, **k)

    st.list_agent_memories = spy_list

    heartbeat_state = {"ticks": 0}

    async def heartbeat(stop):
        while not stop.is_set():
            heartbeat_state["ticks"] += 1
            await asyncio.sleep(0.001)

    from memory_agent import home_profile as hp_mod
    orig_build = hp_mod.build_profile

    def slow_build(store, max_chars=4000):
        seen.append(("build_profile", threading.current_thread().name))
        import time
        time.sleep(0.05)          # 模拟生产里这条同步链的真实占用
        return orig_build(store, max_chars=max_chars)

    hp_mod.build_profile = slow_build
    stop = threading.Event()
    hb = asyncio.create_task(heartbeat(stop))
    await asyncio.sleep(0.01)
    ticks_before = heartbeat_state["ticks"]
    try:
        r = await br.behaviors_home_profile(_request(query="", rt=rt, user={"sub": "u"}))
        print("P2a home-profile ->", r.status_code)
    except Exception as exc:
        print(f"P2a home-profile -> 未捕获 {type(exc).__name__}: {exc}")
    ticks_after = heartbeat_state["ticks"]
    stop.set()
    await hb
    print(f"P2a2 阻塞期间心跳 tick：{ticks_after - ticks_before}"
          f"（handler 内有 50ms 同步占用；卸载到线程则应 >0）")
    print("P2b 线程记录：", seen)
    hp_mod.build_profile = orig_build

    from memory_agent import behavior_predictor as bp_mod
    rec = []
    orig_pa = bp_mod.predict_arrival_time
    orig_pr = bp_mod.predict_daily_routine

    def spy_pa(*a, **k):
        rec.append(("predict_arrival_time", threading.current_thread().name))
        return {"none": True}

    def spy_pr(*a, **k):
        rec.append(("predict_daily_routine", threading.current_thread().name))
        return {"none": True}

    bp_mod.predict_arrival_time = spy_pa
    bp_mod.predict_daily_routine = spy_pr
    # 需要非空 events 才会走到预测函数
    st2, path2 = _store(3)
    rt2 = _rt(st2)
    try:
        r = await br.behaviors_predictions(_request(query="person=Member0", rt=rt2,
                                                    user={"sub": "u"}))
        print("P2c predictions ->", r.status_code, "线程记录：", rec)
    except Exception as exc:
        print(f"P2c predictions -> 未捕获 {type(exc).__name__}: {exc}")
    bp_mod.predict_arrival_time = orig_pa
    bp_mod.predict_daily_routine = orig_pr
    st.close()
    st2.close()
    for base in (path, path2):
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(base + s)
            except OSError:
                pass


async def p3_unguarded_numbers():
    print("--- P3 数字入参守卫缺口（days 有守卫，其余没有）")
    st, path = _store(0)
    act = _StubActivity()
    rt = _rt(st, act)
    cases = [
        ("behaviors_mine_process", {"min_variant_support": "abc"}),
        ("behaviors_mine_process", {"bucket_sec": "abc"}),
        ("behaviors_mine_process", {"min_cases_per_room": "abc"}),
        ("behaviors_mine_process", {"min_case_events": "abc"}),
        ("behaviors_mine_process", {"min_variant_support": 0, "bucket_sec": 0,
                                    "days": 3}),          # 0 档：看它落到哪儿
        ("behaviors_mine_process", {"bucket_sec": 0, "days": 3}),   # 正对照：bucket_sec=0 合法
        ("behaviors_mine_drift", {"min_score": 0.0, "days": 14}),   # 正对照：0.0 合法
        ("behaviors_mine_drift", {"window_size": 0, "days": 14}),   # 越界下界
        ("behaviors_audit_rule_recall", {"min_near_miss": 0}),
        ("behaviors_audit_rule_recall", {"min_near_miss": "abc"}),
        ("behaviors_mine_drift", {"window_size": "abc"}),
        ("behaviors_mine_drift", {"min_score": "abc"}),
        ("behaviors_mine_process", {"days": "abc"}),      # 对照组：这条有守卫
    ]
    for name, body in cases:
        handler = getattr(br, name)
        try:
            r = await handler(_request(method="POST", body=body, rt=rt, user={"sub": "u"}))
            print(f"P3 {name} {body} -> {r.status_code}")
        except Exception as exc:
            print(f"P3 {name} {body} -> 未捕获 {type(exc).__name__}: {exc}")
    print("P3y 收到的值（证明 0 被静默改成默认档）：")
    for c in act.calls:
        print("   ", c[0], "kw=", c[2])
    st.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


async def p4_lookback_split():
    print("--- P4 causal_analyze 的 lookback：一个数两种口径")
    from memory_agent.day_bounds import clamp_days
    import memory_agent.change_attribution as ca
    print("P4a clamp_days(10**9) =", clamp_days(10 ** 9),
          " 而 search_candidate_causes 的 half_life 用的是原值：lookback/2.0 =", 10 ** 9 / 2.0)
    src = inspect_getsource(ca.search_candidate_causes)
    for ln, text in src:
        if "clamp_days" in text or "half_life" in text:
            print(f"P4b {ln}: {text.strip()}")
    st, path = _store(0)
    rt = _rt(st)
    r = await br.causal_analyze(_request(query="person=Member0&days=1000000000"
                                         "&lookback_days=1000000000", rt=rt,
                                         user={"sub": "u"}))
    print("P4c days/lookback 极值 ->", r.status_code, r.body[:120])
    st.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


def inspect_getsource(fn):
    import inspect
    import linecache
    path = inspect.getsourcefile(fn)
    start = fn.__code__.co_firstlineno
    out = []
    i = 0
    while True:
        line = linecache.getline(path, start + i)
        if not line:
            break
        out.append((start + i, line.rstrip("\n")))
        i += 1
        if i > 80:
            break
    return out


async def p5_counterfactual_range():
    print("--- P5 causal_counterfactual 的 days 极值（改前回显 1e9 天窗口、实算 3650 天）")
    st, path = _store(0)
    rt = _rt(st)
    for q in ("person=Member0&event_type=tv_on&days=1000000000",
              "person=Member0&event_type=tv_on&days=0",
              "person=Member0&event_type=tv_on&days=abc",
              "person=Member0&event_type=tv_on&days=30"):
        r = await br.causal_counterfactual(_request(query=q, rt=rt, user={"sub": "u"}))
        print(f"P5 {q} -> {r.status_code} {r.body[:100]}")
    st.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


async def p6_same_family_three():
    print("--- P6 同一族的另外三处现行（顺着「要么落点要么显式拒」查到，审计没点名）")
    st, path = _store(0)
    rt = _rt(st)
    for q in ("limit=abc", "limit=0", "limit=501", "limit=-1"):
        r = await br.rule_channel_audit(_request(query=q, rt=rt, user={"sub": "u"}))
        print(f"P6a rule_channel_audit [{q or '缺省'}] -> {r.status_code} {r.body[:90]}")
    r = await br.rule_channel_audit(_request(query="", rt=rt, user={"sub": "u"}))
    print("P6a2 缺省档 ->", r.status_code)
    for b in ({"min_count": 0}, {"min_count": True}, {"min_count": "abc"}, {}):
        r = await br.negative_sample_suggestions(
            _request(method="POST", body=b, rt=rt, user={"sub": "u"}))
        print(f"P6b negative-samples {b} -> {r.status_code} {r.body[:90]}")
    for b in ({"rule_id": "r", "trigger_id": "abc"}, {"rule_id": "r", "trigger_id": True},
              {"rule_id": "r", "trigger_id": 0}, {"rule_id": "r"},
              {"rule_id": "r", "trigger_id": 999999}):
        try:
            r = await br.rule_channel_false_positive(
                _request(method="POST", body=b, rt=rt, user={"sub": "u"}))
            print(f"P6c false-positive {b} -> {r.status_code} {r.body[:90]}")
        except Exception as exc:                      # 只打类型名，不打值
            print(f"P6c false-positive {b} -> 抛出 {type(exc).__name__}")
    st.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


async def main():
    await p1_bad_cases_limit()
    await p2_loop_blocking()
    await p3_unguarded_numbers()
    await p4_lookback_split()
    await p5_counterfactual_range()
    await p6_same_family_three()
    print("PROBE_DONE")


asyncio.run(main())
