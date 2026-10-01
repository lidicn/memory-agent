"""活动推断模块：洗澡 / 学习 / 看电视 / 睡眠 / 烹饪 + 自定义活动规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (ActivityMatch, EventRecord, InsightConfig, TimeRange, day_key,
                      house_dt)
from .parser.entity import EntityResolver
from .parser.timeframe import split_days

__all__ = ["Signal", "ActivityRule", "BUILTIN_ACTIVITIES", "ActivityEngine", "analyze_rhythm"]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """P2：脏数据（"abc"/None）不再炸，回退默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Signal:
    """活动信号：某个房间 / 类别 / 关键词在窗口内的活跃程度。"""

    room: str = ""
    query: str = ""
    category: str = ""
    domain: str = ""
    min_minutes: float = 0.0
    min_count: int = 1
    optional: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> "Signal":
        if isinstance(raw, Signal):
            return raw
        data = dict(raw or {})
        return cls(
            room=str(data.get("room", "")), query=str(data.get("query", "")),
            category=str(data.get("category", "")), domain=str(data.get("domain", "")),
            min_minutes=_safe_float(data.get("min_minutes", 0.0)),
            min_count=_safe_int(data.get("min_count", 1)),
            optional=bool(data.get("optional", False)))

    @classmethod
    def from_text(cls, token: str) -> "Signal":
        """字符串 DSL：``房间|关键词|最小分钟数``（三段可省略）。"""
        parts = [p.strip() for p in str(token).split("|")]
        room = parts[0] if len(parts) > 0 else ""
        query = parts[1] if len(parts) > 1 else ""
        minutes = _safe_float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
        if not query and room and not any(c >= "\u4e00" and c <= "\u9fff" for c in room):
            query, room = room, ""
        return cls(room=room, query=query, min_minutes=minutes)

    def to_dict(self) -> Dict[str, Any]:
        return {"room": self.room, "query": self.query, "category": self.category,
                "domain": self.domain, "min_minutes": self.min_minutes,
                "min_count": self.min_count, "optional": self.optional}


@dataclass
class ActivityRule:
    """活动规则：若干必要信号 + 若干可选信号 + 时间窗 + 最短时长。"""

    key: str
    name: str
    room: str = ""
    tags: List[str] = field(default_factory=list)
    window: Tuple[int, int] = (0, 24)          # (起始小时, 结束小时)，可跨天
    min_minutes: float = 5.0
    requires: List[Signal] = field(default_factory=list)
    any_of: List[Signal] = field(default_factory=list)
    source: str = "builtin"

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "name": self.name, "room": self.room,
                "tags": list(self.tags), "window": list(self.window),
                "min_minutes": self.min_minutes,
                "requires": [s.to_dict() for s in self.requires],
                "any_of": [s.to_dict() for s in self.any_of],
                "source": self.source}

    @classmethod
    def from_dict(cls, key: str, name: str, raw: Any, tags: Any = None,
                  room: str = "", source: str = "custom") -> "ActivityRule":
        data = dict(raw or {}) if not isinstance(raw, str) else {}
        if isinstance(raw, str):
            requires, any_of = _parse_rule_text(raw)
        else:
            requires = [Signal.from_dict(s) for s in data.get("requires", [])]
            any_of = [Signal.from_dict(s) for s in data.get("any_of", [])]
            for item in data.get("signals", []):   # signals 兼容写法 -> requires
                requires.append(Signal.from_dict(item))
        window = tuple(data.get("window", (0, 24)))
        try:
            window_tuple = (_safe_int(window[0], 0), _safe_int(window[1], 24))
        except (IndexError, TypeError):
            window_tuple = (0, 24)
        return cls(key=key, name=name, room=room or str(data.get("room", "")),
                   tags=list(tags or data.get("tags", [])),
                   window=window_tuple,
                   min_minutes=_safe_float(data.get("min_minutes", 5.0), 5.0),
                   requires=requires, any_of=any_of, source=source)


