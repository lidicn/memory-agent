"""数据模型定义（领域对象、枚举、IO 类型）。

除 ``house_tz()`` 里的一个函数内懒导入（``..house_time``，只为拿 homesdk 的家庭时区）
之外，本文件不依赖 insights 包内其它模块，也不在模块顶层导入包内其它模块，
被所有上层模块单向依赖，因此不会产生循环 import。

约定：
1. 跨层传递的数据结构统一在此定义，并提供 ``to_dict()``；
2. 所有对外输出的条目都带 ``friendly_name`` / ``room``（人类可读性第一）；
3. 附带少量纯函数（时间格式化、分页、时间范围）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Generic, Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")

__all__ = [
    "PAGE_DEFAULT_LIMIT", "SECONDS_PER_DAY", "MINUTES_PER_DAY",
    "now_ts", "fmt_ts", "day_key", "hour_of", "new_id", "serialize",
    "HOUSE_TZ", "HOUSE_TZ_FALLBACK_HOURS", "house_tz", "house_tz_label",
    "set_house_tz_offset",
    "house_ts", "house_now", "house_dt",
    "DeviceCategory", "AnomalyType", "Severity", "StateKind", "Intent",
    "EntityInfo", "EventRecord", "Session", "TimeRange", "Page",
    "Insight", "Anomaly", "ActivityMatch", "UsageStat", "QuestionPlan",
    "InsightConfig",
]

PAGE_DEFAULT_LIMIT = 100
SECONDS_PER_DAY = 86400
MINUTES_PER_DAY = 1440


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
#: 家庭墙钟偏移的**唯一配置项**是 ``Config.tz_offset_hours``（env ``TZ_OFFSET_HOURS``），
#: 由 ``config.get_config()`` 调 ``set_house_tz_offset()`` 注入本模块。
#: 这里的 +8 只是 fallback：生产数据是「深圳墙钟」的 naive ISO 字符串，而容器常跑在
#: UTC，若用机器本地时区做 epoch<->墙钟换算，窗口会整体平移数小时（BUG-TZ1）。
HOUSE_TZ_FALLBACK_HOURS = 8.0
HOUSE_TZ = timezone(timedelta(hours=HOUSE_TZ_FALLBACK_HOURS))


def house_tz() -> timezone:
    """当前家庭时区。

    主路径是 `homesdk.time`（契约 §四，IANA 名、能表达 DST）；homesdk 不在场、或在场
    但家里没按时区名声明时，退化为 `Config.tz_offset_hours` 注入的固定偏移——
    也就是下面那个 ``HOUSE_TZ``。注入通道必须一直有效，否则 `set_house_tz_offset()`
    和整套时区一致性测试会被库默认值盖掉。
    """
    from ..house_time import house_tz as _mechanism_house_tz, is_active

    if is_active():
        return _mechanism_house_tz()
    return HOUSE_TZ


def set_house_tz_offset(hours: Any) -> timezone:
    """把 ``Config.tz_offset_hours`` 灌进本模块——全仓墙钟口径的单点注入口。

    返回生效的 tz；非法值不改变现状（宁可留在 fallback，也不要让一次配置写错
    把整条时间轴打歪）。偏移超出真实时区范围（UTC-12 ~ UTC+14）同样按非法处理，
    因为 ``TZ_OFFSET_HOURS=80`` 这类笔误换算不报错，却会让所有时间窗整体消失。
    """
    global HOUSE_TZ
    try:
        offset = float(hours)
    except (TypeError, ValueError):
        return HOUSE_TZ
    if not (-12.0 <= offset <= 14.0):
        return HOUSE_TZ
    HOUSE_TZ = timezone(timedelta(hours=offset))
    return HOUSE_TZ


def house_tz_label() -> str:
    """墙钟口径的可读标签（只用于回显/meta，不参与换算）。

    homesdk 在场时直接报 IANA 名；退化路径下默认口径沿用「Asia/Shanghai」这个大家
    看得懂的名字，被注入成别的偏移时如实写成 UTC±h，免得对外宣称深圳时间、其实按别的钟面切窗口。
    """
    tz = house_tz()
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    hours = datetime.now(tz).utcoffset() / timedelta(hours=1)
    if hours == HOUSE_TZ_FALLBACK_HOURS:
        return "Asia/Shanghai"
    return f"UTC{hours:+g}"


def house_ts(dt: Any) -> float:
    """naive 墙钟 -> epoch（按家庭墙钟解释，不依赖机器时区）。"""
    if isinstance(dt, (int, float)):
        return float(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=house_tz()).timestamp()
    return dt.timestamp()


def house_now() -> datetime:
    """当前家庭墙钟时间（naive，家庭时区口径）。"""
    return datetime.now(house_tz()).replace(tzinfo=None, microsecond=0)


def house_dt(ts: float) -> datetime:
    """epoch -> 家庭墙钟 naive datetime。"""
    return datetime.fromtimestamp(float(ts), tz=house_tz()).replace(tzinfo=None)


def now_ts() -> float:
    """当前时间戳（秒）。"""
    return time.time()


def fmt_ts(ts: Optional[float]) -> str:
    """时间戳 -> 'YYYY-MM-DD HH:MM:SS'（家庭墙钟），空值返回空串。"""
    if ts is None:
        return ""
    try:
        return house_dt(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def day_key(ts: Optional[float]) -> str:
    """时间戳 -> 'YYYY-MM-DD'（家庭墙钟）。"""
    if ts is None:
        return ""
    return house_dt(float(ts)).strftime("%Y-%m-%d")


def hour_of(ts: float) -> int:
    """时间戳 -> 小时（0-23，家庭墙钟）。"""
    return house_dt(float(ts)).hour


def new_id(prefix: str = "ins") -> str:
    """生成短 ID。"""
    return "%s-%s" % (prefix, uuid.uuid4().hex[:8])


def serialize(item: Any) -> Any:
    """统一序列化：有 ``to_dict`` 的对象调用之，其余原样返回。"""
    to_dict = getattr(item, "to_dict", None)
    return to_dict() if callable(to_dict) else item


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------
class DeviceCategory(str, Enum):
    """设备类别。"""

    CLIMATE = "climate"
    LIGHTING = "lighting"
    MEDIA = "media"
    PRESENCE = "presence"
    APPLIANCE = "appliance"
    SECURITY = "security"
    TELEMETRY = "telemetry"
    OTHER = "other"

    @classmethod
    def coerce(cls, value: Any) -> "DeviceCategory":
        """宽容转换，未知值归为 OTHER。"""
        text = str(value or "").strip().lower()
        for item in cls:
            if item.value == text:
                return item
        return cls.OTHER


class AnomalyType(str, Enum):
    """异常类型。"""

    OFFLINE = "offline"              # 设备离线（超过 N 天无数据）
    FLAPPING = "flapping"            # 状态反复（短时间频繁切换）
    DURATION_SPIKE = "duration_spike"  # 使用时长异常
    MISSING_DATA = "missing_data"    # 数据缺失 / 数据空洞
    UNIT_CONFLICT = "unit_conflict"  # 单位冲突
    NOISE_SOURCE = "noise_source"    # 噪声源（单一设备占比 > 60%）


class Severity(str, Enum):
    """严重级别。"""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StateKind(str, Enum):
    """状态归一化结果。"""

    ON = "on"
    OFF = "off"
    OTHER = "other"


class Intent(str, Enum):
    """自然语言查询意图。"""

    DEVICE_USAGE = "device_usage"
    BEHAVIOR = "behavior"
    ANOMALY = "anomaly"
    RHYTHM = "rhythm"
    ACTIVITY = "activity"
    PERSONA = "persona"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# 领域对象
# --------------------------------------------------------------------------
@dataclass
class EntityInfo:
    """实体目录条目。"""

    entity_id: str
    friendly_name: str = ""
    room: str = ""
    domain: str = ""
    category: str = ""
    unit: str = ""
    enabled: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """人类可读名称（优先 friendly_name）。"""
        return self.friendly_name or self.entity_id

    @property
    def display(self) -> str:
        """带房间的展示名，如 '客厅/客厅空调'。"""
        return "%s/%s" % (self.room, self.label) if self.room else self.label

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "friendly_name": self.friendly_name,
            "room": self.room,
            "domain": self.domain,
            "category": self.category,
            "unit": self.unit,
            "enabled": self.enabled,
            "label": self.label,
            "display": self.display,
        }


@dataclass
class EventRecord:
    """单条事件。"""

    ts: float
    entity_id: str
    state: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    friendly_name: str = ""
    room: str = ""
    domain: str = ""
    unit: str = ""

    @property
    def dt(self) -> datetime:
        """家庭墙钟的 naive datetime——与同一条记录的 `day` / `hour` 共用一条时钟。"""
        return house_dt(float(self.ts))

    @property
    def day(self) -> str:
        return day_key(self.ts)

    @property
    def hour(self) -> int:
        return hour_of(self.ts)

    @property
    def label(self) -> str:
        return self.friendly_name or self.entity_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "time": fmt_ts(self.ts),
            "day": self.day,
            "hour": self.hour,
            "entity_id": self.entity_id,
            "friendly_name": self.friendly_name,
            "room": self.room,
            "domain": self.domain,
            "state": self.state,
            "unit": self.unit,
            "attributes": dict(self.attributes or {}),
        }


@dataclass
class Session:
    """一段「开启 -> 关闭」的使用会话。"""

    entity_id: str
    start: float
    end: float
    friendly_name: str = ""
    room: str = ""
    domain: str = ""
    open: bool = False  # 截止窗口结束仍未关闭

    @property
    def minutes(self) -> float:
        return round(max(0.0, (self.end - self.start) / 60.0), 1)

    @property
    def day(self) -> str:
        return day_key(self.start)

    @property
    def label(self) -> str:
        return self.friendly_name or self.entity_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "friendly_name": self.friendly_name,
            "room": self.room,
            "domain": self.domain,
            "start": fmt_ts(self.start),
            "end": fmt_ts(self.end),
            "start_ts": self.start,
            "end_ts": self.end,
            "day": self.day,
            "minutes": self.minutes,
            "open": self.open,
        }


@dataclass
class TimeRange:
    """时间范围（本地时间）。"""

    start: datetime
    end: datetime
    label: str = ""

    @property
    def start_ts(self) -> float:
        return house_ts(self.start)

    @property
    def end_ts(self) -> float:
        return house_ts(self.end)

    @property
    def days(self) -> float:
        return round(max(0.0, (self.end_ts - self.start_ts) / SECONDS_PER_DAY), 2)

    @property
    def hours(self) -> float:
        return round(max(0.0, (self.end_ts - self.start_ts) / 3600.0), 2)

    def contains(self, ts: float) -> bool:
        return self.start_ts <= ts <= self.end_ts

    def clip_ts(self, ts: float) -> float:
        """时间戳裁剪到范围内（时间范围裁剪）。"""
        return min(max(ts, self.start_ts), self.end_ts)

    def clip(self, start: float, end: float) -> Tuple[float, float]:
        """区间裁剪到范围内，返回 (start, end)。"""
        s = max(start, self.start_ts)
        e = min(end, self.end_ts)
        return (s, e)

    def shift(self, days: float) -> "TimeRange":
        """整体前移 N 天（用于环比对比窗口）。"""
        delta = timedelta(days=days)
        return TimeRange(self.start - delta, self.end - delta, self.label)

    def split_days(self) -> List[Tuple[str, float, float]]:
        """按天切分：[( 'YYYY-MM-DD', start_ts, end_ts ), ...]（已裁剪）。

        日界必须按**家庭墙钟**取。原先用 `datetime.fromtimestamp(ts)`（机器本地时区），
        容器跑 UTC、家庭墙钟 +8 时，日界落在 UTC 零点 = 家庭 08:00，跨天活动会被
        劈成两段、`day_key()` 又按家庭墙钟归日，两侧口径不一致。
        """
        out: List[Tuple[str, float, float]] = []
        cur = house_dt(self.start_ts).replace(hour=0, minute=0, second=0, microsecond=0)
        while house_ts(cur) < self.end_ts:
            nxt = cur + timedelta(days=1)
            s, e = self.clip(house_ts(cur), house_ts(nxt))
            if e > s:
                out.append((cur.strftime("%Y-%m-%d"), s, e))
            cur = nxt
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": fmt_ts(self.start_ts),
            "end": fmt_ts(self.end_ts),
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "days": self.days,
            "label": self.label,
        }


@dataclass
class Page(Generic[T]):
    """统一的分页信封：调用方能明确知道自己有没有取全。"""

    items: List[T] = field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = PAGE_DEFAULT_LIMIT
    has_more: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(cls, items: Iterable[T], offset: int = 0,
              limit: Optional[int] = None, **extra: Any) -> "Page[T]":
        seq = list(items or [])
        total = len(seq)
        off = max(0, int(offset or 0))
        lim = total if limit is None else max(0, int(limit))
        page_items = seq[off:off + lim] if lim else []
        return cls(
            items=page_items,
            total=total,
            offset=off,
            limit=lim,
            has_more=(off + len(page_items)) < total,
            extra=dict(extra),
        )

    def to_dict(self, key: str = "items") -> Dict[str, Any]:
        data: Dict[str, Any] = {
            key: [serialize(i) for i in self.items],
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
            "has_more": self.has_more,
        }
        data.update(self.extra)
        return data


@dataclass
class UsageStat:
    """单个聚合维度的使用统计。"""

    key: str
    label: str = ""
    room: str = ""
    entity_id: str = ""
    domain: str = ""
    category: str = ""
    sessions: int = 0
    on_count: int = 0
    active_minutes: float = 0.0
    avg_session_minutes: float = 0.0
    days_active: int = 0
    event_count: int = 0
    entity_count: int = 1
    first: str = ""
    last: str = ""
    unit: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "friendly_name": self.label,
            "room": self.room,
            "entity_id": self.entity_id,
            "domain": self.domain,
            "category": self.category,
            "sessions": self.sessions,
            "on_count": self.on_count,
            "active_minutes": self.active_minutes,
            "duration_minutes": self.active_minutes,
            "avg_session_minutes": self.avg_session_minutes,
            "days_active": self.days_active,
            "event_count": self.event_count,
            "entity_count": self.entity_count,
            "first": self.first,
            "last": self.last,
            "unit": self.unit,
        }


@dataclass
class Insight:
    """一条行为洞察。"""

    insight_id: str
    type: str
    title: str
    detail: str = ""
    room: str = ""
    entity_id: str = ""
    friendly_name: str = ""
    score: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "insight_id": self.insight_id,
            "type": self.type,
            "title": self.title,
            "detail": self.detail,
            "room": self.room,
            "entity_id": self.entity_id,
            "friendly_name": self.friendly_name,
            "score": self.score,
            "data": dict(self.data),
            "explanation": self.explanation,
        }


@dataclass
class Anomaly:
    """一条异常记录。"""

    type: str
    title: str
    detail: str = ""
    entity_id: str = ""
    friendly_name: str = ""
    room: str = ""
    severity: str = Severity.MEDIUM.value
    value: Optional[float] = None
    expected: Optional[float] = None
    ts: Optional[float] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type if isinstance(self.type, str) else self.type.value,
            "title": self.title,
            "detail": self.detail,
            "entity_id": self.entity_id,
            "friendly_name": self.friendly_name,
            "room": self.room,
            "severity": self.severity if isinstance(self.severity, str) else self.severity.value,
            "value": self.value,
            "expected": self.expected,
            "ts": self.ts,
            "time": fmt_ts(self.ts) if self.ts else "",
            "data": dict(self.data),
        }


@dataclass
class ActivityMatch:
    """一次活动推断结果。"""

    activity: str
    name: str
    room: str = ""
    day: str = ""
    start: float = 0.0
    end: float = 0.0
    minutes: float = 0.0
    confidence: float = 0.0
    tags: List[str] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "builtin"          # builtin | custom | activity_rules
    window: Tuple[int, int] = (0, 24)  # 规则声明的时段（跨天口径见 ActivityRule.window）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "activity": self.activity,
            "name": self.name,
            "room": self.room,
            "day": self.day,
            "start": fmt_ts(self.start),
            "end": fmt_ts(self.end),
            "start_ts": self.start,
            "end_ts": self.end,
            "minutes": self.minutes,
            "duration_minutes": self.minutes,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "signals": list(self.signals),
            "source": self.source,
            "window": list(self.window),
        }


@dataclass
class QuestionPlan:
    """自然语言问题的执行计划。"""

    question: str
    intent: str = Intent.UNKNOWN.value
    route: str = "auto"
    room: str = ""
    category: str = ""
    query: str = ""
    entity_ids: List[str] = field(default_factory=list)
    days: int = 7
    activity: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    hints: List[str] = field(default_factory=list)
    time_range: Optional[TimeRange] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent,
            "route": self.route,
            "room": self.room,
            "category": self.category,
            "query": self.query,
            "entity_ids": list(self.entity_ids),
            "days": self.days,
            "activity": self.activity,
            "params": dict(self.params),
            "hints": list(self.hints),
            "time_range": self.time_range.to_dict() if self.time_range else None,
        }


@dataclass
class InsightConfig:
    """全局配置（可被调用方覆盖）。"""

    cache_ttl: int = 300                  # 缓存 5 分钟
    noise_ratio_cap: float = 0.6          # 单设备占比 > 60% 视为噪声源
    offline_days: int = 3                 # 超过 N 天无数据视为离线
    flapping_window: int = 600            # 状态反复检测窗口（秒）
    flapping_count: int = 6               # 窗口内切换 >= N 次视为反复
    missing_gap_factor: float = 3.0       # 空洞 = 中位间隔 * 系数
    missing_gap_min: float = 1800.0       # 至少 30 分钟才算空洞
    spike_factor: float = 3.0             # 时长异常 = 中位时长 * 系数
    spike_min_minutes: float = 30.0
    default_days: int = 7
    default_limit: int = PAGE_DEFAULT_LIMIT
    max_limit: int = 5000
    max_scan: int = 30000                 # 单次最多扫描的事件数
    min_session_seconds: float = 1.0      # 小于该长度的会话忽略（去抖）
    night_start: int = 21                 # 作息分析：夜窗起点
    night_end: int = 11                   # 作息分析：夜窗终点
    wake_from: int = 4                    # 起床候选小时下界
    wake_to: int = 12
    sleep_from: int = 18                  # 入睡候选小时下界
    exclude_telemetry: bool = True        # 默认干净：排除纯遥测

    def clamp_limit(self, limit: Any) -> int:
        """把 limit 裁剪到 [0, max_limit]，非法值回退默认值。"""
        try:
            value = int(limit)
        except (TypeError, ValueError):
            value = self.default_limit
        return max(0, min(value, self.max_limit))
