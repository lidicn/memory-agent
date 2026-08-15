"""语音问答工具：把高频问题在不调 LLM 的情况下算出来。

设计目标（来自真实场景）：Node-RED 从「小爱音箱」拿到用户口语，例如
「贾维斯 今天书房电脑开机时长是多少」，剥离唤醒词后变成
「今天书房电脑开机时长是多少」。这类问题的结构是固定的：

    [相对时间窗口] + [设备/房间语义定位] + [用量类意图]

因此完全可以用代码：
  1. 解析相对时间为绝对区间（今天 / 昨天 / 最近7天 …）
  2. 用实体目录做关键词命中，定位到 friendly_name
  3. 直接调 ``insights.device_usage`` 拿到 ``total_on_human``
  4. 用模板拼出 answer / speak，全程不碰 LLM

好处：高频问题从「6~15s 的 LLM 推理」降到「一次 DB 聚合（亚秒级）」，
且结果可进一步缓存为精确答案，重复提问近乎 0 延迟。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from .store import now_local

TZ_OFFSET = 8.0

#: 常见唤醒词，由 NR 或服务端在解析时剔除。
WAKE_WORDS = ["贾维斯", "jarvis", "小爱同学", "小白", "嗨小爱"]

#: 用量类意图关键词 —— 命中即走确定性 device_usage 路径。
USAGE_KEYWORDS = (
    "开机", "关机", "用了多久", "开多久", "开多长时间", "在线时长", "使用时间",
    "运行时长", "运行时长", "开机时长", "开了多久", "亮了多久", "工作时间",
    "用了多少", "用了几小时", "用了几个小时", "开了一天", "用了一天",
)

_CN_DIGITS = "零一二三四五六七八九"


def _arabic_to_cn(n: int) -> str:
    """0~99 的阿拉伯数字转中文，供 TTS 文本使用。"""
    if n < 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        tens, ones = n // 10, n % 10
        return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    return str(n)


def _dur_cn(human: str | None) -> str:
    """把 ``6小时23分`` 这类可读时长翻成 TTS 友好的 ``六小时二十三分钟``。"""
    if not human:
        return "零"
    out = human.replace("分", "分钟").replace("秒", "秒")
    return re.sub(r"\d+", lambda m: _arabic_to_cn(int(m.group())), out)


def _seconds_from_human(human: str | None) -> int:
    """从 ``6小时23分15秒`` 解析成总秒数；解析失败返回 0。"""
    if not human:
        return 0
    total = 0
    m = re.search(r"(\d+)小时", human)
    if m:
        total += int(m.group(1)) * 3600
    m = re.search(r"(\d+)分", human)
    if m:
        total += int(m.group(1)) * 60
    m = re.search(r"(\d+)秒", human)
    if m:
        total += int(m.group(1))
    return total


def strip_wake_word(text: str, wake_word: str = "") -> str:
    """剔除唤醒词（大小写不敏感）。"""
    t = text
    words = [wake_word] if wake_word else WAKE_WORDS
    for w in words:
        if w:
            t = t.replace(w, "")
    return re.sub(r"\s+", "", t)


# 用于从问句中剥离时间/用量/疑问等修饰语，提取设备查询核心。
_DEVICE_QUERY_NOISE = (
    # 时间表达（会被 resolve_window 消费）
    "大前天", "前天", "昨天", "昨日", "今天", "今日",
    "本周", "这周", "上一周", "上周", "下周",
    "本月", "这个月", "上个月", "上月", "下月",
    "今年", "去年", "明年",
    # 用量/动作词
    *USAGE_KEYWORDS,
    # 常见疑问/语气词
    "多久", "多长", "多少", "什么", "吗", "呢", "啊", "吧",
)


def _extract_device_query(text: str) -> str:
    """从问句里去掉时间、用量、语气词，留下设备查询核心。"""
    t = strip_wake_word(text)
    # 绝对日期 2026/8/5、8月5号 等
    t = re.sub(r"(?:(\d{4})[年/\-])?(\d{1,2})[月/\-](\d{1,2})[日号]?", "", t)
    # 相对时间词 + 用量词 + 语气词
    for w in sorted(_DEVICE_QUERY_NOISE, key=len, reverse=True):
        t = t.replace(w, "")
    # 去掉零散数字和连接词
    t = re.sub(r"\d+", "", t)
    for w in ("的", "了", "是", "在", "和", "与", "或"):
        t = t.replace(w, "")
    return t.strip()


def _win(start: datetime, end: datetime, label: str) -> dict:
    s = start.replace(hour=0, minute=0, second=0, microsecond=0)
    e = end.replace(hour=23, minute=59, second=59, microsecond=0)
    return {
        "start": s.isoformat(sep="T"),
        "end": e.isoformat(sep="T"),
        "label": label,
    }


def resolve_window(text: str, today: datetime | None = None) -> dict | None:
    """把相对/绝对时间表达解析为绝对区间。失败返回 None。"""
    if today is None:
        today = now_local(TZ_OFFSET)
    t = text

    # 1) 绝对日期：2026年8月5日 / 8月5号 / 2026/8/5
    m = re.search(r"(?:(\d{4})[年/\-])?(\d{1,2})[月/\-](\d{1,2})[日号]?", t)
    if m:
        y = int(m.group(1)) if m.group(1) else today.year
        mo, d = int(m.group(2)), int(m.group(3))
        try:
            start = datetime(y, mo, d)
        except ValueError:
            start = None
        if start:
            return _win(start, start, f"{mo}月{d}日")

    # 2) 天级相对：大前天 / 前天 / 昨天 / 今天
    for word, n in (("大前天", 3), ("前天", 2), ("昨天", 1), ("昨日", 1),
                    ("今天", 0), ("今日", 0)):
        if word in t:
            d0 = today - timedelta(days=n)
            return _win(d0, d0, word)

    # 3) N 天：最近N天 / 近N天 / 过去N天 / N天内
    m = re.search(r"(?:最近|近|过去)\s*(\d+)\s*天", t) or re.search(
        r"(\d+)\s*天[以之]?内", t
    )
    if m:
        n = int(m.group(1))
        end = today.replace(hour=23, minute=59, second=59)
        start = (today - timedelta(days=n - 1)).replace(hour=0, minute=0, second=0)
        return _win(start, end, f"最近{n}天")

    # 4) 周级
    if any(k in t for k in ("本周", "这周", "这一周", "这星期")):
        monday = today - timedelta(days=today.weekday())
        return _win(monday, today, "本周")
    if any(k in t for k in ("上周", "上星期", "上一周", "上个星期")):
        this_monday = today - timedelta(days=today.weekday())
        last_sun = this_monday - timedelta(days=1)
        last_monday = this_monday - timedelta(days=7)
        return _win(last_monday, last_sun, "上周")

    # 5) 月级
    if any(k in t for k in ("本月", "这个月", "当月")):
        return _win(today.replace(day=1), today, "本月")
    if any(k in t for k in ("上月", "上个月", "上月")):
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return _win(last_prev.replace(day=1), last_prev, "上月")

    # 6) 年级
    if "今年" in t:
        return _win(today.replace(month=1, day=1), today, "今年")
    if "去年" in t:
        ly = today.year - 1
        return _win(datetime(ly, 1, 1), datetime(ly, 12, 31, 23, 59, 59), "去年")

    return None


# 能统计"开机/运行时长"的 domain；sensor 等无开关语义的实体优先排除。
_USAGE_DOMAINS = {
    "switch", "binary_sensor", "light", "climate", "fan", "media_player",
    "cover", "lock", "valve", "water_heater", "humidifier", "dehumidifier",
}


def match_device(text: str, insights: Any) -> dict | None:
    """用实体目录匹配设备，返回精确到单个设备的定位结果。

    返回 dict：
      - query: 用于显示/缓存的友好名
      - entity_id: 若精确命中单个设备，可直接传给 device_usage(entity_id=...)
      - room: 若只命中房间聚合，传给 device_usage(room=...)

    匹配策略（按优先级）：
      1. 核心设备词命中 friendly_name。优先在可统计用量的 domain（switch、
         binary_sensor、light 等）里找最长匹配，避免 sensor 等无开关语义实体
         被误命中。
      2. entity_id 最后一段子串命中（如 switch.study_pc → study_pc）→ 返回该设备。
      3. 房间 + 设备关键词命中：问句含房间名且含某类设备关键词，返回该房间内
         friendly_name 包含该关键词的设备；若找不到具体设备但房间名命中，
         返回房间名让 device_usage 做聚合。
    """
    name_map = insights.name_map()
    query_core = _extract_device_query(text)

    # 房间锁定：问句里出现真实存在的 area 名（最长优先）时，后续所有匹配
    # 一律限定在该区域内。这样「房间空调」不会被别的房间的「空调」抢走，
    # 「主卧室」也不会被「卧室」截胡。用户说「所有房间/全屋」时不锁定。
    locked_room = ""
    try:
        locked_room = (insights.resolve_room_in_text(text) or {}).get("room") or ""
    except Exception:
        locked_room = ""

    def _in_room(info: dict) -> bool:
        if not locked_room:
            return True
        return (info.get("room") or "").strip() == locked_room

    def _domain(info: dict) -> str:
        return (info.get("domain") or "").lower()

    def _hit(info: dict) -> bool:
        fn = (info.get("friendly_name") or "").strip()
        if not fn:
            return False
        return bool((query_core and query_core in fn) or (fn in text))

    # 1a) 优先匹配可统计用量的 domain，最长 friendly_name 优先
    matches: list[tuple[int, str, str]] = []
    for eid, info in name_map.items():
        if not _in_room(info):
            continue
        if _domain(info) not in _USAGE_DOMAINS:
            continue
        if not _hit(info):
            continue
        fn = (info.get("friendly_name") or "").strip()
        if not fn:
            continue
        matches.append((len(fn), fn, eid))
    if matches:
        matches.sort(key=lambda x: -x[0])
        return {
            "query": matches[0][1],
            "entity_id": matches[0][2],
            "room": "",
            "_debug_matches": [{"len": m[0], "fn": m[1], "eid": m[2]} for m in matches[:5]],
        }

    # 1b) 没有命中开关类设备时，再回退到所有设备
    matches = []
    for eid, info in name_map.items():
        if not _in_room(info):
            continue
        if not _hit(info):
            continue
        fn = (info.get("friendly_name") or "").strip()
        if not fn:
            continue
        matches.append((len(fn), fn, eid))
    if matches:
        matches.sort(key=lambda x: -x[0])
        return {
            "query": matches[0][1],
            "entity_id": matches[0][2],
            "room": "",
            "_debug_matches": [{"len": m[0], "fn": m[1], "eid": m[2]} for m in matches[:5]],
        }

    # 2) entity_id 最后一段命中（常用于 PC/灯/空调等命名）
    for eid, info in name_map.items():
        if not _in_room(info):
            continue
        short = eid.split(".")[-1] if "." in eid else eid
        if short and short in text:
            fn = (info.get("friendly_name") or "").strip() or eid
            return {"query": fn, "entity_id": eid, "room": ""}

    # 3) 房间 + 设备关键词
    rooms = sorted(
        {(info.get("room") or "").strip() for info in name_map.values() if info.get("room")},
        key=len,
        reverse=True,
    )
    keyword_map = {
        "电脑": ["电脑", "PC", "笔记本", "台式机", "主机"],
        "电视": ["电视", "TV", "投影仪"],
        "音箱": ["音箱", "音响", "小爱", "触屏", "播放控制"],
        "灯": ["灯", "吸顶灯", "台灯", "筒灯", "射灯"],
        "空调": ["空调", "冷气", "暖气", "新风"],
        "风扇": ["风扇", "吊扇"],
        "插座": ["插座", "插头"],
    }
    matched_room = locked_room or next((r for r in rooms if r in text), None)
    if matched_room:
        for keyword, aliases in keyword_map.items():
            if any(alias in text for alias in [keyword] + aliases):
                # 优先返回可开关设备且 friendly_name 包含完整 query_core 的设备
                if query_core:
                    for eid, info in name_map.items():
                        if (info.get("room") or "").strip() != matched_room:
                            continue
                        if _domain(info) not in _USAGE_DOMAINS:
                            continue
                        fn = (info.get("friendly_name") or "").strip()
                        if fn and query_core in fn:
                            return {"query": fn, "entity_id": eid, "room": ""}
                # 其次任何包含完整 query_core 的设备
                if query_core:
                    for eid, info in name_map.items():
                        if (info.get("room") or "").strip() != matched_room:
                            continue
                        fn = (info.get("friendly_name") or "").strip()
                        if fn and query_core in fn:
                            return {"query": fn, "entity_id": eid, "room": ""}
                # 最后按关键词命中
                for eid, info in name_map.items():
                    if (info.get("room") or "").strip() != matched_room:
                        continue
                    fn = (info.get("friendly_name") or "").strip()
                    if fn and any(alias in fn for alias in [keyword] + aliases):
                        return {"query": fn, "entity_id": eid, "room": ""}
                # 房间命中但 friendly_name 没有对应关键词：返回房间名做聚合
                return {"query": matched_room, "entity_id": "", "room": matched_room}
    # 锁定了房间但没定位到具体设备：退回该区域聚合，好过返回 None 让上层乱猜
    if locked_room:
        return {"query": locked_room, "entity_id": "", "room": locked_room}
    return None


def classify_intent(text: str) -> str:
    """判断是否为可走确定性路径的「设备用量」类问题。"""
    if any(k in text for k in USAGE_KEYWORDS):
        return "device_usage"
    return "other"


def render_device_usage(usage: dict, window_label: str, query: str) -> dict | None:
    """把 device_usage 结果渲染成 {answer, speak, data}；无法渲染返回 None。"""
    if not usage or not usage.get("ok"):
        return None

    devices: list[dict] = usage.get("devices") or []
    if len(devices) == 1:
        dev = devices[0]
        label = dev.get("friendly_name") or query or dev.get("entity_id") or "该设备"
        total = dev.get("total_on_human") or "0分"
        on_count = dev.get("on_count", 0)
        data: dict[str, Any] = {
            "device": label,
            "entity_id": dev.get("entity_id"),
            "window": window_label,
            "total_on_human": total,
            "on_count": on_count,
            "raw_event_count": dev.get("raw_event_count", 0),
            "device_count": 1,
        }
        return {
            "answer": f"{label}{window_label}开机约 {total}。",
            "speak": f"{label}{window_label}开机大约{_dur_cn(total)}",
            "data": data,
        }

    # 多设备：报告总体 + 主设备
    total = usage.get("total_on_human") or "0分"
    label = query or "相关设备"
    top = devices[0] if devices else None
    data = {
        "device": label,
        "window": window_label,
        "total_on_human": total,
        "device_count": usage.get("device_count", len(devices)),
    }
    if top:
        data["top_device"] = top.get("friendly_name")
        data["top_total_on_human"] = top.get("total_on_human")
    return {
        "answer": f"{label}{window_label}共开机约 {total}（{data['device_count']} 个设备）。",
        "speak": f"{label}{window_label}共开机大约{_dur_cn(total)}",
        "data": data,
    }


def ask_cache_key(prefix: str, *parts: str) -> str:
    """生成缓存键。parts 已经过归一化（设备 + 时间窗口标签）。"""
    raw = "|".join([prefix, *parts])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def normalize_for_cache(text: str, today: datetime | None = None) -> str:
    """把相对/绝对时间表达统一替换为 ISO 日期，让「今天 / 8月5号」算同一键。"""
    w = resolve_window(text, today)
    if not w:
        return text
    iso = w["start"][:10]
    norm = text
    # 相对时间词 → ISO 日期
    for lbl in ("今天", "今日", "昨天", "昨日", "前天", "大前天",
                "本周", "这周", "上周", "本月", "上月", "今年", "去年"):
        norm = norm.replace(lbl, iso)
    # 绝对日期：2026年8月5日 / 8月5号 / 2026/8/5 → ISO 日期
    norm = re.sub(
        r"(?:(\d{4})[年/\-])?(\d{1,2})[月/\-](\d{1,2})[日号]?", iso, norm
    )
    return norm
