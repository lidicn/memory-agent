"""主动播报闭环（主动感知 v2.0 · Phase 0.4）。

把 ``perception_events`` 中的人脸/看护类事件，经 HA ``doubao_tts``（豆包声音引擎
``tts.speak``）播报出来，形成「眼睛 → 大脑 → 嘴」闭环。复用现有 ``ha_client``
的 ``execute_action``（command=speak → ``tts/speak``），不引入新组件。

默认关闭：需配置 ``announce_tts_entity``（HA 中 doubao_tts 实体 id）才生效。
带同类事件冷却，避免同一类事件刷屏。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .perception_ingest import ANNOUNCE_KINDS, PerceptionEvent

logger = logging.getLogger("memory_agent.announcer")


class Announcer:
    def __init__(
        self,
        ha_client: Any,
        store: Any,
        tts_entity: str = "",
        enabled: bool = False,
        cooldown_sec: int = 30,
    ) -> None:
        self.ha = ha_client
        self.store = store
        self.tts_entity = tts_entity or ""
        self.enabled = bool(enabled) and bool(self.tts_entity)
        self.cooldown_sec = max(0, int(cooldown_sec))
        self._last: dict[str, float] = {}

    def _message(self, ev: PerceptionEvent) -> Optional[str]:
        """根据事件类型构造播报文案。"""
        attrs = ev.payload or {}
        room = ev.room or "客厅"
        if ev.kind == "face_known":
            who = attrs.get("who") or attrs.get("friendly_name") or "熟人"
            return f"{room}的{who}回来了"
        if ev.kind == "face_unknown":
            return f"{room}有陌生人出现"
        if ev.kind == "cry":
            return f"{room}的婴儿在哭"
        if ev.kind == "baby_woke":
            return "婴儿醒了"
        return None

    def announce(self, ev: PerceptionEvent) -> bool:
        """对单条感知事件尝试播报，返回是否实际发出。"""
        if not self.enabled:
            return False
        if ev.kind not in ANNOUNCE_KINDS:
            return False
        msg = self._message(ev)
        if not msg:
            return False
        now = time.monotonic()
        if now - self._last.get(ev.kind, 0.0) < self.cooldown_sec:
            return False
        self._last[ev.kind] = now
        try:
            res = self.ha.execute_action({
                "device": self.tts_entity,
                "command": "speak",
                "params": {"message": msg, "speaker": self.tts_entity},
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("announcer: tts.speak 调用失败: %s", exc)
            return False
        ok = bool(res.get("ok")) if isinstance(res, dict) else False
        if ok:
            logger.info("announcer: 播报「%s」", msg)
        else:
            logger.warning("announcer: 播报未成功: %s", res)
        return ok
