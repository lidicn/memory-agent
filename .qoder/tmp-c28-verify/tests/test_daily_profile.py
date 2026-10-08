"""Phase 5.2 家庭日常画像单元测试。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.daily_profile import (
    compute_return_time_baseline,
    check_return_time_anomaly,
)


def test_baseline_computation():
    """测试回家时间基线计算（中位数+MAD）。"""
    events = [
        {"server_ts": "2026-09-15T20:00:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-16T20:30:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-17T19:45:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-18T20:15:00", "persons": [{"name": "Kevin"}]},
    ]
    baseline = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert baseline is not None
    assert baseline["days"] == 4
    assert 19.5 < baseline["median_hour"] < 21.0  # 中位数在 20:00 左右
    print(f"✅ test_baseline_computation 通过: median={baseline['median_hour']}, mad={baseline['mad']}")


def test_baseline_insufficient_data():
    """测试数据不足时返回 None。"""
    events = [
        {"server_ts": "2026-09-15T20:00:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-16T20:30:00", "persons": [{"name": "Kevin"}]},
    ]
    baseline = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert baseline is None, "数据不足应返回 None"
    print("✅ test_baseline_insufficient_data 通过")


def test_person_filter():
    """测试只计算指定人的事件。"""
    events = [
        {"server_ts": "2026-09-15T20:00:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-16T20:30:00", "persons": [{"name": "Emily"}]},
        {"server_ts": "2026-09-17T19:45:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-18T20:15:00", "persons": [{"name": "Kevin"}]},
    ]
    # Kevin 只有 3 天数据
    baseline_kevin = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert baseline_kevin is not None
    assert baseline_kevin["days"] == 3
    # Emily 只有 1 天数据
    baseline_emily = compute_return_time_baseline(events, "Emily", min_days=3)
    assert baseline_emily is None
    print("✅ test_person_filter 通过")


def test_anomaly_detection():
    """测试偏离基线检测。"""
    baseline = {"median_hour": 20.0, "mad": 0.25}  # 中位数 20:00，MAD 15分钟
    # σ ≈ 1.4826 * 0.25 ≈ 0.37 小时 ≈ 22 分钟
    # 2σ ≈ 44 分钟

    # 正常时间（20:15，偏离 15 分钟 < 44 分钟）
    normal = check_return_time_anomaly(baseline, 20.25)
    assert normal is None, "正常时间不应告警"

    # 异常时间（22:00，偏离 2 小时 > 44 分钟）
    anomaly = check_return_time_anomaly(baseline, 22.0)
    assert anomaly is not None
    assert anomaly["anomaly"] == True
    assert anomaly["deviation_hours"] == 2.0
    print(f"✅ test_anomaly_detection 通过: deviation={anomaly['deviation_hours']}h")


def test_daily_first_occurrence():
    """测试每天只取第一次出现的时间。"""
    events = [
        {"server_ts": "2026-09-15T20:00:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-15T22:00:00", "persons": [{"name": "Kevin"}]},  # 同一天第二次，应忽略
        {"server_ts": "2026-09-16T20:30:00", "persons": [{"name": "Kevin"}]},
        {"server_ts": "2026-09-17T19:45:00", "persons": [{"name": "Kevin"}]},
    ]
    baseline = compute_return_time_baseline(events, "Kevin", min_days=3)
    assert baseline is not None
    assert baseline["days"] == 3  # 不是 4 天
    # 中位数应该是 20:00（第一天）、20:30（第二天）、19:45（第三天）的中位数 = 20:00
    assert abs(baseline["median_hour"] - 20.0) < 0.01
    print(f"✅ test_daily_first_occurrence 通过: days={baseline['days']}, median={baseline['median_hour']}")


if __name__ == "__main__":
    test_baseline_computation()
    test_baseline_insufficient_data()
    test_person_filter()
    test_anomaly_detection()
    test_daily_first_occurrence()
    print("\n🎉 全部 5 个测试通过")
