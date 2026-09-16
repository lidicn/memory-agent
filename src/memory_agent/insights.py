"""行为洞察服务 —— 把「事件数据层」升级为「行为洞察层」

设计原则（来自真实使用反馈）
--------------------------
1. **人类可读性第一**：调用方不应该背 ``binary_sensor.linp_cn_1005393053_hb01_occupancy_status_p_2_1``
   这种 ID。所有入口都支持「房间名 + 设备类别 + 关键词」的语义定位，
   返回结果一律带上 ``friendly_name`` / ``room``。
2. **服务端算好再给**：时长、开关次数、作息、异常都在 SQL/Python 侧算完，
   不把上万条原始事件 dump 给调用方硬算。
3. **默认干净**：功率/温湿度这类每分钟一条的纯遥测默认排除，
   否则小时分布会被拍成均匀的「电表节拍」，完全掩盖真实作息。
4. **透明**：每个查询都返回 ``total`` / ``offset`` / ``has_more``，
   调用方能明确知道自己有没有取全。
"""

from __future__ import annotations

import copy
import json
import re
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from .store import TELEMETRY_DOMAINS, Store, now_local, parse_ts

# 单实体事件数超过房间事件数该比例即视为「噪声源」，从行为/房间使用率中剔除
# （仍保留在设备健康异常检测里）。取值偏保守：真实人类活动通常分散在多个设备，
# 单一设备占比 >60% 几乎必然是报警抖动/状态反复（如加湿器缺水）。
NOISE_RATIO_CAP = 0.6

# ── 语义词典 ────────────────────────────────────────────────────────────────

#: 设备类别 → HA domain 集合。让调用方能用「空调 / 灯 / 媒体」这类人话过滤。
CATEGORY_DOMAINS: dict[str, tuple[str, ...]] = {
    "climate": ("climate", "fan", "humidifier", "water_heater"),
    "lighting": ("light",),
    "media": ("media_player",),
    "presence": ("binary_sensor", "device_tracker", "person"),
    "appliance": ("switch", "vacuum", "input_boolean"),
    "security": ("lock", "cover", "alarm_control_panel", "camera"),
    "telemetry": ("sensor", "number"),
}

#: 中文/英文关键词 → domain。用于 ``query="主卧空调"`` 这类自由文本解析。
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
    "热水器": ("water_heater",),
    "灯": ("light",),
    "照明": ("light",),
    "light": ("light",),
    "电视": ("media_player",),
    "音箱": ("media_player",),
    "媒体": ("media_player",),
    "播放": ("media_player",),
    "tv": ("media_player",),
    "存在": ("binary_sensor",),
    "人体": ("binary_sensor",),
    "有人": ("binary_sensor",),
    "occupancy": ("binary_sensor",),
    "motion": ("binary_sensor",),
    "门": ("binary_sensor", "lock", "cover"),
    "窗帘": ("cover",),
    "门锁": ("lock",),
    "插座": ("switch",),
    "开关": ("switch",),
    "扫地": ("vacuum",),
    "温度": ("sensor",),
    "湿度": ("sensor",),
    "功率": ("sensor",),
    "电量": ("sensor",),
}

#: 视为「关闭 / 不可用」的状态值。其余一律视为「开启」。
#: climate 的 ``heat`` / ``cool``、media_player 的 ``playing`` 都会被正确判为开启。
OFF_STATES: frozenset[str] = frozenset(
    {"off", "closed", "not_home", "unavailable", "unknown", "idle", "standby", "none", ""}
)

# 中文环境下常见的「关闭/开启」状态词（门窗传感器、开关等可能上报中文状态）
_CN_OFF_STATES: frozenset[str] = frozenset({"关", "关闭", "门关", "闭合", "断开", "无", "否", "0"})
_CN_ON_STATES: frozenset[str] = frozenset({"开", "打开", "门开", "开启", "接通", "有", "是", "1"})


def _state_is_off(state: Any) -> bool:
    s = _norm(state)
    return s in OFF_STATES or s in _CN_OFF_STATES


def _state_is_on(state: Any) -> bool:
    s = _norm(state)
    return s in _CN_ON_STATES

#: 抖动阈值：短于该秒数的开启片段视为误触，不计入时长统计。
DEFAULT_DEBOUNCE_SECONDS = 5

#: 「查所有房间」的汇总意图词。只有命中这些词才把问题当成跨房间汇总，
#: 否则一律按「精确到某个 area」处理 —— 这是「房间」既是通用名词
#: 又是真实区域名时最关键的一条判据。
ROOM_AGGREGATE_WORDS: tuple[str, ...] = (
    "所有房间", "每个房间", "各个房间", "全部房间", "所有区域", "每个区域",
    "所有的房间", "全屋", "整个家", "全家", "家里所有", "各房间",
)

#: 既是日常通用名词、又可能被用户拿来当 area 名的词。
#: 命中时需要在返回里显式消歧，避免模型把它当泛称。
GENERIC_ROOM_WORDS: frozenset[str] = frozenset({"房间", "卧室", "屋子", "房子", "家里"})


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def _tokens(text: str) -> list[str]:
    """粗分词：中文按连续汉字块 + 英文按单词切。够用且零依赖。"""
    return [t for t in re.split(r"[\s,，、_./|-]+", _norm(text)) if t]


def fmt_duration(total_seconds: float) -> str:
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


