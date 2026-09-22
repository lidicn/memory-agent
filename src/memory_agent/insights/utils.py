"""通用工具函数：从旧版 insights.py 迁移的工具函数集合。

这些函数都是无状态的纯函数，易于测试和复用。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

#: 抖动阈值：短于该秒数的开启片段视为误触，不计入时长统计。
DEFAULT_DEBOUNCE_SECONDS: int = 5

#: 关闭状态集合（英文）
OFF_STATES = frozenset({
    "off", "closed", "not_home", "unavailable", "unknown",
    "standby", "idle", "paused", "stopped",
})

#: 关闭状态集合（中文）
CN_OFF_STATES = frozenset({"关", "关闭", "门关", "闭合", "断开", "无", "否", "0"})

#: 开启状态集合（中文）
CN_ON_STATES = frozenset({"开", "打开", "开启", "有", "是", "1", "on"})


def normalize_text(text: Any) -> str:
    """标准化文本：转字符串、去空白、转小写。"""
    return str(text or "").strip().lower()


def tokenize(text: str) -> List[str]:
    """粗分词：中文按连续汉字块 + 英文按单词切。够用且零依赖。"""
    return [t for t in re.split(r"[\s,，、_./|-]+", normalize_text(text)) if t]


def state_is_off(state: Any) -> bool:
    """判断状态是否为关闭。"""
    s = normalize_text(state)
    return s in OFF_STATES or s in CN_OFF_STATES


def state_is_on(state: Any) -> bool:
    """判断状态是否为开启。"""
    s = normalize_text(state)
    return s in CN_ON_STATES


def fmt_duration(total_seconds: float) -> str:
    """格式化时长为人类可读字符串。

    示例：
        fmt_duration(3661) -> "1小时1分"
        fmt_duration(90061) -> "1天1小时1分"
        fmt_duration(45) -> "45秒"
    """
    total = max(0, int(total_seconds))
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}天{h}小时{m}分"
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def parse_attrs(raw: Any) -> Dict[str, Any]:
    """解析属性，支持 dict 和 JSON 字符串。

    解析失败时返回空 dict，不抛异常。
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def as_float(v: Any) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def num_stale(entity: Dict[str, Any]) -> Optional[float]:
    """取实体的 stale_days 数值。

    ``entity_catalog`` 里 stale_days 是 ``round(x, 1)`` 得到的 **float**，
    而旧代码用 ``isinstance(v, int)`` 判断 —— 永远为 False。这正是
    「device_health 的 stale 恒为 0」「anomalies 恒为空」的直接原因。

    从旧版 InsightService._num_stale 迁移而来，行为完全一致。
    """
    v = entity.get("stale_days")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def parse_time_range(tr: str) -> Optional[Tuple[int, int, bool]]:
    """解析 'HH:MM-HH:MM' -> (start_min, end_min, crosses_midnight)。

    空/无效返回 None。crosses_midnight 表示 end <= start（如 '22:00-07:00' 跨零点）。

    从旧版 InsightService._parse_time_range 迁移而来，行为完全一致。
    """
    if not tr or "-" not in tr:
        return None
    try:
        a, b = tr.split("-", 1)
        sh, sm = (int(x) for x in a.split(":"))
        eh, em = (int(x) for x in b.split(":"))
    except Exception:
        return None
    if not (0 <= sh < 24 and 0 <= sm < 60 and 0 <= eh < 24 and 0 <= em < 60):
        return None
    smin, emin = sh * 60 + sm, eh * 60 + em
    if smin == 0 and emin >= 1439:
        return None  # 全天窗口（如 00:00-23:59），无需裁剪，等价于不过滤
    return (smin, emin, emin <= smin)


def split_by_day(seg_start: datetime, seg_end: datetime) -> List[Tuple[datetime, datetime]]:
    """把 [seg_start, seg_end] 按自然日切成连续切片，返回 [(s, e), ...]。

    从旧版 InsightService._split_by_day 迁移而来，行为完全一致。
    """
    out = []
    cur = seg_start
    while cur < seg_end:
        nxt = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        pe = min(seg_end, nxt)
        if pe > cur:
            out.append((cur, pe))
        cur = nxt
    return out


