"""通用工具函数：从旧版 insights.py 迁移的工具函数集合。

这些函数都是无状态的纯函数，易于测试和复用。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# 统一委托 parser.entity 的状态判定，避免新旧两套常量口径不一致（U-1）
from .parser.entity import (
    OFF_STATES as _ENTITY_OFF_STATES,
    CN_OFF_STATES as _ENTITY_CN_OFF_STATES,
    CN_ON_STATES as _ENTITY_CN_ON_STATES,
    is_off as _entity_is_off,
    is_on as _entity_is_on,
    category_of_domain as _entity_category_of_domain,
)

#: 抖动阈值：短于该秒数的开启片段视为误触，不计入时长统计。
DEFAULT_DEBOUNCE_SECONDS: int = 5

#: 关闭状态集合（英文）—— 统一委托 parser.entity，避免口径分裂
OFF_STATES = _ENTITY_OFF_STATES

#: 关闭状态集合（中文）—— 统一委托 parser.entity
CN_OFF_STATES = _ENTITY_CN_OFF_STATES

#: 开启状态集合（中文+英文）—— 统一委托 parser.entity
CN_ON_STATES = _ENTITY_CN_ON_STATES


def normalize_text(text: Any) -> str:
    """标准化文本：转字符串、去空白、转小写。"""
    return str(text or "").strip().lower()


def tokenize(text: str) -> List[str]:
    """粗分词：中文按连续汉字块 + 英文按单词切。够用且零依赖。"""
    return [t for t in re.split(r"[\s,，、_./|-]+", normalize_text(text)) if t]


def state_is_off(state: Any) -> bool:
    """判断状态是否为关闭。统一委托 parser.entity.is_off。"""
    return _entity_is_off(state)


def state_is_on(state: Any) -> bool:
    """判断状态是否为开启。统一委托 parser.entity.is_on。"""
    return _entity_is_on(state)


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
    except (ValueError, TypeError, IndexError):
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
        # 兼容 new_state（旧）和 state（新 EventRecord.to_dict）两种键
        st = str(r.get("new_state", r.get("state", "")))
        slot["states"][st] = slot["states"].get(st, 0) + 1
        ts = r.get("ts", "")
        if ts:
            # 归一化 ts：float 时间戳或 ISO 字符串都能处理
            # 之前 ts[11:13] 对 float 抛 TypeError，且 except 只捕获 ValueError/IndexError
            try:
                if isinstance(ts, (int, float)):
                    ts_val = float(ts)
                    hour = datetime.fromtimestamp(ts_val).hour
                elif isinstance(ts, str) and len(ts) >= 13:
                    hour = int(ts[11:13])
                    ts_val = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
                else:
                    ts_val = None
                    hour = None
                if ts_val is not None:
                    slot["first_ts"] = ts_val if slot["first_ts"] in (None, "") else min(float(slot["first_ts"]), ts_val)
                    slot["last_ts"] = ts_val if slot["last_ts"] in (None, "") else max(float(slot["last_ts"]), ts_val)
                if hour is not None:
                    by_hour[hour] += 1
            except (ValueError, IndexError, TypeError, OSError):
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



CATEGORY_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "climate": ("climate", "fan", "humidifier", "water_heater"),
    "lighting": ("light",),
    "media": ("media_player",),
    "presence": ("binary_sensor", "device_tracker", "person"),
    "appliance": ("switch", "vacuum", "input_boolean"),
    "security": ("lock", "cover", "alarm_control_panel", "camera"),
    "telemetry": ("sensor", "number"),
}


def category_of(domain: str) -> str:
    """设备类别。统一委托 parser.entity.category_of_domain。"""
    return _entity_category_of_domain(domain)


def finalize_climate_session(sess: Dict[str, Any], still_on: bool = False) -> Dict[str, Any]:
    """完成气候会话，计算时长、温度统计等。

    从旧版 InsightService._finalize_climate_session 迁移而来，行为完全一致。
    """
    from datetime import datetime
    from ..store import parse_ts
    notes = []
    if still_on:
        notes.append("窗口结束时仍未收到 off，会话未闭合，duration 为「至今」时长")
    dur = 0
    parse_failed = False
    try:
        s = parse_ts(sess["start"]) or datetime.strptime(sess["start"][:19], "%Y-%m-%dT%H:%M:%S")
        e = parse_ts(sess["end"]) or datetime.strptime(sess["end"][:19], "%Y-%m-%dT%H:%M:%S")
        dur = int((e - s).total_seconds() // 60) if s and e else 0
    except (TypeError, ValueError, KeyError):
        # P2：之前裸 except Exception 把解析失败静默归零，脏数据看起来像"正常 0 分钟会话"
        parse_failed = True
    if parse_failed:
        notes.append("start/end 时间戳解析失败，duration 置 0（请检查事件时间字段）")
    elif dur == 0:
        notes.append("会话仅含单条事件（on/off 同秒或采集间隔内完成），时长按 0 计")
    sp = sess["setpoints"]
    rt = sess["room_temps"]
    if not sp and not rt:
        notes.append("该会话事件未携带温度属性，setpoint/room_temp 为 null")
    return {
        "entity_id": sess["entity_id"],
        "room": sess.get("room", ""),
        "start": sess["start"],
        "end": sess["end"],
        "duration_minutes": dur,
        "still_on": still_on,
        "hvac_actions": sorted(sess["hvac_actions"]),
        "setpoint_c": round(sum(sp) / len(sp), 1) if sp else None,
        "setpoint_range_c": [round(min(sp), 1), round(max(sp), 1)] if sp else None,
        "room_temp_c": round(sum(rt) / len(rt), 1) if rt else None,
        "room_temp_range_c": [round(min(rt), 1), round(max(rt), 1)] if rt else None,
        "samples": {"setpoint": len(sp), "room_temp": len(rt)},
        "notes": notes,
    }


def resolve_nl_window(q: str, default_days: int) -> Tuple[str, str, Dict[str, Any]]:
    """从自然语言查询中解析时间窗口。

    支持：昨天/今日/具体星期+时间段/上周/周末/最近N天/默认最近N天。

    返回：(start_iso, end_iso, meta_dict)
    """
    from ..store import now_local
    now = now_local(8)
    ql = q.lower()
    if "昨天" in q or "昨日" in q or "yesterday" in ql:
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59)
        return start.isoformat(), end.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": end.isoformat(), "note": "昨天"}
    if "今晚" in q or "今天" in q or "today" in ql:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), now.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": now.isoformat(), "note": "今天"}
    # 具体星期（周三 / 星期三 / 上周三 / 这周三）——精确到某一天，
    # 必须优先于泛化的「上周/周末」，否则「上周三晚上」会被错当成「整周」。
    m_wd = re.search(r"(上上|上|这|本)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天])", q)
    if m_wd:
        order = "一二三四五六日"
        wd_char = m_wd.group(2)
        wd = 6 if wd_char in ("日", "天") else order.index(wd_char)
        prefix = m_wd.group(1) or ""
        this_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        week_offset = -14 if prefix == "上上" else (-7 if prefix == "上" else 0)
        day0 = this_monday + timedelta(days=week_offset + wd)
        if not prefix and day0 > now:  # 「周三」无限定且未到 → 指上一次
            day0 -= timedelta(days=7)
        start, end = day0, day0 + timedelta(days=1) - timedelta(seconds=1)
        part = ""
        for kw, (h0, h1) in (
            ("凌晨", (0, 6)), ("早上", (5, 9)), ("上午", (8, 12)),
            ("中午", (11, 14)), ("下午", (12, 18)),
            ("晚上", (18, 24)), ("夜里", (20, 24)), ("晚间", (18, 24)),
        ):
            if kw in q:
                start = day0 + timedelta(hours=h0)
                end = day0 + timedelta(hours=h1) - timedelta(seconds=1)
                part = kw
                break
        note = f"{prefix}周{wd_char}{part}"
        return start.isoformat(), end.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": end.isoformat(), "note": note}
    if "上周" in q:
        start = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return start.isoformat(), end.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": end.isoformat(), "note": "上周"}
    if "周末" in q:
        d = now
        while d.weekday() != 5:
            d = d - timedelta(days=1)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1, hours=23, minutes=59, seconds=59)
        return start.isoformat(), end.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": end.isoformat(), "note": "周末"}
    if "最近" in q:
        m = re.search(r"(\d+)\s*天", q)
        if m:
            n = int(m.group(1))
            start = (now - timedelta(days=n)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(), now.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": now.isoformat(), "note": f"最近{n}天"}
    start = (now - timedelta(days=default_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), now.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": now.isoformat(), "note": f"最近{default_days}天"}


def synthesize_persona(persona: dict, top_rooms: list, most_active: str, days: int) -> str:
    """合成用户画像文本。

    输入：persona dict、top_rooms list、most_active str、days int
    输出：用户画像文本
    """
    if not persona:
        return f"近 {days} 天未识别出明确的活动模式（数据不足或被遥测噪声覆盖）。"
    lines = [f"近 {days} 天用户行为画像（共 {len(persona)} 类活动）："]
    for t, d in sorted(persona.items(), key=lambda kv: -kv[1]["occurrences"]):
        rooms = "、".join(d["rooms"]) or "全屋"
        line = (
            f"- {t}：出现 {d['occurrences']} 次 / {d['days_observed']} 天，"
            f"平均置信度 {d['avg_confidence']}，主要房间 {rooms}"
        )
        if d["typical_windows"]:
            line += f"，典型时段 {d['typical_windows'][0]}"
        lines.append(line)
    if top_rooms:
        tr = "、".join(f"{r['room']}({r['events']})" for r in top_rooms)
        lines.append(f"- 房间活跃度 Top：{tr}")
    if most_active:
        lines.append(f"- 最活跃房间：{most_active}")
    return "\n".join(lines)


def synthesize_compare(comparison: dict, days: int, climate_cmp: dict = None) -> str:
    """合成环比比较文本。

    输入：comparison dict、days int、climate_cmp dict
    输出：环比比较文本
    """
    if not comparison:
        base = f"近 {days} 天与上一个 {days} 天窗口均无足够活动数据做环比。"
    else:
        ups, downs, flats = [], [], []
        for t, c in comparison.items():
            d = c["delta_occurrences"]
            if d > 0:
                ups.append(f"{t}(+{d}次)")
            elif d < 0:
                downs.append(f"{t}({d}次)")
            else:
                flats.append(t)
        # 时长变化（仅列有累计时长的活动，修复 #9：强度维度）
        dur_parts = []
        for t, c in comparison.items():
            dm = c["delta_minutes"]
            if dm:
                arrow = "↑" if dm > 0 else "↓"
                dur_parts.append(f"{t}{arrow}{fmt_duration(abs(dm) * 60)}")
        parts = []
        if ups:
            parts.append("次数上升：" + "、".join(ups))
        if downs:
            parts.append("次数下降：" + "、".join(downs))
        if flats:
            parts.append("次数持平：" + "、".join(flats))
        if dur_parts:
            parts.append("时长变化：" + "、".join(dur_parts))
        base = f"近 {days} 天 vs 上一个 {days} 天行为环比 —— " + ("；".join(parts) if parts else "无变化")
    # 温控维度（修复 #9）
    if climate_cmp:
        cur = climate_cmp["current"]
        prev = climate_cmp["previous"]
        dh = climate_cmp["delta_hours"]
        sign = "+" if dh >= 0 else ""
        bits = [f"空调开启 {cur['hours']}h（上周 {prev['hours']}h，{sign}{dh}h）"]
        csp, psp = cur["avg_setpoint_c"], prev["avg_setpoint_c"]
        if csp is not None and psp is not None:
            dsp = climate_cmp["delta_avg_setpoint_c"]
            ssp = "+" if dsp >= 0 else ""
            bits.append(f"平均设定 {csp}°C（上周 {psp}°C，{ssp}{dsp}°C）")
        if cur["avg_room_temp_c"] is not None:
            bits.append(f"室温均 {cur['avg_room_temp_c']}°C")
        base += "。温控：" + "；".join(bits)
    return base


# ── 能力识别常量 ──────────────────────────────────────────────────────────
CAPABILITY_KEYWORDS = (
    "contact_state", "door_state", "window_state", "contact",
    "occupancy_status", "occupancy", "presence_state", "presence",
    "motion_state", "motion", "illuminance", "temperature", "humidity",
    "battery_level", "battery", "power_cost_today", "power_cost",
    "electric_power", "power", "energy", "voltage", "current",
    "distance", "brightness", "position", "switch_status",
)

CAPABILITY_ALIASES = {
    "contact_state": "contact",
    "door_state": "contact",
    "window_state": "contact",
    "occupancy_status": "occupancy",
    "presence_state": "presence",
    "motion_state": "motion",
    "battery_level": "battery",
}

# ── 标签规则常量 ──────────────────────────────────────────────────────────
TAG_RULES: dict = {
    "presence": (
        ("occupancy", "presence", "motion", "pir", "radar", "human", "body", "occupied"),
        ("人体", "存在", "占用", "移动", "雷达", "感应"),
    ),
    "door": (
        ("contact", "door", "window", "opening", "magnet"),
        ("门", "窗", "门磁", "门窗"),
    ),
    "media": (
        ("media_player", "_tv", ".tv", "television", "projector", "soundbar"),
        ("电视", "影音", "投影", "音响", "机顶盒"),
    ),
    "computer": (
        ("pc", "computer", "workstation", "desktop", "imac", "macbook", "nas"),
        ("电脑", "主机", "工作站", "显示器"),
    ),
    "light": (("light.",), ("灯",)),
    "cover": (("cover.", "curtain"), ("窗帘", "卷帘")),
    "climate": (("climate.",), ("空调", "地暖", "暖气")),
    "appliance": (
        ("switch.", "socket", "plug", "outlet"),
        ("插座", "开关", "电饭煲", "油烟机", "热水器"),
    ),
}


def capability_of(entity_id: str) -> str:
    """从 entity_id 提取归一化的能力后缀，用于识别同一物理设备的重复上报。"""
    tail = entity_id.split(".", 1)[-1].lower()
    tail = re.sub(r"_(p_)?\d+(_\d+)*$", "", tail)      # 去 MIoT 属性号 _p_2_1
    tail = re.sub(r"[0-9a-f]{12}", "", tail)            # 去 MAC 片段
    tail = re.sub(r"_?[a-z]{2,4}_[a-z]{2}_\d{6,}_?", "_", tail)  # 去 lumi_cn_123456
    tail = re.sub(r"_?[a-z]{2}_\d{6,}_?", "_", tail)
    for kw in CAPABILITY_KEYWORDS:
        if kw in tail:
            return CAPABILITY_ALIASES.get(kw, kw)
    parts = [p for p in tail.split("_") if p and not p.isdigit()]
    return parts[-1] if parts else tail


def tags_of(eid: str, name: str) -> set:
    """从 entity_id 和 name 提取标签集合。"""
    low = (eid or "").lower()
    nm = name or ""
    tags = set()
    for tag, (id_tokens, name_tokens) in TAG_RULES.items():
        if any(t in low for t in id_tokens) or any(t in nm for t in name_tokens):
            tags.add(tag)
    return tags


def get_runtime():
    """获取 runtime 单例。"""
    from ..runtime import get_runtime
    return get_runtime()


import copy
import time


class ResultCache:
    """内存结果缓存：带 TTL 和容量上限。

    从旧版 InsightsService._cache_get/_cache_put 迁移而来。
    容量超过 max_entries 时自动清理最旧条目。
    """

    def __init__(self, ttl: float = 60.0, max_entries: int = 64) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """获取缓存值，过期或不存在返回 None。"""
        ent = self._store.get(key)
        if not ent:
            return None
        ts, val = ent
        if time.monotonic() - ts > self._ttl:
            self._store.pop(key, None)
            return None
        return copy.deepcopy(val)

    def put(self, key: str, val: Any) -> None:
        """写入缓存，超过容量时清理最旧条目。"""
        self._store[key] = (time.monotonic(), copy.deepcopy(val))
        if len(self._store) > self._max_entries:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)


def parse_datetime(raw: str, tz_offset_hours: float = 8.0) -> datetime | None:
    """解析时间字符串为 naive datetime（本地时区）。

    从旧版 InsightsService._parse 迁移而来。
    支持格式：YYYY-MM-DD、ISO 8601（含 Z 后缀）。
    带时区的时间会转换为指定时区的 naive datetime。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.fromisoformat(f"{text}T00:00:00")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone(timedelta(hours=tz_offset_hours))).replace(tzinfo=None)
    return dt.replace(microsecond=0)




