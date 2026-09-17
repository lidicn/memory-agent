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

# 盒侧 AI 事件实体只来自小米摄像机；排除其他 event 实体（如 HA 自动化事件）
_EVENT_PREFIX = "event.chuangmi_camera"

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
                if eid.startswith(_EVENT_PREFIX):
                    ids.append(eid)
                    room_map[eid] = room
        self._entity_ids = ids
        self._entity_rooms = room_map
        self._discovered_at = now
        if ids:
            logger.info("livingroom_ai: 发现 %d 个盒侧 AI 事件实体", len(ids))

    # -- 单次轮询 ----------------------------------------------------------
    def poll_once(self) -> int:
        """拉取一次客厅盒侧 AI 事件，返回本次新写入条数。"""
        self._discover()
        if not self._entity_ids:
            return 0
        ingested = 0
        for eid in self._entity_ids:
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
                # Phase 0.4 主动播报闭环：人脸/看护类事件经 doubao_tts 播报
                if self.announcer is not None and ev.kind in pi.ANNOUNCE_KINDS:
                    self.announcer.announce(ev)
        if ingested:
            logger.info("livingroom_ai: 本次写入 %d 条盒侧 AI 事件", ingested)
        return ingested

    def run(self) -> int:
        """供 runtime 周期任务调用的同步入口。"""
        return self.poll_once()
