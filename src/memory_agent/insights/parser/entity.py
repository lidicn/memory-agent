"""实体语义解析：关键词 -> domain，房间/类别/关键词 -> 实体列表。

核心目标（人类可读性第一）：
调用方写「客厅 + 空调」，不需要背
``binary_sensor.linp_cn_1005393053_hb01_occupancy_status_p_2_1`` 这种 ID。
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..models import DeviceCategory, EntityInfo, EventRecord, StateKind

__all__ = [
    "CATEGORY_DOMAINS", "KEYWORD_DOMAINS", "OFF_STATES", "CN_OFF_STATES",
    "CN_ON_STATES", "EXTRA_ON_STATES", "EXTRA_OFF_STATES", "ON_STATES",
    "TELEMETRY_KEYWORDS", "ROOM_ALIASES",
    "domain_of", "category_of_domain", "all_domains", "domains_for",
    "normalize_state", "is_on", "is_off", "is_telemetry_name", "is_telemetry_info",
    "EntityResolver",
]

# 设备类别 -> domain 映射（固定契约，勿随意改动）
CATEGORY_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "climate": ("climate", "fan", "humidifier", "water_heater"),
    "lighting": ("light",),
    "media": ("media_player",),
    "presence": ("binary_sensor", "device_tracker", "person"),
    "appliance": ("switch", "vacuum", "input_boolean"),
    "security": ("lock", "cover", "alarm_control_panel", "camera"),
    "telemetry": ("sensor", "number"),
}

# 关键词 -> domain 映射（中英文）
KEYWORD_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "空调": ("climate",), "冷气": ("climate",), "制冷": ("climate",),
    "暖气": ("climate",), "地暖": ("climate",), "ac": ("climate",),
    "风扇": ("fan",), "新风": ("fan",), "加湿": ("humidifier",),
    "热水器": ("water_heater",), "灯": ("light",), "照明": ("light",),
    "light": ("light",), "电视": ("media_player",), "音箱": ("media_player",),
    "媒体": ("media_player",), "播放": ("media_player",), "tv": ("media_player",),
    "存在": ("binary_sensor",), "人体": ("binary_sensor",), "有人": ("binary_sensor",),
    "occupancy": ("binary_sensor",), "motion": ("binary_sensor",),
    "门": ("binary_sensor", "lock", "cover"), "窗帘": ("cover",),
    "门锁": ("lock",), "插座": ("switch",), "开关": ("switch",),
    "扫地": ("vacuum",), "温度": ("sensor",), "湿度": ("sensor",),
    "功率": ("sensor",), "电量": ("sensor",),
}

# 状态归一化：OFF 状态（契约常量）
OFF_STATES = frozenset({"off", "closed", "not_home", "unavailable", "unknown",
                        "idle", "standby", "none", ""})
CN_OFF_STATES = frozenset({"关", "关闭", "门关", "闭合", "断开", "无", "否", "0"})
CN_ON_STATES = frozenset({"开", "打开", "门开", "开启", "接通", "有", "是", "1"})

# 补充集合（契约常量之外的常见 HA 状态，只做加法，不改上面三个常量）
EXTRA_ON_STATES = frozenset({
    "on", "open", "opened", "playing", "cool", "heat", "heat_cool", "auto",
    "fan_only", "dry", "cleaning", "home", "locked", "active", "motion",
    "detected", "occupied", "true", "high", "wet", "moving", "busy", "heating",
})
EXTRA_OFF_STATES = frozenset({
    "clear", "no_motion", "unoccupied", "unlocked", "disconnected", "false",
    "low", "done", "clean", "paused",
})
ON_STATES = CN_ON_STATES | EXTRA_ON_STATES

# 纯遥测命名特征：默认从行为分析中排除，否则小时分布会被拍成均匀「电表节拍」
TELEMETRY_KEYWORDS: Tuple[str, ...] = (
    "功率", "温度", "湿度", "电量", "电压", "电流", "能耗", "气压", "pm25", "pm2.5",
    "co2", "甲醛", "噪声", "信号强度", "battery",
    "power", "temp", "humid", "energy", "kwh", "voltage", "current", "pressure",
    "battery", "rssi", "signal",
)

# 房间别名 -> 标准房间（命中标准房间时才生效）
ROOM_ALIASES: Dict[str, str] = {
    "大厅": "客厅", "起居室": "客厅", "客厅里": "客厅",
    "睡房": "卧室", "主卧": "卧室", "寝室": "卧室",
    "洗手间": "卫生间", "浴室": "卫生间", "厕所": "卫生间", "卫": "卫生间",
    "做饭": "厨房", "灶房": "厨房",
    "门口": "玄关", "大门": "玄关",
}


def domain_of(entity_id: str) -> str:
    """从 entity_id 推断 domain。"""
    return (entity_id or "").split(".", 1)[0].strip().lower()


def category_of_domain(domain: str) -> str:
    """domain -> 设备类别。"""
    dom = (domain or "").lower()
    for cat, doms in CATEGORY_DOMAINS.items():
        if dom in doms:
            return cat
    return DeviceCategory.OTHER.value


def all_domains() -> List[str]:
    """全部已知 domain。"""
    seen: List[str] = []
    for doms in CATEGORY_DOMAINS.values():
        for dom in doms:
            if dom not in seen:
                seen.append(dom)
    return seen


def domains_for(category: str = "", domain: str = "", query: str = "") -> List[str]:
    """根据类别 / domain / 关键词解析出 domain 列表。

    规则：
    - 三者皆空 -> 返回全部 domain；
    - query 命中关键词 -> 返回关键词映射的 domain；
    - query 非空但未命中关键词 -> 返回 []，表示「不限制 domain，改用名称匹配」。
    """
    out: List[str] = []
    cat = (category or "").strip().lower()
    if cat and cat in CATEGORY_DOMAINS:
        out.extend(CATEGORY_DOMAINS[cat])
    dom = (domain or "").strip().lower()
    if dom and dom not in out:
        out.append(dom)
    text = (query or "").strip().lower()
    if text:
        hit = False
        for word, doms in KEYWORD_DOMAINS.items():
            if word in text:
                hit = True
                for d in doms:
                    if d not in out:
                        out.append(d)
        return out if hit else []
    return out or all_domains()


def normalize_state(state: Any) -> str:
    """状态归一化 -> 'on' / 'off' / 'other'。"""
    text = str(state if state is not None else "").strip().lower()
    # unavailable / unknown 表示实体失联或状态未知，不是"关"
    # （P2：之前归 off 会把离线期当成"关着"，flapping/时长统计失真）
    if text in ("unavailable", "unknown"):
        return StateKind.OTHER.value
    # 纯数值（遥测值如 0°C / 1 lux）不是开关状态；
    # 否则 sensor 的 state="0"/"1" 会被 CN_OFF/ON_STATES 误判为 off/on
    if text:
        try:
            float(text)
            return StateKind.OTHER.value
        except (TypeError, ValueError):
            pass
    if text in OFF_STATES or str(state).strip() in CN_OFF_STATES:
        return StateKind.OFF.value
    if text in EXTRA_ON_STATES or str(state).strip() in CN_ON_STATES:
        return StateKind.ON.value
    return StateKind.OTHER.value


def is_on(state: Any) -> bool:
    return normalize_state(state) == StateKind.ON.value


def is_off(state: Any) -> bool:
    return normalize_state(state) == StateKind.OFF.value


def is_telemetry_name(*names: str) -> bool:
    """名称（friendly_name 或 entity_id）是否属于纯遥测。"""
    blob = " ".join(str(n or "").lower() for n in names)
    return any(key in blob for key in TELEMETRY_KEYWORDS)


def is_telemetry_info(info: EntityInfo) -> bool:
    """实体是否属于纯遥测（功率/温湿度这类每分钟一条的数据）。"""
    if info.category == DeviceCategory.TELEMETRY.value:
        return True
    return is_telemetry_name(info.friendly_name, info.entity_id)


class EntityResolver:
    """实体语义解析器：房间名 + 设备类别 + 关键词 -> 实体列表。"""

    def __init__(self, catalog: Optional[Sequence[EntityInfo]] = None) -> None:
        self._items: List[EntityInfo] = []
        self.refresh(catalog or [])

    # ---------------- 目录维护 ----------------
    def refresh(self, catalog: Sequence[EntityInfo]) -> None:
        """重建目录（补齐 domain / category）。"""
        self._items = [self._enrich(e) for e in (catalog or [])]

    @staticmethod
    def _enrich(info: EntityInfo) -> EntityInfo:
        if not info.domain:
            info.domain = domain_of(info.entity_id)
        if not info.category:
            info.category = category_of_domain(info.domain)
        return info

    def empty(self) -> bool:
        return not self._items

    def all(self, only_enabled: bool = True) -> List[EntityInfo]:
        return [e for e in self._items if e.enabled] if only_enabled else list(self._items)

    def rooms(self, only_enabled: bool = True) -> List[str]:
        """房间名称列表（去重、稳定排序）。"""
        seen = {e.room for e in self.all(only_enabled) if e.room}
        return sorted(seen)

    def entity(self, entity_id: str) -> Optional[EntityInfo]:
        for item in self._items:
            if item.entity_id == entity_id:
                return item
        return None

    def meta(self, entity_id: str) -> EntityInfo:
        """取实体元信息；缺失时按 entity_id 兜底构造（不抛异常）。"""
        return self.entity(entity_id) or self._enrich(EntityInfo(entity_id=entity_id))

    # ---------------- 房间解析 ----------------
    def resolve_room_in_text(self, text: str, rooms: Optional[Sequence[str]] = None,
                             ) -> Optional[str]:
        """从文本中解析房间名（最长优先，支持别名）。"""
        blob = str(text or "")
        pool = list(rooms) if rooms is not None else self.rooms()
        if not blob.strip() or not pool:
            return None
        for name in sorted(pool, key=len, reverse=True):
            if name and name in blob:
                return name
        for alias, canonical in sorted(ROOM_ALIASES.items(), key=lambda kv: -len(kv[0])):
            if alias in blob and (not pool or canonical in pool):
                return canonical
        return None

    @staticmethod
    def _strip_room(text: str, room: str) -> str:
        out = str(text or "")
        tokens = {room} | {a for a, c in ROOM_ALIASES.items() if c == room}
        # P2：必须按长度降序替换，否则短别名（如"卫"）会先把长别名
        # （"卫生间"）里的字符拆碎，导致长别名再也匹配不上
        for token in sorted((t for t in tokens if t), key=len, reverse=True):
            out = out.replace(token, " ")
        return " ".join(out.split())

    def split_room_from_query(self, room: str, query: str) -> Tuple[str, str]:
        """从查询中分离房间，返回 (room, query)。"""
        room = (room or "").strip()
        query = str(query or "")
        if room:
            return room, self._strip_room(query, room)
        found = self.resolve_room_in_text(query)
        if not found:
            return "", query.strip()
        return found, self._strip_room(query, found)

    # ---------------- 实体解析 ----------------
    def resolve(self, room: str = "", category: str = "", query: str = "",
                domain: str = "", only_enabled: bool = True) -> List[EntityInfo]:
        """按 房间 + 类别 + 关键词 + domain 解析实体列表。"""
        pool = self.all(only_enabled)
        room = (room or "").strip()
        if room:
            pool = [e for e in pool if e.room == room or room in (e.room or "")]
        doms = domains_for(category=category, domain=domain, query=query)
        if doms:
            pool = [e for e in pool if e.domain in doms]
        text = (query or "").strip().lower()
        if text and not any(word in text for word in KEYWORD_DOMAINS):
            pool = [e for e in pool
                    if text in (e.friendly_name or "").lower()
                    or text in (e.entity_id or "").lower()
                    or text in (e.label or "").lower()]
        return pool

    def resolve_ids(self, room: str = "", category: str = "", query: str = "",
                    domain: str = "", only_enabled: bool = True) -> List[str]:
        return [e.entity_id for e in self.resolve(
            room=room, category=category, query=query,
            domain=domain, only_enabled=only_enabled)]

    def behavior_entities(self, only_enabled: bool = True) -> List[EntityInfo]:
        """行为相关实体（默认排除功率/温湿度这类纯遥测）。"""
        return [e for e in self.all(only_enabled) if not is_telemetry_info(e)]

    def telemetry_entities(self, only_enabled: bool = True) -> List[EntityInfo]:
        return [e for e in self.all(only_enabled) if is_telemetry_info(e)]

    def is_telemetry(self, entity_id: str) -> bool:
        info = self.entity(entity_id)
        return is_telemetry_info(info) if info else is_telemetry_name(entity_id)

    def enrich(self, event: EventRecord) -> EventRecord:
        """给事件补齐 friendly_name / room / domain（返回同一对象）。"""
        info = self.entity(event.entity_id)
        if info:
            event.friendly_name = event.friendly_name or info.friendly_name
            event.room = event.room or info.room
            event.domain = event.domain or info.domain
            event.unit = event.unit or info.unit
        if not event.domain:
            event.domain = domain_of(event.entity_id)
        return event