def diagnose_empty_result(
    entities: list[str],
    room: str = "",
    category: str = "",
    domains: list[str] | None = None,
    seen: dict[str, str] | None = None,
) -> dict:
    """0 结果时给出明确原因，而不是让调用方猜「没数据还是没采集」。

    从旧版 InsightsService._diagnose_empty 迁移而来。
    seen: {entity_id: last_seen_ts}，由调用方通过 store.entity_last_seen() 获取。
    """
    scope = f"{room or '全屋'} / {category or '不限类别'}"
    domains = domains or []
    seen = seen or {}

    if not entities and (room or category or domains):
        return {
            "reason": "no_matching_entity",
            "message": f"「{scope}」下没有配置任何匹配的实体，因此不可能有事件。",
            "next_step": "用 get_entity_catalog() 看看实际有哪些房间和设备类别",
        }
    if entities:
        never = [e for e in entities if not seen.get(e)]
        if len(never) == len(entities):
            return {
                "reason": "never_collected",
                "message": (
                    f"「{scope}」下有 {len(entities)} 个实体，但它们从未产生过任何事件 —— "
                    f"通常是采集未启用该实体，或 HA 侧本就没有历史。"
                ),
                "entities": entities[:20],
                "next_step": "到 WebUI「采集配置」确认这些实体已勾选，再触发一次采集",
            }
        return {
            "reason": "no_event_in_window",
            "message": f"实体有历史数据，但在当前时间窗口内没有状态变化。",
            "last_seen": {e: seen.get(e, "") for e in entities[:20]},
            "next_step": "放大 days，或去掉 state 过滤条件",
        }
    return {
        "reason": "empty_window",
        "message": "该时间窗口内没有任何事件，确认采集是否已运行。",
        "next_step": "调用 get_collect_status() 查看采集进度",
    }



