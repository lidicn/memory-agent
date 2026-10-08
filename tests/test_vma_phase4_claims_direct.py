"""2 期第十三批 :「声明语义 ↔ 直接断言」第三批（UNVERIFIED 只准减，2026-10-08）。

`scripts/scan_claimed_semantics.py` 的基线在 run31 之后还剩 18 条。本文件收六格，分两种收法：

| 格 | docstring 的承诺 | 收法 | 断的那一半 |
| --- | --- | --- | --- |
| `store.py::insert_behavior_event` | 返回**自增** id | 新增直接断言 | 同库连续三条严格递增；**重开库**后继续递增（不是每次开机从 1 重来） |
| `store.py::get_behavior_event` | 按**自增** id 取单条 | 新增直接断言 | 写进去的 id 取得回来（含 persons/appearance 反序列化）；**没写过的 id 返回 None**（不是空 dict） |
| `store.py::save_arena_snapshot` | **递增**版本化快照 | 新增直接断言 | 同分区两次 ⇒ version 1→2 且 id 递增；**不同分区各自从 1 起**（缺 `WHERE arena_id` 就红在这里） |
| `api.py::_closed_ok` | fail-closed | 既有用例 + 本文件补形状档 | `test_fail_closed_ok_is_derived_not_literal` 已把三条不变式各喂一次；本文件再补"键整个不存在 / `count` 是字符串 / `anomalies` 非 list / 两条坏同时成立"四种形状 |
| `api.py::anomaly_report` | fail-closed | 指向既有用例 | 同上 + `test_anomaly_report_query_lands_on_entities_or_fails_closed`（解析不出 ⇒ 不回落全屋异常） |
| `app.py::metrics_ingest_endpoint` | 按 dedupe_key **幂等** | 指向既有用例 | `test_ingest_replaces_the_same_dedupe_key_instead_of_doubling` + 并发 20 条全落 |

后三格的基线文字（"只被上层用例间接覆盖""现无""没有单独用例"）**与现树不符**——那些直接断言是
后续批次补用例时加进去的，而基线只检查"函数还在不在、声明词还成不成立"，不检查"缺的那一半是不是
已经被后来的用例补上了"，所以行一直留在那儿。本批按"基线只准减"把它们移进 `REGISTRY`，
并同样用变异腿证明指向的用例**真能判红**（腿 N06..N09），不是靠用例名字对上了就算。

写法约束与前两批同口径：每条承诺都要有**对偶档**。`insert_behavior_event` 只断"三条不同"是不够的——
把返回值恒写成 `0` 就同时打穿两条腿；`get_behavior_event` 必须断"不存在的 id ⇒ None"；
`save_arena_snapshot` 必须断"换个分区版本从 1 起"。九条变异腿（run32 档 N01–N09）逐条把这些退回去。
"""

import json
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402


def _db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    return path


