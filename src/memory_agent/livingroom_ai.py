"""客厅盒侧 AI 事件接入（主动感知 v2.0 · Phase 0.1）。

MA 没有 HA WebSocket 订阅，统一走 REST 轮询。本模块是一个**轻量常驻轮询器**：
周期性拉取客厅小米摄像机的 ``event.chuangmi_*`` 实体当前状态，按 ``last_changed``
去重后，经 ``perception_ingest`` 归一化写入 ``perception_events``（source=edge_ai）。

与既有 CollectService 的批量历史采集互不干扰：本模块只盯这几路盒侧 AI 事件，
不写 ``behavior_events``，避免污染行为统计；低延迟（默认 10s）靠独立轻量轮询实现，
而非依赖默认关闭/间隔 900s 的自动采集。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from . import perception_ingest as pi
from .store import Store
from .announcer import Announcer

logger = logging.getLogger("memory_agent.livingroom_ai")

# 盒侧 AI 事件实体只来自小米摄像机；排除其他 event 实体（如 HA 自动化事件）。
# 实测实体 id 有两种形态：``event.chuangmi_camera_051a01_*``（旧集成）与
# ``event.chuangmi_cn_1072229835_051a01_*``（米家集成，线上实际形态），
# 因此前缀只取到 ``event.chuangmi``，靠 perception_ingest 的后缀映射区分语义。
_EVENT_PREFIX = "event.chuangmi"

# 有意**不入库**的高频环境事件（后缀）：物体/人形移动、昼夜切换每次检测都触发，
# 入库会淹没语义事件并让 VLM Gate 恒判"边缘信号新鲜"→ 过度跳过取帧。
# 需要时再按房间降频采样，不要简单加进 _CHUANGMI_KIND_MAP。
_SKIP_SUFFIXES = ("object_motion_e_8_1", "people_motion_e_8_2")

# 实体列表发现缓存刷新间隔（秒）：discover_entities 较重，10 分钟刷一次足够
_DISCOVER_TTL = 600


class LivingRoomAIIngest:
    def __init__(
        self,
        ha_client: Any,
        store: Store,
        room_keywords: Optional[list[str]] = None,
        interval_seconds: int = 10,
        announcer: Optional[Announcer] = None,
    ) -> None:
        self.ha = ha_client
        self.store = store
        self.room_keywords = room_keywords or ["客厅"]
        self.interval_seconds = max(1, int(interval_seconds))
        self.announcer = announcer
        self._entity_ids: list[str] = []
        self._entity_rooms: dict[str, str] = {}
        self._seen: dict[str, Optional[str]] = {}
        self._discovered_at: float = 0.0

    # -- 实体发现（带缓存） ------------------------------------------------
    def _discover(self) -> None:
        now = time.monotonic()
        if self._entity_ids and (now - self._discovered_at) < _DISCOVER_TTL:
            return
        try:
            res = self.ha.discover_entities()
        except Exception as exc:  # noqa: BLE001
            logger.warning("livingroom_ai: discover_entities 失败: %s", exc)
            return
        if not isinstance(res, dict) or not res.get("ok"):
            return
        rooms = res.get("rooms", {})
        ids: list[str] = []
        room_map: dict[str, str] = {}
        for room, info in rooms.items():
            if room not in self.room_keywords and "客厅" not in room:
                # 仅关注客厅（含「客厅」关键词的房间名）
                if not any(k in room for k in self.room_keywords):
                    continue
            ents = info.get("entities", {})
            for eid in ents:
                if not eid.startswith(_EVENT_PREFIX):
                    continue
                if eid.endswith(_SKIP_SUFFIXES):
                    continue  # 高频环境事件，有意不入库（见 _SKIP_SUFFIXES 注释）
                ids.append(eid)
                room_map[eid] = room
        self._entity_ids = ids
        self._entity_rooms = room_map
        self._discovered_at = now
        if ids:
            logger.info("livingroom_ai: 发现 %d 个盒侧 AI 事件实体", len(ids))

    # -- 单次轮询 ----------------------------------------------------------
    def _states_index(self) -> dict:
        """一次 ``/api/states`` 批量取回全部实体状态，避免逐实体 N 次请求。

        现场有 ~14 个盒侧 AI 事件实体，10s 轮询若逐个 GET 会是 ~84 req/min；
        批量只需 1 req。HA 客户端未提供 ``get_states`` 时返回空字典，
        自动回退到逐实体 ``get_state``（单测的 FakeHA 即走该路径）。
        """
        bulk = getattr(self.ha, "get_states", None)
        if not callable(bulk):
            return {}
        try:
            states = bulk()
        except Exception as exc:  # noqa: BLE001
            logger.warning("livingroom_ai: 批量取状态失败，回退逐实体: %s", exc)
            return {}
        if not isinstance(states, list):
            return {}
        out: dict = {}
        for s in states:
            if isinstance(s, dict) and s.get("entity_id"):
                out[s["entity_id"]] = s
        return out

    def poll_once(self) -> int:
        """拉取一次客厅盒侧 AI 事件，返回本次新写入条数。"""
        self._discover()
        if not self._entity_ids:
            return 0
        index = self._states_index()
        ingested = 0
        for eid in self._entity_ids:
            if eid in index:
                state = index[eid]
            else:
                try:
                    state = self.ha.get_state(eid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("livingroom_ai: get_state %s 失败: %s", eid, exc)
                    continue
            if not isinstance(state, dict):
                continue
            last = state.get("last_changed") or state.get("last_updated")
            if not last:
                continue
            # 首次见到该实体：仅记录基线，不回填历史，避免启动洪泛
            if self._seen.get(eid) is None:
                self._seen[eid] = last
                continue
            if self._seen[eid] == last:
                continue  # 未变化
            self._seen[eid] = last
            room = self._entity_rooms.get(eid)
            ev = pi.from_ha_event(eid, state, room=room)
            if ev is None:
                continue
            if pi.ingest_event(self.store, ev) > 0:
                ingested += 1
                # Phase 1.1 Gate 层：edge_ai 事件即"已发生事实"，直接结构化进
                # behavior_events（零 VLM 调用）；非行为类 kind 本函数内部忽略。
                pi.gate_promote_to_behavior(self.store, ev)
                # Phase 0.4 主动播报闭环：人脸/看护类事件经 doubao_tts 播报
                if self.announcer is not None and ev.kind in pi.ANNOUNCE_KINDS:
                    self.announcer.announce(ev)
        if ingested:
            logger.info("livingroom_ai: 本次写入 %d 条盒侧 AI 事件", ingested)
        return ingested

    def run(self) -> int:
        """供 runtime 周期任务调用的同步入口。"""
        return self.poll_once()