#: 噪声判定阈值：某实体事件数 > 房间事件数 * NOISE_RATIO_CAP 即视为垄断型噪声。
NOISE_RATIO_CAP = 0.6


def identify_noise_entities(
    hist_raw: dict[str, list[int]],
    tops: list[dict],
    noise_ratio_cap: float = NOISE_RATIO_CAP,
) -> set[str]:
    """识别单实体占比垄断型噪声源。

    从旧版 InsightsService._noise_entities 迁移而来。
    hist_raw: {room: [hourly_counts]}
    tops: [{"entity_id": ..., "room": ..., "count": ...}, ...]
    """
    noise_ids: set[str] = set()
    for room, buckets in hist_raw.items():
        room_total = sum(buckets)
        if room_total <= 0:
            continue
        for t in tops:
            if t.get("room") != room or t["entity_id"] in noise_ids:
                continue
            if t["count"] > noise_ratio_cap * room_total:
                noise_ids.add(t["entity_id"])
    return noise_ids


def find_last_boot_time(
    events: list[dict],
    off_states: frozenset = OFF_STATES,
) -> str | None:
    """从事件列表中找最近一次 off -> on 的开机时刻。

    从旧版 InsightsService._last_boot_time 迁移而来。
    events: 按时间倒序排列的事件列表
    """
    for e in events:
        old = normalize_text(e.get("old_state") or e.get("state_before") or "")
        new = normalize_text(e.get("new_state") or e.get("state_after") or "")
        if old in off_states and new not in off_states:
            return e.get("ts")
    return None



