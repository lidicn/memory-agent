"""``ActiveRuleEngine`` 冷却期的直测（审计 §十七）。

此前 ``_check_cooldown`` 是 ``# TODO / return True`` 桩：``add_rule`` 把
``cooldown_seconds`` 老老实实写进 ``active_rules`` 列，``update_rule`` 也允许改它，
匹配端却从不读——规则作者写的「5 分钟内不重复」完全不成立。运行时复现（连投三条
事件，触发读数 1 1 1）见交付记录。

锁的八条，对着真库真引擎，覆盖三类真会出事的方向：
1. **窗口内抑制、窗口外放行**（冷却的基本语义）；
2. **首次触发不被长冷却吞**（第一轮审计 P1-1 那一类：单调时钟从进程 0 起算，
   冷却 > uptime 就永久静默——本实现改用家庭墙钟，这条是它的反例锁）；
3. **重启后续上窗口**（实时路径读 ``rule_trigger_history``），同时**批量回放不读**
   （回放判「事件时刻」，历史行的 ``triggered_at`` 是当时的真实墙钟，读了会把整轮
   误判成冷却中）；外加 count 接缝、试运行占窗口、坏数值不抑制、删规则不留脏键。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from memory_agent.device_feed import wall_to_epoch
from memory_agent.rule_engine import ActiveRuleEngine
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
