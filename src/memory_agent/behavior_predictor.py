"""P4a 行为预测：基于历史事件预测家人的行为模式。

与 daily_profile.py 的区别：
- daily_profile：统计"今天几点回家"的中位数（事后统计）
- behavior_predictor：预测"明天/下一个周期他会几点回家"（向前看）

核心能力：
1. 按星期几预测回家时间（周一和周五的规律可能不同）
2. 预测"到家后 30 分钟内通常做什么"
3. 离家预测（预测离开家的时间）
4. 模式偏离告警（预测 vs 实际偏差过大时提醒）

`person_names()` / `house_dt()` 是这一族统计的取数底座，`daily_profile.py` 共用同一套口径
（两处原先各读各的形状，是元宝 A2 矩阵 §缺陷表 记下的那一对键名错配）。
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import house_time

#: 到家判据：当天中午之后的首次出现——清晨那次是出门前，不算到家（P1-4）。
_ARRIVAL_AFTER_HOUR = 12.0


def _house_tz(tz_offset_hours: float):
    """与 `house_time.now_local` 同一口径：homesdk 当家时按 IANA 家庭时区（DST 正确），
    未装或家庭时区未按名声明时，才用传入的小时偏移兜底。"""
    if house_time.is_active():
        return house_time.house_tz()
    return timezone(timedelta(hours=float(tz_offset_hours)))


def house_dt(raw: Any, tz_offset_hours: float = 8.0) -> datetime | None:
    """`server_ts` → 家庭墙钟的 naive datetime；解不出来返回 None（该条自己作废）。

    现网两种形状混用（naive 本地时间 + 带 `+00:00` 的 UTC），2026-09-18 同日实测
    44 : 309。不折算有两重后果：aware/naive 混排让 `sorted()` 直接抛
    `can't compare offset-naive and offset-aware datetimes`；带偏移的那 44 条按 UTC
    小时算，傍晚到家被折算成上午，整个掉出「中午后首次出现」的判据窗口。
    """
    text = str(raw or "").strip()
    if len(text) < 16:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_house_tz(tz_offset_hours)).replace(tzinfo=None)
    return dt


def person_names(ev: dict) -> list[str]:
    """一条事件上的人名，两代形状都要认。

    `Store.list_behavior_events`（store.py:2378）把 `persons_json` **弹出**并换成解析好的
    `persons`，而本模块原先只读 `ev.get("persons_json")` ⇒ 现网每条事件都读成"无人员"，
    三个预测函数恒返回空、`data_days` 恒 0，而 `/api/behaviors/predict` 照样 `ok:true`。
    旧形状（JSON 字符串列，以及字符串元素本身就是姓名 `["Kevin"]`）历史上真落进过库，
    一并兼容——`Store._deserialize_persons`（store.py:586）认的就是这一族。

    坏数据只废它自己：解析失败返回空列表，绝不抛异常。两个调用点
    （`api/behavior_routes.py:855`、`mcp_server.py:2736`）都没有 try/except，
    这里抛出就是整条 HTTP/MCP 请求 500。
    """
    raw: Any = ev.get("persons")
    if raw is None:
        raw = ev.get("persons_json")
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for p in raw:
        if isinstance(p, dict):
            name = str(p.get("name") or "").strip()
        elif isinstance(p, str):
            name = p.strip()  # 旧格式：字符串本身就是姓名
        else:
            continue
        if name:
            names.append(name)
    return names


def _appearances_by_day(
    events: Iterable[dict],
    person: str,
    tz_offset_hours: float = 8.0,
    weekday: int | None = None,
) -> dict[str, list[datetime]]:
    """按家庭墙钟的自然日归组某人当天出现的时刻。

    日期取折算后的 `dt`，不是 `server_ts` 的字符串前缀——UTC 尾巴折算到家里可能已是次日。
    """
    by_day: dict[str, list[datetime]] = {}
    for ev in events or []:
        if person not in person_names(ev):
            continue
        dt = house_dt(ev.get("server_ts"), tz_offset_hours)
        if dt is None or (weekday is not None and dt.weekday() != weekday):
            continue
        by_day.setdefault(dt.strftime("%Y-%m-%d"), []).append(dt)
    return by_day


def _as_hour(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0


def _arrival_dt(dts: list[datetime]) -> datetime:
    """一天的到家事件时刻：中午后首次出现；午后无出现（全天在家未出门）取当天最晚兜底。"""
    dts_sorted = sorted(dts)
    return next((d for d in dts_sorted if _as_hour(d) >= _ARRIVAL_AFTER_HOUR), dts_sorted[-1])


def _band(hours: list[float], confidence: float | None = None) -> dict:
    """中位数 ± 2×MAD 的稳健区间；`confidence` 为 None 时不回显该键（离家预测沿用旧形状）。"""
    median = statistics.median(hours)
    mad = statistics.median([abs(h - median) for h in hours])
    out = {
        "predicted_hour": round(median, 2),
        "range": [round(max(0.0, median - 2 * mad), 2), round(min(24.0, median + 2 * mad), 2)],
    }
    if confidence is not None:
        out["confidence"] = round(confidence, 2)
    out["sample_days"] = len(hours)
    return out


def predict_arrival_time(
    events: list[dict],
    person: str,
    weekday: int | None = None,
    min_days: int = 3,
    tz_offset_hours: float = 8.0,
) -> dict | None:
    """预测某人在指定星期几的到家时间。

    events: behavior_events 列表（现行 `persons` 与旧 `persons_json` 两种形状都认）。
    person: 人名（如 "Kevin"）。
    weekday: 0=周一, 6=周日。None=用所有日期统计。
    min_days: 最少需要多少天数据。

    返回 {"predicted_hour": float, "range": [float, float], "confidence": float, "sample_days": int}
    或 None（数据不足）。
    """
    hours = [_as_hour(_arrival_dt(dts)) for dts in _appearances_by_day(
        events, person, tz_offset_hours, weekday).values()]
    if len(hours) < min_days:
        return None

    median = statistics.median(hours)
    mad = statistics.median([abs(h - median) for h in hours])
    # 置信度：样本越多 + 方差越小 = 置信度越高
    confidence = min(1.0, (len(hours) / 10.0) * (
        1.0 if mad < 0.5 else 0.6 if mad < 1.0 else 0.3))
    return _band(hours, confidence)


def predict_post_arrival_activities(
    events: list[dict],
    person: str,
    window_min: int = 30,
    top_n: int = 5,
    tz_offset_hours: float = 8.0,
) -> list[dict]:
    """预测某人到家后 N 分钟内通常做什么。

    从每天的到家时刻开始，往后看 window_min 内的事件（不限本人，家里发生什么都算），
    同一窗口内去重，返回出现频率最高的活动列表。
    """
    by_day = _appearances_by_day(events, person, tz_offset_hours)
    if len(by_day) < 3:
        return []
    arrivals = [_arrival_dt(dts) for dts in by_day.values()]

    timeline = [
        (dt, ev)
        for ev in events or []
        for dt in [house_dt(ev.get("server_ts"), tz_offset_hours)]
        if dt is not None
    ]

    activity_counts: dict[str, int] = defaultdict(int)
    total_windows = 0
    for arrival_dt in arrivals:
        window_end = arrival_dt + timedelta(minutes=window_min)
        labels = {
            (ev.get("scene") or ev.get("action") or "unknown")
            for dt, ev in timeline if arrival_dt <= dt <= window_end
        }
        if labels:
            total_windows += 1
            for label in labels:  # 同一窗口内去重
                activity_counts[label] += 1

    result = sorted(activity_counts.items(), key=lambda x: -x[1])
    return [
        {"activity": label, "frequency": count, "ratio": round(count / total_windows, 2)}
        for label, count in result[:top_n]
    ]


def predict_daily_routine(
    events: list[dict],
    person: str,
    min_days: int = 3,
    tz_offset_hours: float = 8.0,
) -> dict:
    """预测某人的日常作息模式。

    返回各关键时间点的预测（到家、离家、到家后活动）。
    """
    by_day = _appearances_by_day(events, person, tz_offset_hours)
    arrival = predict_arrival_time(
        events, person, weekday=None, min_days=min_days, tz_offset_hours=tz_offset_hours)

    # 离家：每天最后一次出现。取 max 而不是"循环里最后一次赋值"——
    # list_behavior_events 是 DESC，赋值到最后拿到的是当天最早那次（P1-6 同一族顺序依赖）。
    hours = [max(_as_hour(d) for d in dts) for dts in by_day.values()]
    departure = _band(hours) if len(hours) >= min_days else None

    return {
        "person": person,
        "arrival": arrival,
        "departure": departure,
        "post_arrival_activities": predict_post_arrival_activities(
            events, person, window_min=30, top_n=5, tz_offset_hours=tz_offset_hours),
        # 口径：该人确实出现过的自然日数（结构化姓名匹配，不再拿人名做子串搜索）。
        "data_days": len(by_day),
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
