"""C1 退化基线：river 缺失时 `mine_drift` 不整体失败，仍给「同一小时历史分布偏离」一维。

叶子（DCD 20261004 §七十五：「修复＝依赖行＋镜像重建窗（或确定性退化基线）」）
本次选**退化基线**：不需要重烤镜像，C1 从「一调就 INTERNAL」变成「可用（少一维）」。

三条锁定：
1. river 缺失 → ``degraded=true`` + ``degraded_reason``，``drift.detected`` 恒 false，
   但 ``anomalies`` 仍按纯 Python 判据产出；
2. **非缺依赖的失败不吞**——照旧 ``ok=false``（吞了就是假绿）；
3. 退化态 ``persist=true`` 也照常把异常时段写 ``behavior_drifts``（kind=anomaly）。
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
    tmp = tempfile.mkdtemp(prefix="ma_drift_deg_")
    s = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


def _evs(ts: datetime, n: int, room: str = "客厅"):
    return [{
        "entity_id": f"light.living_{i}", "ts": ts.isoformat(sep="T"), "room": room,
        "domain": "light", "new_state": "on", "old_state": "",
    } for i in range(n)]


def _seed_hour8_spike(store):
    """10 天 08:00 各 1 条（平稳基线）＋ 第 11 天 08:00 骤增 50 条（可解释异常）。"""
    base = datetime(2026, 9, 1, 0, 0, 0)
    evs = []
    for d in range(10):
        evs += _evs(base + timedelta(days=d, hours=8), 1)
    evs += _evs(base + timedelta(days=10, hours=8), 50)
    store.insert_events(evs)
    return base, (base + timedelta(days=11)).isoformat(sep="T")


class _NoRiver:
    """顶掉 OnlineAnomalyDetector：模拟运行面无 river。"""

    available = False

    def score_stream(self, *args, **kwargs):  # pragma: no cover - 退化路径不调用它
        return {"ok": False, "error": "river 未安装"}


def test_mine_drift_degrades_without_river(store, monkeypatch):
    from memory_agent import algo_kernel as ak

    monkeypatch.setattr(ak, "OnlineAnomalyDetector", lambda *a, **k: _NoRiver())
    base, end = _seed_hour8_spike(store)

    res = ActivityInferenceService(_runtime(store)).mine_drift(
        start=base.isoformat(sep="T"), end=end, bucket_sec=3600, persist=False)

    assert res["ok"] is True
    assert res["degraded"] is True
    assert "river" in res.get("degraded_reason", "")
    # ADWIN 那一维确实没跑，⛔ 报成"没漂移"
    assert res["drift_count"] == 0
    assert res["drifts"] == []
    # 纯 Python 的可解释异常仍在：08:00 的 50 条被报出来
    assert res["anomaly_count"] >= 1
    hit = [a for a in res["anomalies"] if a["hour"] == 8]
    assert hit and hit[0]["density"] == 50 and hit[0]["expected"] < 5
    # HST 相对排名在退化态为空（不是 0，是"没有这一维"）
    assert res["relative_top"] == []


def test_non_dependency_failure_is_not_swallowed(store, monkeypatch):
    """river 在场但算法自己炸了：必须如实 ok=false，⛔ 退化成"可用"糊过去。"""
    from memory_agent import algo_kernel as ak

    class _Boom:
        available = True

        def score_stream(self, *args, **kwargs):
            return {"ok": False, "error": "boom"}

    monkeypatch.setattr(ak, "OnlineAnomalyDetector", lambda *a, **k: _Boom())
    base = datetime(2026, 9, 1, 0, 0, 0)
    # 每桶 1 条、铺满 12 个小时 ⇒ 观测序列 ≥10 点，才走得到 detector 那一跳
    # （序列只覆盖"首末事件之间"，不是 start..end；挤在同一小时只会得到 1 个点、
    #  在样本不足处提前返回 ok=true，测不到本结论）
    evs = []
    for h in range(12):
        evs += _evs(base + timedelta(hours=h), 1)
    store.insert_events(evs)

    res = ActivityInferenceService(_runtime(store)).mine_drift(
        start=base.isoformat(sep="T"),
        end=(base + timedelta(hours=12)).isoformat(sep="T"), bucket_sec=3600)

    assert res["ok"] is False
    assert res["degraded"] is False
    assert "boom" in (res.get("error") or "")


def test_degraded_persist_writes_anomaly_rows(store, monkeypatch):
    from memory_agent import algo_kernel as ak

    monkeypatch.setattr(ak, "OnlineAnomalyDetector", lambda *a, **k: _NoRiver())
    base, end = _seed_hour8_spike(store)

    res = ActivityInferenceService(_runtime(store)).mine_drift(
        start=base.isoformat(sep="T"), end=end, bucket_sec=3600, persist=True)

    assert res["degraded"] is True and res["persisted"] >= 1
    rows = store.list_behavior_drifts(kind="anomaly")
    assert rows and rows[0]["kind"] == "anomaly"
    assert (rows[0]["detail"] or {}).get("reason") == "hour_of_day_deviation"
