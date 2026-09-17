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