def clip_to_time_range(seg_start: datetime, seg_end: datetime,
                        tr: Optional[Tuple[int, int, bool]]
                        ) -> List[Tuple[datetime, datetime]]:
    """把 segment 按自然日切片，仅保留落在 time_range 周期窗口内的部分。

    tr=None 表示不裁剪（返回整段按天切片，使跨午夜会话正确归到各自日期）。
    返回交集区间 [(s, e), ...]，每段按实际日期，供 by_day/timeline 使用。

    从旧版 InsightService._clip_to_time_range 迁移而来，行为完全一致。
    """
    pieces = split_by_day(seg_start, seg_end)
    if not tr:
        return pieces
    smin, emin, crosses = tr
    out = []
    for ps, pe in pieces:
        day = ps.date()
        day_start = datetime(day.year, day.month, day.day)
        day_end = day_start + timedelta(days=1)
        windows = [(day_start + timedelta(minutes=smin), day_start + timedelta(minutes=emin))] if not crosses \
            else [(day_start + timedelta(minutes=smin), day_end),
                  (day_start, day_start + timedelta(minutes=emin))]
        for ws, we in windows:
            iss, ie = max(ps, ws), min(pe, we)
            if ie > iss:
                out.append((iss, ie))
    return out



def summarize_events(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把裸事件压成「谁、变了多少次、都变成了什么」。

    从旧版 InsightService._summarize 迁移而来，行为完全一致。
    """
    by_entity: Dict[str, Dict[str, Any]] = {}
    by_hour = [0] * 24
    for r in rows:
        eid = r.get("entity_id", "")
        slot = by_entity.setdefault(
            eid,
            {
                "entity_id": eid,
                "friendly_name": r.get("friendly_name", ""),
                "room": r.get("room", ""),
                "changes": 0,
                "states": {},
                "first_ts": r.get("ts", ""),
                "last_ts": r.get("ts", ""),
            },
        )
        slot["changes"] += 1
        st = str(r.get("new_state", ""))
        slot["states"][st] = slot["states"].get(st, 0) + 1
        ts = r.get("ts", "")
        if ts:
            slot["first_ts"] = min(slot["first_ts"] or ts, ts)
            slot["last_ts"] = max(slot["last_ts"] or ts, ts)
            try:
                by_hour[int(ts[11:13])] += 1
            except (ValueError, IndexError):
                pass
    entities = sorted(by_entity.values(), key=lambda e: -e["changes"])
    return {
        "entities": entities[:30],
        "hourly_distribution": by_hour,
        "busiest_hour": by_hour.index(max(by_hour)) if any(by_hour) else None,
    }



def fallback_name(entity_id: str) -> str:
    """没配友好名时，从 entity_id 里挤出一个还算能看的名字。

    从旧版 InsightService._fallback_name 迁移而来，行为完全一致。
    """
    import re
    tail = entity_id.split(".", 1)[-1]
    tail = re.sub(r"_(p_)?\d+(_\d+)*$", "", tail)
    tail = re.sub(r"[a-z]{2}_\d{6,}_?", "", tail)
    return tail.replace("_", " ").strip() or entity_id


def make_activity(day, kind, conf, evidence, start_h, end_h, **extra) -> Dict[str, Any]:
    """构造活动结果字典。

    从旧版 InsightService._act 迁移而来，行为完全一致。
    """
    out = {
        "day": day,
        "activity": kind,
        "confidence": conf,
        "evidence": evidence,
        "typical_window": f"{start_h:02d}:00-{end_h:02d}:00",
    }
    out.update(extra)
    return out


__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "OFF_STATES",
    "CN_OFF_STATES",
    "CN_ON_STATES",
    "normalize_text",
    "tokenize",
    "state_is_off",
    "state_is_on",
    "fmt_duration",
    "parse_attrs",
    "as_float",
    "num_stale",
    "parse_time_range",
    "split_by_day",
    "clip_to_time_range",
    "summarize_events",
    "fallback_name",
    "make_activity",
]
