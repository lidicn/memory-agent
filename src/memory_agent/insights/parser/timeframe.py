"""时间范围解析：自然语言时间 -> TimeRange，并提供切分/裁剪工具。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, List, Optional, Tuple

from ..models import (SECONDS_PER_DAY, TimeRange, house_dt, house_now, house_ts,
                      house_tz)

__all__ = [
    "parse_time", "as_ts", "parse_timeframe", "resolve_range",
    "strip_time_text", "split_days", "split_hours", "TimeRange",
]

_CN_NUM = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5,
}

_UNIT_SECONDS = {
    "秒": 1, "分钟": 60, "分": 60, "min": 60, "minute": 60,
    "小时": 3600, "时": 3600, "hour": 3600, "hr": 3600,
    "天": SECONDS_PER_DAY, "日": SECONDS_PER_DAY, "day": SECONDS_PER_DAY,
    "周": 7 * SECONDS_PER_DAY, "星期": 7 * SECONDS_PER_DAY,
    "week": 7 * SECONDS_PER_DAY,
    "月": 30 * SECONDS_PER_DAY, "个月": 30 * SECONDS_PER_DAY, "month": 30 * SECONDS_PER_DAY,
}

# 固定短语（放在相对正则之前判断）
_PHRASES: Tuple[Tuple[str, int], ...] = (
    ("半个月", 15 * SECONDS_PER_DAY),
    ("一周", 7 * SECONDS_PER_DAY),
    ("一个星期", 7 * SECONDS_PER_DAY),
    ("一个月", 30 * SECONDS_PER_DAY),
    ("半天", 12 * 3600),
    ("一天", SECONDS_PER_DAY),
    ("一小时", 3600),
)

_PREFIX = r"(?:最近|近|过去|这|last|past|in\s+the)\s*"
_RE_REL = re.compile(
    _PREFIX + r"(?P<num>\d+(?:\.\d+)?|[一两二三四五六七八九十半]+)\s*"
    r"(?P<unit>个?月|周|星期|天|日|小时|分钟|秒|months?|weeks?|days?|hours?|hrs?|minutes?|mins?|seconds?)",
    re.I,
)
_RE_DT = r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
_RE_RANGE = re.compile(
    r"(?P<a>" + _RE_DT + r")\s*(?:~|～|到|至|to|—|–|\s-\s)\s*(?P<b>" + _RE_DT + ")", re.I)
_RE_SINGLE = re.compile(_RE_DT)

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                 "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")


def _now(now: Optional[datetime] = None) -> datetime:
    return (now or house_now()).replace(microsecond=0)


def _num(text: str) -> float:
    text = (text or "").strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text in _CN_NUM:
        return float(_CN_NUM[text])
    if len(text) == 2 and text[0] == "十":
        return 10 + float(_CN_NUM.get(text[1], 0))
    if len(text) == 2 and text[1] == "十":
        return float(_CN_NUM.get(text[0], 0)) * 10
    # 补全 "X十Y" 型（如"二十五"→25.0），之前恒返回 1.0
    if "十" in text and len(text) == 3:
        head, _, tail = text.partition("十")
        if head in _CN_NUM or head == "":
            tens = 10.0 if head in ("", "一") else float(_CN_NUM.get(head, 0)) * 10
            ones = float(_CN_NUM.get(tail, 0)) if tail else 0.0
            return tens + ones
    return 1.0


def parse_time(value: Any) -> Optional[datetime]:
    """宽容解析时间：datetime / 时间戳 / 常见字符串格式。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(house_tz()).replace(tzinfo=None, microsecond=0)
        return value.replace(microsecond=0)
    if isinstance(value, (int, float)):
        return house_dt(float(value)).replace(microsecond=0)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10}(\.\d+)?", text):
        return house_dt(float(text)).replace(microsecond=0)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        # 带时区的输入先转家庭时区再去 tzinfo，之前直接丢弃偏移导致窗口偏移
        if dt.tzinfo is not None:
            dt = dt.astimezone(house_tz())
        return dt.replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None


def as_ts(value: Any) -> Optional[float]:
    """任意时间表示 -> 时间戳。"""
    dt = parse_time(value)
    return house_ts(dt) if dt else None


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _keyword_range(text: str, now: datetime) -> Optional[TimeRange]:
    """关键词时间：今天/昨天/前天/本周/上周/本月/上月。"""
    lower = text.lower()
    today = _day_start(now)
    table = (
        (("前天",), today - timedelta(days=2), today - timedelta(days=1)),
        (("昨天", "昨日", "yesterday"), today - timedelta(days=1), today),
        (("今天", "今日", "today"), today, now),
        (("上周", "上星期", "last week"), None, None),
        (("本周", "这周", "这星期", "this week"), None, None),
        (("上月", "上个月", "last month"), None, None),
        (("本月", "这个月", "this month"), None, now),
    )
    for keys, start, end in table:
        if any(k.lower() in lower for k in keys):
            if keys[0] == "上周":
                monday = today - timedelta(days=today.weekday())
                return TimeRange(monday - timedelta(days=7), monday, "上周")
            if keys[0] == "本周":
                monday = today - timedelta(days=today.weekday())
                return TimeRange(monday, now, "本周")
            if keys[0] == "上月":
                first = today.replace(day=1)
                prev = (first - timedelta(days=1)).replace(day=1)
                return TimeRange(prev, first, "上月")
            return TimeRange(start, end, keys[0])
    return None


