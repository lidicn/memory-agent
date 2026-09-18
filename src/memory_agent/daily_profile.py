"""家庭日常画像：Phase 5.2。

设计：
- 统计每个人的回家时间（基于 face_known 事件）
- 计算作息基线（中位数+MAD 稳健统计）
- 偏离 >2σ 时触发提醒
"""

from __future__ import annotations

import statistics
from typing import Any


def compute_return_time_baseline(
    events: list[dict],
    person: str,
    min_days: int = 3,
) -> dict | None:
    """计算某人的回家时间基线（中位数+MAD）。

    events: face_known 事件列表，每项含 server_ts、persons。
    返回 {"median_hour": float, "mad": float, "days": int} 或 None（数据不足）。
    """
    # 提取该人的回家时间（每天第一次出现的小时）
    daily_hours: dict[str, float] = {}
    for ev in events:
        persons = ev.get("persons") or []
        if not any(p.get("name") == person for p in persons):
            continue
        ts = ev.get("server_ts", "")
        if not ts:
            continue
        day = ts[:10]
        hour = float(ts[11:13]) + float(ts[14:16]) / 60.0 if len(ts) >= 16 else float(ts[11:13])
        if day not in daily_hours:
            daily_hours[day] = hour
    hours = list(daily_hours.values())
    if len(hours) < min_days:
        return None
    median = statistics.median(hours)
    mad = statistics.median([abs(h - median) for h in hours])
    return {
        "median_hour": round(median, 2),
        "mad": round(mad, 2),
        "days": len(hours),
        "hours": [round(h, 2) for h in sorted(hours)],
    }


def check_return_time_anomaly(
    baseline: dict,
    current_hour: float,
    sigma_threshold: float = 2.0,
) -> dict | None:
    """检查当前回家时间是否偏离基线。

    返回 {"anomaly": True, "deviation_hours": float, "baseline": dict} 或 None。
    """
    median = baseline["median_hour"]
    mad = baseline["mad"]
    # MAD → σ 近似：σ ≈ 1.4826 * MAD
    sigma = 1.4826 * mad if mad > 0 else 1.0
    deviation = abs(current_hour - median)
    if deviation > sigma_threshold * sigma:
        return {
            "anomaly": True,
            "deviation_hours": round(deviation, 2),
            "baseline_median": median,
            "baseline_mad": mad,
        }
    return None
