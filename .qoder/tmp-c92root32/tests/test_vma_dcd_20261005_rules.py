"""DCD 20261005 §二.2 落码锁：`test_rule` 乙（历史事件预演）+ 写侧挂进 R3 通道。

两件缺陷同族——「参数/入口写在那里，内部却从不读它」：

1. ``test_rule`` 的 ``ignore_trigger`` 从不被读，函数体注释写死「不检查触发策略」。
   默认档（``False``=不忽略触发）跑的却是条件层，于是「这条规则会吵几次」这个
   R3 观察期真正要问的问题，它答不了，只给出一个看起来像答案的数。
2. ``behaviors_add_rule/update_rule/delete_rule`` 三个 handler 直接写 ``active_rules``
   ——挂载它们等于绕开 R3 四条红线（证据门槛 / 观察期 / 可回滚 / 审计）。
   裁定：写 CRUD 挂进通道，``add`` 落 ``candidate_rules``。

所以这里的锁分三块：判定链的语义（含**独立回放状态**——测候选不许占线上冷却窗口）、
时间口径（按事件自身墙钟，缺时间戳就如实判不了）、以及写侧的落点与拒绝分支。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import types
from datetime import datetime, timedelta

import pytest

from memory_agent import rule_engine as rule_engine_module
from memory_agent import rule_lifecycle as rule_lifecycle_module
from memory_agent.rule_engine import ActiveRuleEngine
from memory_agent.rule_lifecycle import RuleLifecycle
from memory_agent.store import (CANDIDATE_ACCEPTED, CANDIDATE_PROMOTED, Store)

COND = {"kind": "device", "tag": "door", "state": "on"}
ACTION = {"type": "log"}
T0 = datetime(2026, 10, 1, 21, 0, 0)          # 夜间：SP 清单最关心的一档


def _store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
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


@pytest.fixture
def engine(store):
    return ActiveRuleEngine(store)


def _event(offset_seconds: int = 0, *, with_ts: bool = True, **kw) -> dict:
    ev = {
        "kind": "device", "domain": "binary_sensor",
        "entity_id": "binary_sensor.door_front", "tags": ["door"],
        "state": "on", "old_state": "off", "action": "off->on", "room": "客厅",
    }
    if with_ts:
        ev["ts"] = (T0 + timedelta(seconds=offset_seconds)).isoformat(sep="T")
    ev.update(kw)
    return ev


def _rule(**kw) -> dict:
    rule = {"rule_id": "t-1", "condition": COND, "action": ACTION,
            "trigger": {}, "cooldown_seconds": 0}
    rule.update(kw)
    return rule


# ── 1. ignore_trigger 真的有语义 ───────────────────────────────────────────

def test_full_chain_counts_firings_not_condition_hits(engine):
    """count 60 秒 3 次（DCD Q2 裁定值）在预演里真的生效：第 3 条起才吵。"""
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    res = engine.test_rule(rule, [_event(i * 10) for i in range(6)])
    assert res["matched"] == 6                                  # 条件层：全中
    assert res["triggered"] == 4                                # 判定链层：第 3~6 条
    assert res["trigger_check"]["mode"] == "full_chain"
    assert res["trigger_check"]["window_seconds"] == 60
    assert res["trigger_check"]["min_count"] == 3


def test_condition_only_mode_reports_none_not_zero(engine):
    """``ignore_trigger=True`` 只测条件：``triggered`` 必须是 None。

    给 0 会被读成「一次都不会吵」，而那件事这一档根本没有判定过。
    """
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    res = engine.test_rule(rule, [_event(i * 10) for i in range(6)],
                           ignore_trigger=True)
    assert res["matched"] == 6
    assert res["triggered"] is None and res["suppressed_cooldown"] is None
    assert res["trigger_check"]["mode"] == "condition_only"
    assert [it["would_trigger"] for it in res["items"]] == [None] * 6


def test_two_modes_differ_on_the_same_events(engine):
    """同一批事件、同一条规则：两档读数必须不同（参数不是装饰）。"""
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    events = [_event(i * 10) for i in range(6)]
    chain = engine.test_rule(rule, events)
    plain = engine.test_rule(rule, events, ignore_trigger=True)
    assert chain["triggered"] == 4 and plain["triggered"] is None
    assert chain["matched"] == plain["matched"]


def test_missing_threshold_never_fires(engine):
    """两条事件没跨过 min_count：判定链 0 次，条件层照样 2 次命中。"""
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    res = engine.test_rule(rule, [_event(0), _event(10)])
    assert res["matched"] == 2 and res["triggered"] == 0


# ── 2. 独立回放状态：测候选不许碰到线上状态 ────────────────────────────────

def test_replay_leaves_live_engine_state_untouched(engine):
    """预演不借线上状态：count 窗口、冷却窗口、触发历史都不碰。

    ``_event_windows`` 里预置的是**线上口径**的 epoch 秒（``_check_count_trigger``
    追加的就是 float）——回放若借用它，落进去的是 datetime，两域混在一支 deque 里
    当场炸；这条锁的正判据是「那个键的内容一字未动」。
    """
    rid = "t-1"
    engine._event_windows[rid].append(1.0)
    engine._cooldown_until[rid] = T0 + timedelta(days=365)   # 线上正在冷却
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    res = engine.test_rule(rule, [_event(0), _event(10), _event(20)])
    assert res["triggered"] == 1          # 那条 365 天的线上冷却不该把预演压成 0
    assert list(engine._event_windows[rid]) == [1.0]
    assert engine._cooldown_until[rid] == T0 + timedelta(days=365)


def test_replay_is_repeatable(engine):
    """跑两遍读数一致：回放状态不落引擎字段，就不会第二遍被第一遍压掉。"""
    rule = _rule(cooldown_seconds=300)
    events = [_event(0), _event(60), _event(400)]
    first = engine.test_rule(rule, events)
    second = engine.test_rule(rule, events)
    assert first["triggered"] == second["triggered"] == 2
    assert first["suppressed_cooldown"] == second["suppressed_cooldown"] == 1


def test_replay_does_not_read_trigger_history(engine, store):
    """线上触发历史不参与预演：它记的是**当时**的墙钟，读了会把整轮历史判成冷却中。"""
    rid = engine.add_rule("门磁", COND, ACTION, cooldown_seconds=86400)["rule_id"]
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO rule_trigger_history (rule_id, event_json, action_json,"
            " triggered_at, dry_run) VALUES (?, '{}', '{}', ?, 0)",
            (rid, T0.isoformat(sep="T")))
    res = engine.test_rule(_rule(rule_id=rid, cooldown_seconds=86400),
                           [_event(0), _event(60)])
    assert res["triggered"] == 1 and res["suppressed_cooldown"] == 1


# ── 3. 时间口径 ────────────────────────────────────────────────────────────

def test_replay_uses_event_time_not_the_wall_clock(engine):
    """相隔 10 分钟的三条事件不该凑成「60 秒内 3 次」。

    拿 ``time.time()`` 当全部事件的「现在」，min_count 门槛就形同不存在。
    """
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    spread = engine.test_rule(rule, [_event(0), _event(600), _event(1200)])
    tight = engine.test_rule(rule, [_event(0), _event(20), _event(40)])
    assert spread["triggered"] == 0 and spread["suppressed_cooldown"] == 0
    assert tight["triggered"] == 1


def test_events_without_timestamp_are_not_silently_fired(engine):
    """没有时间戳 = 判不了，如实计数；不塞进「此刻」凑窗口，也不当成没命中。"""
    rule = _rule(trigger={"type": "count", "window_seconds": 60, "min_count": 3})
    res = engine.test_rule(rule, [_event(with_ts=False) for _ in range(3)])
    assert res["matched"] == 3
    assert res["triggered"] == 0
    assert res["trigger_check"]["events_without_ts"] == 3
    assert [it["would_trigger"] for it in res["items"]] == [None, None, None]


def test_unparseable_timestamp_is_counted_as_missing(engine):
    rule = _rule(cooldown_seconds=0)
    res = engine.test_rule(rule, [_event(ts="昨天傍晚")])
    assert res["matched"] == 1
    assert res["triggered"] == 0 and res["trigger_check"]["events_without_ts"] == 1


# ── 4. 分支与字段形状 ──────────────────────────────────────────────────────

def test_absence_branch_never_fires_in_the_match_path(engine):
    """absence 由后台扫描器处理，匹配路径永不触发——预演与线上同口径。"""
    rule = _rule(trigger={"type": "absence", "window_seconds": 3600})
    res = engine.test_rule(rule, [_event(0), _event(60)])
    assert res["matched"] == 2 and res["triggered"] == 0
    assert res["trigger_check"]["absence_never_fires"] is True
    assert res["trigger_check"]["window_seconds"] is None   # 这一档根本没有窗


def test_cooldown_suppresses_within_window_and_releases_after(engine):
    rule = _rule(cooldown_seconds=300)
    res = engine.test_rule(rule, [_event(0), _event(60), _event(400)])
    assert res["triggered"] == 2 and res["suppressed_cooldown"] == 1
    assert [it["would_trigger"] for it in res["items"]] == [True, False, True]


def test_illegal_cooldown_means_no_cooldown(engine):
    """与 ``_cooldown_seconds`` 同一口径：坏数值按「不冷却」处理，不静默吞掉告警。"""
    res = engine.test_rule(_rule(cooldown_seconds="abc"), [_event(0), _event(1)])
    assert res["trigger_check"]["cooldown_seconds"] == 0
    assert res["triggered"] == 2


def test_unlabeled_event_is_not_reported_as_a_miss(engine):
    """``expected`` 缺失的事件条件命中时，不能一边写 FN 一边不占 FN 计数。"""
    res = engine.test_rule(_rule(), [_event(0)])
    assert res["false_negative"] == 0
    assert res["items"][0]["verdict"] == "UNLABELED"
    labeled = engine.test_rule(_rule(), [_event(0, expected="negative")])
    assert labeled["items"][0]["verdict"] == "FP"
    assert labeled["false_positive"] == 1


# ── 5. 写侧落点：候选而非引擎 ──────────────────────────────────────────────

def _lifecycle(store, engine):
    return RuleLifecycle(store, engine)


def test_manual_add_lands_in_candidate_rules_not_active_rules(engine, store):
    res = _lifecycle(store, engine).manual_add(
        name="晚归门磁", steps=[{"tag": "door", "state": "on"}],
        time_window="20:00-23:00", cooldown_seconds=300)
    assert res["ok"], res
    assert store.list_candidate_rules() and \
        store.list_candidate_rules()[0]["source"] == "manual"
    assert engine.list_rules(enabled_only=False) == []      # 一条都不进引擎


def test_manual_add_gate_shows_what_the_red_lines_still_lack(engine, store):
    """响应固定带 ``gate``：录入成功不等于走得动，缺口当场摆在读数里。"""
    res = _lifecycle(store, engine).manual_add(
        name="无证据的候选", steps=[{"tag": "door", "state": "on"}])
    assert res["ok"] and res["gate"]["eligible"] is False
    assert any(b.startswith("evidence_gate") for b in res["gate"]["blockers"])
    assert any(b.startswith("cooldown_gate") for b in res["gate"]["blockers"])


def test_manual_candidate_cannot_be_promoted_without_evidence(engine, store):
    """四红线不被人工身份豁免：证据不足就是拒，拒因可还原。"""
    lc = _lifecycle(store, engine)
    rid = lc.manual_add(name="想抄近路", steps=[{"tag": "door", "state": "on"}],
                        cooldown_seconds=300)["rule_id"]
    store.set_candidate_rule_status(rid, CANDIDATE_ACCEPTED)
    out = lc.promote(rid)
    assert out["ok"] is False
    assert any("evidence_gate" in b for b in out["blockers"]), out["blockers"]
    assert engine.list_rules(enabled_only=False) == []


def test_manual_add_refuses_to_overwrite_an_inference_suggestion(engine, store):
    store.upsert_candidate_rule("机器建议规则", [{"tag": "door", "state": "on"}],
                                infer="有人回家", confidence=0.6)
    res = _lifecycle(store, engine).manual_add(
        name="机器建议规则", steps=[{"tag": "presence", "state": "on"}])
    assert res["ok"] is False and "机器建议" in res["error"]
    cand = store.list_candidate_rules()[0]
    assert cand["source"] == "inference" and cand["steps"][0]["tag"] == "door"


def test_manual_add_rejects_bad_steps_and_cooldown(engine, store):
    lc = _lifecycle(store, engine)
    assert lc.manual_add(name="空步骤", steps=[]).get("error")
    assert lc.manual_add(name="坏冷却", steps=[{"tag": "door"}],
                         cooldown_seconds="abc").get("error")
    assert store.list_candidate_rules() == []


def test_manual_edit_refuses_promoted_and_inference_rows(engine, store):
    lc = _lifecycle(store, engine)
    rid = lc.manual_add(name="草稿", steps=[{"tag": "door", "state": "on"}])["rule_id"]
    store.set_candidate_rule_status(rid, CANDIDATE_PROMOTED)
    assert "revoke" in lc.manual_edit(rid, time_window="19:00-23:00")["error"]

    inferred = store.upsert_candidate_rule(
        "机器建议", [{"tag": "door", "state": "on"}], confidence=0.6)[0]
    out = lc.manual_edit(inferred, time_window="19:00-23:00")
    assert out["ok"] is False and "机器建议" in out["error"]


def test_manual_edit_updates_only_the_fields_passed(engine, store):
    lc = _lifecycle(store, engine)
    rid = lc.manual_add(name="草稿", steps=[{"tag": "door", "state": "on"}],
                        time_window="20:00-23:00", cooldown_seconds=300)["rule_id"]
    out = lc.manual_edit(rid, time_window="19:00-23:00")
    assert out["ok"], out
    cand = store.get_candidate_rule(rid)
    assert cand["time_window"] == "19:00-23:00"
    assert cand["cooldown_seconds"] == 300              # 没传的字段不动
    assert cand["steps"] == [{"tag": "door", "state": "on"}]


def test_manual_edit_can_withdraw_the_cooldown_setting(engine, store):
    """「没传」与「传 None」是两件事：后者是主动撤回，晋升端要重新拒绝。"""
    lc = _lifecycle(store, engine)
    rid = lc.manual_add(name="草稿", steps=[{"tag": "door", "state": "on"}],
                        cooldown_seconds=300)["rule_id"]
    assert lc.manual_edit(rid, cooldown_seconds=None)["ok"]
    assert store.get_candidate_rule(rid)["cooldown_seconds"] is None
    assert any(b.startswith("cooldown_gate")
               for b in lc.eligibility(rid)["blockers"])


def test_manual_withdraw_deletes_only_pre_engine_manual_rows(engine, store):
    lc = _lifecycle(store, engine)
    rid = lc.manual_add(name="录错了", steps=[{"tag": "door", "state": "on"}])["rule_id"]
    inferred = store.upsert_candidate_rule(
        "机器建议", [{"tag": "door", "state": "on"}], confidence=0.6)[0]
    promoted = lc.manual_add(name="已晋升", steps=[{"tag": "door", "state": "on"}])["rule_id"]
    store.set_candidate_rule_status(promoted, CANDIDATE_PROMOTED)

    assert lc.manual_withdraw(inferred)["ok"] is False
    assert lc.manual_withdraw(promoted)["ok"] is False
    assert lc.manual_withdraw(rid)["ok"] is True
    assert store.get_candidate_rule(rid) is None
    assert {c["name"] for c in store.list_candidate_rules()} == {"机器建议", "已晋升"}


def test_store_delete_respects_the_status_whitelist(store):
    rid = store.upsert_candidate_rule("草稿", [{"tag": "door"}], confidence=0.6)[0]
    from memory_agent.store import CANDIDATE_PRE_ENGINE
    store.set_candidate_rule_status(rid, CANDIDATE_PROMOTED)
    assert store.delete_candidate_rule(rid, CANDIDATE_PRE_ENGINE) is False
    assert store.get_candidate_rule(rid) is not None
    store.set_candidate_rule_status(rid, "staging")
    assert store.delete_candidate_rule(rid, CANDIDATE_PRE_ENGINE) is True


def test_manual_writes_leave_an_audit_trail(engine, store):
    lc = _lifecycle(store, engine)
    rid = lc.manual_add(name="留痕", steps=[{"tag": "door", "state": "on"}],
                        cooldown_seconds=300)["rule_id"]
    lc.manual_withdraw(rid)
    with store._db() as conn:
        actions = [r["action"] for r in conn.execute(
            "SELECT action FROM rule_lifecycle_audit WHERE rule_id=? ORDER BY rowid",
            (rid,)).fetchall()]
    assert actions == ["manual_add", "manual_withdraw"], actions


# ── 6. HTTP 入口可达（Q4「给可达入口」）────────────────────────────────────

def _request(body, rt, *, method="POST", path="/api/behaviors/x", user=None):
    from starlette.requests import Request

    app = types.SimpleNamespace(state=types.SimpleNamespace(runtime=rt))
    scope = {"type": "http", "method": method, "path": path,
             "query_string": b"", "headers": [], "app": app,
             "state": {"user": user} if user else {}}

    async def receive():
        return {"type": "http.request",
                "body": json.dumps(body).encode("utf-8"), "more_body": False}

    return Request(scope, receive)


@pytest.fixture
def rt(store, engine, monkeypatch):
    """handler 走两个全局单例：不接管它们，第二个测试会撞进上一个测试已经关掉的库。

    ``get_rule_engine`` / ``get_rule_lifecycle`` 都是「第一次装配绑定的 store 用到进程结束」，
    生产里只有一份 store 所以没人踩过，测试里则是必然踩。
    """
    monkeypatch.setattr(rule_engine_module, "_engine", engine)
    monkeypatch.setattr(rule_lifecycle_module, "_lifecycle", RuleLifecycle(store, engine))
    return types.SimpleNamespace(store=store, alert_dispatcher=None,
                                 config=types.SimpleNamespace(tz_offset_hours=8.0))


def test_endpoints_require_login(rt):
    from memory_agent.api import behavior_routes as br
    cases = [(br.behaviors_list_rules, {}, "GET", "/api/behaviors/active-rules"),
             (br.behaviors_add_rule, {}, "POST", "/api/behaviors/candidate-rules/add"),
             (br.behaviors_update_rule, {}, "PUT", "/api/behaviors/candidate-rules/r1"),
             (br.behaviors_delete_rule, {}, "DELETE", "/api/behaviors/candidate-rules/r1"),
             (br.behaviors_test_rule, {}, "POST", "/api/behaviors/rule-channel/test-rule")]
    for handler, body, method, path in cases:
        kwargs = {"rule_id": "r1"} if path.endswith("/r1") else {}
        resp = asyncio.run(handler(_request(body, rt, method=method, path=path), **kwargs))
        assert resp.status_code == 401, path


def test_test_rule_endpoint_computes_and_writes_nothing(rt, store, engine):
    from memory_agent.api import behavior_routes as br
    rid = engine.add_rule("门磁告警", COND, ACTION, cooldown_seconds=300)["rule_id"]
    before = len(engine.list_rules(enabled_only=False))
    body = {"rule_id": rid, "ignore_trigger": False,
            "events": [_event(0), _event(60), _event(400)]}
    resp = asyncio.run(br.behaviors_test_rule(
        _request(body, rt, user={"id": "u", "is_admin": True})))
    out = json.loads(resp.body)
    assert out["ok"] is True
    assert out["matched"] == 3 and out["triggered"] == 2
    assert out["suppressed_cooldown"] == 1
    assert len(engine.list_rules(enabled_only=False)) == before
    with store._db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM rule_trigger_history").fetchone()[0] == 0
    assert engine._cooldown_until.get(rid) is None      # 没占线上冷却窗口


def test_test_rule_endpoint_previews_a_candidate_without_touching_the_engine(
        rt, store, engine):
    from memory_agent.api import behavior_routes as br
    lc = RuleLifecycle(store, engine)
    cid = lc.manual_add(name="候选预演", steps=[{"tag": "door", "state": "on"}],
                        cooldown_seconds=300)["rule_id"]
    body = {"candidate_id": cid, "events": [_event(0), _event(1)]}
    out = json.loads(asyncio.run(br.behaviors_test_rule(
        _request(body, rt, user={"id": "u"}))).body)
    assert out["matched"] == 2 and out["triggered"] == 1
    assert engine.list_rules(enabled_only=False) == []  # 预演不建规则


def test_test_rule_endpoint_refuses_an_unbuildable_candidate(rt, store, engine):
    """构造不出触发子的候选要拿拒因，不是一份 matched=0 的假读数。"""
    from memory_agent.api import behavior_routes as br
    cid = store.upsert_candidate_rule("空步骤候选", [{"room": "客厅"}],
                                      confidence=0.6)[0]
    out = json.loads(asyncio.run(br.behaviors_test_rule(
        _request({"candidate_id": cid, "events": [_event(0)]}, rt,
                 user={"id": "u"}))).body)
    assert out["trigger_check"]["mode"] == "not_evaluated"
    assert out["matched"] == 0 and out["triggered"] is None
    assert any(b.startswith("empty_steps") for b in out["blockers"]), out["blockers"]


def test_add_endpoint_writes_candidate_and_echoes_the_gate(rt, store, engine):
    from memory_agent.api import behavior_routes as br
    body = {"name": "晚归门磁", "steps": [{"tag": "door", "state": "on"}],
            "time_window": "20:00-23:00", "cooldown_seconds": 300,
            "evidence": ["2026-10-01 21:05 门磁开", "2026-10-02 21:10 门磁开",
                         "2026-10-03 21:02 门磁开"]}
    out = json.loads(asyncio.run(br.behaviors_add_rule(
        _request(body, rt, path="/api/behaviors/candidate-rules/add",
                 user={"id": "u", "is_admin": True}))).body)
    assert out["ok"] is True and out["rule_id"]
    assert out["gate"]["evidence_days"] == 3
    assert out["gate"]["eligible"] is False        # 还没人工确认，红线 1 未完成
    assert engine.list_rules(enabled_only=False) == []


def test_active_rules_endpoint_lists_engine_rows_not_static_constants(rt, engine):
    from memory_agent.api import behavior_routes as br
    engine.add_rule("引擎里的规则", COND, ACTION)
    out = json.loads(asyncio.run(br.behaviors_list_rules(
        _request({}, rt, method="GET", path="/api/behaviors/active-rules",
                 user={"id": "u"}))).body)
    assert out["count"] == 1 and out["rules"][0]["name"] == "引擎里的规则"


def test_update_and_delete_endpoints_take_rule_id_from_the_path(rt, store, engine):
    from memory_agent.api import behavior_routes as br
    lc = RuleLifecycle(store, engine)
    cid = lc.manual_add(name="草稿", steps=[{"tag": "door", "state": "on"}])["rule_id"]
    user = {"id": "u", "is_admin": True}

    put = _request({"time_window": "19:00-23:00"}, rt, method="PUT",
                   path=f"/api/behaviors/candidate-rules/{cid}", user=user)
    out = json.loads(asyncio.run(br.behaviors_update_rule(put, rule_id=cid)).body)
    assert out["ok"] is True
    assert store.get_candidate_rule(cid)["time_window"] == "19:00-23:00"

    bad = _request({"confidence": "很多"}, rt, method="PUT",
                   path=f"/api/behaviors/candidate-rules/{cid}", user=user)
    assert asyncio.run(br.behaviors_update_rule(bad, rule_id=cid)).status_code == 400

    dele = _request({"reason": "录错了"}, rt, method="DELETE",
                    path=f"/api/behaviors/candidate-rules/{cid}", user=user)
    assert json.loads(asyncio.run(br.behaviors_delete_rule(dele, rule_id=cid)).body)["ok"]
    assert store.get_candidate_rule(cid) is None
