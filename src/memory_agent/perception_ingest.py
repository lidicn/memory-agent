"""统一感知总线：把多源感知事件归一化后写入 ``perception_events``。

来源（见路线图 Gate→Identity→Omni）：
- ``edge_ai``：客厅盒侧 AI（HA ``event.chuangmi_*`` 实体），已结构化，直接进总线
- ``vlm``：现有 VLM 视觉识别结果（vision_service 在 Gate 放行后写入，补语义）
- ``sensor``：现有 HA 采集（poller 拉取的二值/数值传感器）

设计原则：边缘事件已结构化即跳过 VLM（Miloco 的 Gate）；VLM 仅在边缘无信号
或需"在干嘛"语义时补。本模块只做归一化与路由，不触达任何具体硬件。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("memory_agent.perception_ingest")

# 客厅盒侧 AI 事件实体后缀 → 统一 kind 映射（见路线图 Phase 0.1）。
# 实体形如 ``event.chuangmi_camera_051a01_unkonw_face_e_8_7``。
_CHUANGMI_KIND_MAP: dict[str, str] = {
    "unkonw_face_e_8_7": "face_unknown",   # 陌生人脸
    "known_face_e_8_8": "face_known",      # 熟人
    "key_area_human_e_11_1": "human",      # 关键区域有人
    "long_time_no_human_e_11_3": "no_human",  # 长时间无人
    "pet_event_e_8_6": "pet",              # 宠物
    "babycry_event_e_8_5": "cry",          # 哭声
    "baby_woke_up_e_11_2": "baby_woke",    # 婴儿醒
    "gesture_event_e_8_9": "gesture",      # 手势
    "day_to_night_e_8_3": "day_night",     # 昼夜切换
    "night_to_day_e_8_4": "day_night",     # 昼夜切换
    "into_fav_area_e_9_1": "fav_area",     # 进收藏区
    "out_fav_area_e_9_2": "fav_area",      # 出收藏区
}

# 触发主动播报（Phase 0.4）的 kind 集合。
ANNOUNCE_KINDS = {"face_known", "face_unknown", "cry", "baby_woke"}


@dataclass
class PerceptionEvent:
    source: str                           # edge_ai | vlm | sensor
    kind: str
    room: Optional[str] = None
    entity_id: Optional[str] = None
    confidence: Optional[float] = None
    payload: dict = field(default_factory=dict)
    raw_event: Optional[dict] = None
    server_ts: Optional[str] = None


def kind_from_chuangmi_entity(entity_id: str) -> Optional[str]:
    """从 chuangmi camera 事件实体 id 解析统一 ``kind``。

    HA 实体 id 形如 ``event.chuangmi_camera_051a01_unkonw_face_e_8_7``，
    去掉 ``event.`` 前缀后按尾部签名段匹配映射表。
    """
    if not entity_id:
        return None
    stem = entity_id.split(".", 1)[-1]
    for suffix, kind in _CHUANGMI_KIND_MAP.items():
        if stem.endswith(suffix):
            return kind
    return None


def from_ha_event(
    entity_id: str, state: dict, room: Optional[str] = None
) -> Optional[PerceptionEvent]:
    """把 HA ``event.chuangmi_*`` 状态变更构造为 ``PerceptionEvent``。

    ``server_ts`` 取 HA 的 ``last_changed``（回退 ``last_updated``），用作幂等去重基准。
    """
    kind = kind_from_chuangmi_entity(entity_id)
    if kind is None:
        return None
    attrs = state.get("attributes", {}) if isinstance(state, dict) else {}
    ts = state.get("last_changed") or state.get("last_updated")
    return PerceptionEvent(
        source="edge_ai",
        kind=kind,
        room=room,
        entity_id=entity_id,
        confidence=None,
        payload=dict(attrs) if isinstance(attrs, dict) else {},
        raw_event=state,
        server_ts=ts,
    )


def from_vlm(
    room: Optional[str], kind: str, confidence: Optional[float] = None,
    payload: Optional[dict] = None, entity_id: Optional[str] = None,
    server_ts: Optional[str] = None,
) -> PerceptionEvent:
    """构造一条来自 VLM 的感知事件（Gate 已放行后调用）。"""
    return PerceptionEvent(
        source="vlm", kind=kind, room=room, confidence=confidence,
        entity_id=entity_id, payload=payload or {}, server_ts=server_ts,
    )


def from_sensor(
    entity_id: str, kind: str, room: Optional[str] = None,
    confidence: Optional[float] = None, payload: Optional[dict] = None,
) -> PerceptionEvent:
    """构造一条来自 HA 传感器的感知事件。"""
    return PerceptionEvent(
        source="sensor", kind=kind, room=room, entity_id=entity_id,
        confidence=confidence, payload=payload or {},
    )


def ingest_event(store: Any, event: PerceptionEvent) -> int:
    """归一化写入 ``perception_events``，返回自增 id（落库失败 fail-closed 返回 0）。"""
    try:
        return store.insert_perception_event({
            "server_ts": event.server_ts,
            "source": event.source,
            "kind": event.kind,
            "room": event.room,
            "entity_id": event.entity_id,
            "confidence": event.confidence,
            "payload": event.payload,
            "raw_event": event.raw_event,
        })
    except Exception as exc:  # 落库失败记 warning 不抛崩（fail-closed）
        logger.warning(
            "perception_ingest: 写入失败 source=%s kind=%s: %s",
            event.source, event.kind, exc,
        )
        return 0


# ── Phase 1.1 · Gate 层：edge_ai 事件直接晋升为 behavior_events ──────────────
# 设计：边缘 AI 事件即"已发生事实"，直接结构化为「人+动作」事件落 behavior_events，
# 零 VLM 调用（Miloco Gate：已结构化的边缘事件跳过 Omni 补语义）。
# 仅晋升"人+动作"语义类；环境信号（day_night 昼夜 / fav_area 进出区域 /
# no_human 长时无人 / gesture 手势）保留在 perception_events，供后续
# Phase 3 规则 DSL / Phase 5 看护规则消费，不污染行为事件流。
_GATE_BEHAVIOR_KINDS: dict[str, dict] = {
    "human":        {"action": "有人出现", "count": 1, "confidence": None},
    "face_known":   {"action": "熟人出现", "count": 1, "confidence": 0.6},
    "face_unknown": {"action": "陌生人出现", "count": 1, "confidence": 0.6},
    "pet":          {"action": "宠物出现", "count": 1, "confidence": None},
    "cry":          {"action": "婴儿哭声", "count": 1, "confidence": None},
    "baby_woke":    {"action": "婴儿醒来", "count": 1, "confidence": None},
}

# 盒侧 known_face 事件 attributes 中可能携带人名的键（按优先级）。
# HA event 实体 attributes 形如 {"friendly_name": "爸爸", ...}。
_FACE_NAME_KEYS = ("friendly_name", "name", "person_name")


def _known_person_name(event: PerceptionEvent) -> str:
    """从 known_face 事件提取人名；找不到回退"熟人"。"""
    sources: list[dict] = []
    if isinstance(event.payload, dict):
        sources.append(event.payload)
    if isinstance(event.raw_event, dict):
        rattrs = event.raw_event.get("attributes")
        if isinstance(rattrs, dict):
            sources.append(rattrs)
    for src in sources:
        for key in _FACE_NAME_KEYS:
            v = src.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return "熟人"


def gate_promote_to_behavior(store: Any, event: PerceptionEvent) -> int:
    """Phase 1.1 Gate 层：edge_ai 事件直接落 ``behavior_events``（零 VLM）。

    返回 behavior_events 自增 id；未晋升（非行为类/无房间）或落库失败返回 0。
    幂等由上游保证（livingroom_ai 按 ``last_changed`` 去重，且仅在
    ``ingest_event`` 返回 >0 时调用本函数），本函数不做二次去重。
    """
    spec = _GATE_BEHAVIOR_KINDS.get(event.kind)
    if spec is None:
        return 0
    if not event.room:
        return 0

    persons: list[dict] = []
    if event.kind == "face_known":
        persons = [{
            "name": _known_person_name(event),
            "via": "edge_ai",
            "match_confidence": 0.6,
            "member_id": None,
            "detail": dict(event.payload or {}),
        }]
    elif event.kind == "face_unknown":
        persons = [{
            "name": "陌生人",
            "via": "edge_ai",
            "match_confidence": 0.6,
            "member_id": None,
            "detail": {},
        }]

    try:
        return store.insert_behavior_event({
            "server_ts": event.server_ts,
            "room": event.room,
            "camera_src": "",
            "persons": persons,
            "count": spec["count"],
            "action": spec["action"],
            "scene": "",
            "confidence": spec["confidence"],
            "trigger": "edge_ai",
            "status": "ok",
        })
    except Exception as exc:  # 晋升失败不影响主流程（fail-closed）
        logger.warning(
            "gate_promote_to_behavior 失败 kind=%s room=%s: %s",
            event.kind, event.room, exc,
        )
        return 0


# ── Phase 1.2 · Identity 层：face_unknown 即时裁决 ─────────────────────────────
# 设计：客厅盒侧 AI 报 face_unknown（陌生人脸）时，立即用 presence_fusion
# 名册消除法裁决——「当前未识别人数 == 缺席成员数」时，把客厅的陌生人
# 确定性归位到具体成员。这是两路融合：edge_ai 人脸事件 + 名册消除法。
# TV ArcFace / VLM 外观匹配是另外两路独立信号，后续接入时并入此处。
# 零 LLM 调用，纯确定性规则。

_IDENTITY_LOOKBACK_MINUTES = 5


def identity_resolve_unknown(store: Any, event: PerceptionEvent, behavior_event_id: int) -> str | None:
    """Phase 1.2 Identity 层：把 face_unknown 事件即时裁决为具体成员。

    返回裁决到的成员名；无法裁决（等式不成立/无房间/异常）返回 None，
    保持 persons_json 里的「陌生人」占位。
    """
    if event.kind != "face_unknown":
        return None
    if not event.room or behavior_event_id <= 0:
        return None

    try:
        from datetime import datetime, timedelta
        from .presence_fusion import fuse_presence

        tz = getattr(store, "tz_offset_hours", 8.0)
        since_dt = datetime.now() - timedelta(minutes=_IDENTITY_LOOKBACK_MINUTES)
        since = since_dt.isoformat(timespec="seconds")

        roster = store.list_members()
        if not roster:
            return None
        occupancy = store.recent_occupancy(since)
        result = fuse_presence(roster, occupancy)
        if result.get("method") != "elimination":
            return None

        for inferred in result.get("inferred", []):
            if (inferred.get("room") or "").strip() == event.room:
                member_name = (inferred.get("member") or "").strip()
                if not member_name:
                    continue
                confidence = float(inferred.get("confidence") or 0.7)
                member_id = next(
                    (m.get("id") for m in roster if m.get("name") == member_name),
                    None,
                )
                new_persons = [{
                    "name": member_name,
                    "via": "presence_fusion",
                    "match_confidence": confidence,
                    "member_id": member_id,
                    "detail": {
                        "reason": inferred.get("reason", ""),
                        "method": inferred.get("method", "elimination"),
                        "edge_kind": event.kind,
                    },
                }]
                store.update_behavior_event_persons(behavior_event_id, new_persons)
                logger.info(
                    "identity_resolve: face_unknown@%s 裁决为 %s (conf=%.2f)",
                    event.room, member_name, confidence,
                )
                return member_name
    except Exception as exc:
        logger.warning(
            "identity_resolve_unknown 失败 kind=%s room=%s: %s",
            event.kind, event.room, exc,
        )
    return None
