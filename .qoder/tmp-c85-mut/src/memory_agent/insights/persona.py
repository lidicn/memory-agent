"""用户画像模块：画像生成 + 行为对比 + 自然语言总结 + 洞察解释。

[DEPRECATED-待接回] DCD 20261005 §二.2 Q3=乙：本模块当前**零引用**但保留不删，因为
`insights/api.py:754` 那条降级缺的正是 `PersonaBuilder.explain` 要的 `insight_id -> Insight` 索引。
接回排在裁5 Q-B（#40）里做——**现在**把它挂上去只会得到 `found: False`，
即"返回形状合法、内容恒空"那一类缺陷的复发。索引没有生产者之前，这里保持只读存档状态。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from .models import Insight, InsightConfig

__all__ = ["PersonaBuilder"]


class PersonaBuilder:
    """用户画像构建与洞察解释。"""

    def __init__(self, config: Optional[InsightConfig] = None) -> None:
        self.config = config or InsightConfig()

    # ------------------------------------------------------------------
    # 画像
    # ------------------------------------------------------------------
    def build(self, *, days: int, usage_rows: Sequence[Mapping[str, Any]],
              room_rows: Sequence[Mapping[str, Any]],
              rhythm: Mapping[str, Any],
              activities: Sequence[Mapping[str, Any]],
              anomalies: Sequence[Mapping[str, Any]],
              coverage: Mapping[str, Any]) -> Dict[str, Any]:
        """生成用户画像（最活跃房间、最常用设备、作息、偏好、总结）。"""
        usage_rows = list(usage_rows or [])
        room_rows = list(room_rows or [])
        activities = list(activities or [])
        favorite = usage_rows[0] if usage_rows else {}
        active_room = room_rows[0] if room_rows else {}
        activity_rank = self._activity_rank(activities)
        traits = self._traits(favorite, active_room, rhythm, activity_rank)
        persona = {
            "days": days,
            "active_room": {
                "room": active_room.get("room") or active_room.get("label", ""),
                "label": active_room.get("label", ""),
                "friendly_name": active_room.get("label", ""),
                "active_minutes": active_room.get("active_minutes", 0.0),
            },
            "favorite_device": {
                "entity_id": favorite.get("entity_id", ""),
                "label": favorite.get("label", ""),
                "friendly_name": favorite.get("label", ""),
                "room": favorite.get("room", ""),
                "active_minutes": favorite.get("active_minutes", 0.0),
            },
            "top_devices": [{"entity_id": r.get("entity_id", ""),
                             "label": r.get("label", ""),
                             "friendly_name": r.get("label", ""),
                             "room": r.get("room", ""),
                             "active_minutes": r.get("active_minutes", 0.0)}
                            for r in usage_rows[:5]],
            "wake_time": rhythm.get("wake", ""),
            "sleep_time": rhythm.get("sleep", ""),
            "rhythm": dict(rhythm),
            "activities": activity_rank,
            "anomaly_count": len(anomalies),
            "preferences": traits,
            "traits": traits,
            "coverage": dict(coverage),
        }
        persona["summary"] = self.summary(persona)
        return persona

    @staticmethod
    def _activity_rank(activities: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for item in activities:
            key = str(item.get("activity", ""))
            bucket = buckets.setdefault(key, {
                "activity": key, "name": item.get("name", key),
                "count": 0, "minutes": 0.0,
                "room": item.get("room", ""), "tags": list(item.get("tags", []))})
            bucket["count"] += 1
            bucket["minutes"] = round(bucket["minutes"] + float(item.get("minutes", 0.0)), 1)
        return sorted(buckets.values(), key=lambda b: -b["count"])

    @staticmethod
    def _traits(favorite: Mapping[str, Any], active_room: Mapping[str, Any],
                rhythm: Mapping[str, Any], activity_rank: Sequence[Mapping[str, Any]]
                ) -> List[str]:
        traits: List[str] = []
        if active_room.get("label"):
            traits.append("最常待在%s" % active_room["label"])
        if favorite.get("label"):
            traits.append("最依赖%s" % favorite["label"])
        if rhythm.get("sleep") and rhythm.get("wake"):
            traits.append("作息大致是 %s 睡、%s 起" % (rhythm["sleep"], rhythm["wake"]))
        for item in activity_rank[:2]:
            if item.get("count"):
                traits.append("经常%s（%d 次）" % (item.get("name", ""), item["count"]))
        return traits

    @staticmethod
    def summary(persona: Mapping[str, Any]) -> str:
        """自然语言总结。"""
        parts: List[str] = []
        room = persona.get("active_room", {}).get("label")
        if room:
            parts.append("最活跃的房间是%s" % room)
        device = persona.get("favorite_device", {}).get("label")
        if device:
            parts.append("最常用的设备是%s" % device)
        if persona.get("wake_time") and persona.get("sleep_time"):
            parts.append("作息大约是 %s 入睡、%s 起床" % (
                persona["sleep_time"], persona["wake_time"]))
        for trait in persona.get("traits", []):
            if trait not in " ".join(parts):
                parts.append(trait)
        return "；".join(parts) + "。" if parts else "暂无足够数据生成画像。"

    # ------------------------------------------------------------------
    # 洞察解释
    # ------------------------------------------------------------------
    def explain(self, insight_id: str,
                index: Mapping[str, Insight]) -> Dict[str, Any]:
        """解释洞察：给出结论、口径与证据链。"""
        insight = index.get(str(insight_id or ""))
        if not insight:
            # P2：未命中时也补齐与命中分支一致的字段（type/detail/room 等），
            # 避免前端按 found 分支写两套取值逻辑
            return {"insight_id": insight_id, "found": False, "type": "",
                    "title": "", "detail": "", "room": "", "entity_id": "",
                    "friendly_name": "", "score": 0.0,
                    "explanation": "未找到该洞察，请先调用 behavior_insights。",
                    "evidence": [], "data": {},
                    "total": 0, "offset": 0, "has_more": False}
        evidence = [{"key": k, "value": v} for k, v in sorted(insight.data.items())
                    if not isinstance(v, (dict, list))]
        explanation = insight.explanation or "基于窗口内事件统计得到。"
        text = "%s。%s %s" % (insight.title, insight.detail, explanation)
        return {"insight_id": insight.insight_id, "found": True, "type": insight.type,
                "title": insight.title, "detail": insight.detail,
                "room": insight.room, "entity_id": insight.entity_id,
                "friendly_name": insight.friendly_name, "score": insight.score,
                "explanation": text.strip(), "evidence": evidence,
                "data": dict(insight.data),
                "total": 1, "offset": 0, "has_more": False}
