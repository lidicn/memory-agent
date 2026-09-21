"""Phase 5.1 离家模式状态机。

状态机逻辑：
- 客厅 no_human（长时间无人）事件 → 进入离家模式
- 客厅 face_known（熟人）事件 → 解除离家模式
- 离家模式下面对 face_unknown（陌生人）→ 立即高优先级告警（不等名册消除法）

状态持久化到 store.meta 表（key-value），容器重启后恢复。

Phase 4.1：告警冷却统一走 AlertDispatcher（单飞 + 合并 + 优先级淘汰），
不再使用本地 _last_alert_ts。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# meta 表的 key
_AWAY_KEY = "away_mode:active"
_AWAY_SINCE_KEY = "away_mode:since"

# 配置
_DEFAULT_AWAY_ROOM = "客厅"  # 只有客厅有盒侧 AI 的 no_human 事件
_AWAY_ALERT_COOLDOWN_S = 300  # 离家模式下陌生人告警冷却 5 分钟，防刷屏
_AWAY_ALERT_PRIORITY = 10  # 离家模式陌生人告警优先级（高）


class AwayModeManager:
    """离家模式状态机：消费感知事件，维护离家/在家状态，触发告警。

    Phase 4.1：alert_dispatcher 为 None 时回退到本地冷却（向后兼容）。
    """

    def __init__(
        self,
        store,
        room: str = _DEFAULT_AWAY_ROOM,
        alert_dispatcher=None,
    ) -> None:
        self.store = store
        self.room = room
        self._active: bool | None = None  # None=未从 DB 加载
        self._since: str = ""
        self._dispatcher = alert_dispatcher
        # 回退用本地冷却（dispatcher 为 None 时）
        self._last_alert_ts: float = 0.0

    # ── 状态读写 ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """从 meta 表加载状态（懒加载，首次访问时读一次）。"""
        if self._active is not None:
            return
        try:
            conn = getattr(self.store, "_conn", None)
            if conn is None:
                self._active = False
                return
            row = conn.execute(
                "SELECT value FROM meta WHERE key=?", (_AWAY_KEY,)
            ).fetchone()
            self._active = (row is not None and row[0] == "1")
            row2 = conn.execute(
                "SELECT value FROM meta WHERE key=?", (_AWAY_SINCE_KEY,)
            ).fetchone()
            self._since = row2[0] if row2 else ""
        except Exception as exc:
            logger.warning("离家模式状态加载失败: %s", exc)
            self._active = False

    def is_active(self) -> bool:
        self._load()
        return bool(self._active)

    def get_state(self) -> dict[str, Any]:
        """返回当前离家模式状态（供 API 查询）。"""
        self._load()
        state = {
            "active": bool(self._active),
            "since": self._since,
            "room": self.room,
        }
        if self._dispatcher is not None:
            stats = self._dispatcher.get_stats(session_id="away_mode")
            state["alert_stats"] = stats
        else:
            state["last_alert_ago_s"] = (
                round(time.time() - self._last_alert_ts, 1)
                if self._last_alert_ts
                else None
            )
        return state

    def _set_active(self, active: bool, reason: str = "") -> bool:
        """设置离家模式状态，写 meta 表。返回状态是否变化。"""
        self._load()
        if self._active == active:
            return False
        self._active = active
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            conn = getattr(self.store, "_conn", None)
            if conn is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                    (_AWAY_KEY, "1" if active else "0"),
                )
                if active:
                    self._since = now_iso
                    conn.execute(
                        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                        (_AWAY_SINCE_KEY, now_iso),
                    )
                else:
                    self._since = ""
                    conn.execute(
                        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                        (_AWAY_SINCE_KEY, ""),
                    )
                conn.commit()
        except Exception as exc:
            logger.warning("离家模式状态写入失败: %s", exc)
        logger.info(
            "离家模式: %s → %s（原因: %s）",
            "离家" if not active else "在家",
            "离家" if active else "在家",
            reason or "未知",
        )
        return True

    # ── 告警分发（Phase 4.1 统一走 AlertDispatcher）──────────────────────────

    def _should_alert(self, room: str) -> tuple[bool, str, int]:
        """判断是否应该发送告警，返回 (should_send, reason, merged_count)。

        Phase 4.1：优先使用 AlertDispatcher；为 None 时回退本地冷却。
        """
        if self._dispatcher is not None:
            result = self._dispatcher.should_send(
                session_id="away_mode",
                alert_type=f"stranger:{room}",
                priority=_AWAY_ALERT_PRIORITY,
                cooldown_seconds=_AWAY_ALERT_COOLDOWN_S,
            )
            return result["send"], result["reason"], result["merged_count"]

        # 回退：本地冷却
        now = time.time()
        if (now - self._last_alert_ts) > _AWAY_ALERT_COOLDOWN_S:
            self._last_alert_ts = now
            return True, "首次告警（本地冷却回退）", 1
        return False, "陌生人告警冷却中（本地冷却回退）", 0

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def handle_event(self, event_kind: str, room: str, event_ts: str = "") -> dict[str, Any]:
        """处理感知事件，返回动作结果。

        返回:
            {"state_changed": bool, "new_state": bool, "alert": bool, "reason": str,
             "merged_count": int}
        """
        result = {
            "state_changed": False,
            "new_state": self.is_active(),
            "alert": False,
            "reason": "",
            "merged_count": 0,
        }

        # 只处理配置房间的事件
        if room and room != self.room:
            return result

        # 1. 长时间无人 → 进入离家模式
        if event_kind == "no_human":
            changed = self._set_active(True, reason=f"{room}长时间无人")
            result["state_changed"] = changed
            result["new_state"] = True
            result["reason"] = f"{room}长时间无人 → 进入离家模式"
            return result

        # 2. 熟人出现 → 解除离家模式
        if event_kind == "face_known":
            if self.is_active():
                changed = self._set_active(False, reason=f"{room}出现熟人")
                result["state_changed"] = changed
                result["new_state"] = False
                result["reason"] = f"{room}出现熟人 → 解除离家模式"
            else:
                result["reason"] = "已在家状态，face_known 不改变状态"
            return result

        # 3. 离家模式下陌生人 → 立即告警（不等名册消除法）
        if event_kind == "face_unknown" and self.is_active():
            should_send, reason, merged_count = self._should_alert(room)
            result["merged_count"] = merged_count
            if should_send:
                result["alert"] = True
                result["reason"] = f"离家模式下{room}出现陌生人 → 立即告警（{reason}）"
                logger.warning("离家模式告警: %s出现陌生人（ts=%s）", room, event_ts or "未知")
            else:
                result["reason"] = f"陌生人告警被单飞抑制：{reason}"
                logger.info("离家模式告警单飞抑制: %s（%s，已合并 %d 次）", room, reason, merged_count)
            return result

        result["reason"] = f"事件 {event_kind} 不触发离家模式动作"
        return result
