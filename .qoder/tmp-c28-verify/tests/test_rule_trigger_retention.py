"""``Store.purge_rule_triggers`` 直测 —— ADM 联动计划 第 3 步 ④ 的验收项。

计划卡原话：``rule_trigger_history`` 保留期 7 天 + 行数上限 10 万，超限自动裁剪。
此前只有 ``DeviceEventFeed.purge_trigger_history()`` 的**包装层**被测（喂假 store，
断言它把两个上限传下去了、store 坏了不谎报 ok）；真正执行 SQL 的
``Store.purge_rule_triggers`` 零直接测试——上限值传错、误报豁免写反、
rowcount 口径失真，都只在包装层下面发生。

这里对着真库测四条：
1. 到期行被删、未到期行留下；
2. **被人工标为误报的行不受保留期约束**（唯一的负样本，裁不起）；
3. 行数上限裁的是超额最旧行，且误报行既不占额度也不被裁；
4. 返回读数与表内真实计数一致（观察期判据与误报计数都读这个字典）。
"""

from __future__ import annotations

import os
import tempfile

import pytest

from memory_agent.store import Store


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


def _insert(store, rule_id: str, triggered_at: str, *, false_positive: int = 0) -> int:
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO rule_trigger_history (rule_id, event_json, action_json,"
            " triggered_at, false_positive) VALUES (?, '{}', '{}', ?, ?)",
            (rule_id, triggered_at, false_positive),
        )
        return int(cur.lastrowid or 0)


def _rows(store):
    with store.transaction() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT rule_id, triggered_at, false_positive FROM rule_trigger_history"
            " ORDER BY triggered_at, rule_id"
        ).fetchall()]


def test_expired_rows_go_but_fresh_rows_stay(store):
    _insert(store, "r-old", "2026-09-01T08:00:00")
    _insert(store, "r-new", "2026-09-20T08:00:00")
    res = store.purge_rule_triggers("2026-09-10T00:00:00")
    assert res["deleted_expired"] == 1
    assert [r["rule_id"] for r in _rows(store)] == ["r-new"]
    assert res["remaining"] == 1


def test_labeled_false_positive_survives_the_retention_window(store):
    """误报标注是人工判断，不是机器日志：到期也不裁。"""
    _insert(store, "r-labeled", "2026-09-01T08:00:00", false_positive=1)
    _insert(store, "r-plain", "2026-09-01T09:00:00")
    res = store.purge_rule_triggers("2026-09-10T00:00:00")
    assert res["deleted_expired"] == 1
    assert res["kept_labeled"] == 1
    assert [r["rule_id"] for r in _rows(store)] == ["r-labeled"]


def test_row_cap_drops_oldest_unlabeled_only(store):
    for i in range(5):
        _insert(store, f"r{i}", f"2026-09-{10 + i:02d}T08:00:00")
    # 一行超额的都不该动：上限够宽
    untouched = store.purge_rule_triggers("2026-01-01T00:00:00", max_rows=10)
    assert untouched["deleted_over_cap"] == 0
    # 上限 2 → 裁掉最旧的 3 条（r0/r1/r2），留下 r3/r4
    res = store.purge_rule_triggers("2026-01-01T00:00:00", max_rows=2)
    assert res["deleted_over_cap"] == 3
    assert [r["rule_id"] for r in _rows(store)] == ["r3", "r4"]


def test_row_cap_ignores_labeled_rows_in_the_quota(store):
    """误报行不占 10 万额度：否则标注攒到上限就会把观测记录整片挤掉。"""
    _insert(store, "labeled", "2026-09-01T08:00:00", false_positive=1)
    for i in range(4):
        _insert(store, f"r{i}", f"2026-09-{10 + i:02d}T08:00:00")
    res = store.purge_rule_triggers("2026-01-01T00:00:00", max_rows=2)
    assert res["deleted_over_cap"] == 2, "只裁未标注的超额最旧两条"
    kept = _rows(store)
    assert {"labeled"} <= {r["rule_id"] for r in kept}
    assert res["kept_labeled"] == 1
    assert res["remaining"] == len(kept) == 3


def test_purge_on_empty_table_reports_zeros(store):
    res = store.purge_rule_triggers("2026-09-10T00:00:00")
    assert res["deleted_expired"] == 0
    assert res["deleted_over_cap"] == 0
    assert res["remaining"] == 0
    assert res["kept_labeled"] == 0
    assert res["max_rows"] == 100_000, "默认上限即裁定值"
