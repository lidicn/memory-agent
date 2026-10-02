"""DCD R3 候选规则生效通道的四红线锁（20261001-DB六格与MA五题 · MA R3）。

覆盖：
1. 建表自证——全新库必须有 active_rules / rule_trigger_history / rule_lifecycle_audit
   （历史上这两张表只在生产库里存在，src 无 DDL，全新库会把规则链打死）；
2. 证据门槛 / 人工确认 / 引擎实时 feed 词表；
3. 观察期：晋升即 dry_run，只记录不触发，满期零误报才 live；
4. 可回滚：撤销连同它产生的推断一起删除；
5. 审计：每一步留 rule_lifecycle_audit 行（含被门槛拒的路径）；
6. 旁路封堵：update_rule 白名单里没有 mode（一次 PUT 不能跳过观察期）。
"""

import os
import sqlite3
import tempfile
from datetime import timedelta

import pytest

from memory_agent.rule_engine import ActiveRuleEngine
from memory_agent.rule_lifecycle import RuleLifecycle
from memory_agent.store import Store, now_local


def _store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)          # Store 需要自建文件，否则 init_schema 撞上已存在的空文件
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    return st, path


@pytest.fixture
def store():
    st, path = _store()
    yield st
    st.close()
    try:
        os.remove(path)
    except OSError:
        pass


def _lc(store):
    engine = ActiveRuleEngine(store)
    return RuleLifecycle(store, engine, min_evidence=3, dry_run_days=3), engine


def _accepted_candidate(store, name="陌生人入户告警", steps=None, evidence=None,
                        infer="", status="accepted"):
    steps = steps if steps is not None else [{"kind": "face_unknown", "room": "客厅"}]
    if evidence is None:
        evidence = ["2026-09-11 客厅陌生人脸 0.9", "2026-09-12 客厅陌生人脸 0.8",
                    "2026-09-13 客厅陌生人脸 0.9"]
    rid, action = store.upsert_candidate_rule(
        name=name, steps=steps, time_window="", infer=infer, confidence=0.7,
        evidence=evidence)
    assert rid, action
    if status != "staging":
        store.set_candidate_rule_status(rid, status)
    return rid


def _event(day="2026-09-20", room="客厅"):
    return {"kind": "face_unknown", "room": room, "person": "陌生人",
            "confidence": 0.9, "ts": f"{day}T21:10:00"}


def _age_rule(store, rule_id, days):
    """把 activated_at 往前挪 N 天，模拟观察期已过（不动生产时间口径的实现）。"""
    back = (now_local(store.tz_offset_hours) - timedelta(days=days)).isoformat(sep="T")
    conn = store.connect()
    conn.execute("UPDATE active_rules SET activated_at=?, created_at=? WHERE rule_id=?",
                 (back, back, rule_id))
    conn.commit()


# ── 1. 建表自证 ──────────────────────────────────────────────────────────

def test_fresh_db_has_rule_tables(store):
    names = {r[0] for r in store.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"active_rules", "rule_trigger_history", "rule_lifecycle_audit"} <= names
    cols = {r[1] for r in store.connect().execute("PRAGMA table_info(active_rules)")}
    assert {"mode", "origin", "source_rule_id", "evidence_count",
            "activated_at", "revoked_at", "rule_type"} <= cols
    tcols = {r[1] for r in store.connect().execute("PRAGMA table_info(rule_trigger_history)")}
    assert {"dry_run", "false_positive"} <= tcols
    acols = {r[1] for r in store.connect().execute("PRAGMA table_info(detected_activities)")}
    assert "source_rule_id" in acols


def test_engine_crud_works_on_fresh_db(store):
    """全新库上规则引擎可用（旧缺陷：无 DDL → add_rule/list_rules 直接抛错）。"""
    engine = ActiveRuleEngine(store)
    res = engine.add_rule("测试规则", {"kind": "motion"}, {"type": "log"})
    assert res["ok"], res
    assert [r["rule_id"] for r in engine.list_rules()] == [res["rule_id"]]
    rule = engine.get_rule(res["rule_id"])
    assert rule["mode"] == "live"        # 人工规则保持既有语义
    assert rule["origin"] == "manual"


# ── 2. 证据门槛 / 人工确认 / feed 词表 ────────────────────────────────────

def test_promote_requires_human_confirmation(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, status="staging")
    res = lc.promote(rid, actor="agent")
    assert res["ok"] is False
    assert any(b.startswith("evidence_gate") for b in res["blockers"]), res["blockers"]
    assert engine.list_rules(enabled_only=False) == []


