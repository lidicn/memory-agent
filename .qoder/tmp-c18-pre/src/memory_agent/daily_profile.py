"""家庭日常画像：Phase 5.2。

设计：
- 统计每个人的回家时间（基于 face_known 事件）
- 计算作息基线（中位数+MAD 稳健统计）
- 偏离 >2σ 时触发提醒

取数口径与 P4a 共用 `behavior_predictor.person_names/house_dt`：元宝 A2 矩阵记下的那对
键名错配就是这两个模块各读各的形状（一个读 `persons`、一个读 `persons_json`）。
"""

from __future__ import annotations

import statistics

from .behavior_predictor import house_dt, person_names

#: 到家判据：当天中午之后的首次出现（与 `behavior_predictor` 的 P1-4/P1-6 同一口径）。
_ARRIVAL_AFTER_HOUR = 12.0


def compute_return_time_baseline(
    events: list[dict],
    person: str,
    min_days: int = 3,
    tz_offset_hours: float = 8.0,
) -> dict | None:
    """计算某人的回家时间基线（中位数+MAD）。

    events: face_known 事件列表，现行 `persons`（dict 数组）与旧 `persons_json` 两代形状都认。
    返回 {"median_hour": float, "mad": float, "days": int} 或 None（数据不足）。
    """
    # A2 P1-6：提取该人的回家时间。
    # 原实现取"每天第一次出现"，ASC 取离家时间、DESC 取睡前时间，都不是到家。
    # 修复：取当天中午（12:00）之后的首次出现作为到家时间；中午后无出现则取当天最晚兜底。
    # 时间一律折算到家庭墙钟：现网 naive 本地与 `+00:00` 两种形状混用，按字符串切片读小时
    # 会把 UTC 那条读成上午，日子也会跟着切错。
    daily_all: dict[str, list[float]] = {}
    for ev in events:
        if person not in person_names(ev):
            continue
        dt = house_dt(ev.get("server_ts"), tz_offset_hours)
        if dt is None:
            continue
        daily_all.setdefault(dt.strftime("%Y-%m-%d"), []).append(
            dt.hour + dt.minute / 60.0)

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

    baseline = compute_return_time_baseline(
        face_events, person, min_days=min_days, tz_offset_hours=store.tz_offset_hours)

    # 检查今天是否异常（如果今天有 face_known 事件）
    anomaly_today = None
    if baseline:
        # 今天第一次出现的时间。原实现靠 `today_events[-1]`——那是在赌门面返回 DESC，
        # 换个排序就静默变成"最后一次出现"，基线对比整体错位（P1-6 同一族顺序依赖）。
        today_hours = [
            dt.hour + dt.minute / 60.0
            for e in face_events if e.get("day") == today
            for dt in [house_dt(e.get("server_ts"), store.tz_offset_hours)] if dt is not None
        ]
        if today_hours:
            anomaly_today = check_return_time_anomaly(baseline, min(today_hours))

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
