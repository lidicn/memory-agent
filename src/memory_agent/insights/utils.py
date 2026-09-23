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
    """根据 domain 返回 category。

    从旧版 InsightService.category_of 迁移而来，行为完全一致。
    """
    d = normalize_text(domain)
    for cat, domains in CATEGORY_DOMAINS.items():
        if d in domains:
            return cat
    return "other"



def finalize_climate_session(sess: Dict[str, Any], still_on: bool = False) -> Dict[str, Any]:
    """完成气候会话，计算时长、温度统计等。

    从旧版 InsightService._finalize_climate_session 迁移而来，行为完全一致。
    """
    from datetime import datetime
    from ..store import parse_ts
    try:
        s = parse_ts(sess["start"]) or datetime.strptime(sess["start"][:19], "%Y-%m-%dT%H:%M:%S")
        e = parse_ts(sess["end"]) or datetime.strptime(sess["end"][:19], "%Y-%m-%dT%H:%M:%S")
        dur = int((e - s).total_seconds() // 60) if s and e else 0
    except Exception:
        dur = 0
    sp = sess["setpoints"]
    rt = sess["room_temps"]
    notes = []
    if still_on:
        notes.append("窗口结束时仍未收到 off，会话未闭合，duration 为「至今」时长")
    if dur == 0:
        notes.append("会话仅含单条事件（on/off 同秒或采集间隔内完成），时长按 0 计")
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
]
