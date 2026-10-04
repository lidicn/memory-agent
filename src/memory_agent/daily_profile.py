"""家庭日常画像：Phase 5.2。

设计：
- 统计每个人的回家时间（基于 face_known 事件）
- 计算作息基线（中位数+MAD 稳健统计）
- 偏离 >2σ 时触发提醒
"""

from __future__ import annotations

import statistics


def compute_return_time_baseline(
    events: list[dict],
    person: str,
    min_days: int = 3,
) -> dict | None:
    """计算某人的回家时间基线（中位数+MAD）。

    events: face_known 事件列表，每项含 server_ts、persons。
    返回 {"median_hour": float, "mad": float, "days": int} 或 None（数据不足）。
    """
    # A2 P1-6：提取该人的回家时间。
    # 原实现取"每天第一次出现"，ASC 取离家时间、DESC 取睡前时间，都不是到家。
    # 修复：取当天中午（12:00）之后的首次出现作为到家时间；中午后无出现则取当天最晚兜底。
    _ARRIVAL_AFTER_HOUR = 12.0
    daily_all: dict[str, list[float]] = {}
    for ev in events:
        persons = ev.get("persons") or []
        if not any(p.get("name") == person for p in persons):
            continue
        ts = ev.get("server_ts", "")
        if not ts:
            continue
        day = ts[:10]
        hour = float(ts[11:13]) + float(ts[14:16]) / 60.0 if len(ts) >= 16 else float(ts[11:13])
        daily_all.setdefault(day, []).append(hour)

    daily_hours: dict[str, float] = {}
    for day, hrs in daily_all.items():
        hrs_sorted = sorted(hrs)
        afternoon = [h for h in hrs_sorted if h >= _ARRIVAL_AFTER_HOUR]
        daily_hours[day] = afternoon[0] if afternoon else hrs_sorted[-1]
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


# ── 从 store 查询并计算画像 ──────────────────────────────────────────────────

def get_return_time_profile(store, person: str, days: int = 14, min_days: int = 3) -> dict:
    """从 behavior_events 查询某人的回家时间画像。

    返回:
        {"person": str, "baseline": dict|None, "anomaly_today": dict|None, "data_days": int}
    """
    from datetime import timedelta
    from .store import now_local

    # 查询最近 N 天的 face_known 事件（客厅）
    # day_from/day_to 匹配 day 列（家庭墙钟），容器 UTC 时钟会让窗口整体错位一天。
    _now = now_local(store.tz_offset_hours)
    today = _now.strftime("%Y-%m-%d")
    day_from = (_now - timedelta(days=days)).strftime("%Y-%m-%d")
    events = store.list_behavior_events(
        room="客厅", member=person, day_from=day_from, day_to=today, limit=500,
    )
    # 只看 action 包含"回家"或"有人"的事件（Gate 层 face_known 的 action）
    face_events = [e for e in events if "回家" in e.get("action", "") or "有人" in e.get("action", "")]

    baseline = compute_return_time_baseline(face_events, person, min_days=min_days)

    # 检查今天是否异常（如果今天有 face_known 事件）
    anomaly_today = None
    if baseline:
        today_events = [e for e in face_events if e.get("day") == today]
        if today_events:
            # 今天第一次出现的时间
            ts = today_events[-1].get("server_ts", "")  # DESC 排序，最后一个是最早的
            if len(ts) >= 16:
                current_hour = float(ts[11:13]) + float(ts[14:16]) / 60.0
                anomaly_today = check_return_time_anomaly(baseline, current_hour)

    return {
        "person": person,
        "baseline": baseline,
        "anomaly_today": anomaly_today,
        "data_days": baseline.get("days", 0) if baseline else 0,
        "total_events": len(face_events),
    }


def list_all_return_profiles(store, persons: list[str] | None = None, days: int = 14) -> list[dict]:
    """批量查询所有人的回家时间画像。"""
    if persons is None:
        # 从 members 表获取成员列表
        try:
            members = store.list_members()
            persons = [m.get("name", "") for m in members if m.get("name")]
        except Exception:
            persons = []
    return [get_return_time_profile(store, p, days=days) for p in persons if p]
