"""``ActiveRuleEngine`` 冷却期的直测（审计 §十七）。

此前 ``_check_cooldown`` 是 ``# TODO / return True`` 桩：``add_rule`` 把
``cooldown_seconds`` 老老实实写进 ``active_rules`` 列，``update_rule`` 也允许改它，
匹配端却从不读——规则作者写的「5 分钟内不重复」完全不成立。运行时复现（连投三条
事件，触发读数 1 1 1）见交付记录。

锁的十四向，对着真库真引擎，覆盖四类真会出事的方向：
1. **窗口内抑制、窗口外放行**（冷却的基本语义）；
2. **首次触发不被长冷却吞**（第一轮审计 P1-1 那一类：单调时钟从进程 0 起算，
   冷却 > uptime 就永久静默——本实现改用家庭墙钟，这条是它的反例锁）；
3. **重启后续上窗口**（实时路径读 ``rule_trigger_history``），同时**批量回放不读**
   （回放判「事件时刻」，历史行的 ``triggered_at`` 是当时的真实墙钟，读了会把整轮
   误判成冷却中）；外加 count 接缝、试运行占窗口、坏数值不抑制、删规则不留脏键。
4. **DCD 20261004 MA-裁1 Q1（缺省即拒）**：晋升通道必须显式带冷却上限——参数或
   候选行 ``cooldown_seconds`` 列，两者都空就拒绝晋升；坏值也拒；晋升写入的数值
   要真的咬住匹配端。``add_rule`` 的形参默认 300 从此只服务人工建规则那条路径。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from memory_agent.device_feed import wall_to_epoch
from memory_agent.rule_engine import ActiveRuleEngine
from memory_agent.rule_lifecycle import RuleLifecycle
from memory_agent.store import Store, now_local

COND = {"kind": "device", "entity_id": "binary_sensor.door_front", "state": "on"}
ACTION = {"type": "log"}


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
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


def _event(ts: str = "") -> dict:
    """device_feed 产出的那种设备事件（feed 的 ``now_ts`` 由同一个 ts 换算）。"""
    return {
        "kind": "device", "domain": "binary_sensor",
        "entity_id": "binary_sensor.door_front", "tags": ["door"],
        "state": "on", "old_state": "off", "action": "off->on",
        "room": "客厅", "ts": ts,
    }


def _rule(store, engine, *, cooldown_seconds=1800, name="门磁告警", **kw) -> str:
    res = engine.add_rule(name, COND, ACTION,
                          cooldown_seconds=cooldown_seconds, **kw)
    assert res.get("ok"), res
    engine._index_dirty = True      # 让新建的规则立刻进倒排索引，不测缓存新鲜度
    return res["rule_id"]


def _replay(engine, ts: str) -> list[str]:
    """批量回放口径：``now_ts`` = 事件自身的 epoch（device_feed 就是这么传的）。"""
    epoch = wall_to_epoch(datetime.fromisoformat(ts), 8.0)
    return [r["rule_id"] for r in engine.match_event(_event(ts), now_ts=epoch)]


def _history(store, rule_id: str, triggered_at: str, *, dry_run: int = 0) -> None:
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO rule_trigger_history (rule_id, event_json, action_json,"
            " triggered_at, dry_run) VALUES (?, '{}', '{}', ?, ?)",
            (rule_id, triggered_at, dry_run),
        )


def _count_history(store, rule_id: str) -> int:
    with store.transaction() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM rule_trigger_history WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
    return int(row["n"])


# ── 1. 窗口内抑制、窗口外放行 ──────────────────────────────────────────────

def test_repeats_inside_the_window_are_suppressed_and_pass_after_it(store):
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800)

    assert _replay(engine, "2026-01-01T12:00:00") == [rid]   # 首次触发
    assert _replay(engine, "2026-01-01T12:10:00") == []      # 10 分钟后再命中：冷却内
    assert _replay(engine, "2026-01-01T12:29:59") == []      # 窗口内最后一秒
    assert _replay(engine, "2026-01-01T12:30:00") == [rid]   # 满窗即放行：区间是 [触发, 触发+冷却)
    assert _replay(engine, "2026-01-01T12:31:00") == []      # 满窗那一帧又占了新窗


def test_zero_cooldown_fires_on_every_event(store):
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=0)

    assert _replay(engine, "2026-01-01T12:00:00") == [rid]
    assert _replay(engine, "2026-01-01T12:00:30") == [rid]


@pytest.mark.parametrize("bad", ["", "abc", -60, "0"])
def test_unreadable_or_negative_cooldown_never_suppresses(store, bad):
    """坏数值一律读成「不冷却」：拿它抑制触发等于把家里的告警静默丢掉。"""
    assert ActiveRuleEngine._cooldown_seconds({"cooldown_seconds": bad}) == 0
    assert ActiveRuleEngine._cooldown_seconds({}) == 0
    assert ActiveRuleEngine._cooldown_seconds({"cooldown_seconds": None}) == 0

    # 走真路径：先建一条会抑制的规则，再把列改成坏值
    # （None 单列不进这条——该列 NOT NULL，库压根收不下，属另一件事）
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800)
    assert _replay(engine, "2026-01-01T12:00:00") == [rid]
    assert _replay(engine, "2026-01-01T12:00:30") == []      # 改坏之前的真实冷却

    assert engine.update_rule(rid, cooldown_seconds=bad).get("ok") is True
    engine._index_dirty = True
    assert _replay(engine, "2026-01-01T12:01:00") == [rid]


# ── 2. 首次触发不被长冷却吞（P1-1 同类缺陷的反例锁）─────────────────────────

def test_first_trigger_is_not_swallowed_by_a_long_cooldown(store):
    """冷却 1 天、事件时间是 2026-01-01：墙钟口径下首帧必触发。

    换成 ``time.monotonic()`` 口径，这条会因「冷却 > 进程 uptime」变成永不触发
    （第一轮审计 P1-1 的原始形态）。
    """
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=86400)
    assert _replay(engine, "2026-01-01T00:00:00") == [rid]


# ── 3. 重启续窗口（实时路径）/ 回放的时钟口径 ───────────────────────────────

def test_live_path_resumes_the_window_from_trigger_history(store):
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800)

    fired = engine.match_event(_event())
    assert [r["rule_id"] for r in fired] == [rid]
    engine.execute_action(fired[0], _event())
    assert _count_history(store, rid) == 1

    # 进程重启：全新实例，内存窗口为空，只剩库里的触发历史
    fresh = ActiveRuleEngine(store)
    fresh._index_dirty = True
    assert fresh.match_event(_event()) == []


def test_stale_trigger_history_does_not_block_a_new_window(store):
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800)
    _history(store, rid, (now_local(8.0) - timedelta(minutes=31)).isoformat(sep="T"))

    fresh = ActiveRuleEngine(store)
    fresh._index_dirty = True
    assert [r["rule_id"] for r in fresh.match_event(_event())] == [rid]


def test_batch_replay_judges_against_event_time_not_the_history_clock(store):
    """回放 2026-01-01 的历史事件时，不得拿「刚刚」的真实墙钟历史判它冷却中。

    这条锁的是 ``live=`` 那道闸：去掉它，``_last_trigger_wall`` 会把整轮回放吞成
    0 触发——dry_run 对照就此失去意义。
    """
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800)
    _history(store, rid, now_local(8.0).isoformat(sep="T"))   # 刚刚写过一条

    fresh = ActiveRuleEngine(store)
    fresh._index_dirty = True
    assert _replay(fresh, "2026-01-01T12:00:00") == [rid]


# ── 4. 与 count 触发 / 试运行的接缝 ─────────────────────────────────────────

def test_count_rule_fires_once_then_cools(store):
    """DCD Q2 的「60 秒 3 次」通过后只响一次，冷却期内不再重复。"""
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800,
                trigger={"type": "count", "window_seconds": 60, "min_count": 3})

    assert _replay(engine, "2026-01-01T12:00:00") == []       # 1 次
    assert _replay(engine, "2026-01-01T12:00:20") == []       # 2 次
    assert _replay(engine, "2026-01-01T12:00:40") == [rid]    # 3 次：达门槛，触发
    assert _replay(engine, "2026-01-01T12:01:00") == []       # 仍在 count 窗口内，被冷却抑制
    assert _replay(engine, "2026-01-01T12:01:20") == []


def test_dry_run_also_consumes_the_window(store):
    """观察期的规则同样「响一次」：试运行占窗口，触发计数才与转正后一致。"""
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800, mode="dry_run")

    assert _replay(engine, "2026-01-01T12:00:00") == [rid]
    assert _replay(engine, "2026-01-01T12:05:00") == []


def test_delete_rule_drops_the_in_memory_window(store):
    engine = ActiveRuleEngine(store)
    rid = _rule(store, engine, cooldown_seconds=1800)
    assert _replay(engine, "2026-01-01T12:00:00") == [rid]
    assert engine._cooldown_until.get(rid) is not None

    assert engine.delete_rule(rid).get("ok") is True
    assert rid not in engine._cooldown_until


# ── 5. DCD 20261004 MA-裁1 Q1：晋升必须显式带冷却上限（缺省即拒）────────────

def _lc(store):
    return RuleLifecycle(store, ActiveRuleEngine(store), min_evidence=3, dry_run_days=3)


def _accepted_candidate(store, *, name="门磁告警", cooldown=300) -> str:
    """一条过得了其余三条红线的 accepted 候选；``cooldown=None`` 表示列未设定。"""
    rid, action = store.upsert_candidate_rule(
        name=name, steps=[{"tag": "door", "state": "on"}], time_window="",
        infer="", confidence=0.7,
        evidence=["2026-09-11 门磁 1", "2026-09-12 门磁 2", "2026-09-13 门磁 3"])
    assert rid, action
    store.set_candidate_rule_status(rid, "accepted")
    if cooldown is not None:
        assert store.set_candidate_rule_cooldown(rid, cooldown)
    return rid


def test_promotion_without_a_cooldown_value_is_refused(store):
    """列没设定、参数也没给 —— 拒绝晋升，而不是悄悄吃一个从未被裁定的 300。"""
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=None)

    res = lc.promote(rid, actor="user")
    assert res["ok"] is False, res
    assert any(b.startswith("cooldown_gate") for b in res["blockers"]), res["blockers"]
    assert lc.engine.list_rules(enabled_only=False) == []      # 引擎里一条都没多
    assert store.get_candidate_rule(rid)["status"] == "accepted"
    audit = store.list_rule_lifecycle(rid)
    assert audit[0]["to_state"] == "rejected_by_gate"
    assert any("cooldown_gate" in b for b in audit[0]["detail"]["blockers"])


def test_promotion_writes_the_given_value_not_the_formal_default(store):
    """裁定值入库：列里存的是这次晋升给的数，不是 ``add_rule`` 的形参默认。"""
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=None)

    res = lc.promote(rid, actor="user", cooldown_seconds=900)
    assert res["ok"], res
    rule = lc.engine.get_rule(res["rule_id"])
    assert rule["cooldown_seconds"] == 900, rule
    assert res["cooldown_seconds"] == 900
    # 数值来源留痕：事后能区分"人这次给的"和"候选行里本来就有"
    audit = store.list_rule_lifecycle(res["rule_id"])
    assert audit[0]["detail"]["cooldown_seconds"] == 900
    assert audit[0]["detail"]["cooldown_source"] == "argument"
    # 参数值回写候选行，下次读候选就知道当初按多少晋升的
    assert store.get_candidate_rule(rid)["cooldown_seconds"] == 900


def test_candidate_column_is_the_fallback_when_the_call_omits_it(store):
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=600)

    res = lc.promote(rid, actor="user")
    assert res["ok"], res
    assert lc.engine.get_rule(res["rule_id"])["cooldown_seconds"] == 600
    gate = lc.eligibility(rid)
    assert gate["cooldown_source"] == "candidate_column"
    # 已晋升的行重复晋升会被 duplicate 挡住，来源读数仍取列
    assert store.get_candidate_rule(rid)["cooldown_seconds"] == 600


def test_the_argument_overrides_the_column(store):
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=600)

    res = lc.promote(rid, actor="user", cooldown_seconds=120)
    assert res["ok"], res
    assert lc.engine.get_rule(res["rule_id"])["cooldown_seconds"] == 120


@pytest.mark.parametrize("bad", [-60, "abc"])
def test_unusable_cooldown_values_are_refused(store, bad):
    """列里已是坏值也判拒：引擎端会把负数/读不出数的值当成「不冷却」，
    一条每次命中都广播的规则不该悄悄进引擎。"""
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=None)
    with store.transaction() as conn:
        conn.execute("UPDATE candidate_rules SET cooldown_seconds=? WHERE rule_id=?",
                     (bad, rid))
    assert lc.eligibility(rid)["cooldown_seconds"] is None
    res = lc.promote(rid, actor="user")
    assert res["ok"] is False
    assert any(b.startswith("cooldown_gate") for b in res["blockers"]), res["blockers"]
    # 拒因要说清是「这个值坏了」，不是「没设定」——两者都推人去补一个数，
    # 但前者的数本身就不该用。
    assert any(str(bad) in b for b in res["blockers"]), res["blockers"]

    # 列是好值、参数给坏值，同样判拒——显式给错就是错，不该退回列里那个数
    rid2 = _accepted_candidate(store, name="门磁告警2", cooldown=600)
    res2 = lc.promote(rid2, actor="user", cooldown_seconds=bad)
    assert res2["ok"] is False
    assert any(b.startswith("cooldown_gate") for b in res2["blockers"]), res2["blockers"]
    assert any(str(bad) in b for b in res2["blockers"]), res2["blockers"]


def test_pending_promotions_show_the_missing_number(store):
    """预演清单里「差一个数值」要看得见，否则人只能逐条点开才知道被什么挡住。"""
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=None)

    rows = lc.pending_promotions()
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == rid
    assert rows[0]["eligible"] is False
    assert rows[0]["cooldown_seconds"] is None
    assert any(b.startswith("cooldown_gate") for b in rows[0]["blockers"])
    # 其余三条红线全过——缺的只有数值这一项
    assert not any(b.startswith("evidence_gate") for b in rows[0]["blockers"])


def test_manual_rule_path_still_gets_the_default(store):
    """裁定只挪走晋升端的隐式默认：人工建规则那条路径维持既有语义。"""
    engine = ActiveRuleEngine(store)
    res = engine.add_rule("人工规则", COND, ACTION)
    assert res["ok"]
    assert engine.get_rule(res["rule_id"])["cooldown_seconds"] == 300


def test_promoted_rule_actually_honours_its_own_window(store):
    """晋升写入的数值真的会咬住匹配端——不是只躺在列里。

    晋升出的设备规则带 count 触发（60 秒 3 次），所以「够格响」要凑满三帧；
    冷却判的是够了之后响几次，两条闸门各咬一次才算接上。试运行占窗口（Q3）
    也在这条里生效。
    """
    lc = _lc(store)
    rid = _accepted_candidate(store, cooldown=None)
    res = lc.promote(rid, actor="user", cooldown_seconds=1800)
    assert res["ok"], res
    rule_id = res["rule_id"]
    engine = lc.engine

    assert _replay(engine, "2026-01-01T12:00:00") == []
    assert _replay(engine, "2026-01-01T12:00:20") == []
    assert _replay(engine, "2026-01-01T12:00:40") == [rule_id]     # count 达标，第一次响
    assert _replay(engine, "2026-01-01T12:01:00") == []            # 又够格，但冷却中
    assert _replay(engine, "2026-01-01T12:01:20") == []
    assert _replay(engine, "2026-01-01T12:31:00") == []            # 窗口刚满，count 还没凑齐
    assert _replay(engine, "2026-01-01T12:31:20") == []
    assert _replay(engine, "2026-01-01T12:31:40") == [rule_id]     # 满窗后再次响