def compare_windows(compare_days: int, tz_offset_hours: float = 8.0) -> tuple[str, str, str, str]:
    """返回对齐到自然日边界的环比窗口。

    从旧版 InsightsService._compare_windows 迁移而来。
    两个窗口等长、首尾相接不重叠，各恰好 compare_days 个完整自然日。
    返回 (cur_start, cur_end, prev_start, prev_end) 均为 ISO 字符串。
    """
    from ..store import now_local
    cd = max(1, min(int(compare_days or 7), 365))
    today = now_local(tz_offset_hours).date()
    cur_end = (today + timedelta(days=1)).isoformat() + "T00:00:00"
    cur_start = (today + timedelta(days=1) - timedelta(days=cd)).isoformat() + "T00:00:00"
    prev_end = cur_start
    prev_start = (today + timedelta(days=1) - timedelta(days=2 * cd)).isoformat() + "T00:00:00"
    return cur_start, cur_end, prev_start, prev_end



# ─────────────────────────────────────────────────────────────
# 行为洞察模块常量（从旧版 insights_legacy.py 迁移而来）
# ─────────────────────────────────────────────────────────────

#: 噪声判定阈值：某实体事件数 > 房间事件数 * NOISE_RATIO_CAP 即视为垄断型噪声。
# NOISE_RATIO_CAP = 0.6  # 已存在，跳过

