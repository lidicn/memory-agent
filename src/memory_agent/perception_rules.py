"""感知规则引擎：Phase 3 主动规则 DSL。

设计：
- STATIC 规则（确定性，无 LLM）：陌生人→推送告警、昼夜切换→记录。
- DYNAMIC 规则（需语义）：交给 Agent 规划。
- duration 滑窗去抖：同 trigger 事件 N 秒内只触发一次，防单事件误报。
"""

from __future__ import annotations

import time
from typing import Any


# ── STATIC 规则定义 ──────────────────────────────────────────────────────────────

STATIC_RULES: list[dict] = [
    {
        "id": "stranger_alert",
        "trigger": "face_unknown",
        "room": "客厅",
        "action": "alert",
        "cooldown_seconds": 300,  # 5 分钟内不重复告警
        "description": "客厅出现陌生人 → 推送告警",
    },
    {
        "id": "day_night_log",
        "trigger": "day_night",
        "room": "",
        "action": "log",
        "cooldown_seconds": 3600,  # 1 小时内不重复记录
        "description": "昼夜切换 → 记录",
    },
]


# ── 滑窗去抖状态 ──────────────────────────────────────────────────────────────────

class RuleEngine:
    """感知规则引擎：消费 perception_events，匹配规则，触发动作。"""

    def __init__(self, rules: list[dict] | None = None) -> None:
        self.rules = rules or STATIC_RULES
        self._last_triggered: dict[str, float] = {}  # rule_id -> monotonic

    def _in_cooldown(self, rule: dict) -> bool:
        """检查规则是否在冷却期内（滑窗去抖）。"""
        rid = rule["id"]
        cooldown = float(rule.get("cooldown_seconds", 0))
        if cooldown <= 0:
            return False
        last = self._last_triggered.get(rid, 0.0)
        return (time.monotonic() - last) < cooldown

    def _match(self, event_kind: str, room: str) -> list[dict]:
        """匹配规则：trigger 匹配 + room 匹配（空 room 通配）。"""
        matched = []
        for rule in self.rules:
            if rule["trigger"] != event_kind:
                continue
            if rule.get("room") and rule["room"] != room:
                continue
            matched.append(rule)
        return matched

    def evaluate(self, event_kind: str, room: str) -> list[dict]:
        """评估事件，返回命中的规则动作列表（已过冷却）。"""
        results = []
        for rule in self._match(event_kind, room):
            if self._in_cooldown(rule):
                continue
            self._last_triggered[rule["id"]] = time.monotonic()
            results.append({
                "rule_id": rule["id"],
                "action": rule["action"],
                "description": rule.get("description", ""),
            })
        return results