def _drop(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


@pytest.fixture
def store():
    path = _db_path()
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    yield st
    st.close()
    _drop(path)


# ── 1. store.insert_behavior_event：自增 id ─────────────────────────────────

def _bev(seq, room="客厅"):
    return {
        "server_ts": "2026-10-08T%02d:00:00.000000" % seq,
        "day": "2026-10-08",
        "room": room,
        "persons": ["%d号" % seq],
        "count": seq,
        "action": "坐在电竞沙发看书",
        "scene": "晚间阅读",
        "trigger": "manual",
        "status": "ok",
    }


def test_insert_behavior_event_ids_strictly_increase_inside_one_store(store):
    ids = [store.insert_behavior_event(_bev(i)) for i in (1, 2, 3)]
    assert all(i >= 1 for i in ids), ids
    assert ids == sorted(ids) and len(set(ids)) == 3, ids
    rows = store.db_query("SELECT id FROM behavior_events ORDER BY id")
    assert [r["id"] for r in rows] == ids          # 返回的 id 就是落库那一行的 id


def test_insert_behavior_event_keeps_increasing_after_the_store_is_reopened(store):
    path = store.db_path
    first = store.insert_behavior_event(_bev(1))
    second = store.insert_behavior_event(_bev(2))
    store.close()
    reopened = Store(path, tz_offset_hours=8.0)
    try:
        reopened.init_schema()
        third = reopened.insert_behavior_event(_bev(3))
        assert third > second > first, (first, second, third)
        assert len(reopened.db_query("SELECT id FROM behavior_events")) == 3
    finally:
        reopened.close()


# ── 2. store.get_behavior_event：按自增 id 取，取不到就说取不到 ──────────────

def test_get_behavior_event_returns_the_row_that_insert_wrote(store):
    written = store.insert_behavior_event({
        "server_ts": "2026-10-08T21:00:00.000000",
        "day": "2026-10-08",
        "room": "电竞房",
        "persons": ["alice"],
        "count": 2,
        "action": "打游戏",
        "scene": "夜间游戏",
        "confidence": 0.81,
        "appearance": {"outfit": "卫衣"},
        "trigger": "identity_change",
        "vlm_latency_ms": 1234,
        "status": "ok",
        "client": "tv-01",
    })
    row = store.get_behavior_event(written)
    assert row is not None
    assert row["id"] == written
    assert row["room"] == "电竞房" and row["action"] == "打游戏"
    assert row["persons"] == [{"name": "alice", "confidence": 0.0}]
    assert row["appearance"] == {"outfit": "卫衣"}
    assert row["count"] == 2 and row["confidence"] == 0.81
    assert "persons_json" not in row and "appearance_json" not in row


def test_get_behavior_event_returns_none_for_an_id_that_was_never_written(store):
    store.insert_behavior_event(_bev(1))
    assert store.get_behavior_event(0) is None
    assert store.get_behavior_event(999999) is None
    assert store.get_behavior_event(None) is None      # 坏入参也不是"取到一条"


# ── 3. store.save_arena_snapshot：版本递增（按分区）─────────────────────────

def test_save_arena_snapshot_increments_the_version_for_the_same_arena(store):
    first = store.save_arena_snapshot("study", "书房", "light.a", 7, json.dumps({"n": 1}))
    second = store.save_arena_snapshot("study", "书房", "light.a", 7, json.dumps({"n": 2}))
    assert first["version"] == 1 and second["version"] == 2, (first, second)
    assert second["id"] > first["id"]
    rows = store.db_query(
        "SELECT version, snapshot_json FROM arena_snapshots WHERE arena_id=? ORDER BY version",
        ("study",))
    assert [r["version"] for r in rows] == [1, 2]
    assert json.loads(rows[1]["snapshot_json"]) == {"n": 2}   # 新版本装的是新载荷


def test_save_arena_snapshot_versions_are_independent_per_arena(store):
    # 对偶档：MAX(version) 必须带 WHERE arena_id。少了它，第二个分区就从 3 起。
    a1 = store.save_arena_snapshot("arena_a", "书房", "x", 7, "{}")
    b1 = store.save_arena_snapshot("arena_b", "客厅", "y", 7, "{}")
    a2 = store.save_arena_snapshot("arena_a", "书房", "x", 7, "{}")
    assert (a1["version"], b1["version"], a2["version"]) == (1, 1, 2), (a1, b1, a2)
    per_arena = store.db_query(
        "SELECT arena_id, MAX(version) AS v FROM arena_snapshots GROUP BY arena_id ORDER BY arena_id")
    assert [(r["arena_id"], r["v"]) for r in per_arena] == [("arena_a", 2), ("arena_b", 1)]


# ── 4. api._closed_ok：三条不变式的形状档（既有那条只喂了 list/dict 的"标准坏法"）────
#
# 对偶档的**形状**也要有第二档：`test_fail_closed_ok_is_derived_not_literal`
# （tests/test_vma_qb_param_landing.py:251）已经把 count 不符 / 带实体集 / 无 unresolved /
# anomalies=None 四种退回判红；这里补它没喂的边界——**键整个不存在**、`count` 是字符串、
# `anomalies` 是非 list 的可迭代对象、以及"两条坏同时成立时不许只看第一条"。
# 这四格是为了把 `return True`（N06）与"实体集那格关掉"（N07）之外的写法漂移也钉住。

@pytest.mark.parametrize("anomalies,summary,filters,want", [
    ([], {}, {"entity_ids": [], "unresolved": "解析不出实体"}, True),          # count 缺省 = 0
    ([], {"count": "0"}, {"entity_ids": [], "unresolved": "x"}, True),         # 字符串 "0" 也算对上
    ((), [], {"entity_ids": [], "unresolved": "x"}, False),                    # 非 list ⇒ 不算自洽
    ({"a": 1}, {"count": 1}, {"entity_ids": ["light.a"], "unresolved": "x"}, False),  # 两条坏同时成立
    ([], {"count": 0}, {"entity_ids": ["light.a"]}, False),                    # 带实体集又没原因
    ([], {"count": 0}, {"entity_ids": [], "unresolved": ""}, False),           # 空原因 = 静默 0 条
])
def test_closed_ok_requires_both_self_consistency_and_a_stated_reason(anomalies, summary,
                                                                      filters, want):
    from memory_agent.insights.api import InsightService
    assert InsightService._closed_ok(anomalies, summary, filters) is want


# ── 5. 这批登记自身的形状（"指向既有用例"不能是指向一个不存在的名字）──────────

def test_the_six_reductions_are_registered_and_each_has_two_named_cases():
    import importlib.util as _u
    spec = _u.spec_from_file_location("g4", os.path.join(
        os.path.dirname(__file__), "..", "scripts", "scan_claimed_semantics.py"))
    gauge = _u.module_from_spec(spec)
    spec.loader.exec_module(gauge)
    keys = [("store.py", "insert_behavior_event"), ("store.py", "get_behavior_event"),
            ("store.py", "save_arena_snapshot"), ("api.py", "_closed_ok"),
            ("api.py", "anomaly_report"), ("app.py", "metrics_ingest_endpoint")]
    for key in keys:
        assert key in gauge.REGISTRY, key
        assert key not in gauge.UNVERIFIED, key
        entry = gauge.REGISTRY[key]
        assert any(w in entry["claims"] for w in ("自增", "递增", "幂等", "fail-closed")), key
        assert len(entry["cases"]) >= 2, key
        for tfile, tname in entry["cases"]:
            assert tfile and tname, (key, tfile, tname)
