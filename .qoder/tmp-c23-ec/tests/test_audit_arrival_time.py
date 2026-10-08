"""A6 P5 补测：到家时间预测回归测试。

覆盖 P1-4/P1-6：predict_arrival_time 和 compute_return_time_baseline
都应取"中午后首次出现"作为到家时间，而非首事件(离家8:00)或末事件(睡前23:00)。
7 条测试：5 条锁缺陷（原实现红）+ 2 条对照（原实现绿）。
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.behavior_predictor import predict_arrival_time
from memory_agent.daily_profile import compute_return_time_baseline


def _make_behavior_events(days=5, person="Kevin"):
    """构造每天含离家(8:00)、到家(19:00)、睡前(23:00)的 behavior_events。

    persons_json 字段为 JSON 字符串（与 behavior_predictor 接口一致）。
    """
    events = []
    for i in range(days):
        day = f"2026-09-{15 + i:02d}"
        for hour in [8, 19, 23]:
            events.append({
                "server_ts": f"{day}T{hour:02d}:00:00",
                "persons_json": json.dumps([{"name": person, "confidence": 0.95}]),
            })
    return events


def _make_face_events(days=5, person="Kevin"):
    """构造每天含离家(8:00)、到家(19:00)、睡前(23:00)的 face_known 事件。

    persons 字段为 list（与 daily_profile 接口一致）。
    """
    events = []
    for i in range(days):
        day = f"2026-09-{15 + i:02d}"
        for hour in [8, 19, 23]:
            events.append({
                "server_ts": f"{day}T{hour:02d}:00:00",
                "persons": [{"name": person}],
            })
    return events


# ── 5 条锁缺陷（P1-4/P1-6 修复前为红）─────────────────────────────

def test_arrival_desc_matches_real_arrival():
    """predict_arrival_time 应返回到家时间 19.0，而非睡前 23.0。

    原 bug（DESC 取末事件）：返回 23.0（睡前）。
    """
    events = _make_behavior_events(days=5)
    result = predict_arrival_time(events, "Kevin", min_days=3)
    assert result is not None
    assert abs(result["predicted_hour"] - 19.0) < 0.1, (
        f"到家时间应为 19.0，实得 {result['predicted_hour']}"
    )


def test_arrival_asc_also_matches_real_arrival():
    """predict_arrival_time 不应因排序方式不同而返回离家时间 8.0。

    原 bug（ASC 取首事件）：返回 8.0（离家）。
    这条断言专门防止"改成 ASC 即可"的错误修复方案。
    """
    events = _make_behavior_events(days=5)
    # 事件按时间升序传入（模拟 ASC）
    events_asc = sorted(events, key=lambda e: e["server_ts"])
    result = predict_arrival_time(events_asc, "Kevin", min_days=3)
    assert result is not None
    assert abs(result["predicted_hour"] - 19.0) < 0.1, (
        f"ASC 下到家时间也应为 19.0，实得 {result['predicted_hour']}（不能只改排序）"
    )


def test_arrival_is_order_independent():
    """predict_arrival_time 的结果应与事件传入顺序无关。"""
    events = _make_behavior_events(days=5)
    events_asc = sorted(events, key=lambda e: e["server_ts"])
    events_desc = sorted(events, key=lambda e: e["server_ts"], reverse=True)
    r_asc = predict_arrival_time(events_asc, "Kevin", min_days=3)
    r_desc = predict_arrival_time(events_desc, "Kevin", min_days=3)
    assert r_asc is not None and r_desc is not None
    assert abs(r_asc["predicted_hour"] - r_desc["predicted_hour"]) < 0.01, (
        f"顺序无关：ASC={r_asc['predicted_hour']}, DESC={r_desc['predicted_hour']}"
    )


def test_baseline_desc_matches_real_arrival():
    """compute_return_time_baseline 应返回到家中位数 19.0，而非睡前 23.0。

    原 bug（DESC 取末事件）：返回 23.0。
    """
    events = _make_face_events(days=5)
    result = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert result is not None
    assert abs(result["median_hour"] - 19.0) < 0.1, (
        f"回家基线中位数应为 19.0，实得 {result['median_hour']}"
    )


def test_baseline_is_order_independent():
    """compute_return_time_baseline 的结果应与事件传入顺序无关。"""
    events = _make_face_events(days=5)
    events_asc = sorted(events, key=lambda e: e["server_ts"])
    events_desc = sorted(events, key=lambda e: e["server_ts"], reverse=True)
    r_asc = compute_return_time_baseline(events_asc, "Kevin", min_days=3)
    r_desc = compute_return_time_baseline(events_desc, "Kevin", min_days=3)
    assert r_asc is not None and r_desc is not None
    assert abs(r_asc["median_hour"] - r_desc["median_hour"]) < 0.01, (
        f"顺序无关：ASC={r_asc['median_hour']}, DESC={r_desc['median_hour']}"
    )


# ── 2 条对照（修复前后均应为绿）─────────────────────────────────────

def test_arrival_insufficient_data_returns_none():
    """数据不足 min_days 时应返回 None。"""
    events = _make_behavior_events(days=2)
    result = predict_arrival_time(events, "Kevin", min_days=3)
    assert result is None, "2 天数据 < min_days=3 应返回 None"


def test_baseline_insufficient_data_returns_none():
    """数据不足时 baseline 应返回 None。"""
    events = _make_face_events(days=2)
    result = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert result is None, "2 天数据 < min_days=3 应返回 None"