#: 关状态集合（设备关闭/待机/离线等）。
# P2：此处原定义缺 closed/not_home、多 power off/down，与 parser.entity 口径分裂；
# 统一委托 parser.entity（本文件顶部已 import），后续勿在此另起炉灶。
OFF_STATES = _ENTITY_OFF_STATES

#: 房间聚合词（表示"所有房间/全屋"的关键词）。
ROOM_AGGREGATE_WORDS: tuple[str, ...] = (
    "全屋", "所有房间", "全部房间", "每个房间", "整体", "家里", "家中",
)

#: 通用房间词（需要消歧的房间名，如"卧室"可能指"主卧室"或"次卧室"）。
GENERIC_ROOM_WORDS: frozenset[str] = frozenset({"房间", "卧室", "屋子", "房子", "家里"})

#: 关键词 → 域映射（用于从用户查询推断设备类别）。
KEYWORD_DOMAINS: dict[str, tuple[str, ...]] = {
    "light": ("light", "switch.light"),
    "switch": ("switch", "input_boolean"),
    "climate": ("climate", "humidifier", "fan"),
    "media": ("media_player", "remote"),
    "sensor": ("sensor", "binary_sensor"),
    "tv": ("media_player.tv", "remote.tv"),
    "ac": ("climate.ac",),
    "aircon": ("climate.ac",),
}



def iter_all_events(
    query_func,
    start_iso: str,
    end_iso: str,
    max_rows: int = 60000,
    **kw,
) -> list[dict]:
    """分页拉取窗口内全部事件。

    从旧版 InsightsService._iter_all_events 迁移而来。
    query_func: 可调用对象，签名为 (start_iso, end_iso, limit=, offset=, **kw) -> list[dict]
    """
    out: list[dict] = []
    page = 5000
    offset = 0
    while len(out) < max_rows:
        rows = query_func(
            start_iso, end_iso, limit=page, offset=offset, **kw
        )
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out[:max_rows]



def expand_entities_from_config(
    rooms_config: dict,
    category_of_func=None,
    only_enabled: bool = True,
) -> list[dict]:
    """从配置展开实体清单（房间 → 实体）。

    从旧版 InsightsService._config_entities 迁移而来。
    rooms_config: {room_name: {"enabled": bool, "entities": {entity_id: {...}}}}
    category_of_func: 可选，根据 domain 返回 category 的函数
    """
    out: list[dict] = []
    for room, payload in (rooms_config or {}).items():
        if not isinstance(payload, dict):
            continue
        room_enabled = bool(payload.get("enabled", True))
        if only_enabled and not room_enabled:
            continue
        for entity_id, info in (payload.get("entities") or {}).items():
            info = info if isinstance(info, dict) else {}
            if only_enabled and not info.get("enabled", True):
                continue
            domain = info.get("domain") or entity_id.split(".")[0]
            category = category_of_func(domain) if category_of_func else ""
            out.append(
                {
                    "entity_id": entity_id,
                    "friendly_name": info.get("name") or "",
                    "room": room,
                    "domain": domain,
                    "category": category,
                    "enabled": bool(info.get("enabled", True)) and room_enabled,
                }
            )
    return out



#: 设备查询填充词（从自然语言中剔除的无意义词）。
DEVICE_QUERY_FILLER: tuple[str, ...] = (
    "今天", "昨天", "今晚", "昨日", "前天", "这周", "本周", "上周", "周末", "最近",
    "时候", "多长时间", "了多久", "开灯", "关灯",
    "开机", "关机", "了", "多久", "时长", "使用", "运行", "在线", "时间", "查询", "问",
    "多少", "几", "小时", "分钟", "秒", "次", "数", "在", "是", "吗", "怎么", "什么",
    "哪些", "哪", "些", "?", "？", "的",
)


def clean_device_query(q: str) -> str:
    """从自然语言设备问题中清洗出设备关键词。

    从旧版 InsightsService._resolve_device_targets 迁移而来。
    返回清洗后的关键词串，空串表示无法提取有效关键词。
    """
    import re as _re
    qq = _re.sub(r"最近\s*\d+\s*天", "", q)
    qq = normalize_text(qq)
    for w in DEVICE_QUERY_FILLER:
        qq = qq.replace(normalize_text(w), " ")
    return qq.strip()



def synthesize_answer(data: dict, window_desc: str = "") -> str:
    """把结构化洞察合成为一句自然语言回答 + 证据引用（不依赖 LLM，纯规则合成）。

    从旧版 InsightsService._synthesize_answer 迁移而来。
    """
    parts = [f"关于「{window_desc or '该时段'}」的行为记忆检索结果："]
    total = data.get("total_events", 0)
    parts.append(f"区间内共 {total} 条事件。")
    rooms = data.get("rooms") or {}
    if rooms:
        top = sorted(rooms.items(), key=lambda kv: -kv[1].get("event_count", 0))[:3]
        parts.append(
            "最活跃房间：" + "、".join(f"{r}({v.get('event_count', 0)})" for r, v in top) + "。"
        )
    anomalies = data.get("anomalies") or []
    if anomalies:
        parts.append(
            f"检测到 {len(anomalies)} 处异常："
            + "；".join(a.get("title", "") for a in anomalies[:3])
            + "。"
        )
    else:
        parts.append("未检测到明显异常。")
    sessions = data.get("climate_sessions") or []
    if sessions:
        parts.append(f"空调/温控会话 {len(sessions)} 段。")
    parts.append(
        "（以上为基于结构化事件库的统计检索；向量语义检索仅在命中时附加 similarity 证据，"
        "未命中则完全不依赖向量库。）"
    )
    return "".join(parts)