def _parse_rule_text(text: str) -> Tuple[List[Signal], List[Signal]]:
    """字符串规则 DSL：``房间|关键词|分钟 AND 房间|关键词``（OR 进 any_of）。"""
    requires: List[Signal] = []
    any_of: List[Signal] = []
    for part in _split_tokens(text, (" OR ", " 或 ")):
        group = _split_tokens(part, (" AND ", " 和 ", ","))
        for index, token in enumerate(group):
            signal = Signal.from_text(token)
            (any_of if index > 0 else requires).append(signal)
    return requires, any_of


def _split_tokens(text: str, seps: Sequence[str]) -> List[str]:
    chunks = [str(text)]
    for sep in seps:
        expanded: List[str] = []
        for chunk in chunks:
            expanded.extend(chunk.split(sep))
        chunks = expanded
    return [c.strip() for c in chunks if c.strip()]


def _builtin() -> List[ActivityRule]:
    """内置活动规则（洗澡/学习/看电视/睡眠/烹饪）。"""
    return [
        ActivityRule(
            key="bath", name="洗澡", room="卫生间", tags=["hygiene", "routine"],
            window=(0, 24), min_minutes=8.0,
            requires=[Signal(room="卫生间", query="存在", min_minutes=5.0)],
            any_of=[Signal(room="卫生间", query="热水器", min_count=1)]),
        ActivityRule(
            key="study", name="学习", room="书房", tags=["focus"], window=(8, 24),
            min_minutes=30.0,
            requires=[Signal(room="书房", query="存在", min_minutes=30.0)]),
        ActivityRule(
            key="tv", name="看电视", room="客厅", tags=["leisure"], window=(6, 24),
            min_minutes=15.0,
            requires=[Signal(room="客厅", category="media", min_minutes=10.0),
                      Signal(room="客厅", query="存在", min_minutes=10.0)]),
        ActivityRule(
            key="sleep", name="睡眠", room="卧室", tags=["rest"], window=(20, 11),
            min_minutes=120.0,
            requires=[Signal(room="卧室", query="存在", min_minutes=120.0)],
            any_of=[Signal(room="卧室", query="灯", min_count=1)]),
        ActivityRule(
            key="cooking", name="烹饪", room="厨房", tags=["chore"], window=(5, 22),
            min_minutes=5.0,
            requires=[Signal(room="厨房", query="存在", min_minutes=3.0)],
            any_of=[Signal(room="厨房", category="appliance", min_count=1),
                    Signal(room="厨房", query="插座", min_count=1)]),
    ]


BUILTIN_ACTIVITIES: List[ActivityRule] = _builtin()