def parse_timeframe(text: str, now: Optional[datetime] = None,
                    default_days: int = 7) -> Optional[TimeRange]:
    """解析自然语言时间范围；解析不出返回 None。

    支持：最近7天 / 近一周 / 过去3小时 / 上周 / 昨天 / 本月 /
    last 3 days / 2026-03-01 ~ 2026-03-05 / 2026-03-01 08:00 到 12:00 等。
    """
    blob = str(text or "")
    if not blob.strip():
        return None
    now = _now(now)

    matched = _RE_RANGE.search(blob)
    if matched:
        a = parse_time(matched.group("a"))
        b = parse_time(matched.group("b"))
        if a and b:
            start, end = (a, b) if a <= b else (b, a)
            if end.hour == 0 and end.minute == 0 and (end - start).days >= 1:
                end = end + timedelta(days=1)  # 右开区间按整天处理
            return TimeRange(start, end, matched.group(0))

    for phrase, seconds in _PHRASES:
        if phrase in blob:
            return TimeRange(now - timedelta(seconds=seconds), now, phrase)

    rel = _RE_REL.search(blob)
    if rel:
        seconds = _num(rel.group("num")) * _UNIT_SECONDS.get(
            rel.group("unit").lower().replace("个", ""), SECONDS_PER_DAY)
        return TimeRange(now - timedelta(seconds=seconds), now, rel.group(0))

    kw = _keyword_range(blob, now)
    if kw:
        return kw

    single = _RE_SINGLE.search(blob)
    if single:
        dt = parse_time(single.group(0))
        if dt:
            return TimeRange(dt, min(dt + timedelta(days=1), now), single.group(0))
    return None


def resolve_range(start: Any = "", end: Any = "", days: int = 0,
                  now: Optional[datetime] = None,
                  default_days: int = 7,
                  label: str = "") -> TimeRange:
    """把 (start, end, days) 归一为 TimeRange。

    优先级：显式 start/end > 自然语言文本 > days > default_days。
    """
    now = _now(now)
    s = parse_time(start)
    e = parse_time(end)
    if s and e:
        a, b = (s, e) if s <= e else (e, s)
        return TimeRange(a, b, label)
    if s and not e:
        return TimeRange(s, now, label)
    if e and not s:
        span = timedelta(days=float(days or default_days))
        return TimeRange(e - span, e, label)
    for value in (start, end):
        if isinstance(value, str) and value.strip():
            tf = parse_timeframe(value, now=now, default_days=default_days)
            if tf:
                return TimeRange(tf.start, tf.end, label or tf.label)
    span = timedelta(days=float(days or default_days))
    return TimeRange(now - span, now, label)


def strip_time_text(text: str) -> str:
    """去掉文本里的时间描述（用于问题解析后提取设备/关键词）。"""
    out = str(text or "")
    out = _RE_RANGE.sub(" ", out)
    out = _RE_REL.sub(" ", out)
    for keys in (("前天", "昨天", "今日", "今天", "上周", "上星期", "本周", "这周",
                  "上月", "上个月", "本月", "这个月", "yesterday", "today")):
        out = out.replace(keys, " ")
    for phrase, _ in _PHRASES:
        out = out.replace(phrase, " ")
    out = _RE_SINGLE.sub(" ", out)
    return " ".join(out.split())


def split_days(tr: TimeRange) -> List[Tuple[str, float, float]]:
    """按天切分。"""
    return tr.split_days()


def split_hours(tr: TimeRange) -> List[Tuple[str, float, float]]:
    """按小时切分：[( 'YYYY-MM-DD HH', start_ts, end_ts ), ...]"""
    out: List[Tuple[str, float, float]] = []
    cur = house_dt(tr.start_ts).replace(minute=0, second=0, microsecond=0)
    while house_ts(cur) < tr.end_ts:
        nxt = cur + timedelta(hours=1)
        s, e = tr.clip(house_ts(cur), house_ts(nxt))
        if e > s:
            out.append((cur.strftime("%Y-%m-%d %H"), s, e))
        cur = nxt
    return out