def is_tv_duration_sensor(entity_id: str, friendly_name: str = "") -> bool:
    """判断是否为电视播放时长传感器。

    从旧版 InsightsService._tv_from_telemetry 迁移而来。
    识别规则：名称含「电视/tv」且含「播放时长/观看时长/duration/playtime」。
    """
    disp = str(friendly_name or "")
    blob = (entity_id + disp).lower()
    return ("电视" in disp or "tv" in blob) and (
        "播放时长" in disp or "观看时长" in disp
        or "duration" in blob or "playtime" in blob
    )


def is_yesterday_duration_sensor(entity_id: str, friendly_name: str = "") -> bool:
    """判断是否为「昨日累计」类型的传感器（值归属到前一天）。

    从旧版 InsightsService._tv_from_telemetry 迁移而来。
    """
    disp = str(friendly_name or "")
    blob = (entity_id + disp).lower()
    return "昨日" in disp or "yesterday" in blob



def aggregate_climate_sessions(sessions: list[dict]) -> dict:
    """聚合温控会话统计：总时长 + 平均设定/室温 + 设定温度区间。

    从旧版 InsightsService._climate_compare 迁移而来。
    """
    total_min = sum(s.get("duration_minutes", 0) for s in sessions)
    sp = [s["setpoint_c"] for s in sessions if s.get("setpoint_c") is not None]
    rt = [s["room_temp_c"] for s in sessions if s.get("room_temp_c") is not None]
    return {
        "sessions": len(sessions),
        "hours": round(total_min / 60, 1),
        "avg_setpoint_c": round(sum(sp) / len(sp), 1) if sp else None,
        "avg_room_temp_c": round(sum(rt) / len(rt), 1) if rt else None,
        "min_setpoint_c": round(min(sp), 1) if sp else None,
        "max_setpoint_c": round(max(sp), 1) if sp else None,
    }


def compare_climate(current: dict, previous: dict) -> dict:
    """计算两个窗口的温控环比变化。

    从旧版 InsightsService._climate_compare 迁移而来。
    """
    d_sp = None
    if current["avg_setpoint_c"] is not None and previous["avg_setpoint_c"] is not None:
        d_sp = round(current["avg_setpoint_c"] - previous["avg_setpoint_c"], 1)
    return {
        "current": current,
        "previous": previous,
        "delta_hours": round(current["hours"] - previous["hours"], 1),
        "delta_avg_setpoint_c": d_sp,
    }



def match_pattern(value: Any, target: Any, pattern: str = "eq") -> bool:
    """根据 pattern 判断 value 是否匹配 target。

    从旧版 InsightsService._count_by_filter 迁移而来。
    pattern: eq / contains / ne / regex
    """
    import re as _re
    if value is None:
        return False
    sv = normalize_text(value)
    tv = normalize_text(target)
    if pattern == "contains":
        return tv in sv
    if pattern == "ne":
        return sv != tv
    if pattern == "regex":
        try:
            return _re.search(str(target), sv) is not None
        except Exception:
            return False
    return sv == tv


def extract_attr_value(row: dict, attribute: str) -> Any:
    """从事件行提取属性值。

    从旧版 InsightsService._count_by_filter 迁移而来。
    attribute: "state" / "new_state" / "attributes.xxx"
    """
    if attribute in ("", "state", "new_state"):
        return row.get("new_state")
    attr_key = attribute.split(".", 1)[1] if attribute.startswith("attributes.") else attribute
    return parse_attrs(row.get("attrs_json")).get(attr_key)



def apply_semantic_filter(
    items: list[dict],
    rooms: set[str] | None = None,
    domains: set[str] | None = None,
    q_tokens: list[str] | None = None,
) -> list[dict]:
    """按 room/domain/query 关键词过滤实体列表。

    从旧版 InsightsService._apply_semantic_filter 迁移而来。
    有精确命中就用精确结果，否则回退到宽松结果。
    """
    rooms = rooms or set()
    domains = domains or set()
    q_tokens = q_tokens or []

    loose: list[dict] = []
    strict: list[dict] = []
    for it in items:
        if rooms and it.get("room", "") not in rooms:
            continue
        if domains and normalize_text(it.get("domain", "")) not in domains:
            continue
        loose.append(it)
        if q_tokens:
            haystack = normalize_text(
                f"{it.get('entity_id', '')} {it.get('friendly_name', '')} {it.get('room', '')} {it.get('domain', '')}"
            )
            if any(t in haystack for t in q_tokens):
                strict.append(it)
    if not q_tokens:
        return loose
    if strict:
        return strict
    return loose if (rooms or domains) else []



def is_device_on(state: Any, allow_on: set[str] | None = None) -> bool:
    """判断设备状态是否为「开启」。

    从旧版 InsightsService._usage_one 迁移而来。
    allow_on: 若指定，只有在这个集合里的状态才算开启；否则用 OFF_STATES 判断。
    """
    s = normalize_text(state)
    if allow_on:
        return s in allow_on
    return s not in OFF_STATES



