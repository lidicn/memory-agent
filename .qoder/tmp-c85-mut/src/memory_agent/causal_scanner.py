"""P5e 因果归因主动告警：每日定时扫描成员行为变化，显著变化写入 behavior_events。

设计：
- 对每个成员的关键指标（arrival_time / activity_count / active_duration）运行 detect_change
- 检测到显著变化（effect_size 超阈值 + p_value 显著）时，写入 behavior_events 表
  action="behavior_change_alert"，供 butler / 其他 agent 查询触发后续动作
- 去重：同一天同一成员同一指标只写一次（查 behavior_events 表）
- 单次失败仅记录，不影响主流程
"""

from __future__ import annotations

import json
from typing import Any

from .change_attribution import detect_change
from .store import now_local


DEFAULT_METRICS = ("arrival_time", "activity_count", "active_duration")
ALERT_ACTION = "behavior_change_alert"
ALERT_TRIGGER = "causal_scanner"


class CausalScanner:
    """每日因果归因扫描器。"""

    def __init__(
        self,
        store: Any,
        metrics: tuple[str, ...] = DEFAULT_METRICS,
        lookback_days: int = 30,
        min_days: int = 5,
    ) -> None:
        self.store = store
        self.metrics = tuple(metrics)
        self.lookback_days = max(7, int(lookback_days))
        self.min_days = max(2, int(min_days))

    # ── 去重 ──────────────────────────────────────────────────────────

    def _already_alerted_today(self, person: str, metric: str) -> bool:
        """检查今天是否已对该成员+指标写过告警。"""
        # day 列按家庭墙钟落库；UTC 容器里的裸 datetime.now() 会让「今天」在
        # 凌晨 0–8 点指到昨天，去重随之失效（一天内重复告警）。
        today = now_local(self.store.tz_offset_hours).strftime("%Y-%m-%d")
        scene_prefix = f"{metric}:"
        conn = self.store.connect()
        with self.store._lock:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM behavior_events "
                "WHERE day = ? AND action = ? AND scene LIKE ?",
                (today, ALERT_ACTION, f"{scene_prefix}%"),
            ).fetchone()
        return int(row["c"] or 0) > 0

    # ── 写入告警 ──────────────────────────────────────────────────────

    def _write_alert(self, person: str, result: dict) -> int:
        """把一条行为变化告警写入 behavior_events 表。"""
        metric = result["metric"]
        direction = result.get("direction", "none")
        before = result.get("before") or {}
        after = result.get("after") or {}
        detail = {
            "metric": metric,
            "direction": direction,
            "effect_size": round(float(result.get("effect_size") or 0), 4),
            "p_value": round(float(result.get("p_value") or 1), 6),
            "before_mean": round(float(before.get("mean") or 0), 2),
            "after_mean": round(float(after.get("mean") or 0), 2),
            "before_n": int(before.get("n") or 0),
            "after_n": int(after.get("n") or 0),
        }
        payload = {
            "action": ALERT_ACTION,
            "scene": f"{metric}:{direction}",
            "persons": [{"name": person}],
            "confidence": round(float(result.get("effect_size") or 0), 4),
            "raw_response": json.dumps(detail, ensure_ascii=False),
            "trigger": ALERT_TRIGGER,
            "client": ALERT_TRIGGER,
            "status": "ok",
        }
        return self.store.insert_behavior_event(payload)

    # ── 主扫描 ────────────────────────────────────────────────────────

    def scan(self) -> dict:
        """扫描所有成员的行为变化，返回统计。

        返回：{"scanned_members": n, "scanned_metrics": n,
               "alerts_written": n, "skipped_dedup": n, "details": [...]}
        """
        details: list[dict] = []
        alerts_written = 0
        skipped_dedup = 0
        scanned_metrics = 0

        try:
            members = self.store.list_members()
        except Exception as exc:  # noqa: BLE001
            print(f"[CausalScanner] 获取成员列表失败: {exc}")
            return {"scanned_members": 0, "scanned_metrics": 0,
                    "alerts_written": 0, "skipped_dedup": 0, "details": []}

        member_ids = []
        for m in members:
            mid = m.get("member_id") or m.get("id") or m.get("name")
            if mid:
                member_ids.append(str(mid))

        if not member_ids:
            print("[CausalScanner] 无成员，跳过扫描")
            return {"scanned_members": 0, "scanned_metrics": 0,
                    "alerts_written": 0, "skipped_dedup": 0, "details": []}

        try:
            from .mcp_server import _fetch_attribution_events
            events = _fetch_attribution_events(self.store, days=self.lookback_days)
        except Exception as exc:  # noqa: BLE001
            print(f"[CausalScanner] 获取事件失败: {exc}")
            return {"scanned_members": len(member_ids), "scanned_metrics": 0,
                    "alerts_written": 0, "skipped_dedup": 0, "details": []}

        for person in member_ids:
            for metric in self.metrics:
                scanned_metrics += 1
                try:
                    result = detect_change(events, person, metric)
                except Exception as exc:  # noqa: BLE001
                    print(f"[CausalScanner] detect_change({person}/{metric}) 异常: {exc}")
                    continue

                if not result.get("changed"):
                    continue

                # 数据量不足时不告警（避免假阳性）
                before_n = int((result.get("before") or {}).get("count") or 0)
                after_n = int((result.get("after") or {}).get("count") or 0)
                if before_n + after_n < self.min_days:
                    continue

                if self._already_alerted_today(person, metric):
                    skipped_dedup += 1
                    continue

                try:
                    eid = self._write_alert(person, result)
                    alerts_written += 1
                    details.append({
                        "person": person, "metric": metric,
                        "direction": result.get("direction"),
                        "effect_size": round(float(result.get("effect_size") or 0), 4),
                        "p_value": round(float(result.get("p_value") or 1), 6),
                        "event_id": eid,
                    })
                    print(f"[CausalScanner] 告警: {person}/{metric} "
                          f"{result.get('direction')} (d={result.get('effect_size'):.3f}, "
                          f"p={result.get('p_value'):.4f}) → event#{eid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[CausalScanner] 写入告警失败({person}/{metric}): {exc}")

        return {
            "scanned_members": len(member_ids),
            "scanned_metrics": scanned_metrics,
            "alerts_written": alerts_written,
            "skipped_dedup": skipped_dedup,
            "details": details,
        }
