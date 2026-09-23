"""P4a 行为预测：基于历史事件预测家人的行为模式。

与 daily_profile.py 的区别：
- daily_profile：统计"今天几点回家"的中位数（事后统计）
- behavior_predictor：预测"明天/下一个周期他会几点回家"（向前看）

核心能力：
1. 按星期几预测回家时间（周一和周五的规律可能不同）
2. 预测"到家后 30 分钟内通常做什么"
3. 离家预测（预测离开家的时间）
4. 模式偏离告警（预测 vs 实际偏差过大时提醒）
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any


def predict_arrival_time(
    events: list[dict],
    person: str,
    weekday: int | None = None,
    min_days: int = 3,
) -> dict | None:
    """预测某人在指定星期几的到家时间。

    events: behavior_events 列表，每项含 server_ts、persons_json。
    person: 人名（如 "Kevin"）。
    weekday: 0=周一, 6=周日。None=用所有日期统计。
    min_days: 最少需要多少天数据。

    返回 {"predicted_hour": float, "range": [float, float], "confidence": float}
    或 None（数据不足）。
    """
    # 提取每天的到家时间（每天第一次出现该人的时间）
    daily_arrivals: dict[str, float] = {}
    for ev in events:
        persons_raw = ev.get("persons_json") or "[]"
        try:
            import json
            persons = json.loads(persons_raw) if isinstance(persons_raw, str) else persons_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not any(p.get("name") == person for p in persons):
            continue
        ts = ev.get("server_ts", "")
        if not ts or len(ts) < 16:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        # 过滤：只保留指定星期几
        if weekday is not None and dt.weekday() != weekday:
            continue
        day = ts[:10]
        hour = dt.hour + dt.minute / 60.0
        if day not in daily_arrivals:
            daily_arrivals[day] = hour

    hours = list(daily_arrivals.values())
    if len(hours) < min_days:
        return None

    median = statistics.median(hours)
    mad = statistics.median([abs(h - median) for h in hours])
    # 置信度：样本越多 + 方差越小 = 置信度越高
    n = len(hours)
    confidence = min(1.0, (n / 10.0) * (1.0 if mad < 0.5 else 0.6 if mad < 1.0 else 0.3))
    # 预测范围：中位数 ± 2*MAD
    lower = max(0.0, median - 2 * mad)
    upper = min(24.0, median + 2 * mad)

    return {
        "predicted_hour": round(median, 2),
        "range": [round(lower, 2), round(upper, 2)],
        "confidence": round(confidence, 2),
        "sample_days": n,
    }


def predict_post_arrival_activities(
    events: list[dict],
    person: str,
    window_min: int = 30,
    top_n: int = 5,
) -> list[dict]:
    """预测某人到家后 N 分钟内通常做什么。

    从历史 face_known 事件开始，往后看 window_min 内的 action/scene。
    返回出现频率最高的活动列表。
    """
    # 先找出每天的到家事件时间点
    arrival_times: list[tuple[str, datetime]] = []
    for ev in events:
        persons_raw = ev.get("persons_json") or "[]"
        try:
            import json
            persons = json.loads(persons_raw) if isinstance(persons_raw, str) else persons_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not any(p.get("name") == person for p in persons):
            continue
        ts = ev.get("server_ts", "")
        if not ts or len(ts) < 16:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        day = ts[:10]
        # 只记录每天第一次出现
        if not arrival_times or arrival_times[-1][0] != day:
            arrival_times.append((day, dt))

    if len(arrival_times) < 3:
        return []

    # 对每个到家时间，往后找 window_min 内的事件
    activity_counts: dict[str, int] = defaultdict(int)
    total_windows = 0

    for _, arrival_dt in arrival_times:
        window_end = arrival_dt + timedelta(minutes=window_min)
        window_events = []
        for ev in events:
            ts = ev.get("server_ts", "")
            if not ts or len(ts) < 16:
                continue
            try:
                ev_dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if arrival_dt <= ev_dt <= window_end:
                action = ev.get("action") or "unknown"
                scene = ev.get("scene") or ""
                label = scene if scene else action
                window_events.append(label)

        if window_events:
            total_windows += 1
            for label in set(window_events):  # 同一窗口内去重
                activity_counts[label] += 1

    # 按频率排序，取 top_n
    result = sorted(activity_counts.items(), key=lambda x: -x[1])
    return [
        {"activity": label, "frequency": count, "ratio": round(count / total_windows, 2)}
        for label, count in result[:top_n]
    ]


def predict_daily_routine(
    events: list[dict],
    person: str,
    min_days: int = 3,
) -> dict:
    """预测某人的日常作息模式。

    返回各关键时间点的预测（到家、离家、就寝等）。
    """
    # 到家预测（用所有日期，不按星期几）
    arrival = predict_arrival_time(events, person, weekday=None, min_days=min_days)

    # 统计离家时间（最后一次出现后 30 分钟内没有事件）
    # 简化：找每天最后一次出现的时间
    daily_last_seen: dict[str, float] = {}
    for ev in events:
        persons_raw = ev.get("persons_json") or "[]"
        try:
            import json
            persons = json.loads(persons_raw) if isinstance(persons_raw, str) else persons_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not any(p.get("name") == person for p in persons):
            continue
        ts = ev.get("server_ts", "")
        if not ts or len(ts) < 16:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        day = ts[:10]
        hour = dt.hour + dt.minute / 60.0
        daily_last_seen[day] = hour

    departure = None
    if len(daily_last_seen) >= min_days:
        hours = list(daily_last_seen.values())
        median = statistics.median(hours)
        mad = statistics.median([abs(h - median) for h in hours])
        departure = {
            "predicted_hour": round(median, 2),
            "range": [round(max(0.0, median - 2 * mad), 2), round(min(24.0, median + 2 * mad), 2)],
            "sample_days": len(hours),
        }

    # 到家后活动
    post_arrival = predict_post_arrival_activities(events, person, window_min=30, top_n=5)

    return {
        "person": person,
        "arrival": arrival,
        "departure": departure,
        "post_arrival_activities": post_arrival,
        "data_days": len(set(ev.get("server_ts", "")[:10] for ev in events if person in (ev.get("persons_json") or ""))),
    }


def detect_pattern_deviation(
    predicted: dict,
    actual_hour: float,
    person: str,
) -> dict:
    """检测实际行为是否偏离预测模式。

    predicted: predict_arrival_time 返回的结果
    actual_hour: 实际发生时间（小时）
    """
    if not predicted or not predicted.get("range"):
        return {"deviation": "unknown", "confidence": 0.0}

    lower, upper = predicted["range"]
    median = predicted["predicted_hour"]

    if lower <= actual_hour <= upper:
        return {
            "deviation": "normal",
            "delta_hours": round(actual_hour - median, 2),
            "within_range": True,
        }
    else:
        # 偏离方向
        direction = "late" if actual_hour > upper else "early"
        delta = abs(actual_hour - median)
        severity = "mild" if delta < 1.0 else "moderate" if delta < 2.0 else "severe"
        return {
            "deviation": direction,
            "severity": severity,
            "delta_hours": round(actual_hour - median, 2),
            "within_range": False,
            "predicted_range": [lower, upper],
        }