class ActivityEngine:
    """活动推断引擎。"""

    def __init__(self, resolver: EntityResolver,
                 config: Optional[InsightConfig] = None) -> None:
        self.resolver = resolver
        self.config = config or InsightConfig()
        self.custom: Dict[str, ActivityRule] = {}

    # ---------------- 规则管理 ----------------
    def rules(self) -> List[ActivityRule]:
        return list(BUILTIN_ACTIVITIES) + list(self.custom.values())

    def define_activity(self, name: str, rule: Any, tags: Any = None,
                        room: str = "") -> Dict[str, Any]:
        """定义自定义活动；rule 支持 dict 或字符串 DSL。"""
        name = str(name or "").strip()
        if not name:
            raise ValueError("活动名称不能为空")
        if rule is None or (isinstance(rule, str) and not rule.strip()) or (
                isinstance(rule, dict) and not rule):
            raise ValueError("活动规则不能为空")
        key = name if not name.isascii() else name.lower().replace(" ", "_")
        activity = ActivityRule.from_dict(key, name, rule, tags=tags, room=room)
        if not activity.requires and not activity.any_of:
            raise ValueError("活动规则至少需要一个信号")
        self.custom[key] = activity
        return {"ok": True, "activity": activity.to_dict()}

    def select(self, activities: Any = None,
               rooms: Any = None) -> List[ActivityRule]:
        """按名称/房间筛选规则；activities 也可直接传规则定义。"""
        pool = self.rules()
        if isinstance(activities, (list, tuple)) and activities:
            picked: List[ActivityRule] = []
            for item in activities:
                if isinstance(item, ActivityRule):
                    picked.append(item)
                elif isinstance(item, dict) and ("requires" in item or "any_of" in item
                                                 or "signals" in item):
                    picked.append(ActivityRule.from_dict(
                        str(item.get("key") or item.get("name") or "custom"),
                        str(item.get("name") or item.get("key") or "自定义活动"), item))
                else:
                    text = str(item).strip()
                    picked.extend([r for r in pool
                                   if text in (r.key, r.name) or text in r.tags])
            pool = picked
        room_list = [r.strip() for r in str(rooms or "").replace("、", ",").split(",") if r.strip()]
        if room_list:
            pool = [r for r in pool if not r.room or r.room in room_list]
        return pool

    # ---------------- 推断 ----------------
    def infer(self, events: Sequence[EventRecord], tr: TimeRange,
              rooms: str = "", activities: Any = None) -> List[ActivityMatch]:
        """活动推断：按天 + 时间窗匹配信号，输出带置信度的活动片段。"""
        matches: List[ActivityMatch] = []
        by_entity: Dict[str, List[EventRecord]] = {}
        # P2：入口先排序，保证后续 session 切分/时序判定稳定（调用方可能传入乱序事件）
        for ev in sorted(events, key=lambda e: e.ts):
            by_entity.setdefault(ev.entity_id, []).append(ev)
        for rule in self.select(activities, rooms):
            signals = rule.requires + rule.any_of
            ids = [self._signal_ids(sig, rule) for sig in signals]
            for day, day_start, _day_end in split_days(tr):
                start, end = self._window_range(day_start, rule.window)
                start, end = tr.clip(start, end)
                if end <= start:
                    continue
                metrics = [self._signal_metrics(by_entity, ids[i], start, end)
                           for i in range(len(signals))]
                match = self._evaluate(rule, signals, metrics, day, start, end)
                if match:
                    matches.append(match)
        matches.sort(key=lambda m: (m.start, m.activity))
        return matches

    def _signal_ids(self, sig: Signal, rule: ActivityRule) -> List[str]:
        info = self.resolver.resolve(room=sig.room or rule.room, query=sig.query,
                                     category=sig.category, domain=sig.domain)
        return [e.entity_id for e in info]

    @staticmethod
    def _window_range(day_start: float, window: Tuple[int, int]) -> Tuple[float, float]:
        """把 (起始小时, 结束小时) 展开为时间戳区间；结束 <= 起始 表示跨天。"""
        h0, h1 = int(window[0]), int(window[1])
        start = day_start + h0 * 3600.0
        end = day_start + h1 * 3600.0
        if h1 <= h0:
            end += 86400.0
        return start, end

    def _signal_metrics(self, by_entity: Dict[str, List[EventRecord]],
                        ids: Sequence[str], start: float, end: float
                        ) -> Dict[str, Any]:
        from .service import compute_sessions  # 局部导入避免循环依赖
        wanted = set(ids)
        subset = [ev for eid in wanted for ev in by_entity.get(eid, ())
                  if start <= ev.ts < end]
        sessions = compute_sessions(
            subset, TimeRange(house_dt(start), house_dt(end)), self.config.min_session_seconds)
        flat = [s for sess in sessions.values() for s in sess]
        return {"minutes": round(sum(s.minutes for s in flat), 1),
                "count": len(flat),
                "first": min((s.start for s in flat), default=0.0),
                "last": max((s.end for s in flat), default=0.0),
                "entities": sorted(wanted & set(sessions.keys()))}

    @staticmethod
    def _evaluate(rule: ActivityRule, signals: Sequence[Signal],
                  metrics: Sequence[Dict[str, Any]], day: str,
                  start: float, end: float) -> Optional[ActivityMatch]:
        def ok(index: int) -> bool:
            sig, met = signals[index], metrics[index]
            return (met["minutes"] >= sig.min_minutes
                    and met["count"] >= (sig.min_count if sig.min_count > 0 else 0))

        # 按位置切分：前 len(requires) 个是必要信号，之后是 any_of 可选信号
        # 之前用 optional 标志切分 + 切片，导致必要信号被漏检、可选被强制要求
        n_req = len(rule.requires)
        required = [i for i in range(n_req) if not signals[i].optional]
        optional = list(range(n_req, len(signals))) +                    [i for i in range(n_req) if signals[i].optional]
        if required and not all(ok(i) for i in required):
            return None
        if optional and not any(ok(i) for i in optional):
            return None
        hit = [i for i in range(len(signals)) if ok(i)]
        minutes = max((metrics[i]["minutes"] for i in hit), default=0.0)
        if minutes < rule.min_minutes:
            return None
        first = min((metrics[i]["first"] for i in hit if metrics[i]["first"]), default=start)
        last = max((metrics[i]["last"] for i in hit if metrics[i]["last"]), default=end)
        cover = len(hit) / float(max(1, len(signals)))
        extra = min(1.0, minutes / float(max(1.0, rule.min_minutes * 2)))
        confidence = round(min(1.0, 0.55 + 0.25 * cover + 0.2 * extra), 2)
        detail = [{"room": signals[i].room or rule.room, "query": signals[i].query,
                   "category": signals[i].category, "satisfied": ok(i),
                   "minutes": metrics[i]["minutes"], "count": metrics[i]["count"],
                   "entities": metrics[i]["entities"]} for i in range(len(signals))]
        return ActivityMatch(activity=rule.key, name=rule.name, room=rule.room,
                             day=day_key(first), start=first, end=last,
                             minutes=minutes, confidence=confidence,
                             tags=list(rule.tags), signals=detail)