def test_promote_requires_independent_evidence_days(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, evidence=["2026-09-11 第一次", "2026-09-11 第二次",
                                               "2026-09-11 第三次"])
    res = lc.promote(rid)
    assert res["ok"] is False
    assert res["gate"]["evidence_days"] == 1
    assert res["gate"]["evidence_basis"] == "distinct_day"
    assert "evidence_gate" in res["blockers"][0]
    assert engine.list_rules(enabled_only=False) == []
    # 门槛被拒也留审计（红线"审计"要覆盖失败路径）
    audit = store.list_rule_lifecycle(rid)
    assert audit and audit[0]["action"] == "promote"
    assert audit[0]["to_state"] == "rejected_by_gate"


def test_device_sequence_candidate_promotes_as_dry_run_with_ruled_trigger(store):
    """DCD 20261001 §二 之后的口径：设备序列候选不再是「引擎没有这个字」。

    晋升产物必须是 dry_run 规则 + 裁定值触发窗（60 秒 3 次）真的入库，序列未表达的
    部分写在 description 里，而不是假装保留了语义。
    """
    lc, engine = _lc(store)
    rid = _accepted_candidate(
        store, name="书房工作序列",
        steps=[{"tag": "door", "state": "on"}, {"tag": "light", "state": "on"}])
    res = lc.promote(rid)
    assert res["ok"], res
    rule = engine.get_rule(res["rule_id"])
    assert rule["mode"] == "dry_run"
    assert rule["condition"] == {"kind": "device", "tag": "door", "state": "on"}
    assert rule["trigger"] == {"type": "count", "window_seconds": 60, "min_count": 3}
    assert "序列步骤未全部表达" in rule["description"]


def test_device_candidate_outside_feed_vocabulary_is_blocked(store):
    """放宽只放宽「引擎有没有这个字」，不放宽「feed 能不能产出这个 tag」。"""
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, name="温度序列",
                              steps=[{"tag": "temperature", "state": "on"}])
    res = lc.promote(rid)
    assert res["ok"] is False
    assert any(b.startswith("device_feed_gap") for b in res["blockers"]), res["blockers"]
    assert engine.list_rules(enabled_only=False) == []


def test_perception_candidate_outside_engine_feed_is_blocked(store):
    """感知链路仍按 ENGINE_FEED_KINDS 判，词表外的 kind 不许晋升成死规则。"""
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, name="幻影事件",
                              steps=[{"kind": "teleport", "room": "客厅"}])
    res = lc.promote(rid)
    assert res["ok"] is False
    assert any(b.startswith("engine_feed_gap") for b in res["blockers"]), res["blockers"]
    assert engine.list_rules(enabled_only=False) == []