#: 温控实体的开启状态集合。
CLIMATE_OPEN_STATES: frozenset[str] = frozenset({
    "heat", "cool", "dry", "fan", "auto", "heat_cool", "boost", "fan_only",
})

#: 温控实体的关闭状态集合。
CLIMATE_CLOSED_STATES: frozenset[str] = frozenset({"off"})



#: 设备非活动状态集合（用于活动检测的静默判断）。
INACTIVE_STATES: frozenset[str] = frozenset({
    "off", "idle", "0", "unavailable", "unknown", "none", "closed",
    "standby", "not_home", "", "false",
})



def parse_attrs(raw) -> dict:
    """解析事件的 attrs_json 字段，返回 dict。

    从旧版 insights_legacy._parse_attrs 迁移而来。
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    import json as _json
    try:
        val = _json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def as_float(v):
    """安全转换为 float，失败返回 None。

    从旧版 insights_legacy._as_float 迁移而来。
    """
    try:
        return float(v)
    except (TypeError, ValueError):
        return None



def tokenize(text: str) -> list[str]:
    """粗分词：中文按连续汉字块 + 英文按单词切。够用且零依赖。

    从旧版 insights_legacy._tokens 迁移而来。
    """
    import re as _re
    return [t for t in _re.split(r"[\s,，、_./|-]+", normalize_text(text)) if t]


def fmt_duration(total_seconds: float) -> str:
    """把秒数格式化为人类可读时长。

    从旧版 insights_legacy.fmt_duration 迁移而来。
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



def normalize_text(text) -> str:
    """文本归一化：转字符串、去空格、小写。

    从旧版 insights_legacy._norm 迁移而来。
    """
    return str(text or "").strip().lower()



def resolve_domains(category: str = "", domain: str = "", query: str = "") -> list[str]:
    """把「类别 / domain / 自由文本」统一解析成 domain 列表。

    从旧版 insights_legacy.domains_for 迁移而来。
    """
    out: set[str] = set()
    if category:
        out.update(CATEGORY_DOMAINS.get(normalize_text(category), ()))
    if domain:
        out.update(d.strip() for d in normalize_text(domain).split(",") if d.strip())
    if query:
        q = normalize_text(query)
        for keyword, domains in KEYWORD_DOMAINS.items():
            if keyword in q:
                out.update(domains)
    return sorted(out)



def is_off_state(state) -> bool:
    """判断状态是否为关闭。

    从旧版 insights_legacy._state_is_off 迁移而来。
    """
    s = normalize_text(state)
    return s in OFF_STATES or s in CN_OFF_STATES


def is_on_state(state) -> bool:
    """判断状态是否为开启。

    从旧版 insights_legacy._state_is_on 迁移而来。
    """
    s = normalize_text(state)
    return s in CN_ON_STATES



#: 中文关闭状态集合
CN_OFF_STATES: frozenset[str] = frozenset({"关", "关闭", "门关", "闭合", "断开", "无", "否", "0"})

#: 中文开启状态集合
CN_ON_STATES: frozenset[str] = frozenset({"开", "打开", "门开", "开启", "接通", "有", "是", "1"})



#: 抖动阈值：短于该秒数的开启片段视为误触，不计入时长统计
DEFAULT_DEBOUNCE_SECONDS = 5



#: 「查所有房间」的汇总意图词
ROOM_AGGREGATE_WORDS: tuple[str, ...] = (
    "所有房间", "每个房间", "各个房间", "全部房间", "所有区域", "每个区域",
    "所有的房间", "全屋", "整个家", "全家", "家里所有", "各房间",
)

#: 既是日常通用名词、又可能被用户拿来当 area 名的词
GENERIC_ROOM_WORDS: frozenset[str] = frozenset({"房间", "卧室", "屋子", "房子", "家里"})



#: 关闭状态集合（英文）
OFF_STATES: frozenset[str] = frozenset(
    {"off", "closed", "not_home", "unavailable", "unknown", "idle", "standby", "none", ""}
)



#: 类别 → domain 映射
CATEGORY_DOMAINS: dict[str, tuple[str, ...]] = {
    "climate": ("climate", "fan", "humidifier", "water_heater"),
    "lighting": ("light",),
    "media": ("media_player",),
    "presence": ("binary_sensor", "device_tracker", "person"),
    "appliance": ("switch", "vacuum", "input_boolean"),
    "security": ("lock", "cover", "alarm_control_panel", "camera"),
    "telemetry": ("sensor", "number"),
}

#: 中文/英文关键词 → domain。用于 query="主卧空调" 这类自由文本解析
KEYWORD_DOMAINS: dict[str, tuple[str, ...]] = {
    "空调": ("climate",),
    "冷气": ("climate",),
    "制冷": ("climate",),
    "暖气": ("climate",),
    "地暖": ("climate",),
    "ac": ("climate",),
    "风扇": ("fan",),
    "新风": ("fan",),
    "加湿": ("humidifier",),
    "灯": ("light",),
    "灯": ("light",),
    "电视": ("media_player",),
    "投影": ("media_player",),
    "扫地": ("vacuum",),
    "门锁": ("lock",),
    "窗帘": ("cover",),
}



