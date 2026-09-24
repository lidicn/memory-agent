"""tests/test_change_attribution.py — 行为变化归因单元测试

覆盖验收标准 §8 全部 14 条：
  5  无变化随机数据        → test_detect_no_change_random
  6  显著变化数据           → test_detect_significant_change
  7  detect_change 8 键     → test_detect_return_keys
  8  before/after 5 键      → test_detect_before_after_keys
  9  候选原因 7 键          → test_search_keys
  10 confidence 降序        → test_search_sorted_by_confidence
  11 changed=False 空候选   → test_attribute_no_change_empty_causes
  12 changed=True 非空候选  → test_attribute_with_change_has_causes
  13 全字段 + candidate     → test_attribute_returns_all_fields
  14 边界覆盖               → 各 test_boundary_* 条目
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.change_attribution import (   # noqa: E402
    _cohens_d,
    _confidence,
    _mw_p,
    attribute,
    detect_change,
    search_candidate_causes,
)


# ─── 测试工具 ──────────────────────────────────────────────────

def _ev(
    ts: str,
    action: str = "tv_on",
    scene: str = "客厅",
    room: str = "客厅",
    persons: list | None = None,
    extra: str = "{}",
) -> dict:
    """构造 behavior_event 字典。"""
    return {
        "id": 0,
        "server_ts": ts,
        "action": action,
        "scene": scene,
        "room": room,
        "persons_json": json.dumps(persons or []),
        "source": "edge_ai",
        "confidence": 0.9,
        "extra_json": extra,
    }


def _pev(ts: str, person: str, action: str = "face_known", **kw) -> dict:
    """构造含指定人员的事件。"""
    return _ev(ts, action=action, persons=[{"name": person, "confidence": 0.9}], **kw)


def _mk_arrival_events(
    person: str, start: datetime, hours: list[float],
) -> list[dict]:
    """按 hours 列表逐天创建到家事件（每天 1 条）。"""
    evs = []
    for i, h in enumerate(hours):
        d = start + timedelta(days=i)
        hh, mm = int(h), max(0, min(59, int((h - int(h)) * 60)))
        ts = d.replace(hour=hh, minute=mm, second=0)
        evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), person))
    return evs


# ═══════════════════════════════════════════════════════════════
# detect_change 测试
# ═══════════════════════════════════════════════════════════════

class TestDetectChange:
    """detect_change 核心功能与边界。"""

    # ── 验收 7：8 键 ────────────────────────────────────────

    def test_detect_return_keys(self):
        """返回值必须含 changed/metric/person/before/after/effect_size/p_value/direction 八键。"""
        hours = [18.0, 18.5, 19.0, 18.0, 18.5, 17.5, 19.0, 18.0, 18.5, 19.0]
        evs = _mk_arrival_events("Kevin", datetime(2026, 9, 1), hours)
        r = detect_change(evs, "Kevin", "arrival_time")
        expected = {
            "changed", "metric", "person", "before", "after",
            "effect_size", "p_value", "direction",
        }
        assert set(r.keys()) == expected

    # ── 验收 8：before/after 各 5 键 ─────────────────────────

    def test_detect_before_after_keys(self):
        """before 和 after 各含 mean/median/std/count/period 五个键。"""
        hours = [18.0, 19.0, 17.0, 20.0, 18.0, 19.0, 17.5, 18.5, 19.5, 18.0]
        evs = _mk_arrival_events("K", datetime(2026, 9, 1), hours)
        r = detect_change(evs, "K", "arrival_time")
        expected = {"mean", "median", "std", "count", "period"}
        assert set(r["before"].keys()) == expected
        assert set(r["after"].keys()) == expected
        # period 格式验证
        assert "~" in r["before"]["period"]
        assert "~" in r["after"]["period"]

    # ── 验收 5：无变化随机数据 → changed=False ───────────────

    def test_detect_no_change_random(self):
        """无变化随机数据（同分布）返回 changed=False。"""
        rng = random.Random(2026)
        evs = []
        base = datetime(2026, 6, 1)
        for i in range(60):
            d = base + timedelta(days=i)
            h = max(0.0, min(23.98, 18.0 + rng.gauss(0, 0.5)))
            hh, mm = int(h), max(0, min(59, int((h - int(h)) * 60)))
            ts = d.replace(hour=hh, minute=mm, second=0)
            evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K"))
        r = detect_change(evs, "K", "arrival_time")
        assert r["changed"] is False, (
            f"effect_size={r['effect_size']}, p_value={r['p_value']}"
        )

    # ── 验收 6：显著变化 → changed=True 且 effect_size>0.5 ──

    def test_detect_significant_change(self):
        """均值偏移 > 1 个标准差 → changed=True 且 effect_size > 0.5。"""
        rng = random.Random(42)
        evs = []
        base = datetime(2026, 9, 1)
        for i in range(20):
            d = base + timedelta(days=i)
            h = max(0.0, min(23.98, (18.0 if i < 10 else 21.0) + rng.gauss(0, 0.3)))
            hh, mm = int(h), max(0, min(59, int((h - int(h)) * 60)))
            ts = d.replace(hour=hh, minute=mm, second=0)
            evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K"))
        r = detect_change(evs, "K", "arrival_time")
        assert r["changed"] is True
        assert r["effect_size"] > 0.5

    # ── 方向检测 ─────────────────────────────────────────────

    def test_detect_direction_increase(self):
        """均值显著增加 → direction='increase'。"""
        hours = (
            [17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.5]
            + [21.0, 21.5, 22.0, 21.0, 21.5, 22.0, 21.0, 21.5, 22.0, 21.5]
        )
        evs = _mk_arrival_events("K", datetime(2026, 9, 1), hours)
        r = detect_change(evs, "K", "arrival_time")
        assert r["changed"] is True
        assert r["direction"] == "increase"

    def test_detect_direction_decrease(self):
        """均值显著减少 → direction='decrease'。"""
        hours = (
            [21.0, 21.5, 22.0, 21.0, 21.5, 22.0, 21.0, 21.5, 22.0, 21.5]
            + [17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.5]
        )
        evs = _mk_arrival_events("K", datetime(2026, 9, 1), hours)
        r = detect_change(evs, "K", "arrival_time")
        assert r["changed"] is True
        assert r["direction"] == "decrease"

    # ── 各 metric 类型 ───────────────────────────────────────

    def test_detect_activity_count(self):
        """activity_count：活动量从 2 次/天 变为 8 次/天。"""
        evs = []
        base = datetime(2026, 9, 1)
        for i in range(20):
            d = base + timedelta(days=i)
            n_ev = 2 if i < 10 else 8
            for j in range(n_ev):
                ts = d.replace(hour=8 + j, minute=0)
                evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K"))
        r = detect_change(evs, "K", "activity_count")
        assert r["changed"] is True
        assert r["effect_size"] > 0.5
        assert r["direction"] == "increase"

    def test_detect_room_distribution(self):
        """room_distribution：书房占比从 0.75 变为 0.25。"""
        evs = []
        base = datetime(2026, 9, 1)
        for i in range(20):
            d = base + timedelta(days=i)
            for j in range(8):
                ts = d.replace(hour=9 + j, minute=0)
                if i < 10:
                    rm = "书房" if j < 6 else "客厅"
                else:
                    rm = "书房" if j < 2 else "客厅"
                evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K", room=rm, scene=rm))
        r = detect_change(evs, "K", "room_distribution", room="书房")
        assert r["changed"] is True
        assert r["direction"] == "decrease"

    def test_detect_active_duration(self):
        """active_duration：活跃时长从 2h 变为 8h。"""
        evs = []
        base = datetime(2026, 9, 1)
        for i in range(20):
            d = base + timedelta(days=i)
            hrs = [18, 20] if i < 10 else [10, 18]
            for h in hrs:
                ts = d.replace(hour=h, minute=0)
                evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K"))
        r = detect_change(evs, "K", "active_duration")
        assert r["changed"] is True
        assert r["direction"] == "increase"

    # ── split_ratio ──────────────────────────────────────────

    def test_detect_split_ratio(self):
        """split_ratio=0.3 → before 3 天，after 7 天。"""
        hours = [18.0, 19.0, 20.0, 21.0, 22.0, 18.0, 19.0, 20.0, 21.0, 22.0]
        evs = _mk_arrival_events("K", datetime(2026, 9, 1), hours)
        r = detect_change(evs, "K", "arrival_time", split_ratio=0.3)
        assert r["before"]["count"] == 3
        assert r["after"]["count"] == 7

    # ── 边界 ─────────────────────────────────────────────────

    def test_boundary_empty_events(self):
        """边界：空事件列表 → changed=False，count=0。"""
        r = detect_change([], "K", "arrival_time")
        assert r["changed"] is False
        assert r["before"]["count"] == 0
        assert r["after"]["count"] == 0

    def test_boundary_person_not_found(self):
        """边界：事件中不含此人 → changed=False。"""
        evs = [_pev("2026-09-01T18:00:00", "Alice")]
        r = detect_change(evs, "Bob", "arrival_time")
        assert r["changed"] is False

    def test_boundary_single_day(self):
        """边界：仅 1 天数据（无法分割） → changed=False。"""
        evs = [_pev("2026-09-01T18:00:00", "K")]
        r = detect_change(evs, "K", "arrival_time")
        assert r["changed"] is False
        assert r["effect_size"] == 0.0

    def test_boundary_single_person_multi_day(self):
        """边界：单人多天事件正常处理。"""
        hours = [18.0, 18.5, 17.5, 19.0, 18.0, 18.5, 17.5, 19.0, 18.0, 18.5]
        evs = _mk_arrival_events("Kevin", datetime(2026, 9, 1), hours)
        r = detect_change(evs, "Kevin", "arrival_time")
        assert r["person"] == "Kevin"
        assert r["metric"] == "arrival_time"
        assert r["before"]["count"] == 5
        assert r["after"]["count"] == 5


# ═══════════════════════════════════════════════════════════════
# 统计方法单元测试
# ═══════════════════════════════════════════════════════════════

class TestStatistics:
    """Cohen's d 与 Mann-Whitney U 辅助函数。"""

    def test_cohens_d_positive(self):
        """after > before → 正 d。"""
        assert _cohens_d([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) > 0

    def test_cohens_d_negative(self):
        """after < before → 负 d。"""
        assert _cohens_d([4.0, 5.0, 6.0], [1.0, 2.0, 3.0]) < 0

    def test_cohens_d_equal_means(self):
        """均值相等 → d = 0。"""
        assert _cohens_d([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == 0.0

    def test_cohens_d_zero_variance(self):
        """零方差 + 不同均值 → 封顶值。"""
        d = _cohens_d([1.0, 1.0, 1.0], [2.0, 2.0, 2.0])
        assert d == 10.0

    def test_mw_identical(self):
        """完全相同两组 → p = 1.0。"""
        assert _mw_p([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0

    def test_mw_complete_separation(self):
        """完全分离的两组 → p < 0.05。"""
        p = _mw_p([1.0, 2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0, 10.0])
        assert p < 0.05

    def test_mw_empty_group(self):
        """任一组为空 → p = 1.0。"""
        assert _mw_p([], [1.0, 2.0]) == 1.0
        assert _mw_p([1.0, 2.0], []) == 1.0


# ═══════════════════════════════════════════════════════════════
# search_candidate_causes 测试
# ═══════════════════════════════════════════════════════════════

class TestSearchCandidateCauses:
    """候选原因搜索。

    change_start_ts = 2026-09-10 → 变化窗口 [09-03, 09-10]，基线 [08-27, 09-03)。
    """

    # ── 验收 9：8 键 ─────────────────────────────────────────

    def test_search_keys(self):
        """每个候选含 cause_type/event_type/count/baseline_count/correlation/temporal_proximity/confidence/description 八键。"""
        evs = [
            _ev("2026-09-08T20:00:00", "tv_on"),
            _ev("2026-09-09T20:00:00", "tv_on"),
            _ev("2026-09-04T20:00:00", "tv_on"),
        ]
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        assert len(results) > 0
        expected = {
            "cause_type", "event_type", "count", "baseline_count",
            "correlation", "temporal_proximity", "confidence", "description",
        }
        for r in results:
            assert set(r.keys()) == expected
            assert isinstance(r["description"], str)
            assert len(r["description"]) > 0

    # ── 验收 10：confidence 降序 ─────────────────────────────

    def test_search_sorted_by_confidence(self):
        """候选列表按 confidence 降序排列。"""
        evs = []
        for i in range(5):
            evs.append(_ev(f"2026-09-0{i + 4}T20:00:00", "tv_on"))
        evs.append(_ev("2026-09-01T20:00:00", "tv_on"))
        for i in range(3):
            evs.append(_ev(f"2026-09-0{i + 5}T10:00:00", "door_open"))
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        confs = [r["confidence"] for r in results]
        assert confs == sorted(confs, reverse=True)

    # ── 各类型候选 ───────────────────────────────────────────

    def test_search_device_change(self):
        """设备变化：tv_on 变化窗口 2 次，基线 1 次。"""
        evs = [
            _ev("2026-09-08T20:00:00", "tv_on"),
            _ev("2026-09-09T20:00:00", "tv_on"),
            _ev("2026-09-01T20:00:00", "tv_on"),
        ]
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        tv = [r for r in results if r["event_type"] == "tv_on"]
        assert len(tv) == 1
        assert tv[0]["cause_type"] == "device_change"
        assert tv[0]["count"] == 2
        assert tv[0]["baseline_count"] == 1
        assert tv[0]["confidence"] > 0

    def test_search_person_change(self):
        """人员变化：face_unknown 变化窗口 2 次，基线 1 次。"""
        evs = [
            _ev("2026-09-08T12:00:00", "face_unknown"),
            _ev("2026-09-09T12:00:00", "face_unknown"),
            _ev("2026-09-02T12:00:00", "face_unknown"),
        ]
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        fu = [r for r in results if r["event_type"] == "face_unknown"]
        assert len(fu) == 1
        assert fu[0]["cause_type"] == "person_change"

    def test_search_environment_change(self):
        """环境变化：door_open 变化窗口 3 次，基线 1 次。"""
        evs = [
            _ev("2026-09-05T08:00:00", "door_open"),
            _ev("2026-09-07T08:00:00", "door_open"),
            _ev("2026-09-09T08:00:00", "door_open"),
            _ev("2026-09-01T08:00:00", "door_open"),
        ]
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        doors = [r for r in results if r["event_type"] == "door_open"]
        assert len(doors) == 1
        assert doors[0]["cause_type"] == "environment_change"
        assert doors[0]["count"] == 3
        assert doors[0]["baseline_count"] == 1

    def test_search_schedule_change(self):
        """日程变化：person 活跃日从工作日为主变周末为主。"""
        evs = []
        # 基线期 (08-27 ~ 09-02): person 只在工作日活跃
        # 2026-08-27(Thu) 08-28(Fri) 08-31(Mon) 09-01(Tue) 09-02(Wed)
        for d in ["2026-08-27", "2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02"]:
            evs.append(_pev(f"{d}T10:00:00", "K"))
        # 变化期 (09-03 ~ 09-10): person 只在周末活跃
        # 2026-09-06(Sat) 09-07(Sun)
        for d in ["2026-09-06", "2026-09-07"]:
            evs.append(_pev(f"{d}T10:00:00", "K"))
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        sched = [r for r in results if r["cause_type"] == "schedule_change"]
        assert len(sched) > 0
        # 工作日活跃天数减少
        wd = [r for r in sched if r["event_type"] == "weekday"]
        if wd:
            assert wd[0]["baseline_count"] > wd[0]["count"]

    # ── 边界 ─────────────────────────────────────────────────

    def test_search_empty_events(self):
        """边界：空事件 → 空候选列表。"""
        assert search_candidate_causes([], "K", "2026-09-10T00:00:00") == []

    def test_search_invalid_ts(self):
        """边界：无效时间戳 → 空候选列表。"""
        assert search_candidate_causes([], "K", "not_a_timestamp") == []

    def test_search_no_change_in_events(self):
        """边界：事件频次无变化 → 不产生事件类候选。"""
        evs = [
            _ev("2026-09-08T20:00:00", "tv_on"),
            _ev("2026-09-01T20:00:00", "tv_on"),
        ]
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        tv = [r for r in results if r["event_type"] == "tv_on"]
        assert len(tv) == 0  # count=1 == baseline_count=1，无变化

    # ── 时间接近度衰减 ───────────────────────────────────────

    def test_temporal_proximity_range(self):
        """temporal_proximity 在 0~1 范围内。"""
        evs = [
            _ev("2026-09-08T20:00:00", "tv_on"),
            _ev("2026-09-09T20:00:00", "tv_on"),
            _ev("2026-09-04T20:00:00", "tv_on"),
        ]
        results = search_candidate_causes(evs, "K", "2026-09-10T00:00:00", lookback_days=7)
        for r in results:
            assert 0.0 <= r["temporal_proximity"] <= 1.0

    def test_temporal_proximity_closer_higher(self):
        """离变化点更近的事件 temporal_proximity 更高。"""
        # A 组：事件集中在变化点前 1 天
        evs_a = [
            _ev("2026-09-09T10:00:00", "tv_on"),
            _ev("2026-09-09T20:00:00", "tv_on"),
            _ev("2026-09-01T10:00:00", "tv_on"),  # 基线
        ]
        # B 组：事件集中在变化点前 6 天
        evs_b = [
            _ev("2026-09-04T10:00:00", "door_open"),
            _ev("2026-09-04T20:00:00", "door_open"),
            _ev("2026-09-01T10:00:00", "door_open"),  # 基线
        ]
        ra = search_candidate_causes(evs_a, "K", "2026-09-10T00:00:00", lookback_days=7)
        rb = search_candidate_causes(evs_b, "K", "2026-09-10T00:00:00", lookback_days=7)
        tv_a = [r for r in ra if r["event_type"] == "tv_on"][0]
        door_b = [r for r in rb if r["event_type"] == "door_open"][0]
        assert tv_a["temporal_proximity"] > door_b["temporal_proximity"]

    def test_temporal_proximity_affects_confidence(self):
        """时间接近度影响置信度：相同频次下，更近的事件置信度更高。"""
        # 两组事件频次相同（变化期 2 次，基线 1 次），但时间距离不同
        evs_near = [
            _ev("2026-09-09T10:00:00", "tv_on"),
            _ev("2026-09-09T20:00:00", "tv_on"),
            _ev("2026-09-01T10:00:00", "tv_on"),
        ]
        evs_far = [
            _ev("2026-09-04T10:00:00", "door_open"),
            _ev("2026-09-04T20:00:00", "door_open"),
            _ev("2026-09-01T10:00:00", "door_open"),
        ]
        rn = search_candidate_causes(evs_near, "K", "2026-09-10T00:00:00", lookback_days=7)
        rf = search_candidate_causes(evs_far, "K", "2026-09-10T00:00:00", lookback_days=7)
        near = [r for r in rn if r["event_type"] == "tv_on"][0]
        far = [r for r in rf if r["event_type"] == "door_open"][0]
        # 频次相同 → correlation 和 magnitude 相同；时间接近度不同 → confidence 不同
        assert near["confidence"] > far["confidence"]

    # ── 置信度公式：lift_score + count_score + proximity ────

    def test_confidence_lift_score_saturation(self):
        """lift_score 饱和：lift≥3 时 lift_score=1.0，继续增大不改变置信度。"""
        # baseline=0 → lift=count+1
        # count=5 → lift=6 → lift_score=1.0（饱和）, count_score=1.0
        c5 = _confidence(5, 0, proximity=0.0)
        # count=10 → lift=11 → lift_score 仍 1.0, count_score 仍 1.0
        c10 = _confidence(10, 0, proximity=0.0)
        assert c5 == c10
        # count=1 → lift=2 → lift_score=0.5, count_score=0.2，应低于饱和值
        c1 = _confidence(1, 0, proximity=0.0)
        assert c1 < c5

    def test_confidence_count_score_saturation(self):
        """count_score 饱和：count≥5 时 count_score=1.0，继续增大不改变置信度。"""
        # 固定 lift=2（lift_score=0.5），proximity=0，只变 count
        # count=5, baseline=2 → lift=2.0, count_score=1.0（饱和）
        c5 = _confidence(5, 2, proximity=0.0)
        # count=10, baseline=5 → lift=1.83（lift_score≈0.42）, count_score=1.0
        # 两者 count_score 都饱和，但 lift_score 不同 → confidence 不同
        # 改用 baseline=0 固定 lift 饱和：
        c5b = _confidence(5, 0, proximity=0.0)  # lift=6, count=5
        c10b = _confidence(10, 0, proximity=0.0)  # lift=11, count=10
        assert c5b == c10b  # lift_score 和 count_score 都饱和
        # count=1（不饱和）vs count=5（饱和），相同 baseline=0
        c1b = _confidence(1, 0, proximity=0.0)  # lift=2, count=1
        assert c1b < c5b


# ═══════════════════════════════════════════════════════════════
# attribute 测试
# ═══════════════════════════════════════════════════════════════

class TestAttribute:
    """完整归因流程。"""

    # ── 验收 11：changed=False → 空 candidate_causes ─────────

    def test_attribute_no_change_empty_causes(self):
        """无变化时 candidate_causes 必须为空列表。"""
        rng = random.Random(2026)
        evs = []
        base = datetime(2026, 6, 1)
        for i in range(60):
            d = base + timedelta(days=i)
            h = max(0.0, min(23.98, 18.0 + rng.gauss(0, 0.5)))
            hh, mm = int(h), max(0, min(59, int((h - int(h)) * 60)))
            ts = d.replace(hour=hh, minute=mm, second=0)
            evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K"))
        r = attribute(evs, "K", "arrival_time")
        assert r["changed"] is False
        assert r["candidate_causes"] == []

    # ── 验收 12：changed=True → 非空 candidate_causes ────────

    def test_attribute_with_change_has_causes(self):
        """有变化时 candidate_causes 至少 1 个候选。"""
        evs = []
        base = datetime(2026, 9, 1)
        for i in range(20):
            d = base + timedelta(days=i)
            h = 18.0 if i < 10 else 21.0
            ts = d.replace(hour=int(h), minute=0)
            evs.append(_pev(ts.strftime("%Y-%m-%dT%H:%M:%S"), "K"))
            # 变化期追加 tv_on（制造候选原因）
            if i >= 10:
                evs.append(_ev(
                    d.replace(hour=22, minute=0).strftime("%Y-%m-%dT%H:%M:%S"), "tv_on",
                ))
        r = attribute(evs, "K", "arrival_time")
        assert r["changed"] is True
        assert len(r["candidate_causes"]) >= 1
        for c in r["candidate_causes"]:
            assert "cause_type" in c
            assert "confidence" in c

    def test_attribute_with_change_fallback(self):
        """有变化但无外部原因时，降级候选保证非空。"""
        # 只有到家事件，无其他事件类型变化
        hours = (
            [17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.5]
            + [22.0, 22.5, 23.0, 22.0, 22.5, 23.0, 22.0, 22.5, 23.0, 22.5]
        )
        evs = _mk_arrival_events("K", datetime(2026, 9, 1), hours)
        r = attribute(evs, "K", "arrival_time")
        assert r["changed"] is True
        assert len(r["candidate_causes"]) >= 1  # 含降级候选

    # ── 验收 13：全字段 ──────────────────────────────────────

    def test_attribute_returns_all_fields(self):
        """返回 detect_change 全部字段 + candidate_causes。"""
        hours = [18.0, 18.5, 19.0, 18.0, 18.5, 17.5, 19.0, 18.0, 18.5, 19.0]
        evs = _mk_arrival_events("K", datetime(2026, 9, 1), hours)
        r = attribute(evs, "K", "arrival_time")
        base_keys = {
            "changed", "metric", "person", "before", "after",
            "effect_size", "p_value", "direction", "candidate_causes",
        }
        assert base_keys.issubset(set(r.keys()))

    # ── 验收 14：边界条件 ────────────────────────────────────

    def test_attribute_boundary_empty(self):
        """边界：空事件。"""
        r = attribute([], "K", "arrival_time")
        assert r["changed"] is False
        assert r["candidate_causes"] == []

    def test_attribute_boundary_single_day(self):
        """边界：仅 1 天数据。"""
        evs = [_pev("2026-09-01T18:00:00", "K")]
        r = attribute(evs, "K", "arrival_time")
        assert r["changed"] is False
        assert r["candidate_causes"] == []

    def test_attribute_boundary_single_person(self):
        """边界：单人事件正常归因。"""
        hours = (
            [17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.0, 17.5, 18.0, 17.5]
            + [22.0, 22.5, 23.0, 22.0, 22.5, 23.0, 22.0, 22.5, 23.0, 22.5]
        )
        evs = _mk_arrival_events("Kevin", datetime(2026, 9, 1), hours)
        r = attribute(evs, "Kevin", "arrival_time")
        assert r["person"] == "Kevin"
        assert isinstance(r["candidate_causes"], list)