def analyze_rhythm(buckets: List[int], coverage: float = 0.8,
                    night_start: int = 21, night_end: int = 11) -> Dict[str, Any]:
    """从 24 小时直方图里推断作息。

    ``active_window`` 取「覆盖 ``coverage`` 比例事件的最窄环形时段」——
    用「大于均值」这类阈值法在稀疏数据上会退化成 00:00-24:00，等于没说。
    环形窗口能正确表达「22:00-次日 02:00」这种跨零点的作息。

    ``night_start`` / ``night_end`` 为夜间窗（跨天），默认对齐 InsightConfig
    的 21:00-次日 11:00；P2 之前硬编码 0-6 点与配置口径不一致。

    从旧版 InsightService._rhythm 迁移而来，行为完全一致。
    """
    total = sum(buckets)
    if not total:
        return {
            "active_window": "",
            "active_hours": [],
            "peak_hour": None,
            "quiet_hours": list(range(24)),
            "first_activity_hour": None,
            "last_activity_hour": None,
            "night_ratio_percent": 0.0,
        }

    need = total * coverage
    best: Optional[Tuple[int, int]] = None  # (length, start)
    for start in range(24):
        acc = 0
        for length in range(1, 25):
            acc += buckets[(start + length - 1) % 24]
            if acc >= need:
                if best is None or length < best[0]:
                    best = (length, start)
                break
    window = ""
    active_hours: List[int] = []
    if best:
        length, start = best
        active_hours = [(start + i) % 24 for i in range(length)]
        window = f"{start:02d}:00-{(start + length) % 24:02d}:00"

    nonzero = [h for h, v in enumerate(buckets) if v > 0]
    # P2：跨夜窗口求和（如 21:00-次日 11:00 = buckets[21:24] + buckets[0:11]）
    night_buckets: List[int] = []
    h = night_start % 24
    while h != night_end % 24:
        night_buckets.append(h)
        h = (h + 1) % 24
    night_buckets.append(night_end % 24)
    night_sum = sum(buckets[h] for h in night_buckets)
    return {
        "active_window": window,
        "active_hours": active_hours,
        "peak_hour": buckets.index(max(buckets)),
        "quiet_hours": [h for h, v in enumerate(buckets) if v == 0],
        "first_activity_hour": min(nonzero) if nonzero else None,
        "last_activity_hour": max(nonzero) if nonzero else None,
        "night_ratio_percent": round(night_sum / total * 100, 1),
    }
