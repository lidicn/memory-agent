"""主动播报闭环（主动感知 v2.0 · Phase 0.4）。

把 ``perception_events`` 中的人脸/看护类事件播报出来，形成「眼睛 → 大脑 → 嘴」闭环。
**两条路并存，按配置优先级**（DCD 20261007 §四 Q1 = 裁 B）：

1. **HA 直发优先**：配了 ``announce_tts_entity`` **且** ``announce_target`` 时走
   ``ha_client.execute_action``（command=speak → ``tts/speak``）——只配实体不配播放设备时
   HA 会静默不发声（合成完就没下文），那种"配了等于没配"不能算就绪；
2. **回落投收件箱**：HA 那条没配起来时，投 ``butler/inbox/speak`` 请 DB 决定谁开口。

两条**不会同时发**：优先级是"先看 1、1 不通才看 2"，不是"两条都试"。这正是裁定驳回
A（直接翻成投 speak）与 C（备而不发）时想要的形状——A 会让"DB 不认 speak"变成静默没声，
C 让件 5 端到端完不成；而两处同时说话是哪一条都没被批准的失败模式。

总开关仍是 ``announce_enabled``（默认关）。带同类事件冷却，避免同一类事件刷屏。
"""
from __future__ import annotations

import logging
import time
import uuid
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
        target: str = "",
        mqtt: Any = None,
    ) -> None:
        self.ha = ha_client
        self.store = store
        self.tts_entity = tts_entity or ""
        self.mqtt = mqtt
        # HA 的 tts.speak 需要 media_player_entity_id 才会真正发声；
        # 未配置播放设备时只合成不播放，故视为未就绪（走回落那条）。
        self.target = target or ""
        self.switch = bool(enabled)
        self.enabled = self.switch and bool(self.tts_entity) and bool(self.target)
        self.cooldown_sec = max(0, int(cooldown_sec))
        self._last: dict[str, float] = {}

    @property
    def inbox_ready(self) -> bool:
        """回落路是否可用：总开关开着、收件箱桥在场且启用（``enabled`` 只说 HA 那条）。"""
        return bool(self.switch and self.mqtt is not None
                    and getattr(self.mqtt, "enabled", False))

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
        if not self.switch:
            return False
        if not self.enabled and not self.inbox_ready:
            # 两条路都不通：返回 False 而不是"当作发了"。播报这种事没有回执就是没发生，
            # 报成成功会把"配错了"变成"听起来一切正常"。
            logger.debug("announcer: HA 直发未就绪且收件箱不可用，%s 未播报", ev.kind)
            return False
        if ev.kind not in ANNOUNCE_KINDS:
            return False
        msg = self._message(ev)
        if not msg:
            return False
        now = time.monotonic()
        # P1-1：用 None 哨兵而非 0.0——monotonic() 返回系统启动至今秒数，
        # 0.0 会让 cooldown > uptime 的规则在重启后首次触发被静默吞掉
        last = self._last.get(ev.kind)
        if last is not None and now - last < self.cooldown_sec:
            return False
        self._last[ev.kind] = now
        # 优先级而不是并行：HA 就绪时绝不另投收件箱，否则同一次事件两处说话。
        return self._via_inbox(msg) if not self.enabled else self._via_ha(msg)

    def _via_ha(self, msg: str) -> bool:
        try:
            res = self.ha.execute_action({
                "device": self.tts_entity,
                "command": "speak",
                "params": {"message": msg, "speaker": self.tts_entity,
                           "target": self.target},
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

    def _via_inbox(self, msg: str) -> bool:
        """回落：投 ``butler/inbox/speak``，由 DB 决定谁开口（契约 §七 件 5）。"""
        tid = uuid.uuid4().hex
        try:
            ok = bool(self.mqtt.publish_speak(msg, trace_id=tid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("announcer: 收件箱播报调用失败: %s", exc)
            return False
        if ok:
            logger.info("announcer: 投 butler/inbox/speak 请 DB 播报「%s」（trace_id=%s）", msg, tid)
        else:
            # 桥自己按契约记了码（payload_invalid / broker_unreachable / internal），
            # 这里只补"哪条事件因此没声"，不重复造一枚码。
            logger.warning("announcer: 收件箱播报未成功，trace_id=%s", tid)
        return ok