#: 语义标签匹配规则：entity_id token 与中文友好名双通道匹配
TAG_RULES: dict[str, tuple[tuple, tuple]] = {
    "presence": (
        ("occupancy", "presence", "motion", "pir", "radar", "human", "body", "occupied"),
        ("人体", "存在", "占用", "移动", "雷达", "感应"),
    ),
    "door": (
        ("contact", "door", "window", "opening", "magnet"),
        ("门", "窗", "门磁", "门窗"),
    ),
    "media": (
        ("media_player", "_tv", ".tv", "television", "projector", "soundbar"),
        ("电视", "影音", "投影", "音响", "机顶盒"),
    ),
    "computer": (
        ("pc", "computer", "workstation", "desktop", "imac", "macbook", "nas"),
        ("电脑", "主机", "工作站", "显示器"),
    ),
    "light": (("light.",), ("灯",)),
    "cover": (("cover.", "curtain"), ("窗帘", "卷帘")),
    "climate": (("climate.",), ("空调", "地暖", "暖气")),
    "appliance": (
        ("switch.", "socket", "plug", "outlet"),
        ("插座", "开关", "电饭煲", "油烟机", "热水器"),
    ),
}



#: 能力后缀关键词：把厂商前缀 / MAC / MIoT 属性号剥掉，只留「这个实体测什么」
#: 顺序敏感——长词必须排在其前缀词之前（power_cost_today 要在 power 之前）
CAPABILITY_KEYWORDS = (
    "contact_state", "door_state", "window_state", "contact",
    "occupancy_status", "occupancy", "presence_state", "presence",
    "motion_state", "motion", "illuminance", "temperature", "humidity",
    "battery_level", "battery", "power_cost_today", "power_cost",
    "electric_power", "power", "energy", "voltage", "current",
    "distance", "brightness", "position", "switch_status",
)

#: 能力别名映射
CAPABILITY_ALIASES = {
    "contact_state": "contact",
    "door_state": "contact",
    "window_state": "contact",
    "occupancy_status": "occupancy",
    "presence_state": "presence",
    "motion_state": "motion",
    "battery_level": "battery",
}

# ── 房间语义：从旧版迁移的兼容函数 ─────────────────────────────────

def match_rooms(room: str, rooms_dict: dict) -> list[str]:
    """房间名模糊匹配。支持「主卧」→「主卧室」这类包含关系。"""
    if not room:
        return []
    target = normalize_text(room)
    names = list((rooms_dict or {}).keys())
    exact = [n for n in names if normalize_text(n) == target]
    if exact:
        return exact
    loose = [n for n in names if target in normalize_text(n) or normalize_text(n) in target]
    return loose


def room_names_list(rooms_dict: dict, only_enabled: bool = True) -> list[str]:
    """HA 中真实存在的区域（area）名，按「长度降序」返回，便于最长优先匹配。"""
    names: list[str] = []
    for name, payload in (rooms_dict or {}).items():
        if not name:
            continue
        if only_enabled and isinstance(payload, dict) and not payload.get("enabled", True):
            continue
        names.append(str(name))
    return sorted(set(names), key=lambda n: (-len(n), n))


def resolve_room_in_text(
    text: str,
    room_names: list[str],
    aggregate_words: frozenset = None,
    generic_room_words: frozenset = None,
) -> dict:
    """从自由文本里**精确**识别区域名（最长优先）。"""
    t = normalize_text(text)
    names = list(room_names) if room_names else []
    aggregate_words = aggregate_words or ROOM_AGGREGATE_WORDS
    generic_room_words = generic_room_words or GENERIC_ROOM_WORDS
    aggregate = any(w in t for w in aggregate_words)
    matched = sorted(
        [n for n in names if normalize_text(n) and normalize_text(n) in t],
        key=lambda n: (-len(n), n),
    )
    primary = "" if aggregate else (matched[0] if matched else "")
    return {
        "room": primary,
        "aggregate": aggregate,
        "matched": matched,
        "ambiguous": bool(primary) and primary in generic_room_words,
        "rooms_available": names,
    }


def split_room_from_query(room: str, query: str, room_names: list[str]) -> tuple[str, str]:
    """调用方只给了 query="房间空调" 时，把区域名切出来变成 room="房间"。"""
    if room or not query:
        return room, query
    hint = resolve_room_in_text(query, room_names)
    matched = hint.get("room") or ""
    if not matched:
        return room, query
    rest = normalize_text(query).replace(normalize_text(matched), " ").strip()
    return matched, (rest or query)




def decorate_rows(rows, names, fallback_name_fn, category_of_fn):
    """给原始事件补上友好名和类别，让返回结果不再是 cryptic ID。"""
    out = []
    for r in rows:
        meta = names.get(r.get("entity_id", ""), {})
        out.append({
            **r,
            "friendly_name": meta.get("friendly_name") or fallback_name_fn(r.get("entity_id", "")),
            "category": meta.get("category") or category_of_fn(r.get("domain", "")),
        })
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
    "CATEGORY_DOMAINS",
    "category_of",
    "finalize_climate_session",
    "resolve_nl_window",
    "synthesize_persona",
    "synthesize_compare",
    "CAPABILITY_KEYWORDS",
    "CAPABILITY_ALIASES",
    "TAG_RULES",
    "capability_of",
    "tags_of",
    "get_runtime",
    "ResultCache",
    "parse_datetime",
    "diagnose_empty_result",
    "NOISE_RATIO_CAP",
    "identify_noise_entities",
    "find_last_boot_time",
    "compare_windows",
    "match_rooms",
    "room_names_list",
    "resolve_room_in_text",
    "split_room_from_query",
    "decorate_rows",
]