def test_promote_happy_path_lands_in_dry_run(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    res = lc.promote(rid, actor="butler_token")
    assert res["ok"], res
    rule = engine.get_rule(res["rule_id"])
    assert rule["mode"] == "dry_run"
    assert rule["origin"] == "candidate_promoted"
    assert rule["source_rule_id"] == rid
    assert rule["evidence_count"] == 3
    assert rule["condition"] == {"kind": "face_unknown", "room": "客厅"}
    assert rule["action"] == {"type": "log"}
    # 候选状态离开 accepted，避免重复晋升；user_confirmed 不能被洗掉
    cand = store.get_candidate_rule(rid)
    assert cand["status"] == "promoted" and int(cand["user_confirmed"]) == 1
    audit = store.list_rule_lifecycle(rule["rule_id"])
    assert audit[0]["action"] == "promote" and audit[0]["actor"] == "butler_token"


def test_promote_with_infer_builds_infer_activity_action(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, name="陌生人来客", infer="guest_arrival")
    rule = engine.get_rule(lc.promote(rid)["rule_id"])
    assert rule["action"]["type"] == "infer_activity"
    assert rule["action"]["activity"] == "guest_arrival"


# ── 3. 观察期：只记录不触发 ───────────────────────────────────────────────

def test_dry_run_matches_but_dispatches_nothing(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, infer="guest_arrival")
    rule_id = lc.promote(rid)["rule_id"]
    engine._index_dirty = True
    dispatched = []
    engine._action_infer_activity = lambda *a, **k: dispatched.append(a)
    triggered = engine.match_event(_event())
    assert [r["rule_id"] for r in triggered] == [rule_id], "试运行照常匹配"
    res = engine.execute_action(engine.get_rule(rule_id), _event())
    assert res["dry_run"] is True and res["dispatched"] is False
    assert dispatched == [], "试运行不得派发任何动作"
    triggers = store.list_rule_triggers(rule_id)
    assert len(triggers) == 1 and int(triggers[0]["dry_run"]) == 1
    assert store.connect().execute(
        "SELECT COUNT(*) FROM detected_activities").fetchone()[0] == 0


def test_advance_blocked_before_observation_period(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    rule_id = lc.promote(rid)["rule_id"]
    res = lc.advance_to_live(rule_id)
    assert res["ok"] is False
    assert any("观察期未满" in b for b in res["blockers"]), res["blockers"]
    assert engine.get_rule(rule_id)["mode"] == "dry_run"
    # 拒绝转正也要留审计
    assert store.list_rule_lifecycle(rule_id)[0]["action"] == "advance_live"
    assert store.list_rule_lifecycle(rule_id)[0]["to_state"] == "rejected_by_gate"


def test_advance_after_observation_then_dispatches(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    rule_id = lc.promote(rid)["rule_id"]
    _age_rule(store, rule_id, 4)
    obs = lc.observation(rule_id)
    assert obs["observed_days"] >= 3 and obs["eligible_for_live"] is True
    res = lc.advance_to_live(rule_id, actor="user")
    assert res["ok"], res
    assert engine.get_rule(rule_id)["mode"] == "live"
    engine._index_dirty = True
    called = []
    engine._action_log = lambda rule, event: called.append(rule["rule_id"]) or {"ok": True}
    engine.execute_action(engine.get_rule(rule_id), _event("2026-09-21"))
    assert called == [rule_id], "转正后应真的派发"
    assert store.list_rule_lifecycle(rule_id)[0]["action"] == "advance_live"
    assert store.list_rule_lifecycle(rule_id)[0]["to_state"] == "live"


def test_false_positive_in_observation_blocks_live(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    rule_id = lc.promote(rid)["rule_id"]
    engine.execute_action(engine.get_rule(rule_id), _event())
    trigger_id = store.list_rule_triggers(rule_id)[0]["trigger_id"]
    assert lc.flag_false_positive(rule_id, trigger_id, reason="家人朋友")["ok"]
    _age_rule(store, rule_id, 5)
    res = lc.advance_to_live(rule_id)
    assert res["ok"] is False
    assert any("误报" in b for b in res["blockers"]), res["blockers"]
    assert engine.get_rule(rule_id)["mode"] == "dry_run"
    assert lc.observation(rule_id)["false_positives"] == 1


def test_update_rule_cannot_skip_observation_period(store):
    """红线旁路封堵：mode 不在 update_rule 白名单里，一次 PUT 不能把 dry_run 洗成 live。"""
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    rule_id = lc.promote(rid)["rule_id"]
    engine.update_rule(rule_id, mode="live", enabled=True)
    assert engine.get_rule(rule_id)["mode"] == "dry_run"


# ── 4. 可回滚 ─────────────────────────────────────────────────────────────

def test_revoke_rolls_back_inferences_the_rule_produced(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, infer="guest_arrival")
    rule_id = lc.promote(rid)["rule_id"]
    _age_rule(store, rule_id, 4)
    assert lc.advance_to_live(rule_id)["ok"]
    engine._index_dirty = True
    res = engine.execute_action(engine.get_rule(rule_id), _event("2026-09-22"))
    assert res["ok"], res
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) FROM detected_activities WHERE source_rule_id=?",
                        (rule_id,)).fetchone()[0] == 1
    out = lc.revoke(rule_id, actor="user", reason="家人聚会误判")
    assert out["ok"] and out["rolled_back_inferences"] == 1
    assert conn.execute("SELECT COUNT(*) FROM detected_activities").fetchone()[0] == 0
    rule = engine.get_rule(rule_id)
    assert rule["mode"] == "revoked" and rule["revoked_at"] and not rule["enabled"]
    audit = store.list_rule_lifecycle(rule_id)
    assert audit[0]["action"] == "revoke"
    assert audit[0]["detail"]["rolled_back_inferences"] == 1


def test_revoke_can_keep_inferences_when_asked(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store, infer="guest_arrival")
    rule_id = lc.promote(rid)["rule_id"]
    _age_rule(store, rule_id, 4)
    lc.advance_to_live(rule_id)
    engine._index_dirty = True
    engine.execute_action(engine.get_rule(rule_id), _event("2026-09-22"))
    out = lc.revoke(rule_id, rollback_inferences=False)
    assert out["ok"] and out["rolled_back_inferences"] == 0
    assert store.connect().execute(
        "SELECT COUNT(*) FROM detected_activities WHERE source_rule_id=?",
        (rule_id,)).fetchone()[0] == 1


def test_manual_rule_can_be_revoked_with_rollback(store):
    """撤销入口对人工规则同样有效（红线约束候选晋升，不削人工规则能力）。"""
    lc, engine = _lc(store)
    rule_id = engine.add_rule("人工规则", {"kind": "motion"},
                              {"type": "infer_activity", "activity": "cooking"})["rule_id"]
    engine.execute_action(engine.get_rule(rule_id),
                          {"kind": "motion", "room": "厨房", "confidence": 0.8,
                           "ts": "2026-09-23T12:00:00"})
    out = lc.revoke(rule_id, reason="测试")
    assert out["ok"] and out["rolled_back_inferences"] == 1


# ── 5. 读侧全景与幂等 ─────────────────────────────────────────────────────

def test_pending_promotions_lists_gate_per_candidate(store):
    lc, _engine = _lc(store)
    ok_id = _accepted_candidate(store, name="可晋升")
    block_id = _accepted_candidate(store, name="证据不足", evidence=["2026-09-11 一条"])
    rows = lc.pending_promotions()
    by_id = {r["candidate_id"]: r for r in rows}
    assert by_id[ok_id]["eligible"] is True
    assert by_id[block_id]["eligible"] is False
    assert rows[0]["candidate_id"] == ok_id, "可晋升的排前面"


def test_double_promote_is_refused(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    first = lc.promote(rid)
    assert first["ok"]
    second = lc.promote(rid)
    assert second["ok"] is False
    assert any(b.startswith("duplicate") for b in second["blockers"]), second["blockers"]
    assert len(engine.list_rules(enabled_only=False)) == 1


def test_channel_status_buckets_and_stats_use_real_flags(store):
    lc, engine = _lc(store)
    rid = _accepted_candidate(store)
    rule_id = lc.promote(rid)["rule_id"]
    engine.execute_action(engine.get_rule(rule_id), _event())
    snap = lc.channel_status()
    assert [r["rule_id"] for r in snap["promoted_rules"]["dry_run"]] == [rule_id]
    assert snap["promoted_rules"]["live"] == []
    assert snap["min_evidence"] == 3 and snap["dry_run_days"] == 3.0
    dry = snap["promoted_rules"]["dry_run"][0]
    assert dry["dry_run_hits"] == 1 and dry["false_positives"] == 0
    stats = engine.get_rule_stats(rule_id, days=7)
    assert stats["dry_run_count"] == 1 and stats["trigger_count"] == 1
    assert stats["precision"] == 1.0


def test_confirmation_is_audited(store):
    """红线"审计"的人工确认环节：确认（HTTP/MCP 入口做的事）落 rule_lifecycle_audit。"""
    from memory_agent.rule_lifecycle import log_confirmation
    rid = _accepted_candidate(store, status="staging")
    assert store.get_candidate_rule(rid)["user_confirmed"] == 0
    store.set_candidate_rule_status(rid, "accepted")   # 状态字 + 红线位
    log_confirmation(store, rid, "accepted", actor="user")
    rows = store.list_rule_lifecycle(rid)
    assert rows[0]["action"] == "confirm" and rows[0]["actor"] == "user"
    assert store.get_candidate_rule(rid)["user_confirmed"] == 1
    # log_confirmation 只留痕，不改状态（撤销/查询复用同一条链路时不能误写）
    log_confirmation(store, rid, "rejected", actor="user")
    assert store.get_candidate_rule(rid)["status"] == "accepted"


# ── 6. 生产库形状兼容（迁移不破坏既有表） ─────────────────────────────────

def test_init_schema_on_legacy_db_shape_is_idempotent(tmp_path):
    """模拟历史库：active_rules 只有旧 9 列，rule_trigger_history 无新列。"""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE active_rules (
        rule_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
        condition_json TEXT DEFAULT '{}', action_json TEXT DEFAULT '{}',
        enabled INTEGER DEFAULT 1, cooldown_seconds INTEGER DEFAULT 300,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE rule_trigger_history (
        trigger_id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id TEXT NOT NULL,
        event_json TEXT DEFAULT '{}', action_json TEXT DEFAULT '{}',
        triggered_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE detected_activities (
        activity_id TEXT PRIMARY KEY, day TEXT, activity TEXT, confidence REAL,
        evidence TEXT, room TEXT, session_id TEXT, created_at TEXT)""")
    conn.execute("INSERT INTO active_rules VALUES('r1','旧规则','','{}','{}',1,300,'a','b')")
    conn.commit()
    conn.close()

    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    st.init_schema()                     # 迁移可重复执行
    try:
        engine = ActiveRuleEngine(st)
        rows = engine.list_rules(enabled_only=False)
        assert [r["rule_id"] for r in rows] == ["r1"]
        assert rows[0]["mode"] == "live"      # 旧规则默认 live，行为不变
        assert rows[0]["origin"] == "manual"
        assert engine.get_rule_stats("r1", days=7)["trigger_count"] == 0
    finally:
        st.close()
