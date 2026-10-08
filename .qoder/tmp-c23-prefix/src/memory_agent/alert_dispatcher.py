"""Phase 4.1 统一告警分发单飞：同 session 同类告警只发一次，冷却期内合并。

设计：
- 基于内存的最近告警记录（session_id + alert_type → last_sent_ts）
- 同 session 同类型告警在 cooldown_seconds 内只发一次（单飞）
- 高优先级告警可覆盖低优先级（优先级淘汰）
- 同类告警合并计数（返回 merged_count）
"""

from __future__ import annotations

import time
from typing import Any


class AlertDispatcher:
    """统一告警分发器：单飞 + 合并 + 优先级淘汰。"""

    def __init__(self, default_cooldown_seconds: int = 300) -> None:
        self._default_cooldown = max(0.01, float(default_cooldown_seconds))
        # session_id -> {alert_type: {"last_ts": float, "count": int, "priority": int}}
        self._recent: dict[str, dict[str, dict]] = {}
        # 全局上限，防止内存泄漏
        self._max_sessions = 1000
        self._max_alerts_per_session = 50

    def _evict_if_needed(self) -> None:
        """会话数超上限时清理最旧的。"""
        if len(self._recent) <= self._max_sessions:
            return
        # 简单清理：删最早的 10%
        sorted_sessions = sorted(
            self._recent.items(),
            key=lambda x: max(a["last_ts"] for a in x[1].values()),
        )
        for sid, _ in sorted_sessions[: max(1, len(sorted_sessions) // 10)]:
            self._recent.pop(sid, None)

    def should_send(
        self,
        session_id: str,
        alert_type: str,
        priority: int = 0,
        cooldown_seconds: int | None = None,
    ) -> dict[str, Any]:
        """判断是否应该发送告警，返回 {"send": bool, "reason": str, "merged_count": int}。

        priority: 数字越大优先级越高。高优先级告警可绕过低优先级的冷却。
        """
        cooldown = max(0.01, float(cooldown_seconds)) if cooldown_seconds is not None else self._default_cooldown
        now = time.monotonic()

        self._evict_if_needed()

        session_alerts = self._recent.setdefault(session_id, {})
        if len(session_alerts) > self._max_alerts_per_session:
            # 单 session 告警类型超上限，清理最旧的
            sorted_types = sorted(session_alerts.items(), key=lambda x: x[1]["last_ts"])
            for t, _ in sorted_types[: len(sorted_types) // 4]:
                session_alerts.pop(t, None)

        existing = session_alerts.get(alert_type)
        if existing is None:
            session_alerts[alert_type] = {"last_ts": now, "count": 1, "priority": priority}
            return {"send": True, "reason": "首次告警", "merged_count": 1}

        elapsed = now - existing["last_ts"]
        # 高优先级告警可绕过低优先级的冷却
        if priority > existing["priority"] and elapsed > cooldown / 2:
            existing["last_ts"] = now
            existing["count"] = 1
            existing["priority"] = priority
            return {"send": True, "reason": "高优先级覆盖", "merged_count": 1}

        if elapsed < cooldown:
            existing["count"] += 1
            return {
                "send": False,
                "reason": f"冷却中（{int(cooldown - elapsed)}s 后可重发）",
                "merged_count": existing["count"],
            }

        # 冷却已过，重置计数
        existing["last_ts"] = now
        existing["count"] = 1
        existing["priority"] = priority
        return {"send": True, "reason": "冷却已过", "merged_count": 1}

    def get_stats(self, session_id: str | None = None) -> dict[str, Any]:
        """获取分发统计。"""
        if session_id:
            alerts = self._recent.get(session_id, {})
            return {
                "session_id": session_id,
                "alert_types": len(alerts),
                "types": {k: {"count": v["count"], "priority": v["priority"]} for k, v in alerts.items()},
            }
        return {
            "total_sessions": len(self._recent),
            "total_alerts": sum(len(v) for v in self._recent.values()),
        }

    def clear(self, session_id: str | None = None) -> None:
        """清理告警记录。"""
        if session_id:
            self._recent.pop(session_id, None)
        else:
            self._recent.clear()
