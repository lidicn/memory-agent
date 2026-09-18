"""离家模式状态机：Phase 5.1。

设计：
- 状态：在家 / 离家
- 触发：long_time_no_human → 离家；face_known → 在家
- 离家模式下 face_unknown 立即告警（不等名册消除法）
"""

from __future__ import annotations

import time
from typing import Any


class AwayMode:
    """离家模式状态机。"""

    def __init__(self, no_human_threshold_seconds: int = 1800) -> None:
        self.state = "home"  # home / away
        self.last_human_ts: float = time.monotonic()
        self.no_human_threshold = float(no_human_threshold_seconds)
        self._state_changed_at: float = time.monotonic()

    def report_human(self) -> None:
        """报告有人（face_known 或 human 事件）→ 重置计时器，若离家则切回在家。"""
        self.last_human_ts = time.monotonic()
        if self.state == "away":
            self.state = "home"
            self._state_changed_at = time.monotonic()

    def report_no_human(self) -> str | None:
        """报告无人（long_time_no_human）→ 超过阈值则切离家，返回新状态。"""
        elapsed = time.monotonic() - self.last_human_ts
        if elapsed >= self.no_human_threshold and self.state == "home":
            self.state = "away"
            self._state_changed_at = time.monotonic()
            return "away"
        return None

    def is_away(self) -> bool:
        return self.state == "away"

    def should_alert_unknown(self) -> bool:
        """离家模式下 face_unknown 立即告警。"""
        return self.state == "away"