class InsightService:
    """语义定位 + 服务端聚合。所有 MCP「洞察类」工具都走这里。"""

    def __init__(self, config, store: Store):
        self.config = config
        self.store = store
        # 审计 I1：结果缓存。infer_activities / get_behavior_insights 属全窗口重扫描，
        # 同一窗口短时间内重复调用（Agent 连续问 / 环比内部 cur+prev）直接复用，
        # 避免重复全表扫描。TTL 默认 60s；进程内状态，容器重启清零。
        self._result_cache: dict = {}
        self._CACHE_TTL = float(getattr(config, "insight_cache_ttl", 60.0) or 60.0)

    def _cache_get(self, key):
        ent = self._result_cache.get(key)
        if not ent:
            return None
        ts, val = ent
        if time.monotonic() - ts > self._CACHE_TTL:
            self._result_cache.pop(key, None)
            return None
        return copy.deepcopy(val)

    def _cache_put(self, key, val) -> None:
        self._result_cache[key] = (time.monotonic(), copy.deepcopy(val))
        # 简单容量上限：超过 64 条清理最旧，避免长期驻留无限增长。
        if len(self._result_cache) > 64:
            oldest = min(self._result_cache, key=lambda k: self._result_cache[k][0])
            self._result_cache.pop(oldest, None)

    # ── 时间语义（统一 days / start / end）────────────────────────────────

    @property
    def tz(self) -> float:
        return getattr(self.config, "tz_offset_hours", 8)

    def resolve_range(
        self, days: int = 7, start: str = "", end: str = ""
    ) -> tuple[str, str, dict]:
        """统一时间语义：``days`` 与 ``start/end`` 二选一，``days`` 优先级更低。

        返回 ``(start_iso, end_iso, meta)``，``meta`` 里带上时区说明，
        让调用方不必猜「这个时间到底是 UTC 还是本地」。
        """
        now = now_local(self.tz)
        end_dt = self._parse(end) or now
        if start:
            start_dt = self._parse(start) or (end_dt - timedelta(days=7))
        else:
            span = max(1, min(int(days or 7), 365))
            start_dt = end_dt - timedelta(days=span)
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        meta = {
            "start": start_dt.isoformat(sep="T"),
            "end": end_dt.isoformat(sep="T"),
            "timezone": f"UTC{self.tz:+g}",
            "days": round((end_dt - start_dt).total_seconds() / 86400, 2),
        }
        return meta["start"], meta["end"], meta

    def _parse(self, raw: str) -> datetime | None:
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

            dt = dt.astimezone(timezone(timedelta(hours=self.tz))).replace(tzinfo=None)
        return dt.replace(microsecond=0)

    # ── 设备目录 ──────────────────────────────────────────────────────────

    def _config_entities(self, only_enabled: bool = True) -> list[dict]:
        """从配置展开实体清单（房间 → 实体）。"""
        out: list[dict] = []
        for room, payload in (self.config.rooms or {}).items():
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
                out.append(
                    {
                        "entity_id": entity_id,
                        "friendly_name": info.get("name") or "",
                        "room": room,
                        "domain": domain,
                        "category": self.category_of(domain),
                        "enabled": bool(info.get("enabled", True)) and room_enabled,
                    }
                )
        return out

    @staticmethod
    def category_of(domain: str) -> str:
        d = _norm(domain)
        for cat, domains in CATEGORY_DOMAINS.items():
            if d in domains:
                return cat
        return "other"

    def entity_catalog(
        self,
        room: str = "",
        category: str = "",
        domain: str = "",
        query: str = "",
        only_enabled: bool = True,
        days: int = 7,
    ) -> dict:
        """设备目录：友好名 + 房间 + 类别 + 最后在线 + 近期活跃度。

        这是解决「实体 ID 反人类」的入口 —— 先在这里用人话找到设备，
        再把返回的 ``entity_id`` 交给 ``get_device_usage`` 等精确工具。
        """
        items = self._config_entities(only_enabled)
        items = self._apply_semantic_filter(items, room, category, domain, query)

        start, end, meta = self.resolve_range(days)
        last_seen = self.store.entity_last_seen()
        counts = self.store.entity_event_counts(start, end)
        now = now_local(self.tz)

        by_room: dict[str, list[dict]] = {}
        for it in items:
            eid = it["entity_id"]
            seen = last_seen.get(eid, "")
            stale_days = None
            if seen:
                seen_dt = self._parse(seen)
                if seen_dt:
                    stale_days = round((now - seen_dt).total_seconds() / 86400, 1)
            enriched = {
                **it,
                "display_name": it["friendly_name"] or self._fallback_name(eid),
                "last_seen": seen,
                "stale_days": stale_days,
                "events_in_window": counts.get(eid, 0),
                "has_data": bool(seen),
            }
            by_room.setdefault(it["room"], []).append(enriched)

        for entities in by_room.values():
            entities.sort(key=lambda e: (-e["events_in_window"], e["display_name"]))

        # 去重：同一物理设备常被多个集成重复上报（网关 + 厂商集成各一条）。
        # 上一版用「房间+域+友好名」做键，但两条记录的友好名往往不同
        # （"书房门窗" vs "书房门窗传感器 接触状态"），于是 duplicates_merged 恒为 0。
        # 现改为「房间 + 域 + 能力后缀归一」，例如：
        #   binary_sensor.e4aaec34e80f_contact
        #   binary_sensor.isa_cn_xxx_dw2hl_contact_state_p_2_2   → 都归一为 contact
        # 并加一道保险：只有事件数接近（镜像上报的特征）才真合并，否则仅列为候选，
        # 避免把同房间两扇窗的两个真实传感器错误合并。
        duplicate_candidates: list[dict] = []
        merged_rooms: dict[str, list[dict]] = {}
        for room, entities in by_room.items():
            groups: dict[tuple, list[dict]] = {}
            for e in entities:
                key = (e.get("domain"), self._capability_of(e["entity_id"]))
                groups.setdefault(key, []).append(e)
            kept: list[dict] = []
            for key, grp in groups.items():
                if len(grp) == 1:
                    kept.append(grp[0])
                    continue
                # 事件多的当主记录，保证保留信息量最大的一条
                grp = sorted(
                    grp, key=lambda e: (-e.get("events_in_window", 0), len(e["entity_id"]))
                )
                canon = grp[0]
                for dup in grp[1:]:
                    a = canon.get("events_in_window", 0)
                    b = dup.get("events_in_window", 0)
                    mirrored = abs(a - b) <= max(3, round(max(a, b) * 0.2))
                    duplicate_candidates.append({
                        "canonical": canon["entity_id"],
                        "duplicate": dup["entity_id"],
                        "room": room,
                        "capability": key[1],
                        "events": [a, b],
                        "merged": mirrored,
                        "reason": (
                            "事件数接近，判定为同一物理设备的镜像上报，已合并"
                            if mirrored
                            else "能力相同但事件数差异大，可能是两个不同设备，保留未合并"
                        ),
                    })
                    if not mirrored:
                        kept.append(dup)
                        continue
                    canon = {
                        **canon,
                        "events_in_window": max(a, b),
                        "has_data": bool(canon.get("has_data")) or bool(dup.get("has_data")),
                        "last_seen": max(canon.get("last_seen") or "", dup.get("last_seen") or ""),
                        "stale_days": min(
                            [v for v in (canon.get("stale_days"), dup.get("stale_days")) if v is not None],
                            default=canon.get("stale_days"),
                        ),
                        "merged_from": (canon.get("merged_from") or []) + [dup["entity_id"]],
                    }
                kept.append(canon)
            merged_rooms[room] = kept

        flat = [e for group in merged_rooms.values() for e in group]
        silent = [e["entity_id"] for e in flat if not e["has_data"]]
        return {
            "ok": True,
            "total": len(flat),
            "raw_entity_count": len([e for g in by_room.values() for e in g]),
            "duplicates_merged": sum(1 for d in duplicate_candidates if d["merged"]),
            "duplicate_candidates": duplicate_candidates,
            "window": meta,
            "rooms": {
                room: {
                    "entity_count": len(entities),
                    "categories": sorted({e["category"] for e in entities}),
                    "entities": entities,
                }
                for room, entities in sorted(
                    merged_rooms.items(), key=lambda kv: -sum(e["events_in_window"] for e in kv[1])
                )
            },
            "categories_available": sorted({e["category"] for e in flat}),
            "no_data_entities": silent,
            "hint": (
                "用 display_name 认设备，把 entity_id 交给 get_device_usage / query_events。"
                if flat
                else "没有匹配的实体，试试放宽 room/category 或先在「采集配置」里启用实体。"
            ),
        }

    @staticmethod
    def _fallback_name(entity_id: str) -> str:
        """没配友好名时，从 entity_id 里挤出一个还算能看的名字。"""
        tail = entity_id.split(".", 1)[-1]
        tail = re.sub(r"_(p_)?\d+(_\d+)*$", "", tail)
        tail = re.sub(r"[a-z]{2}_\d{6,}_?", "", tail)
        return tail.replace("_", " ").strip() or entity_id

    # 能力后缀归一：把厂商前缀 / MAC / MIoT 属性号剥掉，只留「这个实体测什么」。
    # 顺序敏感——长词必须排在其前缀词之前（power_cost_today 要在 power 之前）。
    _CAPABILITY_KEYWORDS = (
        "contact_state", "door_state", "window_state", "contact",
        "occupancy_status", "occupancy", "presence_state", "presence",
        "motion_state", "motion", "illuminance", "temperature", "humidity",
        "battery_level", "battery", "power_cost_today", "power_cost",
        "electric_power", "power", "energy", "voltage", "current",
        "distance", "brightness", "position", "switch_status",
    )
    _CAPABILITY_ALIASES = {
        "contact_state": "contact",
        "door_state": "contact",
        "window_state": "contact",
        "occupancy_status": "occupancy",
        "presence_state": "presence",
        "motion_state": "motion",
        "battery_level": "battery",
    }

    @classmethod
    def _capability_of(cls, entity_id: str) -> str:
        """从 entity_id 提取归一化的能力后缀，用于识别同一物理设备的重复上报。"""
        tail = entity_id.split(".", 1)[-1].lower()
        tail = re.sub(r"_(p_)?\d+(_\d+)*$", "", tail)      # 去 MIoT 属性号 _p_2_1
        tail = re.sub(r"[0-9a-f]{12}", "", tail)            # 去 MAC 片段
        tail = re.sub(r"_?[a-z]{2,4}_[a-z]{2}_\d{6,}_?", "_", tail)  # 去 lumi_cn_123456
        tail = re.sub(r"_?[a-z]{2}_\d{6,}_?", "_", tail)
        for kw in cls._CAPABILITY_KEYWORDS:
            if kw in tail:
                return cls._CAPABILITY_ALIASES.get(kw, kw)
        parts = [p for p in tail.split("_") if p and not p.isdigit()]
        return parts[-1] if parts else tail

    # ── 语义匹配 ──────────────────────────────────────────────────────────

    def match_rooms(self, room: str) -> list[str]:
        """房间名模糊匹配。支持「主卧」→「主卧室」这类包含关系。"""
        if not room:
            return []
        target = _norm(room)
        names = list((self.config.rooms or {}).keys())
        exact = [n for n in names if _norm(n) == target]
        if exact:
            return exact
        loose = [n for n in names if target in _norm(n) or _norm(n) in target]
        return loose

    def room_names(self, only_enabled: bool = True) -> list[str]:
        """HA 中真实存在的区域（area）名，按「长度降序」返回，便于最长优先匹配。"""
        names: list[str] = []
        for name, payload in (self.config.rooms or {}).items():
            if not name:
                continue
            if only_enabled and isinstance(payload, dict) and not payload.get("enabled", True):
                continue
            names.append(str(name))
        return sorted(set(names), key=lambda n: (-len(n), n))

    def resolve_room_in_text(self, text: str, rooms: list[str] | None = None) -> dict:
        """从自由文本里**精确**识别区域名（最长优先）。

        这是「房间名歧义」的确定性解法：只要 HA 里真的存在同名 area，
        就按精确区域处理（``房间`` → area「房间」，不是「随便哪个房间」）；
        只有用户明确说「所有房间 / 全屋」时才认定为跨房间汇总。
        「主卧室空调」里同时含「主卧室」和「卧室」，取更长的「主卧室」。
        """
        t = _norm(text)
        names = self.room_names() if rooms is None else list(rooms)
        aggregate = any(w in t for w in ROOM_AGGREGATE_WORDS)
        matched = sorted(
            [n for n in names if _norm(n) and _norm(n) in t],
            key=lambda n: (-len(n), n),
        )
        primary = "" if aggregate else (matched[0] if matched else "")
        return {
            "room": primary,
            "aggregate": aggregate,
            "matched": matched,
            "ambiguous": bool(primary) and primary in GENERIC_ROOM_WORDS,
            "rooms_available": names,
        }

    def domains_for(self, category: str = "", domain: str = "", query: str = "") -> list[str]:
        """把「类别 / domain / 自由文本」统一解析成 domain 列表。"""
        out: set[str] = set()
        if category:
            out.update(CATEGORY_DOMAINS.get(_norm(category), ()))
        if domain:
            out.update(d.strip() for d in _norm(domain).split(",") if d.strip())
        if query:
            q = _norm(query)
            for keyword, domains in KEYWORD_DOMAINS.items():
                if keyword in q:
                    out.update(domains)
        return sorted(out)

    def _apply_semantic_filter(
        self,
        items: list[dict],
        room: str = "",
        category: str = "",
        domain: str = "",
        query: str = "",
    ) -> list[dict]:
        room, query = self.split_room_from_query(room, query)
        rooms = set(self.match_rooms(room)) if room else set()
        domains = set(self.domains_for(category, domain, query))
        q_tokens = [t for t in _tokens(query) if t not in KEYWORD_DOMAINS]

        loose: list[dict] = []   # 只过 room/domain
        strict: list[dict] = []  # 还额外命中了 query 关键词
        for it in items:
            if rooms and it["room"] not in rooms:
                continue
            if domains and _norm(it["domain"]) not in domains:
                continue
            loose.append(it)
            if q_tokens:
                haystack = _norm(
                    f"{it['entity_id']} {it['friendly_name']} {it['room']} {it['domain']}"
                )
                if any(t in haystack for t in q_tokens):
                    strict.append(it)
        if not q_tokens:
            return loose
        # 有关键词命中就用精确结果，避免「房间电脑」被摊成整个房间的设备；
        # 一个都没命中时，仅在有 room/domain 约束的情况下回退到宽松结果。
        if strict:
            return strict
        return loose if (rooms or domains) else []

    def resolve_entities(
        self, room: str = "", category: str = "", domain: str = "", query: str = ""
    ) -> list[str]:
        """语义条件 → entity_id 列表。空列表代表「不加实体过滤」。"""
        if not any([room, category, domain, query]):
            return []
        items = self._apply_semantic_filter(
            self._config_entities(only_enabled=True), room, category, domain, query
        )
        return [it["entity_id"] for it in items]

    def split_room_from_query(self, room: str, query: str) -> tuple[str, str]:
        """调用方只给了 ``query="房间空调"`` 时，把区域名切出来变成 ``room="房间"``。

        模型经常把房间塞进 query 里，导致跨房间模糊匹配。这里在服务端兜底，
        让「房间」这类既是通用词又是 area 名的情况也能落到精确区域上。
        """
        if room or not query:
            return room, query
        hint = self.resolve_room_in_text(query)
        matched = hint.get("room") or ""
        if not matched:
            return room, query
        rest = _norm(query).replace(_norm(matched), " ").strip()
        return matched, (rest or query)

    def name_map(self) -> dict[str, dict]:
        return {it["entity_id"]: it for it in self._config_entities(only_enabled=False)}

    def decorate(self, rows: Iterable[dict]) -> list[dict]:
        """给原始事件补上友好名，让返回结果不再是一堆 cryptic ID。"""
        names = self.name_map()
        out = []
        for r in rows:
            meta = names.get(r.get("entity_id", ""), {})
            out.append(
                {
                    **r,
                    "friendly_name": meta.get("friendly_name")
                    or self._fallback_name(r.get("entity_id", "")),
                    "category": meta.get("category")
                    or self.category_of(r.get("domain", "")),
                }
            )
        return out

    # ── 事件搜索（语义过滤 + 可选摘要）────────────────────────────────────

    def search_events(
        self,
        room: str = "",
        category: str = "",
        domain: str = "",
        query: str = "",
        entity_id: str = "",
        state: str = "",
        days: int = 7,
        start: str = "",
        end: str = "",
        limit: int = 200,
        offset: int = 0,
        order: str = "desc",
        behavior_only: bool = True,
        summarize: bool = False,
    ) -> dict:
        start_iso, end_iso, meta = self.resolve_range(days, start, end)
        # entity_id 显式指定时直接作为过滤实体，绕过语义解析（修复「entity_id 被忽略」）
        if entity_id:
            entities = [e.strip() for e in entity_id.split(",") if e.strip()]
        else:
            entities = self.resolve_entities(room, category, domain, query)
        rooms = self.match_rooms(room)
        domains = self.domains_for(category, domain, query)
        excl = list(TELEMETRY_DOMAINS) if behavior_only else None
        if excl:
            # 显式点名 domain 或 entity_id 时，不把命中的域当污染源剔除：
            # 否则用户明明指定了某实体，却因它属于 sensor/event 等遥测域被静默过滤成 0 条，
            # 模型会误判成「没数据 / 没有 MCP 工具」。语义检索（room/category/query）命中的
            # 域仍按原逻辑豁免；仅当用户直接给出 entity_id 时才对点名实体破例。
            carve = set(domains or [])
            if entity_id:
                carve |= {e.split(".", 1)[0] for e in entities if "." in e}
            if carve:
                excl = [d for d in excl if d not in carve] or None
        states = [s.strip() for s in str(state or "").split(",") if s.strip()] or None

        if room and not entities and not rooms:
            return {
                "ok": False,
                "error": f"没有匹配到房间「{room}」",
                "available_rooms": list((self.config.rooms or {}).keys()),
                "hint": "先调用 get_entity_catalog 查看可用房间与设备",
            }

        limit = max(1, min(int(limit or 200), 2000))
        offset = max(0, int(offset or 0))

        total = self.store.count_events(
            start_iso,
            end_iso,
            rooms or None,
            entities or None,
            domains or None,
            None,
            states,
            excl,
        )
        rows = self.store.query_events(
            start_iso,
            end_iso,
            rooms or None,
            entities or None,
            domains or None,
            None,
            limit,
            offset,
            order,
            states,
            excl,
        )
        decorated = self.decorate(rows)

        payload: dict[str, Any] = {
            "ok": True,
            "window": meta,
            "filters": {
                "room": room or "(全部)",
                "category": category or "(全部)",
                "entity_id": entity_id or "(全部)",
                "domain": ",".join(domains) if domains else "(全部)",
                "state": state or "(全部)",
                "behavior_only": behavior_only,
                "entities_resolved": len(entities),
            },
            "total": total,
            "count": len(decorated),
            "offset": offset,
            "has_more": offset + len(decorated) < total,
            "next_offset": offset + len(decorated) if offset + len(decorated) < total else None,
        }
        if total == 0:
            payload["diagnosis"] = self._diagnose_empty(entities, domains, room, category)
            # 显式点名了实体却拿到 0 条，最常见的原因是它属于遥测域、被 behavior_only 静默过滤
            if entity_id and behavior_only:
                telemetry = [
                    e for e in entities
                    if e.split(".", 1)[0] in TELEMETRY_DOMAINS
                ]
                if telemetry:
                    payload["diagnosis"]["reason"] = (
                        f"指定的实体 {', '.join(telemetry)} 属于遥测域"
                        f"（{'/'.join(sorted(TELEMETRY_DOMAINS))}），"
                        "在 behavior_only=true 下被默认排除，所以是 0 条而不是没数据。"
                        "传 behavior_only=false 即可查看。"
                    )
                    payload["diagnosis"]["fix"] = "behavior_only=false"
        if summarize:
            payload["summary"] = self._summarize(decorated)
            payload["events"] = decorated[:50]
            payload["note"] = "summarize=true：events 仅保留前 50 条样本，请看 summary"
        else:
            payload["events"] = decorated
        return payload

    def _diagnose_empty(
        self, entities: list[str], domains: list[str], room: str, category: str
    ) -> dict:
        """0 结果时给出明确原因，而不是让调用方猜「没数据还是没采集」。"""
        scope = f"{room or '全屋'} / {category or '不限类别'}"
        if not entities and (room or category or domains):
            return {
                "reason": "no_matching_entity",
                "message": f"「{scope}」下没有配置任何匹配的实体，因此不可能有事件。",
                "next_step": "用 get_entity_catalog() 看看实际有哪些房间和设备类别",
            }
        if entities:
            seen = self.store.entity_last_seen(entities)
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

    @staticmethod
    def _summarize(rows: list[dict]) -> dict:
        """把裸事件压成「谁、变了多少次、都变成了什么」。"""
        by_entity: dict[str, dict] = {}
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

    # ── 设备用量（on/off 时长与次数）──────────────────────────────────────

    def device_usage(
        self,
        entity_id: str = "",
        room: str = "",
        category: str = "",
        query: str = "",
        days: int = 7,
        start: str = "",
        end: str = "",
        on_states: str = "",
        debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
        include_timeline: bool = True,
    ) -> dict:
        """按设备统计开启时长 / 开关次数 / 每日分布。

        不再要求调用方手动对时间戳做差 —— 状态配对、跨窗口截断、
        去抖、跨天拆分都在这里算好。
        """
        start_iso, end_iso, meta = self.resolve_range(days, start, end)

        targets: list[str]
        if entity_id:
            targets = [e.strip() for e in entity_id.split(",") if e.strip()]
        else:
            targets = self.resolve_entities(room, category, "", query)
        if not targets:
            return {
                "ok": False,
                "error": "没有定位到任何设备",
                "hint": "传 entity_id，或用 room/category/query 语义定位；可先调 get_entity_catalog",
            }
        if len(targets) > 40:
            targets = targets[:40]

        names = self.name_map()
        allow_on = {s.strip().lower() for s in on_states.split(",") if s.strip()}
        results = []
        for eid in targets:
            usage = self._usage_one(
                eid, start_iso, end_iso, allow_on, debounce_seconds, include_timeline
            )
            meta_info = names.get(eid, {})
            usage.update(
                {
                    "friendly_name": meta_info.get("friendly_name")
                    or self._fallback_name(eid),
                    "room": meta_info.get("room", ""),
                    "domain": meta_info.get("domain", eid.split(".")[0]),
                }
            )
            results.append(usage)

        results.sort(key=lambda x: -x["total_on_seconds"])
        grand = sum(r["total_on_seconds"] for r in results)
        return {
            "ok": True,
            "window": meta,
            "device_count": len(results),
            "total_on_seconds": round(grand, 1),
            "total_on_human": fmt_duration(grand),
            "devices": results,
        }

    @staticmethod
    def _parse_time_range(tr: str):
        """解析 'HH:MM-HH:MM' -> (start_min, end_min, crosses_midnight)。空/无效返回 None。
        crosses_midnight 表示 end <= start（如 '22:00-07:00' 跨零点）。"""
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

    @staticmethod
    def _split_by_day(seg_start, seg_end):
        """把 [seg_start, seg_end] 按自然日切成连续切片，返回 [(s, e), ...]。"""
        out = []
        cur = seg_start
        while cur < seg_end:
            nxt = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            pe = min(seg_end, nxt)
            if pe > cur:
                out.append((cur, pe))
            cur = nxt
        return out

    @staticmethod
    def _clip_to_time_range(seg_start, seg_end, tr):
        """把 segment 按自然日切片，仅保留落在 time_range 周期窗口内的部分。
        tr=None 表示不裁剪（返回整段按天切片，使跨午夜会话正确归到各自日期）。
        返回交集区间 [(s, e), ...]，每段按实际日期，供 by_day/timeline 使用。"""
        pieces = InsightService._split_by_day(seg_start, seg_end)
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

    def _usage_one(
        self,
        entity_id: str,
        start_iso: str,
        end_iso: str,
        allow_on: set[str],
        debounce: int,
        include_timeline: bool,
        time_range: str = "",
    ) -> dict:
        # 窗口起点之前的最后一次状态：决定进入窗口时设备是否已经开着，
        # 少了这一步「昨晚开到今早」的空调时长会被整段吞掉。
        prior = self.store.query_events(
            None, start_iso, entities=[entity_id], limit=1, order="desc"
        )
        rows = self.store.query_events(
            start_iso, end_iso, entities=[entity_id], limit=5000, order="asc"
        )

        def is_on(state: Any) -> bool:
            s = _norm(state)
            if allow_on:
                return s in allow_on
            return s not in OFF_STATES

        window_start = datetime.fromisoformat(start_iso)
        window_end = min(datetime.fromisoformat(end_iso), now_local(self.tz))

        segments: list[dict] = []
        cur_start: datetime | None = None
        if prior and is_on(prior[0].get("new_state")):
            cur_start = window_start

        on_count = off_count = 0
        for r in rows:
            ts = self._parse(r.get("ts", ""))
            if not ts:
                continue
            if is_on(r.get("new_state")):
                on_count += 1
                if cur_start is None:
                    cur_start = ts
            else:
                off_count += 1
                if cur_start is not None:
                    segments.append({"start": cur_start, "end": ts, "open": False})
                    cur_start = None
        if cur_start is not None:
            segments.append({"start": cur_start, "end": window_end, "open": True})

        tr = self._parse_time_range(time_range)
        if tr:
            clipped = []
            for seg in segments:
                for (s, e) in self._clip_to_time_range(seg["start"], seg["end"], tr):
                    clipped.append({"start": s, "end": e})
            segments = clipped

        timeline = []
        by_day: dict[str, float] = {}
        durations: list[float] = []
        for seg in segments:
            dur = (seg["end"] - seg["start"]).total_seconds()
            if dur <= 0 or dur < debounce:
                continue  # 去抖：极短片段视为误触
            durations.append(dur)
            day = seg["start"].strftime("%Y-%m-%d")
            by_day[day] = round(by_day.get(day, 0.0) + dur, 1)
            timeline.append(
                {
                    "start": seg["start"].isoformat(sep="T"),
                    "end": seg["end"].isoformat(sep="T"),
                    "duration_seconds": round(dur, 1),
                    "duration_human": fmt_duration(dur),
                    "still_on": seg["end"] >= window_end,
                }
            )

        total = sum(durations)
        span_days = max(1.0, (window_end - window_start).total_seconds() / 86400)
        out = {
            "entity_id": entity_id,
            "sessions": len(durations),
            "switch_on_count": on_count,
            "switch_off_count": off_count,
            "total_on_seconds": round(total, 1),
            "total_on_human": fmt_duration(total),
            "avg_session_seconds": round(total / len(durations), 1) if durations else 0,
            "avg_session_human": fmt_duration(total / len(durations)) if durations else "0秒",
            "longest_session_human": fmt_duration(max(durations)) if durations else "0秒",
            "daily_average_human": fmt_duration(total / span_days),
            "duty_cycle_percent": round(
                total / ((window_end - window_start).total_seconds() or 1) * 100, 1
            ),
            "by_day_seconds": by_day,
            "raw_event_count": len(rows),
        }
        if include_timeline:
            out["timeline"] = timeline[:200]
        if not rows and not prior:
            out["notice"] = "该实体在窗口内没有任何事件，可能未被采集或一直未变化"
        return out

    def _usage_by_attr(
        self,
        entity_id: str,
        attribute: str,
        value: Any,
        pattern: str,
        start_iso: str,
        end_iso: str,
        debounce: int = DEFAULT_DEBOUNCE_SECONDS,
        include_timeline: bool = True,
        time_range: str = "",
    ) -> dict:
        """按「属性值」判定的开启时长（覆盖 source=='HDMI 3' 这类属性级条件）。

        与 ``_usage_one`` 算法一致（状态配对 / 跨窗口截断 / 去抖 / 跨天拆分），
        仅把「是否开启」的判定从 ``new_state`` 改为事件属性值匹配。
        属性在 store 中是 diff 存储，需要沿时间轴 carry-forward 最近一次取值。
        """
        attr_key = attribute.split(".", 1)[1] if attribute.startswith("attributes.") else attribute

        def get_attr(row):
            return _parse_attrs(row.get("attrs_json")).get(attr_key)

        def matches(v) -> bool:
            if v is None:
                return False
            sv = _norm(v)
            tv = _norm(value)
            if pattern == "contains":
                return tv in sv
            if pattern == "ne":
                return sv != tv
            if pattern == "regex":
                try:
                    return re.search(str(value), sv) is not None
                except Exception:
                    return False
            return sv == tv

        window_start = datetime.fromisoformat(start_iso)
        window_end = min(datetime.fromisoformat(end_iso), now_local(self.tz))

        # 窗口起点前的属性种子：沿时间 carry-forward 最近一次取值
        cur_attr = None
        try:
            seed_start = (window_start - timedelta(days=30)).isoformat()
            seed_rows = self.store.query_events(
                seed_start, start_iso, entities=[entity_id], limit=500, order="desc"
            )
            for r in seed_rows:
                av = get_attr(r)
                if av is not None:
                    cur_attr = av
                    break
        except Exception:
            pass

        prior = self.store.query_events(
            None, start_iso, entities=[entity_id], limit=1, order="desc"
        )
        if prior:
            pv = get_attr(prior[0])
            if pv is not None:
                cur_attr = pv

        rows = self.store.query_events(
            start_iso, end_iso, entities=[entity_id], limit=5000, order="asc"
        )

        segments: list[dict] = []
        cur_start: datetime | None = None
        if matches(cur_attr):
            cur_start = window_start

        match_count = 0
        for r in rows:
            ts = self._parse(r.get("ts", ""))
            if not ts:
                continue
            av = get_attr(r)
            if av is not None:
                cur_attr = av
            if matches(cur_attr):
                match_count += 1
                if cur_start is None:
                    cur_start = ts
            else:
                if cur_start is not None:
                    segments.append({"start": cur_start, "end": ts})
                    cur_start = None
        if cur_start is not None:
            segments.append({"start": cur_start, "end": window_end})

        tr = self._parse_time_range(time_range)
        if tr:
            clipped = []
            for seg in segments:
                for (s, e) in self._clip_to_time_range(seg["start"], seg["end"], tr):
                    clipped.append({"start": s, "end": e})
            segments = clipped

        timeline = []
        by_day: dict[str, float] = {}
        durations: list[float] = []
        for seg in segments:
            dur = (seg["end"] - seg["start"]).total_seconds()
            if dur <= 0 or dur < debounce:
                continue
            durations.append(dur)
            day = seg["start"].strftime("%Y-%m-%d")
            by_day[day] = round(by_day.get(day, 0.0) + dur, 1)
            timeline.append({
                "start": seg["start"].isoformat(sep="T"),
                "end": seg["end"].isoformat(sep="T"),
                "duration_seconds": round(dur, 1),
                "duration_human": fmt_duration(dur),
                "still_on": seg["end"] >= window_end,
            })

        total = sum(durations)
        span_days = max(1.0, (window_end - window_start).total_seconds() / 86400)
        out = {
            "entity_id": entity_id,
            "attribute": attribute,
            "value": value,
            "pattern": pattern,
            "match_count": match_count,
            "sessions": len(durations),
            "total_on_seconds": round(total, 1),
            "total_on_human": fmt_duration(total),
            "avg_session_seconds": round(total / len(durations), 1) if durations else 0,
            "avg_session_human": fmt_duration(total / len(durations)) if durations else "0秒",
            "longest_session_human": fmt_duration(max(durations)) if durations else "0秒",
            "daily_average_human": fmt_duration(total / span_days),
            "duty_cycle_percent": round(
                total / ((window_end - window_start).total_seconds() or 1) * 100, 1
            ),
            "by_day_seconds": by_day,
            "raw_event_count": len(rows),
        }
        if include_timeline:
            out["timeline"] = timeline[:200]
        if not rows and not prior:
            out["notice"] = "该实体在窗口内没有任何事件，可能未被采集或一直未变化"
        return out

    def _count_by_filter(
        self,
        entity_id: str,
        attribute: str,
        value: Any,
        pattern: str,
        start_iso: str,
        end_iso: str,
    ) -> dict:
        """按状态或属性过滤，统计匹配事件数与分日分布（metric=count 用）。"""
        attr_key = attribute.split(".", 1)[1] if attribute.startswith("attributes.") else attribute

        def val_of(row):
            if attribute in ("", "state", "new_state"):
                return row.get("new_state")
            return _parse_attrs(row.get("attrs_json")).get(attr_key)

        def matches(v) -> bool:
            if v is None:
                return False
            sv = _norm(v)
            tv = _norm(value)
            if pattern == "contains":
                return tv in sv
            if pattern == "ne":
                return sv != tv
            if pattern == "regex":
                try:
                    return re.search(str(value), sv) is not None
                except Exception:
                    return False
            return sv == tv

        rows = self.store.query_events(
            start_iso, end_iso, entities=[entity_id], limit=5000, order="asc"
        )
        by_day: dict[str, int] = {}
        cnt = 0
        for r in rows:
            if matches(val_of(r)):
                cnt += 1
                ts = self._parse(r.get("ts", ""))
                if ts:
                    day = ts.strftime("%Y-%m-%d")
                    by_day[day] = by_day.get(day, 0) + 1
        return {
            "entity_id": entity_id,
            "attribute": attribute,
            "value": value,
            "pattern": pattern,
            "match_count": cnt,
            "by_day_count": by_day,
            "raw_event_count": len(rows),
        }

    # ── 行为洞察（服务端出结论）───────────────────────────────────────────

    def _noise_entities(self, start_iso: str, end_iso: str, rooms=None) -> set:
        """识别单实体占比垄断型噪声源（如加湿器缺水反复跳变、人体传感器误报）。

        判定准则与 behavior_insights 完全一致：某实体事件数 > 房间事件数 * NOISE_RATIO_CAP
        即视为垄断型噪声。返回需从「人的行为 / 房间使用率 / 静默判定」中剔除的 entity_id 集合。

        这些实体只参与设备健康(anomaly)检测，不污染行为洞察与睡眠/离家识别。
        """
        excl = list(TELEMETRY_DOMAINS)
        hist_raw = self.store.hour_histogram(start_iso, end_iso, rooms, excl)
        tops = self.store.top_entities(start_iso, end_iso, rooms, 40, excl)
        noise_ids: set = set()
        for room, buckets in hist_raw.items():
            room_total = sum(buckets)
            if room_total <= 0:
                continue
            for t in tops:
                if t.get("room") != room or t["entity_id"] in noise_ids:
                    continue
                if t["count"] > NOISE_RATIO_CAP * room_total:
                    noise_ids.add(t["entity_id"])
        return noise_ids

    def behavior_insights(
        self,
        days: int = 7,
        rooms: str = "",
        behavior_only: bool = True,
        start: str = "",
        end: str = "",
    ) -> dict:
        start_iso, end_iso, meta = self.resolve_range(days, start, end)
        room_list = [r.strip() for r in str(rooms or "").split(",") if r.strip()]
        resolved_rooms: list[str] = []
        for r in room_list:
            resolved_rooms.extend(self.match_rooms(r))
        resolved_rooms = sorted(set(resolved_rooms)) or None

        excl = list(TELEMETRY_DOMAINS) if behavior_only else None
        hist_raw = self.store.hour_histogram(start_iso, end_iso, resolved_rooms, excl)
        hist = hist_raw
        tops = self.store.top_entities(start_iso, end_iso, resolved_rooms, 40, excl)
        daily = self.store.daily_counts_by_room(start_iso, end_iso, resolved_rooms)
        transitions = self.store.transitions(
            start_iso, end_iso, resolved_rooms, limit=20, exclude_domains=excl
        )
        total = self.store.count_events(
            start_iso, end_iso, resolved_rooms, exclude_domains=excl
        )
        total_raw = self.store.count_events(start_iso, end_iso, resolved_rooms)

        names = self.name_map()

        # ── 单实体占比封顶：自动识别并排除噪声源（如加湿器缺水反复跳变）──
        # 不靠猜测域，而是按「单实体事件数 > 房间事件数 * 阈值」判定，
        # 把垄断型实体从「行为/房间使用率」中剔除，但仍保留在设备健康(anomaly)里。
        # 同一套逻辑也用于活动识别的静默判定（见 infer_activities / _detect_activities）。
        noise_ids = self._noise_entities(start_iso, end_iso, resolved_rooms)
        noise_sources: list[dict] = []
        for room, buckets in hist_raw.items():
            room_total = sum(buckets)
            if room_total <= 0:
                continue
            for t in tops:
                if t.get("room") != room or t["entity_id"] not in noise_ids:
                    continue
                if any(ns["entity_id"] == t["entity_id"] for ns in noise_sources):
                    continue
                eid = t["entity_id"]
                noise_sources.append({
                    "entity_id": eid,
                    "room": room,
                    "count": t["count"],
                    "room_share": round(t["count"] / room_total, 3),
                    "friendly_name": names.get(eid, {}).get("friendly_name")
                    or self._fallback_name(eid),
                })
        if noise_ids:
            hist = self.store.hour_histogram(
                start_iso, end_iso, resolved_rooms, excl,
                exclude_entities=list(noise_ids),
            )
            tops = self.store.top_entities(
                start_iso, end_iso, resolved_rooms, 40, excl,
                exclude_entities=list(noise_ids),
            )
            total_clean = self.store.count_events(
                start_iso, end_iso, resolved_rooms,
                exclude_domains=excl, exclude_entities=list(noise_ids),
            )
        else:
            total_clean = total

        tops_by_room: dict[str, list[dict]] = {}
        for t in tops:
            meta_info = names.get(t["entity_id"], {})
            tops_by_room.setdefault(t.get("room", ""), []).append(
                {
                    "entity_id": t["entity_id"],
                    "friendly_name": meta_info.get("friendly_name")
                    or self._fallback_name(t["entity_id"]),
                    "category": meta_info.get("category")
                    or self.category_of(t.get("domain", "")),
                    "count": t["count"],
                }
            )

        # 原始小时直方图（含噪声）留给 anomaly 做设备健康检测；
        # 输出用的节奏/房间洞察用剔除噪声后的直方图。
        global_hourly_raw = [0] * 24
        for buckets in hist_raw.values():
            for i, v in enumerate(buckets):
                global_hourly_raw[i] += v

        global_hourly = [0] * 24
        room_insights: dict[str, dict] = {}
        for room, buckets in hist.items():
            for i, v in enumerate(buckets):
                global_hourly[i] += v
            room_total = sum(buckets)
            insight = {
                "event_count": room_total,
                "hourly": buckets,
                **self._rhythm(buckets),
                "top_entities": tops_by_room.get(room, [])[:5],
            }
            # 低频房间样本过小，凌晨偶发事件会把 night_ratio_percent 带偏，置空而非误导
            if room_total < 20:
                insight["night_ratio_percent"] = None
                insight["night_ratio_note"] = "样本过少，该指标不可靠"
            room_insights[room] = insight

        room_insights = dict(
            sorted(room_insights.items(), key=lambda kv: -kv[1]["event_count"])
        )

        by_day: dict[str, int] = {}
        for row in daily:
            by_day[row["day"]] = by_day.get(row["day"], 0) + row["count"]

        anomaly = self.anomaly_report(by_day, global_hourly_raw, start_iso, end_iso, self.store)

        return {
            "ok": True,
            "window": meta,
            "behavior_only": behavior_only,
            "total_events": total_clean,
            "total_events_raw": total_raw,
            "telemetry_filtered": total_raw - total if behavior_only else 0,
            "noise_filtered": (total - total_clean) if noise_ids else 0,
            "noise_sources": noise_sources,
            "daily_rhythm": {
                "hourly": global_hourly,
                **self._rhythm(global_hourly),
            },
            "rooms": room_insights,
            "most_active_room": next(iter(room_insights), ""),
            "room_transitions": [
                {
                    "room": t["room"],
                    # transitions 的 from/to 是 "entity_id=state" 形式，拆出真实 entity_id 用于命名/检索
                    "from_eid": t["from"].split("=", 1)[0],
                    "to_eid": t["to"].split("=", 1)[0],
                    "from": names.get(t["from"].split("=", 1)[0], {}).get("friendly_name")
                    or self._fallback_name(t["from"].split("=", 1)[0]),
                    "to": names.get(t["to"].split("=", 1)[0], {}).get("friendly_name")
                    or self._fallback_name(t["to"].split("=", 1)[0]),
                    "from_entity": t["from"].split("=", 1)[0],
                    "to_entity": t["to"].split("=", 1)[0],
                    "count": t["count"],
                    "avg_gap_seconds": round(t["avg_gap_s"], 1),
                }
                for t in transitions
            ],
            "daily_totals": by_day,
            "anomalies": anomaly["anomalies"],
            # 逐条规则留痕：为空到底是「真没异常」还是「规则没跑」，这里说得清清楚楚
            "anomaly_checks": anomaly["checks"],
            "hint": "需要具体设备时长请用 get_device_usage；需要原始事件请用 search_events",
        }

    def query_behavior_events(
        self,
        room: str = "",
        member: str = "",
        days: int = 7,
        start: str = "",
        end: str = "",
        limit: int = 50,
    ) -> dict:
        """查询多模态视觉识别记录的历史行为事件（谁在哪个房间、什么时间）。"""
        start_iso, end_iso, meta = self.resolve_range(days, start, end)
        day_from = start_iso[:10]
        day_to = end_iso[:10]

        resolved_room = ""
        if room:
            matched = self.match_rooms(room)
            if matched:
                resolved_room = matched[0]
            else:
                return {
                    "ok": False,
                    "error": f"没有匹配到房间「{room}」",
                    "available_rooms": list((self.config.rooms or {}).keys()),
                }

        events = self.store.list_behavior_events(
            room=resolved_room or None,
            member=member or None,
            day_from=day_from,
            day_to=day_to,
            limit=max(1, min(int(limit or 50), 200)),
        )

        out: list[dict] = []
        for e in events:
            persons = e.get("persons") or []
            names = [p.get("name") or "未识别" for p in persons]
            out.append(
                {
                    "time": e.get("server_ts", ""),
                    "room": e.get("room"),
                    "persons": names,
                    "scene": e.get("scene"),
                    "action": e.get("action"),
                    "count": e.get("count"),
                }
            )

        return {
            "ok": True,
            "window": meta,
            "room": resolved_room or room or "全部",
            "member_filter": member or None,
            "count": len(out),
            "events": out,
        }

    @staticmethod
    def _rhythm(buckets: list[int], coverage: float = 0.8) -> dict:
        """从 24 小时直方图里推断作息。

        ``active_window`` 取「覆盖 ``coverage`` 比例事件的最窄环形时段」——
        用「大于均值」这类阈值法在稀疏数据上会退化成 00:00-24:00，等于没说。
        环形窗口能正确表达「22:00-次日 02:00」这种跨零点的作息。
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
        best: tuple[int, int] | None = None  # (length, start)
        for start in range(24):
            acc = 0
            for length in range(1, 25):
                acc += buckets[(start + length - 1) % 24]
                if acc >= need:
                    if best is None or length < best[0]:
                        best = (length, start)
                    break
        window = ""
        active_hours: list[int] = []
        if best:
            length, start = best
            active_hours = [(start + i) % 24 for i in range(length)]
            window = f"{start:02d}:00-{(start + length) % 24:02d}:00"

        nonzero = [h for h, v in enumerate(buckets) if v > 0]
        return {
            "active_window": window,
            "active_hours": active_hours,
            "peak_hour": buckets.index(max(buckets)),
            "quiet_hours": [h for h, v in enumerate(buckets) if v == 0],
            "first_activity_hour": min(nonzero) if nonzero else None,
            "last_activity_hour": max(nonzero) if nonzero else None,
            "night_ratio_percent": round(sum(buckets[0:6]) / total * 100, 1),
        }

    @staticmethod
    def _num_stale(entity: dict):
        """取实体的 stale_days 数值。

        ``entity_catalog`` 里 stale_days 是 ``round(x, 1)`` 得到的 **float**，
        而旧代码用 ``isinstance(v, int)`` 判断 —— 永远为 False。这正是
        「device_health 的 stale 恒为 0」「anomalies 恒为空」的直接原因。
        """
        v = entity.get("stale_days")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    def _anomalies(
        self,
        by_day: dict[str, int],
        hourly: list[int],
        start_iso: str = "",
        end_iso: str = "",
        store=None,
    ) -> list[dict]:
        """向后兼容包装：只要异常列表。"""
        return self.anomaly_report(by_day, hourly, start_iso, end_iso, store)["anomalies"]

    def anomaly_report(
        self,
        by_day: dict[str, int],
        hourly: list[int],
        start_iso: str = "",
        end_iso: str = "",
        store=None,
    ) -> dict:
        """异常检测 + 逐条规则的执行诊断。

        每条规则无论命中与否都会在 ``checks`` 里留痕（ran / skipped + 原因 + 命中数），
        这样「anomalies 为空」到底是真没异常、还是规则压根没跑，一眼可辨，
        不用再靠猜是不是空桩。
        """
        out: list[dict] = []
        checks: list[dict] = []
        values = list(by_day.values())
        window_days = len(values)

        # 1) 日事件量离群（需要 ≥3 天样本才有统计意义）
        if window_days >= 3:
            mean = statistics.mean(values)
            stdev = statistics.pstdev(values) or 1.0
            fired = 0
            for day, count in sorted(by_day.items()):
                z = (count - mean) / stdev
                if abs(z) >= 2:
                    fired += 1
                    msg = (
                        f"{day} 事件量 {count}，显著"
                        f"{'高于' if z > 0 else '低于'}日常均值 {mean:.0f}"
                    )
                    out.append(
                        {
                            "type": "daily_volume",
                            "title": f"{day} 事件量异常",
                            "day": day,
                            "count": count,
                            "expected": round(mean, 1),
                            "direction": "high" if z > 0 else "low",
                            "severity": "high" if abs(z) >= 3 else "medium",
                            "message": msg,
                            "reason": msg,
                        }
                    )
            checks.append(
                {
                    "rule": "daily_volume",
                    "status": "ran",
                    "fired": fired,
                    "detail": f"{window_days} 天样本，均值 {mean:.0f}，阈值 |z|≥2",
                }
            )
        else:
            checks.append(
                {
                    "rule": "daily_volume",
                    "status": "skipped",
                    "fired": 0,
                    "detail": f"需要 ≥3 天样本做 z 检验，当前仅 {window_days} 天",
                }
            )

        # 2) 凌晨活动占比
        night = sum(hourly[0:5])
        day_total = sum(hourly) or 1
        ratio = night / day_total
        if day_total < 50:
            checks.append(
                {
                    "rule": "night_activity",
                    "status": "skipped",
                    "fired": 0,
                    "detail": f"窗口事件仅 {day_total} 条，占比类指标不可靠（需 ≥50）",
                }
            )
        elif ratio > 0.15:
            msg = f"凌晨 0-5 点活动占比 {ratio * 100:.1f}%，可能存在夜间起夜或设备误触发"
            out.append(
                {
                    "type": "night_activity",
                    "title": "凌晨活动偏多",
                    "count": night,
                    "ratio": round(ratio * 100, 1),
                    "severity": "medium",
                    "message": msg,
                    "reason": msg,
                }
            )
            checks.append(
                {"rule": "night_activity", "status": "ran", "fired": 1,
                 "detail": f"占比 {ratio * 100:.1f}% > 15%"}
            )
        else:
            checks.append(
                {"rule": "night_activity", "status": "ran", "fired": 0,
                 "detail": f"占比 {ratio * 100:.1f}%，未超过 15% 阈值"}
            )

        # ── 语义异常（设备失联 / 空调整夜未关 / 睡过头 / 数据质量）──
        if store and start_iso and end_iso:
            try:
                out.extend(
                    self._semantic_anomalies(start_iso, end_iso, store, checks=checks)
                )
            except Exception as exc:  # 增强项失败绝不拖垮主报告
                print(f"[Insights] 语义异常检测跳过: {exc}")
                checks.append(
                    {"rule": "semantic_all", "status": "error", "fired": 0,
                     "detail": f"{type(exc).__name__}: {exc}"}
                )
        else:
            checks.append(
                {"rule": "semantic_all", "status": "skipped", "fired": 0,
                 "detail": "缺少 store / 时间窗，语义规则未执行"}
            )
        return {"anomalies": out, "checks": checks}

    def _semantic_anomalies(
        self, start_iso: str, end_iso: str, store, checks: list | None = None
    ) -> list[dict]:
        """语义层异常：设备失联 / 空调整夜未关 / 睡过头 / 数据质量。"""
        from collections import defaultdict
        from datetime import timedelta as _td

        out: list[dict] = []
        checks = checks if checks is not None else []

        def _note(rule, status, fired, detail):
            checks.append(
                {"rule": rule, "status": status, "fired": fired, "detail": detail}
            )

        # 1) 设备失联：窗口内曾有数据，但近 N 天突然静默
        try:
            end_dt = parse_ts(end_iso) or now_local(self.config.tz_offset_hours)
            start_dt = parse_ts(start_iso) or end_dt
            days = max(1, (end_dt.date() - start_dt.date()).days + 1)
            # only_enabled=True：与 get_entity_catalog / get_device_health 同口径，
            # 否则这里会扫到几百个未纳管实体，和别处的设备数对不上
            catalog = self.entity_catalog("", "", "", "", True, days)
            # 同 device_health：catalog 没有顶层 entities 键，必须摊平 rooms
            catalog_entities = [
                e
                for grp in catalog.get("rooms", {}).values()
                for e in grp.get("entities", [])
            ]
            fired = 0
            for e in catalog_entities:
                ls = e.get("last_seen")
                # stale_days 是 float，旧代码 isinstance(stale, int) 恒为 False → 永不触发
                stale = self._num_stale(e)
                if e.get("has_data") and stale is not None and stale >= 2:
                    fired += 1
                    name = e.get("display_name") or e.get("name") or e.get("entity_id")
                    msg = f"设备失联：{name} 已 {stale:g} 天无数据（最后在线 {ls}）"
                    out.append({
                        "type": "device_lost",
                        "title": f"{name} 失联",
                        "reason": msg,
                        "message": msg,
                        "severity": "high" if stale >= 5 else "medium",
                        "entity_id": e.get("entity_id"),
                        "name": name,
                        "last_seen": ls,
                        "stale_days": stale,
                    })
            _note(
                "device_lost", "ran", fired,
                f"扫描 {len(catalog_entities)} 个实体，阈值 stale_days≥2",
            )
        except Exception as exc:
            print(f"[Insights] 设备失联检测跳过: {exc}")
            _note("device_lost", "error", 0, f"{type(exc).__name__}: {exc}")

        # 2) 空调整夜未关：climate 在 0-5 点处于开启态 ≥3 晚
        try:
            rows = store.query_events(
                start_iso, end_iso, domains=["climate"], limit=8000, order="asc"
            )
            night_on: dict = defaultdict(set)  # entity -> {day}
            for r in rows:
                try:
                    h = int(str(r.get("ts", ""))[11:13])
                except Exception:
                    continue
                if 0 <= h < 6:
                    state = str(r.get("new_state", "")).lower()
                    if state and state not in {
                        "off", "idle", "0", "unavailable", "none", ""
                    }:
                        night_on[r["entity_id"]].add(str(r.get("ts", ""))[:10])
            # 阈值自适应：短窗口里要求「≥3 晚」等于永不触发，按窗口天数缩放
            end_dt2 = parse_ts(end_iso) or now_local(self.config.tz_offset_hours)
            start_dt2 = parse_ts(start_iso) or end_dt2
            win_days = max(1, (end_dt2.date() - start_dt2.date()).days + 1)
            min_nights = 3 if win_days >= 5 else 1
            fired = 0
            for entity_id, dayset in night_on.items():
                if len(dayset) >= min_nights:
                    fired += 1
                    msg = (
                        f"空调整夜未关：{entity_id} 在 "
                        f"{len(dayset)} 个夜晚的 0-5 点仍处于开启状态"
                    )
                    out.append({
                        "type": "ac_overnight",
                        "title": "空调夜间未关",
                        "reason": msg,
                        "message": msg,
                        "severity": "medium" if len(dayset) >= 3 else "low",
                        "entity_id": entity_id,
                        "nights": sorted(dayset),
                    })
            _note(
                "ac_overnight", "ran", fired,
                f"窗口 {win_days} 天，阈值 ≥{min_nights} 晚；"
                f"命中候选 {len(night_on)} 个 climate 实体",
            )
        except Exception as exc:
            print(f"[Insights] 空调整夜检测跳过: {exc}")
            _note("ac_overnight", "error", 0, f"{type(exc).__name__}: {exc}")

        # 3) 睡过头：某天首次活动明显晚于窗口基线（>2 小时且晚于 09:00）
        try:
            first_hours: list[tuple] = []
            d = parse_ts(start_iso) or now_local(self.config.tz_offset_hours)
            end_d = parse_ts(end_iso) or d
            while d.date() <= end_d.date():
                day_start = f"{d.date().isoformat()}T00:00:00"
                day_end = f"{d.date().isoformat()}T23:59:59"
                row = store.query_events(day_start, day_end, limit=1, order="asc")
                if row:
                    try:
                        fh = int(str(row[0].get("ts", ""))[11:13])
                        first_hours.append((d.date().isoformat(), fh))
                    except Exception:
                        pass
                d = d + _td(days=1)
            if len(first_hours) >= 3:
                hours = [h for _, h in first_hours]
                median = sorted(hours)[len(hours) // 2]
                fired = 0
                for day, fh in first_hours:
                    if fh >= 9 and fh - median >= 2:
                        fired += 1
                        msg = (
                            f"睡过头：{day} 首次活动在 {fh:02d}:00，"
                            f"晚于平时基线（中位 {median:02d}:00）约 {fh - median} 小时"
                        )
                        out.append({
                            "type": "late_riser",
                            "title": f"{day} 起床偏晚",
                            "reason": msg,
                            "message": msg,
                            "severity": "low",
                            "day": day,
                            "first_active_hour": fh,
                            "baseline_hour": median,
                        })
                _note(
                    "late_riser", "ran", fired,
                    f"{len(first_hours)} 天样本，基线中位 {median:02d}:00",
                )
            else:
                _note(
                    "late_riser", "skipped", 0,
                    f"需要 ≥3 天样本建立基线，当前 {len(first_hours)} 天",
                )
        except Exception as exc:
            print(f"[Insights] 睡过头检测跳过: {exc}")
            _note("late_riser", "error", 0, f"{type(exc).__name__}: {exc}")

        # 4) 数据质量异常：电量倒灌 / 单位冲突 / 心跳计数器
        try:
            issues = self.data_quality_issues(start_iso, end_iso, store)
            for it in issues:
                out.append({
                    "type": f"data_quality.{it['kind']}",
                    "title": it["title"],
                    "reason": it["message"],
                    "message": it["message"],
                    "severity": it.get("severity", "low"),
                    "entity_id": it.get("entity_id"),
                    "evidence": it.get("evidence"),
                })
            _note("data_quality", "ran", len(issues), "电量倒灌 / state-attr 单位冲突 / 心跳计数器")
        except Exception as exc:
            print(f"[Insights] 数据质量检测跳过: {exc}")
            _note("data_quality", "error", 0, f"{type(exc).__name__}: {exc}")

        return out

    def _iter_all_events(self, start_iso: str, end_iso: str, max_rows: int = 60000, **kw):
        """分页拉取窗口内全部事件。

        ``store.query_events`` 单次有 ``min(limit, 5000)`` 的硬上限，直接传
        limit=20000 会被**静默截断**成 5000 条——不报错、只少数据，
        导致活动识别 / 数据质量扫描看到的是「最早的 5000 条」而非全量。
        """
        out: list[dict] = []
        page = 5000
        offset = 0
        while len(out) < max_rows:
            rows = self.store.query_events(
                start_iso, end_iso, limit=page, offset=offset, **kw
            )
            out.extend(rows)
            if len(rows) < page:
                break
            offset += page
        return out[:max_rows]

    # ── 数据质量扫描 ────────────────────────────────────────────
    _BATTERY_TOKENS = ("battery", "电量", "电池")
    _HEARTBEAT_TOKENS = (
        "时间调试", "utc", "heartbeat", "心跳", "uptime", "计数器",
        "timestamp", "time_test", "时间戳",
    )

    def data_quality_issues(self, start_iso: str, end_iso: str, store, limit=30000) -> list[dict]:
        """扫描源数据本身的质量问题，把「脏数据」显式暴露而不是默默展示。

        覆盖用户实测到的三类：
        1. **电量倒灌** —— 非充电设备电量从 57 跳回 65，物理上不可能，是坏值；
           不做单调性校验会误导「设备快没电了吗」这类判断。
        2. **单位冲突** —— 同一物理量 state=0.019 而 attrs 里 =19.0，差 1000 倍
           （kWh vs Wh），前端混用会差三个数量级。
        3. **心跳计数器** —— 每 30 分钟固定 +N 的自增值（如面板灯「时间调试」），
           不是用户行为，会污染行为计数。
        """
        from collections import defaultdict

        rows = self._iter_all_events(
            start_iso, end_iso, max_rows=limit, domains=["sensor"], order="asc"
        )
        series: dict[str, list[tuple]] = defaultdict(list)
        unit_conflicts: dict[str, dict] = {}
        for r in rows:
            eid = str(r.get("entity_id", ""))
            val = _as_float(r.get("new_state"))
            if val is not None:
                series[eid].append((str(r.get("ts", "")), val))
            # 单位冲突：属性里存在与实体同名的数值，且与 state 相差约 1000 倍
            if val:
                attrs = _parse_attrs(r.get("attrs_json"))
                tail = eid.split(".", 1)[-1]
                for k, v in attrs.items():
                    av = _as_float(v)
                    if av is None or av == 0 or k not in tail:
                        continue
                    ratio = av / val
                    if 900 <= abs(ratio) <= 1100 or 0.0009 <= abs(ratio) <= 0.0011:
                        unit_conflicts.setdefault(eid, {
                            "entity_id": eid, "attr": k, "state": val,
                            "attr_value": av, "ratio": round(ratio, 4),
                        })

        names = self.name_map()
        issues: list[dict] = []

        def _disp(eid):
            return (names.get(eid, {}) or {}).get("friendly_name") or eid

        # 1) 电量倒灌
        for eid, pts in series.items():
            low = eid.lower()
            disp = str(_disp(eid))
            if not any(t in low or t in disp for t in self._BATTERY_TOKENS):
                continue
            if len(pts) < 3:
                continue
            rises = [
                {"at": pts[i][0], "from": pts[i - 1][1], "to": pts[i][1]}
                for i in range(1, len(pts))
                if pts[i][1] - pts[i - 1][1] >= 2
            ]
            if rises:
                issues.append({
                    "kind": "battery_backflow",
                    "title": f"{disp} 电量倒灌",
                    "entity_id": eid,
                    "severity": "medium",
                    "message": (
                        f"{disp} 电量在窗口内出现 {len(rises)} 次上升"
                        f"（如 {rises[0]['from']:g}→{rises[0]['to']:g}），"
                        "非充电设备不可能回升，属源数据错误，请勿据此判断续航"
                    ),
                    "evidence": rises[:5],
                })

        # 2) 单位冲突
        for eid, c in unit_conflicts.items():
            issues.append({
                "kind": "unit_conflict",
                "title": f"{_disp(eid)} 单位不一致",
                "entity_id": eid,
                "severity": "medium",
                "message": (
                    f"{_disp(eid)} 的 state={c['state']:g} 与属性 {c['attr']}={c['attr_value']:g} "
                    f"相差约 {abs(c['ratio']):.0f} 倍（疑似 kWh / Wh 混用），"
                    "前端混用会差三个数量级"
                ),
                "evidence": c,
            })

        # 3) 心跳计数器
        for eid, pts in series.items():
            low = eid.lower()
            disp = str(_disp(eid))
            if not any(t in low or t in disp.lower() for t in self._HEARTBEAT_TOKENS):
                continue
            if len(pts) < 5:
                continue
            # 心跳计数器的特征是「绝大多数时候按固定步长自增」。
            # 不能要求步长唯一（实测厨房面板灯是 +30 / +226 交替），
            # 也不能要求全程单调（实测有 2 次 2^24 级别的计数器回绕），
            # 否则这类噪声永远抓不到。
            diffs = [round(pts[i][1] - pts[i - 1][1], 6) for i in range(1, len(pts))]
            pos = [d for d in diffs if d > 0]
            if len(pos) < max(4, int(len(diffs) * 0.8)):
                continue
            step, freq = Counter(pos).most_common(1)[0]
            if freq < max(3, len(diffs) * 0.3):
                continue
            wraps = [d for d in diffs if d < 0]
            issues.append({
                "kind": "heartbeat_counter",
                "title": f"{disp} 是设备心跳计数器",
                "entity_id": eid,
                "severity": "low",
                "message": (
                    f"{disp} 共 {len(pts)} 条，{len(pos)}/{len(diffs)} 次为固定步长自增"
                    f"（主导步长 +{step:g}）"
                    + (f"，另有 {len(wraps)} 次计数器回绕（最大跳变 {min(wraps):g}）"
                       if wraps else "")
                    + "，属设备自检心跳而非用户行为，不应计入行为统计"
                ),
                "evidence": {
                    "samples": len(pts),
                    "dominant_step": step,
                    "wrap_count": len(wraps),
                    "first": pts[0],
                    "last": pts[-1],
                },
            })
        return issues

    # ─────────────────────────────────────────────────────────────
    # 新增能力：设备健康 / 数据覆盖 / 气候会话 / 活动识别 / 自然语言问答
    # ─────────────────────────────────────────────────────────────

    def device_health(
        self, room="", category="", query="", days=7, stale_days=3, only_enabled=True
    ) -> dict:
        """设备健康探测：基于 entity_catalog 的 has_data / last_seen / stale_days，
        主动揪出失联 / 没电 / 长期静默的设备。

        口径与 ``get_entity_catalog`` 对齐：默认 ``only_enabled=True``，即只看
        「已启用采集」的实体。之前这里硬编码 only_enabled=False，把禁用/未纳管实体
        也算进来，导致 health 报 1307 而 catalog 报 197，两个工具对「有多少设备」
        差 6.6 倍。需要看全量时显式传 ``only_enabled=False``。
        """
        catalog = self.entity_catalog(room, category, "", query, only_enabled, days)
        # entity_catalog 返回 {rooms: {room: {entities: [...]}}}，没有顶层 entities 键，
        # 之前 catalog.get("entities", []) 永远取到 []，导致 total_entities=0。必须摊平。
        entities = [
            e
            for grp in catalog.get("rooms", {}).values()
            for e in grp.get("entities", [])
        ]

        # 被模板引用标记：哪些实体被行为洞察模板（run_template）引用，优先修。
        # 引用来源 = 模板实体的 entity_id（裸实体）+ logical_id 经身份层解析出的候选实体。
        referenced_set = set()
        try:
            rt = self._get_rt()
            if rt is not None and getattr(rt, "templates", None) is not None:
                for tpl in rt.templates.list_all():
                    for eq in (getattr(tpl, "entities", None) or []):
                        eid = getattr(eq, "entity_id", "") or ""
                        if eid:
                            referenced_set.add(eid)
                        lid = getattr(eq, "logical_id", "") or ""
                        if lid and getattr(rt, "identity", None) is not None:
                            try:
                                resolved, _ = rt.identity.resolve(lid)
                                referenced_set.update(resolved or [])
                            except Exception:
                                pass
        except Exception as exc:
            print(f"[Insights] 设备健康引用标记跳过: {exc}")

        healthy, no_data, stale = [], [], []
        for e in entities:
            e["referenced"] = bool(e.get("entity_id") in referenced_set)
            sd = self._num_stale(e)  # stale_days 是 float，不能用 isinstance(x, int)
            if not e.get("has_data"):
                no_data.append(e)
            elif sd is not None and sd >= stale_days:
                stale.append(e)
            else:
                healthy.append(e)

        # 数据质量问题（电量倒灌 / 单位冲突 / 心跳计数器）随健康报告一起给出，
        # 避免用户看着「电量 57→65」还以为设备真的在回血。
        quality: list[dict] = []
        try:
            start_iso, end_iso, _m = self.resolve_range(days, "", "")
            quality = self.data_quality_issues(start_iso, end_iso, self.store)
        except Exception as exc:
            print(f"[Insights] 健康报告的数据质量扫描跳过: {exc}")

        return {
            "ok": True,
            "window_days": days,
            "stale_threshold_days": stale_days,
            "only_enabled": only_enabled,
            "scope": (
                "仅统计已启用采集的实体，与 get_entity_catalog 口径一致"
                if only_enabled
                else "统计全部实体（含未启用采集的），数量会明显多于 get_entity_catalog 默认口径"
            ),
            "total_entities": len(entities),
            "catalog_total": catalog.get("total"),
            "referenced_entities": sum(1 for e in entities if e.get("referenced")),
            "referenced_entity_ids": sorted(
                e["entity_id"] for e in entities if e.get("referenced")
            ),
            "healthy": len(healthy),
            "no_data": len(no_data),
            "stale": len(stale),
            "no_data_entities": no_data,
            "stale_entities": stale,
            "healthy_entities": healthy,
            "data_quality_issues": quality,
            "data_quality_count": len(quality),
            "hint": "no_data=从未采集到；stale=最近 N 天无数据，可能是没电/失联/长期关闭；"
            "data_quality_issues=源数据本身有问题（电量倒灌/单位冲突/心跳计数器）",
        }

    def data_coverage(self, days=7, start="", end="") -> dict:
        """数据覆盖报告：明确指出窗口内实际「有数据」的日期，避免误以为前几天也有数据。"""
        start_iso, end_iso, meta = self.resolve_range(days, start, end)
        # day_counts() 返回的是 {day: count} 字典（不是元组列表），直接复用即可；
        # 之前用 `for d, c in rows` 当元组迭代，会对字符串键解包 → 崩溃。
        by_day = self.store.day_counts(start_iso, end_iso)
        try:
            d0 = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
            d1 = datetime.strptime(end_iso[:10], "%Y-%m-%d").date()
        except Exception:
            d0 = d1 = None
        days_list = []
        total = 0
        if d0 and d1:
            cur = d0
            while cur <= d1:
                day = cur.isoformat()
                c = by_day.get(day, 0)
                days_list.append({"day": day, "events": c, "has_data": c > 0})
                total += c
                cur = cur + timedelta(days=1)
        with_data = [d["day"] for d in days_list if d["has_data"]]
        note = ""
        if days_list and len(with_data) < len(days_list):
            note = (
                f"窗口共 {len(days_list)} 天，实际只有 {len(with_data)} 天有数据："
                f"{', '.join(with_data) if with_data else '无'}"
            )
        return {
            "ok": True,
            "window": meta,
            "total_events": total,
            "days_with_data": len(with_data),
            "days_total": len(days_list),
            "first_day_with_data": with_data[0] if with_data else None,
            "last_day_with_data": with_data[-1] if with_data else None,
            "missing_days": [d["day"] for d in days_list if not d["has_data"]],
            "note": note,
            "days": days_list,
        }

    def climate_sessions(self, query="", room="", days=7, start="", end="") -> dict:
        """气候会话：把 climate 实体的开启时段拼成「设定温度 + 室温 + 运行时长」。

        直接从事件 attrs 解析 current_temperature（室温）与 temperature（设定温度），
        解决原先「只有 hvac_action、缺温度」的问题。
        """
        start_iso, end_iso, meta = self.resolve_range(days, start, end)
        entities = None
        if query or room:
            cat = self.entity_catalog(
                room=room, category="climate", query=query, only_enabled=False
            )
            # catalog 没有顶层 entities 键（同 device_health 的坑），必须摊平 rooms，
            # 否则这里恒为 []，query 过滤形同虚设。
            entities = [
                e["entity_id"]
                for grp in cat.get("rooms", {}).values()
                for e in grp.get("entities", [])
            ] or None
        rows = self.store.query_events(
            start_iso, end_iso, rooms=[room] if room else None,
            entities=entities, domains=["climate"], limit=5000, order="asc",
        )
        OPEN = {"heat", "cool", "dry", "fan", "auto", "heat_cool", "boost", "fan_only"}
        CLOSED = {"off"}
        cur: dict = {}
        sessions: list = []
        rows_seen = 0
        rows_ignored = 0
        for r in rows:
            rows_seen += 1
            eid = r["entity_id"]
            state = str(r.get("new_state", "")).strip().lower()
            attrs = _parse_attrs(r.get("attrs_json"))
            setpoint = _as_float(attrs.get("temperature"))
            if setpoint is None:
                setpoint = _as_float(attrs.get("target_temp_high"))
            cur_temp = _as_float(attrs.get("current_temperature"))
            sess = cur.get(eid)

            # 关键修复：只有显式 "off" 才结束会话。
            # 属性变更事件常带空/unknown/unavailable 状态，旧逻辑把它们当作「关机」，
            # 于是每条 cool 事件都被立刻截断成 start==end 的 0 分钟会话。
            if state in CLOSED:
                if sess is not None:
                    sess["end"] = r["ts"]
                    sessions.append(self._finalize_climate_session(sess, still_on=False))
                    cur[eid] = None
                continue
            if state not in OPEN:
                rows_ignored += 1
                # 状态不可判定，但温度仍要采信，挂到进行中的会话上
                if sess is not None:
                    if setpoint is not None:
                        sess["setpoints"].append(setpoint)
                    if cur_temp is not None:
                        sess["room_temps"].append(cur_temp)
                    sess["end"] = r["ts"]
                continue

            if sess is None:
                sess = {
                    "entity_id": eid,
                    "room": r.get("room", ""),
                    "start": r["ts"],
                    "end": r["ts"],
                    "hvac_actions": set(),
                    "setpoints": [],
                    "room_temps": [],
                }
                cur[eid] = sess
            sess["end"] = r["ts"]
            if state:
                sess["hvac_actions"].add(state)
            if setpoint is not None:
                sess["setpoints"].append(setpoint)
            if cur_temp is not None:
                sess["room_temps"].append(cur_temp)
        for _eid, sess in cur.items():
            if sess is not None:
                # 窗口结束时仍未收到 off —— 是「未闭合」而不是「已结束」，必须说明
                sessions.append(self._finalize_climate_session(sess, still_on=True))
        sessions.sort(key=lambda s: s["start"])
        with_temp = sum(1 for s in sessions if s.get("room_temp_c") is not None)
        return {
            "ok": True,
            "window": meta,
            "total_sessions": len(sessions),
            "events_scanned": rows_seen,
            "events_state_unknown": rows_ignored,
            "sessions_with_temperature": with_temp,
            "temperature_note": (
                "温度取自事件 attrs 的 current_temperature / temperature；"
                "采集端在本次修复后才开始全量快照温度，早于修复时间的历史会话仍会是 null"
                if with_temp < len(sessions)
                else "全部会话均已带室温/设定温度"
            ),
            "sessions": sessions,
        }

    @staticmethod
    def _finalize_climate_session(sess, still_on: bool = False) -> dict:
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

    def infer_activities(self, days=7, rooms="", start="", end="", activities=None) -> dict:
        """活动识别：基于设备共现与时段，识别做饭 / 洗澡 / 睡眠 / 看电视 / 离家。

        返回每个活动在窗口内的发生记录（日期 + 时段 + 置信度 + 证据），
        让 Agent 直接拿到「语义层」结论，而不用自己拼原始动线。
        activities：可选活动类型白名单（内置名 cooking/bathing/watching_tv/working/sleeping/away
        或 define_activity 注册的自定义名），只返回命中的类型。
        """
        start_iso, end_iso, meta = self.resolve_range(days, start, end)
        room_filter = [r.strip() for r in str(rooms or "").split(",") if r.strip()] or None
        # 审计 I1：同窗口重复识别直接复用缓存结果，避免重复全窗口事件扫描。
        # days 语义下窗口终点=当前时刻（逐秒变化），会让 key 抖动导致缓存永不命中；
        # 统一按「分钟」粒度对齐 key（≤60s 陈旧，与 TTL 同量级）。显式 start/end 时
        # （如环比内部 cur/prev）本就是日对齐字符串，量化无影响。
        ckey = ("infer_activities", tuple(room_filter or ()),
                start_iso[:16], end_iso[:16],
                tuple(sorted(activities)) if activities else None)
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached
        rows = self._iter_all_events(
            start_iso, end_iso, rooms=room_filter,
            exclude_domains=list(TELEMETRY_DOMAINS), order="asc",
        )
        # 复用与 behavior_insights 一致的噪声识别：加湿器缺水等垄断型 alarm/抖动实体
        # 会从事件流里剔除，否则它们每几分钟一发、把全屋静默间隔填满，
        # 导致 sleeping/away 永远不满足「≥3h/≥90min 无事件」。
        noise_ids = self._noise_entities(start_iso, end_iso, room_filter)
        detected = self._detect_activities(
            rows, activities, exclude_entities=noise_ids,
            start_iso=start_iso, end_iso=end_iso,
        )
        activities = detected["activities"]

        # 看电视兜底：这个家里没有 media_player 实体，电视只暴露一个「昨日播放时长」
        # 遥测传感器（属 TELEMETRY_DOMAINS，被行为层排除）。与其报「识别不出」，
        # 不如用它给出结论，同时说清这是设备自报日累计、不是精确时段。
        if not any(a["activity"] == "watching_tv" for a in activities):
            try:
                tv_acts = self._tv_from_telemetry(start_iso, end_iso)
                if tv_acts:
                    activities.extend(tv_acts)
                    activities.sort(key=lambda a: (a["day"], a.get("start_ts") or "", a["activity"]))
                    detected["detectors"]["watching_tv"] = {
                        "fired_days": len(tv_acts),
                        "matched_events": len(tv_acts),
                        "reason": "无 media_player 实体，改用电视「播放时长」遥测传感器推断",
                    }
            except Exception as exc:
                print(f"[Insights] 电视遥测兜底跳过: {exc}")

        result = {
            "ok": True,
            "window": meta,
            "behavior_only": True,
            "total_activities": len(activities),
            "activities": activities,
            "activity_types": sorted({a["activity"] for a in activities}),
            # 每类检测器都留痕：没识别出「看电视/工作」到底是没这回事，还是没有对应实体，
            # 这里给出可核对的原因，不再让人怀疑是空桩。
            "detector_report": detected["detectors"],
            "signal_inventory": detected["inventory"],
        }
        self._cache_put(ckey, result)
        return result

    # ── Phase 2-A：用户画像 / 行为环比 / 证据溯源 ──────────────────────────
    def get_user_persona(self, days: int = 14) -> dict:
        """合成用户滚动行为画像：近期活动聚合（出现天数/频次/时段/房间/置信度）+ 房间活跃度。

        做长期用户理解、个性化、健康提醒时直接调用，不要自己拉原始事件算。
        """
        res = self.infer_activities(days=days)
        acts = res.get("activities", [])
        by_type: dict = {}
        for a in acts:
            t = a["activity"]
            d = by_type.setdefault(
                t, {"days": set(), "count": 0, "conf_sum": 0.0,
                    "rooms": set(), "windows": [], "evidence": []}
            )
            d["days"].add(a["day"])
            d["count"] += 1
            d["conf_sum"] += float(a.get("confidence", 0))
            if a.get("room"):
                d["rooms"].add(a["room"])
            if a.get("observed_window"):
                d["windows"].append(a["observed_window"])
            if a.get("evidence"):
                d["evidence"].append(a["evidence"])
        bi = self.behavior_insights(days=days)
        bi_rooms = bi.get("rooms") or {}
        top_rooms = [
            {"room": r, "events": d.get("event_count", 0)}
            for r, d in sorted(bi_rooms.items(), key=lambda kv: -kv[1].get("event_count", 0))[:3]
        ]
        persona = {}
        for t, d in by_type.items():
            persona[t] = {
                "days_observed": len(d["days"]),
                "occurrences": d["count"],
                "avg_confidence": round(d["conf_sum"] / max(1, d["count"]), 2),
                "rooms": sorted(d["rooms"]),
                "typical_windows": d["windows"][:5],
                "sample_evidence": d["evidence"][:3],
            }
        summary = self._synthesize_persona(persona, top_rooms, bi.get("most_active_room"), days)
        return {
            "ok": True,
            "window_days": days,
            "persona": persona,
            "top_rooms": top_rooms,
            "most_active_room": bi.get("most_active_room"),
            "total_events": bi.get("total_events"),
            "summary": summary,
        }

    def _compare_windows(self, compare_days: int):
        """返回对齐到自然日边界的环比窗口（修复 #10：避免 7×24h 滚动窗口跨 8 个日历日）。

        两个窗口等长、首尾相接不重叠，各恰好 compare_days 个完整自然日。
        返回 (cur_start, cur_end, prev_start, prev_end) 均为 ISO 字符串。
        """
        cd = max(1, min(int(compare_days or 7), 365))
        today = now_local(self.tz).date()
        # cur 窗口 = 含今天在内的最近 cd 个完整自然日：[tomorrow-cd, tomorrow)
        cur_end = (today + timedelta(days=1)).isoformat() + "T00:00:00"
        cur_start = (today + timedelta(days=1) - timedelta(days=cd)).isoformat() + "T00:00:00"
        prev_end = cur_start
        prev_start = (today + timedelta(days=1) - timedelta(days=2 * cd)).isoformat() + "T00:00:00"
        return cur_start, cur_end, prev_start, prev_end

    def get_behavior_insights(self, compare_days: int = 7) -> dict:
        """行为环比：最近 compare_days 天 vs 上一个等长窗口。

        修复 #9：除活动次数/天数外，新增 **时长/强度**（各活动累计分钟）与 **温控维度**
        （空调开启时长、平均设定/室温、设定温度区间变化）；修复 #10：窗口对齐到自然日
        边界，每个窗口恰好 compare_days 个日历日，不再跨 8 天。

        做周报、习惯变化追踪时调用。
        """
        cur_start, cur_end, prev_start, prev_end = self._compare_windows(compare_days)

        # 审计 I1：整个环比结果按 compare_days 缓存（TTL 60s）。Agent 连续调用同
        # 环比时不重复做 cur/prev 两窗口的全量识别 + 温控会话聚合，首调后立即可用。
        ckey = ("get_behavior_insights", int(compare_days or 7))
        cached = self._cache_get(ckey)
        if cached is not None:
            return cached

        cur = self.infer_activities(start=cur_start, end=cur_end)
        prev = self.infer_activities(start=prev_start, end=prev_end)

        def _agg(acts):
            out: dict = {}
            for a in acts:
                e = out.setdefault(a["activity"], {"count": 0, "days": set(), "minutes": 0})
                e["count"] += 1
                e["days"].add(a["day"])
                dm = a.get("duration_minutes")
                if dm is None and a.get("start_ts") and a.get("end_ts"):
                    d0, d1 = parse_ts(a["start_ts"]), parse_ts(a["end_ts"])
                    dm = int((d1 - d0).total_seconds() // 60) if d0 and d1 else None
                if dm is not None:
                    e["minutes"] += dm
            return out

        ca, pa = _agg(cur["activities"]), _agg(prev["activities"])
        comparison = {}
        for t in sorted(set(ca) | set(pa)):
            c = ca.get(t, {"count": 0, "days": set(), "minutes": 0})
            p = pa.get(t, {"count": 0, "days": set(), "minutes": 0})
            comparison[t] = {
                "current_occurrences": c["count"],
                "previous_occurrences": p["count"],
                "delta_occurrences": c["count"] - p["count"],
                "current_days": len(c["days"]),
                "previous_days": len(p["days"]),
                "delta_days": len(c["days"]) - len(p["days"]),
                "current_minutes": c["minutes"],
                "previous_minutes": p["minutes"],
                "delta_minutes": c["minutes"] - p["minutes"],
            }
        # 温控维度（修复 #9）
        climate_cmp = self._climate_compare(cur_start, cur_end, prev_start, prev_end)
        summary = self._synthesize_compare(comparison, compare_days, climate_cmp)
        result = {
            "ok": True,
            "compare_days": compare_days,
            "current_window": {"start": cur_start, "end": cur_end, "days": compare_days, "count": len(cur["activities"])},
            "previous_window": {"start": prev_start, "end": prev_end, "days": compare_days, "count": len(prev["activities"])},
            "comparison": comparison,
            "climate_comparison": climate_cmp,
            "summary": summary,
        }
        self._cache_put(ckey, result)
        return result

    def _climate_compare(self, cur_start, cur_end, prev_start, prev_end) -> dict:
        """温控维度环比（修复 #9）：空调开启时长 + 平均设定/室温 + 设定温度区间变化。"""
        def _agg_cl(start, end):
            cs = self.climate_sessions(start=start, end=end)
            sessions = cs.get("sessions", [])
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
        cur = _agg_cl(cur_start, cur_end)
        prev = _agg_cl(prev_start, prev_end)
        d_sp = None
        if cur["avg_setpoint_c"] is not None and prev["avg_setpoint_c"] is not None:
            d_sp = round(cur["avg_setpoint_c"] - prev["avg_setpoint_c"], 1)
        return {
            "current": cur,
            "previous": prev,
            "delta_hours": round(cur["hours"] - prev["hours"], 1),
            "delta_avg_setpoint_c": d_sp,
        }

    def explain_insight(self, insight_id: str) -> dict:
        """证据溯源：给定 insight(活动 id) 或 agent 记忆 id，返回底层触发事件与 source_refs 解析。

        - insight id 来自 infer_activities 每个活动的 id 字段；
        - agent 记忆 id 来自 add_semantic_memory 返回。
        """
        act = self.store.get_detected_activity(insight_id)
        if act:
            entities = act.get("entities_json") or []
            events = []
            if entities and act.get("day"):
                for eid in entities:
                    for ev in self.store.get_events_by_entity(eid, act.get("day")):
                        events.append({
                            k: ev[k] for k in (
                                "ts", "room", "entity_id", "domain", "action",
                                "new_state", "old_state",
                            ) if k in ev
                        })
            return {
                "ok": True, "type": "detected_activity",
                "activity_id": act.get("activity_id"), "activity": act.get("activity"),
                "day": act.get("day"), "confidence": act.get("confidence"),
                "room": act.get("room"), "evidence": act.get("evidence"),
                "entities": entities, "events": events[:15],
            }
        mem = self.store.get_agent_memory(insight_id)
        if mem:
            refs = json.loads(mem.get("source_refs_json") or "[]")
            resolved = []
            for r in refs:
                if r.startswith("event:"):
                    ev = self.store.get_event(r[6:])
                    if ev:
                        resolved.append({
                            "ref": r,
                            **{k: ev[k] for k in (
                                "ts", "room", "entity_id", "domain", "action", "new_state"
                            ) if k in ev},
                        })
                elif r.startswith("insight:"):
                    a2 = self.store.get_detected_activity(r[8:])
                    if a2:
                        resolved.append({
                            "ref": r, "activity": a2.get("activity"),
                            "day": a2.get("day"), "confidence": a2.get("confidence"),
                            "evidence": a2.get("evidence"),
                        })
            return {
                "ok": True, "type": "agent_memory",
                "memory_id": insight_id, "text": mem.get("text"),
                "state": mem.get("state"), "trust": mem.get("trust"),
                "source_refs": refs, "resolved": resolved,
            }
        return {"ok": False, "error": "未找到该 id 对应的洞察或记忆"}

    @staticmethod
    def _synthesize_persona(persona: dict, top_rooms: list, most_active: str, days: int) -> str:
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

    @staticmethod
    def _synthesize_compare(comparison: dict, days: int, climate_cmp: dict = None) -> str:
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

    # ── Phase 2-B：自定义活动规则注册 ──────────────────────────────────────
    def define_activity(
        self, name, room="", tags=None, start_hour=0, end_hour=23,
        min_events=1, confidence=0.6, note="",
    ) -> dict:
        """注册/更新一条自定义活动识别规则（agent 教系统识别新行为，如午睡/健身）。

        - name：活动类型名（英文 snake_case），作为 infer_activities 的 activity 类型；
        - room：房间子串（可选），命中才计；tags：需命中的设备标签（可选，如 presence/door/media）；
        - start_hour/end_hour：生效时段；min_events：最小触发次数；confidence：置信度。
        注册后 infer_activities 自动套用；识别出的活动可被 source_refs=insight:<id> 引用写回记忆。

        实体覆盖预检（修复 #7）：若规则要求的 tags 在指定房间（或全屋）最近窗口内
        完全没有对应实体产生过事件，明确报『缺实体类型』并给出代理建议，而非运行期
        含糊的『标签没匹配 / 未命中』。
        """
        rule = {
            "name": name, "room": room, "tags": tags or [],
            "start_hour": start_hour, "end_hour": end_hour,
            "min_events": min_events, "confidence": confidence, "note": note,
        }
        rule_id = self.store.upsert_activity_rule(rule)
        rule["rule_id"] = rule_id
        out = {
            "ok": True, "rule": rule,
            "message": "规则已注册，下次 infer_activities 自动套用；可经 source_refs=insight:<id> 写回记忆",
        }
        warn = self._check_activity_rule_coverage(room, tags or [])
        if warn:
            out["coverage_warning"] = warn["message"]
            out["missing_tags"] = warn["missing_tags"]
            out["message"] += " ⚠️ " + warn["message"]
        return out

    def _check_activity_rule_coverage(self, room, tags, window_days: int = 14) -> Optional[dict]:
        """注册自定义规则前的实体覆盖预检（修复 #7）。

        返回 None 表示覆盖良好；否则返回 warning dict（含 missing_tags 与 message），
        说明规则要求的标签在指定房间（或全屋）最近 window_days 天内没有任何对应实体
        产生过事件——即『缺实体类型』，规则将无法命中。

        注意：这是诊断而非拦截。规则仍会被注册（用户可先注册、补齐传感器后自动生效），
        但会显式给出『缺实体类型』的明确结论与代理建议，避免运行期含糊的『未命中』。
        """
        if not tags:
            return None
        end = now_local(self.tz)
        start = end - timedelta(days=window_days)
        names = self.name_map()
        present: set = set()
        try:
            for r in self._iter_all_events(start.isoformat(), end.isoformat(), max_rows=5000, order="desc"):
                eid = str(r.get("entity_id", ""))
                room_r = str(r.get("room", "") or "")
                if room and room not in room_r:
                    continue
                disp = str((names.get(eid, {}) or {}).get("friendly_name") or "")
                present |= (self._tags_of(eid, disp) & set(tags))
        except Exception:
            return None  # 预检失败不阻塞注册，只跳过告警
        missing = [t for t in tags if t not in present]
        if not missing:
            return None
        scope = f"房间「{room}」" if room else "全屋"
        return {
            "ok": False,
            "missing_tags": missing,
            "message": (
                f"{scope}最近{window_days}天内没有 {missing} 类型实体产生过事件，"
                f"该规则将无法判定——这是『缺实体类型』而非标签匹配问题。"
                f"建议：改用该房间已有的信号做代理（如 door 门磁 / climate 空调 / "
                f"appliance 插座 / media 电视），或先补齐 {missing} 传感器再注册。"
            ),
        }

    def _tv_from_telemetry(self, start_iso: str, end_iso: str) -> list[dict]:
        """从电视自报的「播放时长」遥测传感器反推观看行为。

        注意口径：这类传感器报的是**昨日累计**，所以归属到事件日期的前一天；
        原始值没有单位（可能是小时），因此只给结论不硬编时段。
        """
        names = self.name_map()
        # 先按名字挑出候选实体再查，避免把整个 sensor 域拉回来（还会撞上 5000 条上限）
        candidates = []
        for eid, info in names.items():
            disp = str((info or {}).get("friendly_name") or "")
            blob = (eid + disp).lower()
            if ("电视" in disp or "tv" in blob) and (
                "播放时长" in disp or "观看时长" in disp
                or "duration" in blob or "playtime" in blob
            ):
                candidates.append(eid)
        if not candidates:
            return []
        rows = self._iter_all_events(
            start_iso, end_iso, entities=candidates, order="asc"
        )
        out: list[dict] = []
        seen: set = set()
        for r in rows:
            eid = str(r.get("entity_id", ""))
            disp = str((names.get(eid, {}) or {}).get("friendly_name") or "")
            blob = (eid + disp).lower()
            val = _as_float(r.get("new_state"))
            if not val or val <= 0:
                continue
            ts = str(r.get("ts", ""))
            # 「昨日播放时长」→ 实际观看发生在前一天
            day = ts[:10]
            if "昨日" in disp or "yesterday" in blob:
                d = parse_ts(ts)
                if d:
                    day = (d - timedelta(days=1)).strftime("%Y-%m-%d")
            if (day, eid) in seen:
                continue
            seen.add((day, eid))
            out.append(self._act(
                day, "watching_tv", 0.5,
                f"电视自报「{disp or eid}」= {val:g}（该值无单位标注，疑似小时）；"
                "家中没有 media_player 实体，这是设备侧日累计统计，"
                "只能证明当天看过电视，无法定位具体时段",
                18, 23,
                entities=[eid],
                method="遥测传感器（日累计），非事件级推断",
                raw_value=val,
            ))
        return out

    # 语义标签：entity_id token 与中文友好名双通道匹配。
    # 只靠 entity_id 子串（如 "occupancy"）会漏掉厂商命名不同的设备，
    # 这是上一版 sleeping/working/watching_tv 全都识别不出来的原因。
    _TAG_RULES: dict[str, tuple[tuple, tuple]] = {
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

    @classmethod
    def _tags_of(cls, eid: str, name: str) -> set:
        low = (eid or "").lower()
        nm = name or ""
        tags = set()
        for tag, (id_tokens, name_tokens) in cls._TAG_RULES.items():
            if any(t in low for t in id_tokens) or any(t in nm for t in name_tokens):
                tags.add(tag)
        return tags

    def _detect_activities(self, rows, activity_filter=None, exclude_entities=None,
                           start_iso=None, end_iso=None) -> dict:
        """基于事件流识别活动。

        与旧版的区别：
        1. 识别不再只匹配 entity_id 子串，而是 **entity_id + 中文友好名** 双通道打标；
        2. sleeping / away 改为「行为事件的静默间隔」推断，能给出真实起止时间，
           而不是靠某个占用传感器恰好在夜间报 off；
        3. 每类检测器都返回命中/未命中的原因，便于核对。
        """
        from collections import defaultdict

        names = self.name_map()
        INACTIVE = {
            "off", "idle", "0", "unavailable", "unknown", "none", "closed",
            "standby", "not_home", "", "false",
        }

        _excl = set(exclude_entities or set())
        events: list[dict] = []
        for r in rows:
            ts = str(r.get("ts", ""))
            dt = parse_ts(ts)
            if dt is None:
                continue
            eid = str(r.get("entity_id", ""))
            # 噪声源（加湿器缺水等垄断型 alarm/抖动）不进入事件流，
            # 否则会伪造「全屋仍有活动」、破坏睡眠/离家的静默间隔判定。
            if eid in _excl:
                continue
            disp = str((names.get(eid, {}) or {}).get("friendly_name") or "")
            state = str(r.get("new_state", "")).strip().lower()
            events.append({
                "dt": dt,
                "day": ts[:10],
                "hour": dt.hour,
                "ts": ts,
                "eid": eid,
                "name": disp or self._fallback_name(eid),
                "state": state,
                "room": str(r.get("room", "") or ""),
                "active": state not in INACTIVE,
                # 归一为 set：`_tags_of` 契约返回 set，但测试/调用方可能以 list 覆盖，
                # 后续静默判定用集合运算（`tags & {...}`），此处统一类型避免 list&set 崩溃。
                "tags": set(self._tags_of(eid, disp) or ()),
            })
        events.sort(key=lambda e: e["dt"])

        # 静默判定只看「强人类活动」信号（人在 / 门 / 家电 / 电脑）。
        # 摄像头 AI 场景、电视/音箱、灯、窗帘、自动化触发等环境抖动并不证明
        # 「人醒着在动」，若计入会把夜间静默间隔填满，导致睡眠/离家永远不成立。
        _SILENCE_BREAK_TAGS = {"presence", "door", "appliance", "computer"}
        silence_events = [e for e in events if e["tags"] & _SILENCE_BREAK_TAGS]
        # 睡眠静默忽略 presence：雷达存在传感器在「人睡着在床」时仍会持续触发，
        # 若计入则夜间永远满事件、睡眠无法成立。睡眠只看「人主动在操作」的信号
        # （家电 / 门 / 电脑），媒体关闭 + 无操作 ≥3h 即视为入睡。
        _SLEEP_BREAK_TAGS = {"appliance", "door", "computer"}
        sleep_silence_events = [e for e in events if e["tags"] & _SLEEP_BREAK_TAGS]

        # ── 信号硬排除层（学习策略：teach_signal kind='hard'）──
        # 一次加载生效中的排除规则；命中实体在对应 scope 直接跳过（无歧义硬排）。
        _sig_excl = self.store.list_signal_exclusions(include_revoked=False)
        _sig_all = {x["entity_id"] for x in _sig_excl if x["scope"] == "all"}
        _sig_pair = {(x["entity_id"], x["scope"]) for x in _sig_excl}

        def _signal_excluded(eid, scope):
            """某实体在某检测维度是否被硬排除（scope='all' 或精确匹配均生效）。"""
            return eid in _sig_all or (eid, scope) in _sig_pair

        inventory = {
            tag: sorted({e["name"] for e in events if tag in e["tags"]})[:8]
            for tag in self._TAG_RULES
        }
        detectors: dict[str, dict] = {}

        def _mark(kind, fired, reason, matched=0):
            d = detectors.setdefault(kind, {"fired_days": 0, "matched_events": 0, "reason": reason})
            d["matched_events"] += matched
            if fired:
                d["fired_days"] += 1
                d["reason"] = reason
            elif not d["fired_days"]:
                d["reason"] = reason

        def _span(evs):
            return f"{evs[0]['ts'][11:16]}-{evs[-1]['ts'][11:16]}"

        def _who(evs, n=2):
            seen, out = set(), []
            for e in evs:
                if e["name"] not in seen:
                    seen.add(e["name"])
                    out.append(e["name"])
                if len(out) >= n:
                    break
            return "、".join(out)

        def _bath_occupancy_episodes(day_events, min_min: float = 10.0):
            """从卫生间占用 + 灯事件重建「洗澡样」片段。

            旧逻辑把「全天任一占用」当成洗澡并铺满整天，造成 19h/全天误报与天级过预测。
            这里改为：提取占用脉冲（on→off 连续段），仅保留 **期间开灯且时长 ≥ min_min 分钟**
            的片段（时长超 30min 即使无灯记录也采信，兼容缺灯传感器），返回 [(start_dt, end_dt), ...]。
            每个片段即一段真实洗澡区间，供时段级/段级评估做时间定位。
            """
            pres = [e for e in day_events
                    if "卫生间" in e["room"] and "presence" in e["tags"]]
            light_dt = sorted(
                e["dt"] for e in day_events
                if "卫生间" in e["room"] and "light" in e["tags"] and e["active"]
            )
            eps: list = []
            cur = None
            for e in pres:
                if e["active"]:
                    if cur is None:
                        cur = [e["dt"], e["dt"]]
                    else:
                        cur[1] = e["dt"]
                else:
                    if cur is not None:
                        cur[1] = e["dt"]   # 收尾时刻取 off 时间，而非最后的 on 时间
                        eps.append(cur)
                        cur = None
            if cur is not None:
                eps.append(cur)
            out = []
            for s, en in eps:
                mins = (en - s).total_seconds() / 60.0
                if mins < min_min:
                    continue
                lit = any(s - timedelta(minutes=5) <= lt <= en + timedelta(minutes=5)
                          for lt in light_dt)
                if lit or mins >= 30:
                    out.append((s, en))
            return out

        by_day: dict[str, list] = defaultdict(list)
        for e in events:
            by_day[e["day"]].append(e)

        results: list[dict] = []

        # ── 洗澡识别的用水证据：卫生间增压泵高功率运行 = 真实用水 ──
        # 原逻辑只用「卫生间占用传感器」推断，占用一整天都触发 → 出现 19h 假阳性。
        # 改用增压泵电源功率（待机 ~1W、运行 ~170W）作为用水证据，窗口=真实洗澡时长。
        _WATER_POWER_TH = 50.0
        water_entities = [
            eid for eid, meta in names.items()
            if "增压泵" in (meta.get("friendly_name") or "")
            and (meta.get("domain") in ("sensor", "switch"))
        ]
        water_windows: dict[str, list] = {}
        if water_entities and start_iso and end_iso:
            wrows = self.store.query_events(start_iso, end_iso, entities=water_entities, order="asc")
            bursts: list = []
            cur = None
            last_dt = None
            for r in wrows:
                dt = parse_ts(str(r.get("ts", "")))
                if dt is None:
                    continue
                eid = str(r.get("entity_id", ""))
                st = str(r.get("new_state", "")).strip().lower()
                dom = (names.get(eid, {}) or {}).get("domain") or eid.split(".", 1)[0]
                if dom == "sensor":
                    try:
                        running = float(st) >= _WATER_POWER_TH
                    except ValueError:
                        running = False
                else:
                    running = st in ("on", "1", "true", "开", "运行")
                if running:
                    if cur is None:
                        cur = [dt, dt]
                    elif (dt - last_dt).total_seconds() <= 600:
                        cur[1] = dt
                    else:
                        bursts.append(cur)
                        cur = [dt, dt]
                else:
                    if cur is not None:
                        bursts.append(cur)
                        cur = None
                last_dt = dt
            if cur is not None:
                bursts.append(cur)
            for b in bursts:
                mins = (b[1] - b[0]).total_seconds() / 60.0
                if mins >= 3:  # 过滤马桶冲水等极短用水
                    d = b[0].strftime("%Y-%m-%d")
                    water_windows.setdefault(d, []).append(
                        (b[0].strftime("%H:%M"), b[1].strftime("%H:%M"), mins)
                    )

        bath_water_days = 0
        bath_occ_days = 0

        # ── 逐日检测：做饭 / 洗澡 / 看电视 / 工作 ──
        for day, evs in sorted(by_day.items()):
            cook = [
                e for e in evs
                if "厨房" in e["room"] and e["active"]
                and (6 <= e["hour"] <= 9 or 11 <= e["hour"] <= 14 or 17 <= e["hour"] <= 21)
            ]
            if cook:
                _cs, _ce = parse_ts(cook[0]["ts"]), parse_ts(cook[-1]["ts"])
                _cmin = int((_ce - _cs).total_seconds() // 60) if _cs and _ce else 0
                results.append(self._act(
                    day, "cooking", 0.6,
                    f"厨房设备在用餐时段活跃（{_who(cook)}，{_span(cook)}，{len(cook)} 次触发）",
                    6, 21, entities=sorted({e['eid'] for e in cook})[:5],
                    observed_window=_span(cook), event_count=len(cook),
                    start_ts=cook[0]["ts"], end_ts=cook[-1]["ts"], duration_minutes=_cmin,
                ))
            _mark("cooking", bool(cook), "厨房无用餐时段活跃事件" if not cook else "命中", len(cook))

            bath = [
                e for e in evs
                if ("浴室" in e["room"] or "卫生间" in e["room"])
                and e["active"] and "presence" in e["tags"]
            ]
            # 洗澡：优先用增压泵用水证据（真实洗澡时长）。每个用水窗口各自成一条记录，
            # 并补真实起止时间 start_ts/end_ts，供时段级/段级评估做时间定位。
            bath_windows = water_windows.get(day, [])
            if bath_windows:
                for (a_str, b_str, mn) in bath_windows:
                    st = f"{day}T{a_str}:00"
                    et = f"{day}T{b_str}:00"
                    results.append(self._act(
                        day, "bathing", 0.7,
                        f"卫生间增压泵用水（{a_str}-{b_str}，约 {mn:.0f} 分钟）；"
                        "以增压泵高功率运行作为用水证据，替代原占用传感器推断（消除全天误报）",
                        0, 23, entities=water_entities[:5],
                        observed_window=f"{a_str}-{b_str}({mn:.0f}min)", event_count=1,
                        start_ts=st, end_ts=et, duration_minutes=int(mn),
                    ))
                bath_water_days += 1
                _mark("bathing", True, "命中", len(bath_windows))
            else:
                # 无用水证据时回退占用传感器，但按「占用脉冲 + 开灯」重建真实洗澡片段，
                # 而非「全天任一占用即整天洗澡」（旧逻辑造成 19h/全天误报、天级过预测）。
                bath_eps = _bath_occupancy_episodes(evs, min_min=20)
                if bath_eps:
                    bath_entities = sorted({e["eid"] for e in bath})[:5]
                    for (s_dt, e_dt) in bath_eps:
                        sm, em = s_dt.strftime("%H:%M"), e_dt.strftime("%H:%M")
                        mins = int((e_dt - s_dt).total_seconds() // 60)
                        results.append(self._act(
                            day, "bathing", 0.45,
                            f"卫生间占用+开灯片段（{sm}-{em}，约 {mins} 分钟）；"
                            "仅 occupancy 占用推断、无独立用水传感器佐证，可能误报",
                            0, 23, entities=bath_entities,
                            observed_window=f"{sm}-{em}({mins}min)", event_count=1,
                            start_ts=s_dt.isoformat(), end_ts=e_dt.isoformat(),
                            duration_minutes=mins,
                        ))
                    bath_occ_days += 1
                    _mark("bathing", True, "命中(仅占用推断)", len(bath_eps))
                else:
                    _mark("bathing", False, "卫生间无用水/可用占用脉冲", 0)

            tv = [
                e for e in evs
                if "media" in e["tags"] and e["active"]
                and not _signal_excluded(e["eid"], "watching_tv")
            ]
            if tv:
                _ts, _te = parse_ts(tv[0]["ts"]), parse_ts(tv[-1]["ts"])
                _tmin = int((_te - _ts).total_seconds() // 60) if _ts and _te else 0
                results.append(self._act(
                    day, "watching_tv", 0.7,
                    f"影音设备处于开启/播放状态（{_who(tv)}，{_span(tv)}）",
                    tv[0]["hour"], tv[-1]["hour"],
                    entities=sorted({e['eid'] for e in tv})[:5],
                    observed_window=_span(tv), event_count=len(tv),
                    start_ts=tv[0]["ts"], end_ts=tv[-1]["ts"], duration_minutes=_tmin,
                ))
            _mark(
                "watching_tv", bool(tv),
                ("未发现 media 类实体（电视/影音），行为库里没有可用信号"
                 if not inventory["media"] else "media 实体存在但窗口内无开启事件")
                if not tv else "命中",
                len(tv),
            )

            work = [
                e for e in evs
                if e["active"] and 9 <= e["hour"] <= 19
                and ("computer" in e["tags"] or ("书房" in e["room"] and "presence" in e["tags"]))
                and not _signal_excluded(e["eid"], "working")
                and not _signal_excluded(e["eid"], "presence")
            ]
            if work:
                _ws, _we = parse_ts(work[0]["ts"]), parse_ts(work[-1]["ts"])
                _wmin = int((_we - _ws).total_seconds() // 60) if _ws and _we else 0
                results.append(self._act(
                    day, "working", 0.6,
                    f"书房白天有人或电脑在线（{_who(work)}，{_span(work)}）",
                    9, 19, entities=sorted({e['eid'] for e in work})[:5],
                    observed_window=_span(work), event_count=len(work),
                    start_ts=work[0]["ts"], end_ts=work[-1]["ts"], duration_minutes=_wmin,
                ))
            _mark("working", bool(work), "书房白天无占用、也无电脑类实体在线" if not work else "命中", len(work))

        # 洗澡报告汇总：如实反映「用水证据优先、占用推断兜底」
        if detectors.get("bathing", {}).get("fired_days"):
            if bath_water_days:
                detectors["bathing"]["reason"] = (
                    f"命中（{bath_water_days} 天有增压泵用水证据"
                    + (f"，{bath_occ_days} 天仅占用推断" if bath_occ_days else "")
                    + "）"
                )
            else:
                detectors["bathing"]["reason"] = "命中(仅占用推断)"

        # ── 跨日检测：睡眠 / 离家（基于行为事件的静默间隔）──
        # 睡眠按「夜」聚合，同一夜只保留最长的那段静默，避免一夜切出好几段假睡眠
        best_sleep: dict[str, tuple] = {}
        away_hits = 0

        # 离家：白天全屋无人在（presence 也消失）+ 门磁动作 + ≥90min 静默
        for a, b in zip(silence_events, silence_events[1:]):
            mins = (b["dt"] - a["dt"]).total_seconds() / 60.0
            if mins < 90:
                continue
            if 7 <= a["hour"] <= 20:
                door_before = [
                    e for e in events
                    if "door" in e["tags"]
                    and 0 <= (a["dt"] - e["dt"]).total_seconds() <= 1800
                ]
                if door_before:
                    away_hits += 1
                    results.append(self._act(
                        a["day"], "away", 0.6,
                        f"{door_before[-1]['ts'][11:16]} 门磁「{door_before[-1]['name']}」动作后，"
                        f"全屋从 {a['ts'][11:16]} 起静默 {mins / 60:.1f} 小时（疑似离家）",
                        7, 20,
                        observed_window=f"{a['ts'][11:16]}-{b['ts'][11:16]}",
                        start_ts=a["ts"], end_ts=b["ts"],
                        duration_minutes=int(mins),
                        entities=[door_before[-1]["eid"]],
                    ))

        # 睡眠：在家在床，忽略 presence 雷达误触，无家电/门/电脑操作 + 媒体关闭 ≥3h
        for a, b in zip(sleep_silence_events, sleep_silence_events[1:]):
            mins = (b["dt"] - a["dt"]).total_seconds() / 60.0
            if mins < 180:
                continue
            if (a["hour"] >= 20 or a["hour"] <= 3) and 4 <= b["hour"] <= 12:
                # 归属到「入睡那一夜」：20 点后算当天，凌晨算前一天
                night = a["day"] if a["hour"] >= 20 else (
                    (a["dt"] - timedelta(days=1)).strftime("%Y-%m-%d")
                )
                if night not in best_sleep or mins > best_sleep[night][2]:
                    best_sleep[night] = (a, b, mins)
        for night, (a, b, mins) in sorted(best_sleep.items()):
            # 学习策略：起床锚定（静默后首个动作 b）若被硬排除为自动化信号，
            # 则不予采信——避免「小爱音箱定时模式切换」被误当成起床。
            b_excluded = _signal_excluded(b["eid"], "wake_anchor")
            anchor = ""
            if "cover" in a["tags"]:
                anchor = "（入睡前有窗帘动作）"
            elif (not b_excluded) and "cover" in b["tags"]:
                anchor = "（起床后有窗帘动作）"
            who_b = "" if b_excluded else f"，静默后首个动作「{b['name']}」"
            excl_note = "；⚠️ 起床锚定实体已被硬排除为自动化信号，不予采信" if b_excluded else ""
            sleep_window = f"{a['ts'][11:16]}-{b['ts'][11:16]}"
            results.append(self._act(
                night, "sleeping",
                0.75 if mins >= 300 else 0.6,
                f"当夜最长的全屋静默区间：{a['ts'][11:16]} → {b['ts'][11:16]}，"
                f"持续 {mins / 60:.1f} 小时{anchor}；"
                f"静默前最后动作「{a['name']}」{who_b}{excl_note}",
                22, 7,
                # 名义 typical_window 固定为 22:00-07:00，会被误读成真实入睡/起床时刻（曾导致
                # Agent 写出「21:30 睡-07:00 起」的错误作息标签）。用实测静默区间覆盖，
                # 并以同一区间作为 observed_window 暴露给画像聚合，保证作息判定有真实依据。
                typical_window=sleep_window,
                observed_window=sleep_window,
                start_ts=a["ts"], end_ts=b["ts"],
                duration_minutes=int(mins),
                entities=[a["eid"]] + ([] if b_excluded else [b["eid"]]),
                method="全屋行为事件静默间隔（无睡眠专用传感器）",
                caveat="这是睡眠时长的**下界**：夜间任何传感器误触发都会把区间切短，"
                       "因此不能直接当作精确的入睡/起床时刻",
            ))
        detectors["sleeping"] = {
            "fired_days": len(best_sleep),
            "matched_events": len(events),
            "reason": "命中" if best_sleep else "未发现夜间 ≥3 小时的无操作静默（家电/门/电脑均静默；若卧室有雷达存在传感器持续触发属正常，不代表没睡）",
        }
        detectors["away"] = {
            "fired_days": away_hits,
            "matched_events": len([e for e in events if "door" in e["tags"]]),
            "reason": "命中" if away_hits else "未发现「门磁动作 + 白天 ≥90 分钟静默」的组合",
        }

        # ── 自定义活动规则（Phase 2-B：define_activity 注册，infer_activities 套用）──
        try:
            custom_rules = self.store.list_activity_rules(enabled_only=True)
        except Exception:
            custom_rules = []
        if custom_rules:
            af_set = set(activity_filter) if activity_filter else None
            # 实体覆盖预检：窗口内 房间 -> 出现过的标签集合（修复 #7：缺实体类型要显式报）
            coverage: dict = {}
            for day, evs in by_day.items():
                for e in evs:
                    coverage.setdefault(e["room"] or "", set()).update(e["tags"])
            all_tags: set = set().union(*coverage.values()) if coverage else set()
            for rule in custom_rules:
                if af_set is not None and rule["name"] not in af_set:
                    continue
                needed = set(rule["tags"])
                if needed:
                    room_tags = (
                        set().union(*[t for rm, t in coverage.items() if rule["room"] and rule["room"] in rm])
                        if rule["room"] else all_tags
                    )
                    missing = needed - room_tags
                    if missing:
                        _mark(rule["name"], False,
                              f"缺实体类型：{('房间「' + rule['room'] + '」') if rule['room'] else '全屋'}"
                              f"无 {sorted(missing)} 传感器（窗口内无对应事件），规则无法命中", 0)
                        continue
                for day, evs in by_day.items():
                    matched = [
                        e for e in evs
                        if e["active"]
                        and (not rule["room"] or rule["room"] in (e["room"] or ""))
                        and (rule["start_hour"] <= e["hour"] <= rule["end_hour"])
                        and (not rule["tags"] or (set(rule["tags"]) & set(e["tags"])))
                    ]
                    if len(matched) >= rule["min_events"]:
                        times = [e["ts"] for e in matched if e["ts"]]
                        start = min(times) if times else ""
                        end = max(times) if times else ""
                        room = rule["room"] or (matched[0]["room"] if matched else "")
                        results.append(self._act(
                            day, rule["name"], rule["confidence"],
                            f"自定义规则「{rule['name']}」触发 {len(matched)} 次"
                            f"（房间={room or '全屋'}，时段={rule['start_hour']}:00-{rule['end_hour']}:00）",
                            rule["start_hour"], rule["end_hour"],
                            entities=sorted({e["eid"] for e in matched})[:5],
                            observed_window=f"{start[11:16]}-{end[11:16]}" if start else "",
                            event_count=len(matched),
                            rule=rule["name"], custom=True,
                        ))
                        _mark(rule["name"], True, "命中", len(matched))

        if activity_filter is not None:
            _af = set(activity_filter)
            results = [a for a in results if a["activity"] in _af]

        results.sort(key=lambda a: (a["day"], a.get("start_ts") or "", a["activity"]))

        # 落地 detected_activities：供 source_refs 真溯源 与 promote(a) 联动解析（best-effort）
        try:
            from .agent_memory import make_activity_id

            tz = getattr(self.config, "tz_offset_hours", 8)
            rows = []
            for a in results:
                aid = make_activity_id(
                    a.get("activity", ""),
                    a.get("day", ""),
                    str(a.get("start_ts", "")),
                    str(a.get("end_ts", "")),
                    a.get("room", ""),
                )
                a["id"] = aid  # 给活动附稳定 id（不破坏既有返回契约）
                rows.append({
                    "activity_id": aid,
                    "day": a.get("day"),
                    "activity": a.get("activity"),
                    "confidence": a.get("confidence"),
                    "evidence": a.get("evidence"),
                    "room": a.get("room"),
                    "session_id": "",
                    "created_at": now_local(tz).isoformat(timespec="seconds"),
                    "entities_json": json.dumps(a.get("entities", []), ensure_ascii=False),
                    "events_json": "[]",
                })
            if rows:
                self.store.upsert_detected_activities(rows)
        except Exception as exc:  # pragma: no cover - 落地失败绝不拖垮主流程
            print(f"[Insights] 落地 detected_activities 失败（不影响主流程）: {exc}")

        return {"activities": results, "detectors": detectors, "inventory": inventory}

    @staticmethod
    def _act(day, kind, conf, evidence, start_h, end_h, **extra):
        out = {
            "day": day,
            "activity": kind,
            "confidence": conf,
            "evidence": evidence,
            "typical_window": f"{start_h:02d}:00-{end_h:02d}:00",
        }
        out.update(extra)
        return out

    def _synthesize_answer(self, data: dict, window_desc: str) -> str:
        """把结构化洞察合成为一句自然语言回答 + 证据引用（不依赖 LLM，纯规则合成）。

        解决 ask_memory「只做路由、没有任何自然语言回答」的问题：即便向量库没命中，
        也至少给出一段可读的总结，并明确声明检索方式，避免把用户蒙在鼓里。
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

    @staticmethod
    def _get_rt():
        try:
            from .runtime import get_runtime
            return get_runtime()
        except Exception:
            return None

    def _semantic_evidence(self, q: str, rt):
        """取语义副驾证据：系统集合 semantic_search + agent 写回记忆（已晋升 live，按 trust 重排）。

        v0.9 离线降级：embedding 网关不可达时经断路器**短路**（避免每次调用都付超时代价），
        命中打开态直接跳过语义路，回退纯结构化统计（答案仍由 ``_synthesize_answer`` 合成）。
        """
        from .circuit_breaker import get_breaker

        semantic_hits = []
        agent_hits = []
        br = get_breaker("embedding", 3, 60.0)
        if not br.allow():
            return semantic_hits, agent_hits  # 断路器打开：短路，纯结构化
        ok_any = False
        if rt is not None and rt.history is not None:
            try:
                semantic_hits = rt.history.semantic_search(q, n_results=3)
                ok_any = True
            except Exception as exc:  # noqa: BLE001
                br.record_failure(f"semantic_search: {exc}")
        if rt is not None and getattr(rt, "agent_memory", None) is not None:
            try:
                agent_hits = rt.agent_memory.retrieve(q, top_k=5)
                ok_any = True
            except Exception as exc:  # noqa: BLE001
                br.record_failure(f"agent_memory.retrieve: {exc}")
        if ok_any:
            br.record_success()
        return semantic_hits, agent_hits

    # 净水器出水实体（与 api/nr_routes.py 的 WATER_PURIFIER_ENTITY 保持一致）
    _WATER_PURIFIER_ENTITY = "event.chunmi_cn_334432105_600f2_water_out_finish_e_7_1"

    def water_purifier_usage(self, start, end) -> dict:
        """净水器每日饮水统计（结构化计算，供 ask_memory 的净水器分支调用）。

        解析净水器 event 实体的 out_data 属性（格式 start_ts-end_ts-volume_mL-tds_in,tds_out），
        按日聚合饮水量与 TDS 变化，返回总量与每日明细。
        """
        entity = self._WATER_PURIFIER_ENTITY
        try:
            rt = self._get_rt()
            if rt is None or getattr(rt, "ha", None) is None:
                return {"ok": False, "error": "HA 客户端不可用"}
            history_dict = rt.ha.get_history([entity], start)
            records = history_dict.get(entity, []) if isinstance(history_dict, dict) else []
            if not records:
                return {"ok": True, "entity": entity, "total_volume_ml": 0,
                        "total_count": 0, "days": [],
                        "no_records": True,
                        "skipped_reason": "HA 历史中该实体在窗口内无任何记录"}
            daily: dict = {}
            total_volume = 0
            total_count = 0
            skipped_unparsable = 0
            for record in records:
                # v0.7 修复（双重 bug）：
                # 1) 该 event 实体的 state 是**时间戳**（如 2026-09-09T07:17:46+00:00），
                #    原先 `state != "on"` 会把所有记录全部跳过；
                # 2) 属性名实际是中文「出水数据」，原先只取英文 out_data 取不到值。
                # 改为：只要拿到出水数据就处理，并兼容两种键名。
                attrs = record.get("attributes", {}) or {}
                out_data = attrs.get("out_data") or attrs.get("出水数据") or ""
                if not out_data:
                    skipped_unparsable += 1
                    continue
                parts = str(out_data).split("-")
                if len(parts) < 4:
                    skipped_unparsable += 1
                    continue
                try:
                    volume_ml = int(parts[2]) if parts[2] else 0
                    tds_parts = parts[3].split(",") if parts[3] else []
                    tds_in = int(tds_parts[0]) if len(tds_parts) > 0 and tds_parts[0] else 0
                    tds_out = int(tds_parts[1]) if len(tds_parts) > 1 and tds_parts[1] else 0
                    ts_raw = record.get("last_changed") or record.get("last_updated") or ""
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                except (ValueError, KeyError, IndexError):
                    continue
                # get_history 可能只按 start 截断，这里再按窗口上界过滤
                if end and str(ts.isoformat()) > str(end):
                    continue
                date_key = ts.strftime("%Y-%m-%d")
                stats = daily.setdefault(date_key, {
                    "date": date_key, "count": 0,
                    "total_volume_ml": 0, "tds_in_sum": 0, "tds_out_sum": 0,
                })
                stats["count"] += 1
                stats["total_volume_ml"] += volume_ml
                stats["tds_in_sum"] += tds_in
                stats["tds_out_sum"] += tds_out
                total_volume += volume_ml
                total_count += 1
            days = []
            for date_key in sorted(daily.keys()):
                s = daily[date_key]
                c = s["count"]
                days.append({
                    "date": s["date"],
                    "count": c,
                    "total_volume_l": round(s["total_volume_ml"] / 1000, 2),
                    "avg_tds_in": round(s["tds_in_sum"] / c) if c else 0,
                    "avg_tds_out": round(s["tds_out_sum"] / c) if c else 0,
                    "tds_reduction_pct": round((1 - s["tds_out_sum"] / s["tds_in_sum"]) * 100, 1)
                    if s["tds_in_sum"] > 0 else 0,
                })
            result = {"ok": True, "entity": entity, "total_volume_ml": total_volume,
                      "total_count": total_count, "days": days}
            if total_count == 0:
                # 明确区分「真没出水」与「有记录但解析不了」，不再静默归零
                result["skipped_reason"] = (
                    f"窗口内 {len(records)} 条记录，但出水数据不可解析"
                    f"（跳过 {skipped_unparsable} 条）"
                )
            return result
        except Exception as exc:
            return {"ok": False, "error": f"净水器统计失败：{exc}"}

    def plan_question(self, question, days=7) -> dict:
        """路由/规划工具：内置 LLM 拿到用户问题后**第一步**调用。

        用确定性代码「摸排」问题：解析时间窗口、匹配设备、判定意图、检索可复用模板，
        返回一份执行计划（推荐工具 + 参数建议 + 步骤）。模型只要照计划调用具体工具，
        不必自己从零规划——这对轻量模型（GLM-Flash 等）能显著提升第一遍准确率。
        若命中已存分析模板，提示优先调用 export_insight 复用，而非重新统计。
        """
        from .voice_util import resolve_window, match_device, classify_intent
        try:
            rt = self._get_rt()
            today = now_local(8.0)
            window = resolve_window(question, today)
            if window:
                start_iso = window["start"]
                end_iso = window["end"]
                window_label = window.get("label", "")
            else:
                end = today.replace(hour=23, minute=59, second=59)
                start = (today - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0)
                start_iso = start.isoformat()
                end_iso = end.isoformat()
                window_label = f"最近{days}天"
            device = match_device(question, self)
            ql = question

            # 模板匹配：是否有可复用的分析模板（提前到意图路由之前，便于识别"用/按 xxx 模板"直接执行）
            matched_templates = []
            try:
                tpls = (rt.templates.list_all() if rt and getattr(rt, "templates", None) else [])
                q_tokens = re.findall(r"[一-鿿]{2,}|[a-zA-Z]{3,}", question)
                for t in tpls:
                    name = t.name or ""
                    desc = t.description or ""
                    if any(tok in name or tok in desc for tok in q_tokens):
                        matched_templates.append({
                            "id": t.id, "name": name,
                            "default_days": getattr(t, "default_days", 0) or 0,
                        })
            except Exception:
                pass

            # 意图路由（与 ask_memory 同源，但只决策不执行）
            # 「用/按/跑/执行 xxx 模板」→ 直接执行模板出结果（run_analysis_template）
            if "模板" in ql and matched_templates and any(
                v in ql for v in ("用", "使用", "按", "套用", "跑", "执行", "调用", "直接", "根据", "生成")
            ):
                t0 = matched_templates[0]
                route = "run_template"
                tool = "run_analysis_template"
                args = {"template_id": t0["id"], "days": t0.get("default_days") or days}
            elif any(k in ql for k in ("净水器", "饮水", "出水", "喝水", "净化水", "water purifier", "water_purifier")):
                route = "water_purifier_usage"
                tool = "ask_memory"
                args = {"question": question}
            elif any(k in ql for k in ("做饭", "煮饭", "厨房", "cook", "烧菜")):
                route = "cooking"
                tool = "infer_activities"
                args = {"days": days}
            elif any(k in ql for k in ("空调", "暖气", "温度", "climate", "制冷", "制热", "地暖")):
                route = "climate"
                tool = "get_climate_sessions"
                args = {"days": days}
            elif any(k in ql for k in ("电视", "看剧", "media", "电影", "观影")):
                route = "media"
                tool = "search_events"
                args = {"category": "media", "days": days, "summarize": True}
            # 视觉识别（摄像头/VLM）优先于行为洞察，避免「看看谁在客厅」被错路由到传感器统计。
            elif any(k in ql for k in ("看看", "摄像头", "监控", "摄像机", "画面", "图像",
                                       "拍一下", "瞅瞅", "照一下")) or \
                    (any(k in ql for k in ("谁", "有人", "没人", "异常", "快递", "包裹",
                                           "宠物", "猫", "狗", "东西")) and
                     any(k in ql for k in ("客厅", "房间", "门口", "玄关", "卧室", "厨房",
                                           "阳台", "书房", "监控", "摄像头"))):
                route = "vision"
                tool = "analyze_camera"
                preset = "people"
                if any(k in ql for k in ("异常", "安全", "摔倒", "入侵", "危险", "可疑")):
                    preset = "security"
                elif any(k in ql for k in ("快递", "包裹", "宠物", "猫", "狗", "东西", "物品")):
                    preset = "object"
                resolved_room = ""
                try:
                    room_hint = self.resolve_room_in_text(question)
                    resolved_room = room_hint.get("room") or ""
                except Exception:
                    pass
                args = {"room": resolved_room, "prompt_preset": preset}
            elif any(k in ql for k in ("有人", "人体", "存在", "移动", "motion", "presence",
                                       "occupancy", "人在", "活动", "做了什么", "几点睡",
                                       "洗澡", "睡眠", "离家", "回家")):
                route = "presence_or_activities"
                tool = "get_behavior_insights"
                args = {"days": days}
            elif classify_intent(ql) == "device_usage" or any(
                k in ql for k in ("电脑", "pc", "笔记本", "台式", "开机", "使用", "运行",
                                  "在线", "多久", "次数", "开关", "开了", "看", "时长")
            ):
                route = "device_usage"
                tool = "get_device_usage"
                if device and device.get("entity_id"):
                    args = {"entity_id": device["entity_id"], "days": days}
                elif device and device.get("room"):
                    args = {"room": device["room"], "days": days}
                else:
                    args = {"query": (device or {}).get("query") or question, "days": days}
            else:
                route = "overview"
                tool = "get_behavior_insights"
                args = {"days": days}

            # 房间名精确消解（确定性）：拿真实 area 清单做最长匹配。
            # 「房间」只要是一个真实存在的区域名，就按精确区域处理，绝不当泛称。
            room_hint = self.resolve_room_in_text(question)
            resolved_room = room_hint.get("room") or ""
            if resolved_room and tool in (
                "get_device_usage", "search_events", "get_climate_sessions"
            ):
                args["room"] = resolved_room
                # 匹配到的设备若不在这个区域，说明是跨房间误命中：
                # 丢掉 entity_id，改用「精确房间 + 关键词」重新定位。
                eid = args.get("entity_id") or ""
                if eid:
                    ent_room = (self.name_map().get(eid, {}) or {}).get("room", "")
                    if ent_room and ent_room != resolved_room:
                        args.pop("entity_id", None)
                        args["query"] = (device or {}).get("query") or question

            # 房间名歧义提示：如果命中了像「房间」这种既是通用名词又是具体 area 的名字，
            # 在 plan 里显式提醒模型按精确 room 处理，不要当泛称汇总。
            disambiguation = ""
            if room_hint.get("aggregate"):
                disambiguation = (
                    "用户明确要求跨房间汇总，不要限定 room；"
                    f"当前家庭的区域为：{'、'.join(room_hint.get('rooms_available') or []) or '（未配置）'}。"
                )
            elif resolved_room:
                disambiguation = (
                    f"注意：「{resolved_room}」是 Home Assistant 中一个**具体的区域名**"
                    f"（本家庭区域清单：{'、'.join(room_hint.get('rooms_available') or [])}）。"
                    f"调用工具时必须原样传 room=\"{resolved_room}\"，只统计该区域；"
                    "不要把它理解成泛指的「某个房间」，也不要汇总其它区域。"
                )
                if room_hint.get("ambiguous"):
                    disambiguation += (
                        f" 这个名字和日常口语的通用词同形，容易误判：只有用户说「所有房间/全屋」"
                        f"时才做跨区域汇总，否则一律按 room=\"{resolved_room}\" 精确查询。"
                    )

            steps = [
                f"1) 时间窗口已解析为「{window_label}」(start={start_iso}, end={end_iso})。",
                f"2) 意图判定为「{route}」，推荐工具：{tool}，参数建议：{json.dumps(args, ensure_ascii=False)}。",
                "3) 若推荐工具返回空/未命中，先用 get_entity_catalog 按房间+关键词定位设备，再重试。",
            ]
            if disambiguation:
                steps.append(disambiguation)
            if matched_templates and route != "run_template":
                steps.append(
                    "4) 检测到可复用分析模板，若用户想要的是这类周期性报告，"
                    "直接调用 export_insight(template_id=<上方模板 id>) 生成，无需重新统计。"
                )
            elif route == "run_template":
                steps.append(
                    "4) 已识别为「执行模板」意图，请直接调用 run_analysis_template("
                    "template_id=<上方模板 id>, days=<模板默认天数>) 获取结构化结果，"
                    "并基于其 summary_text/entities 作答，不要自己重新统计。"
                )

            return {
                "ok": True,
                "question": question,
                "window": {"label": window_label, "start": start_iso, "end": end_iso},
                "device": device,
                "room": resolved_room,
                "room_scope": "all" if room_hint.get("aggregate") else ("one" if resolved_room else "unspecified"),
                "rooms_available": room_hint.get("rooms_available") or [],
                "intent": route,
                "recommended_tool": tool,
                "suggested_args": args,
                "matched_templates": matched_templates,
                "steps": steps,
                "answer": "请严格按照 steps 调用推荐工具，并基于工具真实返回作答；不要凭空编造数字。",
            }
        except Exception as exc:
            return {"ok": False, "error": f"规划失败：{exc}"}

    def _resolve_device_targets(self, q: str) -> tuple[list[str], bool]:
        """从自然语言设备问题中挤出设备关键词，定位具体 entity_id。

        返回 (entity_ids, resolved)。resolved=True 表示成功定位到具体实体，
        调用方应改用 entity_id 精确查询，而不是把整句问题当 query 传下去——

        否则 ``_tokens`` 会把「书房电脑今天开机了多久」当成一整个 token，
        在实体 haystack 里匹配不到，导致 ``device_usage`` 回退成「全部实体」、
        截断 40 个后把真正的目标设备挤掉（见 HANDOFF_memory_agent_ask_memory）。
        """
        import re as _re
        filler = (
            "今天", "昨天", "今晚", "昨日", "前天", "这周", "本周", "上周", "周末", "最近",
            "时候", "多长时间", "了多久", "开灯", "关灯",
            "开机", "关机", "了", "多久", "时长", "使用", "运行", "在线", "时间", "查询", "问",
            "多少", "几", "小时", "分钟", "秒", "次", "数", "在", "是", "吗", "怎么", "什么",
            "哪些", "哪", "些", "?", "？", "的",
        )
        qq = _re.sub(r"最近\s*\d+\s*天", "", q)
        qq = _norm(qq)
        for w in filler:
            qq = qq.replace(_norm(w), " ")
        qq = qq.strip()
        if not qq:
            return [], False
        cat = self.entity_catalog(query=qq)
        flat = [e for room in cat.get("rooms", {}).values() for e in room.get("entities", [])]
        if not flat:
            return [], False
        # 事件最多的前 5 个作为目标（与 device_usage 的截断一致）
        return [e["entity_id"] for e in flat[:5]], True

    def _last_boot_time(self, entity_id: str, days: int = 60) -> str | None:
        """返回该设备最近一次「off -> on」的开机时刻（跨天连续开机时用于说明起点）。"""
        try:
            r = self.search_events(entity_id=entity_id, days=days, order="desc", limit=300)
            for e in r.get("events", []):
                old = _norm(e.get("old_state") or e.get("state_before") or "")
                new = _norm(e.get("new_state") or e.get("state_after") or "")
                if old in OFF_STATES and new not in OFF_STATES:
                    return e.get("ts")
        except Exception:
            return None
        return None

    def get_last_event(self, entity_id=None, domain=None, room=None, transition="off", days=30) -> dict:
        """返回指定实体/域/房间最近一次状态变化事件（transition='off' 为关闭，'on' 为开启，'any' 为任意）。"""
        try:
            cfg = self.entity_catalog()
            name_map = {}
            for room_data in cfg.get("rooms", {}).values():
                for e in room_data.get("entities", []):
                    name_map[e["entity_id"]] = e.get("friendly_name") or e["entity_id"]
            r = self.search_events(
                entity_id=entity_id, domain=domain, room=room,
                days=days, order="desc", limit=200,
            )
            for ev in r.get("events", []):
                eid = ev.get("entity_id")
                if not eid:
                    continue
                new = ev.get("new_state")
                old = ev.get("old_state")
                matched = False
                if transition == "off" and _state_is_off(new):
                    matched = True
                elif transition == "on" and _state_is_off(old) and _state_is_on(new):
                    matched = True
                elif transition == "any":
                    matched = True
                if matched:
                    return {
                        "ok": True,
                        "entity_id": eid,
                        "friendly_name": name_map.get(eid, eid),
                        "ts": ev.get("ts"),
                        "old_state": ev.get("old_state"),
                        "new_state": ev.get("new_state"),
                        "transition": transition,
                    }
            return {"ok": False, "error": f"未找到 {transition} 事件"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def ask_memory(self, question, days=7, route="auto", return_hints=False) -> dict:
        """自然语言问答：把口语问法映射到既有洞察工具；问法太模糊时回落到向量库语义检索。

        关系库（SQL 映射）为主，向量库（semantic_search + agent_memory）为副驾。

        route:
          - "auto"       关键词路由走结构化，默认分支附带语义/agent 副驾证据（兼容旧行为）
          - "structured" 仅结构化，不挂语义/agent 证据
          - "semantic"   跳过结构化路由，直接返回语义 + agent 记忆检索结果
        return_hints: True 时保留 semantic_hints / agent_memory_hints（调试/混合用）
        """
        q = (question or "").strip()
        if not q:
            return {"ok": False, "error": "question 不能为空"}
        start_iso, end_iso, meta = self._resolve_nl_window(q, days)
        rt = self._get_rt()

        if route == "semantic":
            semantic_hits, agent_hits = self._semantic_evidence(q, rt)
            data = {
                "route": "semantic",
                "question": q,
                "window": meta,
                "semantic_hints": semantic_hits,
                "agent_memory_hints": agent_hits,
                "retrieval": {
                    "route_used": "semantic",
                    "semantic_used": bool(semantic_hits or agent_hits),
                },
            }
            return {"ok": True, **data}

        ql = q

        # 净水器出水量 / 饮水统计（必须放在"厨房/做饭"等泛关键词之前，否则
        # "厨房净水器出水量"会优先命中厨房分支而拿不到具体出水量）。
        if any(k in ql for k in ("净水器", "饮水", "出水", "喝水", "净化水", "water purifier", "water_purifier")):
            data = self.water_purifier_usage(start=start_iso, end=end_iso)
            data["route"] = "water_purifier_usage"
            window_desc = meta.get("note", "该时段")
            if data.get("ok"):
                vol_l = round(data.get("total_volume_ml", 0) / 1000, 2)
                cnt = data.get("total_count", 0)
                if cnt > 0:
                    ans = f"{window_desc}，净水器共出水 {vol_l} 升，累计 {cnt} 次。"
                    days = data.get("days", [])
                    if days:
                        d0 = days[-1]
                        ans += f" 最近一天（{d0['date']}）出水 {d0['total_volume_l']} 升、{d0['count']} 次。"
                else:
                    ans = f"{window_desc}未发现净水器出水记录。"
            else:
                ans = f"净水器统计失败：{data.get('error', '未知错误')}。"
            data["answer"] = ans
            return {"ok": True, "question": q, "window": meta, **data}

        if any(k in ql for k in ("做饭", "煮饭", "厨房", "cook", "烧菜")):
            data = self.infer_activities(start=start_iso, end=end_iso)
            data["route"] = "infer_activities(cooking)"
            return {"ok": True, "question": q, "window": meta, **data}
        if any(k in ql for k in ("空调", "暖气", "温度", "climate", "制冷", "制热", "地暖")):
            data = self.climate_sessions(start=start_iso, end=end_iso)
            data["route"] = "climate_sessions"
            return {"ok": True, "question": q, "window": meta, **data}
        if any(k in ql for k in ("电视", "看剧", "media", "电影", "观影")):
            data = self.search_events(category="media", start=start_iso, end=end_iso, summarize=True)
            data["route"] = "search_events(media)"
            return {"ok": True, "question": q, "window": meta, **data}

        # 有人/活动/存在类问题用行为洞察总览，避免误匹配到单一开关设备
        if any(k in ql for k in ("有人", "人体", "存在", "移动", "motion", "presence", "occupancy", "人在", "活动")):
            data = self.behavior_insights(start=start_iso, end=end_iso)
            data["route"] = "behavior_insights(presence)"
            data["answer"] = self._synthesize_answer(data, meta.get("note", ""))
            return {"ok": True, "question": q, "window": meta, **data}

        # 设备用量：电脑/空调/灯具等开关时长、次数（注意：不包含"有人"，避免误命中电视）
        if any(k in ql for k in ("电脑", "pc", "笔记本", "台式", "开机", "使用", "运行", "在线", "多久", "次数", "开关", "灯", "开灯", "关灯", "时长", "照明")):
            # 先按自然语言解析具体实体，避免整句问题被当成 query 时分词失败而匹配不到设备
            # （HANDOFF_memory_agent_ask_memory：友好名解析缺失 + 连续开机误判）。
            eids, resolved = self._resolve_device_targets(q)
            if resolved:
                data = self.device_usage(entity_id=",".join(eids), start=start_iso, end=end_iso)
            else:
                data = self.device_usage(query=q, start=start_iso, end=end_iso)
            data["route"] = "device_usage"
            window_desc = meta.get("note", "该时段")
            if data.get("ok") and data.get("devices"):
                primary = data["devices"][0]
                name = primary.get("friendly_name") or primary.get("entity_id", "")
                human = primary.get("total_on_human", "0秒")
                sessions = primary.get("sessions", 0)
                all_names = ", ".join(
                    d.get("friendly_name") or d.get("entity_id", "") for d in data["devices"][:5]
                )
                ans = f"{window_desc}，{name} 累计运行/开启约 {human}，共 {sessions} 次会话。"
                # 连续开机（跨天）：窗口内无 off 事件但当前仍开着 —— 不要报「算不出」，
                # 而是说明自上次开机起持续开机，今日窗口内无新开关事件。
                tl = primary.get("timeline") or []
                if primary.get("switch_off_count", 0) == 0 and tl and tl[0].get("still_on"):
                    boot = self._last_boot_time(primary["entity_id"])
                    if boot:
                        ans += (f"（设备当前为开，自 {boot} 起持续开机未关；"
                                f"{window_desc}窗口内无新的开关事件，属跨天连续开机）")
                elif primary.get("total_on_seconds", 0) == 0:
                    ans += f"（{window_desc}未检测到该设备的开启事件）"
                if len(data["devices"]) > 1:
                    ans += f" 此外还匹配到：{all_names}。"
            elif data.get("ok"):
                ans = (f"{window_desc}未找到与问题完全匹配的设备。"
                       f"可用 get_entity_catalog 按房间/名称定位实体后，再用 get_device_usage 查询。")
            else:
                err = data.get("error", "设备用量查询失败")
                ans = f"设备用量查询失败：{err}。"
            data["answer"] = ans
            return {"ok": True, "question": q, "window": meta, **data}

        # 默认：行为洞察总览
        data = self.behavior_insights(start=start_iso, end=end_iso)
        data["route"] = "get_behavior_insights"
        semantic_hits = []
        agent_hits = []
        if route in ("auto",) and rt is not None:
            semantic_hits, agent_hits = self._semantic_evidence(q, rt)
            if semantic_hits:
                data["semantic_hints"] = semantic_hits
            if agent_hits:
                data["agent_memory_hints"] = agent_hits
        # 合成自然语言回答 + 诚实声明检索方式
        data["answer"] = self._synthesize_answer(data, meta.get("note", ""))
        data["retrieval"] = {
            "route_used": route,
            "semantic_used": bool(semantic_hits or agent_hits),
            "note": "向量库(chroma)作为语义副驾，命中时提供 similarity 证据；"
                    "agent_memory 为 Agent 写回并经晋升的 live 记忆，按 trust 重排；"
                    "未命中则完全基于结构化统计检索，不依赖向量库。",
        }
        if not return_hints:
            data.pop("semantic_hints", None)
            data.pop("agent_memory_hints", None)
        return {"ok": True, "question": q, "window": meta, **data}

    def get_data_quality(self, days=30) -> dict:
        """聚合数据质量：现有 data_quality_issues（电量倒流/单位冲突/心跳/陈旧）+ agent 记忆镜像缺口。"""
        try:
            health = self.get_device_health(days)
            quality_issues = health.get("data_quality_issues", [])
        except Exception:
            quality_issues = []
        agent = {}
        try:
            rt = self._get_rt()
            if rt is not None and getattr(rt, "agent_memory", None) is not None:
                agent = rt.agent_memory.health()
        except Exception:
            agent = {"ok": False, "error": "agent_memory 不可用"}
        return {
            "ok": True,
            "days": days,
            "data_quality_issues": quality_issues,
            "agent_memory": agent,
        }

    @staticmethod
    def _resolve_nl_window(q, default_days):
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
        import re as _re
        m_wd = _re.search(r"(上上|上|这|本)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天])", q)
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
            import re as _re
            m = _re.search(r"(\d+)\s*天", q)
            if m:
                n = int(m.group(1))
                start = (now - timedelta(days=n)).replace(hour=0, minute=0, second=0, microsecond=0)
                return start.isoformat(), now.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": now.isoformat(), "note": f"最近{n}天"}
        start = (now - timedelta(days=default_days)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), now.isoformat(), {"timezone": "Asia/Shanghai", "start": start.isoformat(), "end": now.isoformat(), "note": f"最近{default_days}天"}


def _parse_attrs(raw):
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


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
