"""P1.2 在线异常 / 概念漂移（river）单测：存储幂等 + 服务层漂移检出。

与 P1.1 互补：P1.1 是"按天做事后一致性检验"，P1.2 是"把活跃度当时间序列看突变"。
river 缺失时服务层用例自动跳过；存储用例不依赖第三方库。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402


class _FakeInsights:
    def _tags_of(self, eid, name):
        return InsightService._tags_of(eid, name)


def _runtime(store):
    cfg = SimpleNamespace(tz_offset_hours=0.0, rooms={})
    return SimpleNamespace(config=cfg, store=store, insights=_FakeInsights())


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_drift_")
    s = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _evs(ts: datetime, n: int, room: str = "客厅"):
    return [{
        "entity_id": f"light.living_{i}", "ts": ts.isoformat(sep="T"), "room": room,
        "domain": "light", "new_state": "on", "old_state": "",
    } for i in range(n)]


# ── 存储：按 (桶, 类型) 幂等 ────────────────────────────────────────────────

def test_drift_upsert_idempotent_by_bucket_and_kind(store):
    did, action = store.upsert_behavior_drift(
        "2026-09-10T08:00:00", kind="drift", density=12.0, score=0.31,
        tags=["light", "presence"])
    assert action == "added"

    # 同一 (桶, 类型) 重跑 → 更新而非新增
    did2, action2 = store.upsert_behavior_drift(
        "2026-09-10T08:00:00", kind="drift", density=13.0, score=0.42)
    assert did2 == did and action2 == "updated"

    # 同一桶不同类型 → 两条记录
    store.upsert_behavior_drift("2026-09-10T08:00:00", kind="anomaly", score=0.9)
    assert len(store.list_behavior_drifts()) == 2
    assert len(store.list_behavior_drifts(kind="drift")) == 1

    row = store.list_behavior_drifts(kind="drift")[0]
    assert row["density"] == 13.0 and row["score"] == 0.42
    assert row["day"] == "2026-09-10"


def test_drift_list_filters_and_purge(store):
    store.upsert_behavior_drift("2026-09-01T01:00:00", kind="drift", density=1.0)
    store.upsert_behavior_drift("2026-09-10T01:00:00", kind="drift", density=5.0)
    assert [r["day"] for r in store.list_behavior_drifts()] == ["2026-09-10", "2026-09-01"]
    assert len(store.list_behavior_drifts(day_from="2026-09-05")) == 1
    assert store.purge_behavior_drifts("2026-09-30") == 2
    assert store.list_behavior_drifts() == []


# ── 服务层：作息/活跃度阶跃 → 检出漂移 ──────────────────────────────────────

def _seed_regime_shift(store, sparse_hours: int = 240, dense_hours: int = 240):
    """前 10 天稀疏（1 事件/小时），后 10 天密集（8 事件/小时）。"""
    base = datetime(2026, 9, 1, 0, 0, 0)
    evs = []
    for h in range(sparse_hours):
        evs += _evs(base + timedelta(hours=h), 1)
    for h in range(sparse_hours, sparse_hours + dense_hours):
        evs += _evs(base + timedelta(hours=h), 8)
    store.insert_events(evs)
    return (base.isoformat(sep="T"),
            (base + timedelta(hours=sparse_hours + dense_hours)).isoformat(sep="T"))


def test_mine_drift_detects_regime_shift(store):
    pytest.importorskip("river")
    start, end = _seed_regime_shift(store)
    svc = ActivityInferenceService(_runtime(store))

    res = svc.mine_drift(start=start, end=end, bucket_sec=3600)
    assert res["ok"] is True
    assert res["points"] >= 480                      # 补零后覆盖整段窗口
    assert res["bucket_sec"] == 3600
    assert res["drift_count"] >= 1                   # ADWIN 检出活跃度分布突变
    assert res["persisted"] >= 1

    rows = store.list_behavior_drifts(kind="drift")
    assert rows and rows[0]["kind"] == "drift"
    assert rows[0]["detail"].get("reason") == "adwin_mean_shift"


def test_mine_drift_persist_false(store):
    pytest.importorskip("river")
    start, end = _seed_regime_shift(store)
    svc = ActivityInferenceService(_runtime(store))
    res = svc.mine_drift(start=start, end=end, bucket_sec=3600, persist=False)
    assert res["ok"] is True and res["persisted"] == 0
    assert store.list_behavior_drifts() == []


def test_mine_drift_insufficient_points(store):
    """样本点太少 → 明确告知不做检测，而不是硬算出一个假结论。"""
    base = datetime(2026, 9, 1, 0, 0, 0)
    store.insert_events(_evs(base, 2))
    svc = ActivityInferenceService(_runtime(store))
    res = svc.mine_drift(start=base.isoformat(sep="T"),
                         end=(base + timedelta(hours=3)).isoformat(sep="T"),
                         bucket_sec=3600)
    assert res["ok"] is True
    assert res["drift_count"] == 0 and res["persisted"] == 0
    assert "不足" in res.get("note", "")
