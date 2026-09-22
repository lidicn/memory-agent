"""自然语言查询：问题规划（意图/实体/时间） + 答案合成。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .models import Intent, InsightConfig, QuestionPlan, TimeRange, fmt_ts
from .parser.entity import EntityResolver
from .parser.timeframe import parse_timeframe, resolve_range, strip_time_text

__all__ = ["NLQueryEngine", "detect_intent"]

# (意图, 正则) —— 顺序即优先级
_INTENT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (Intent.ANOMALY.value, r"异常|故障|问题|报错|坏了|离线|offline|anomaly|质量"),
    (Intent.RHYTHM.value, r"几点|作息|起床|睡觉|入睡|睡眠时间|wake|sleep\s*time"),
    (Intent.ACTIVITY.value, r"洗澡|活动|做了什么|干了什么|activity|看电视|做饭|学习"),
    (Intent.PERSONA.value, r"画像|习惯|我是谁|总结|persona|character"),
    (Intent.BEHAVIOR.value, r"待了多久|停留|待过|在[^，。？！]{0,8}(待|停)|presence|待的时间"),
    (Intent.DEVICE_USAGE.value, r"用了多久|使用时长|用了|用过|开过多久|开过|usage|多久|时长"),
)

_ROUTE_HINTS: Dict[str, List[str]] = {
    Intent.DEVICE_USAGE.value: ["试试：上周空调用了多久", "试试：最近 3 天客厅灯用了多久"],
    Intent.BEHAVIOR.value: ["试试：昨天在客厅待了多久", "试试：最近 7 天卧室停留时间"],
    Intent.ANOMALY.value: ["试试：最近有什么异常", "试试：上周有哪些设备离线"],
    Intent.RHYTHM.value: ["试试：我一般几点睡觉", "试试：最近作息怎么样"],
    Intent.ACTIVITY.value: ["试试：最近 7 天有哪些活动", "试试：昨天洗澡了吗"],
    Intent.PERSONA.value: ["试试：给我的用户画像", "试试：总结一下我的习惯"],
}


def detect_intent(question: str) -> str:
    """意图识别。"""
    text = str(question or "")
    for intent, pattern in _INTENT_PATTERNS:
        if re.search(pattern, text, re.I):
            return intent
    return Intent.UNKNOWN.value


class NLQueryEngine:
    """自然语言查询引擎。"""

    def __init__(self, service: Any, resolver: EntityResolver,
                 config: Optional[InsightConfig] = None) -> None:
        self.service = service
        self.resolver = resolver
        self.config = config or InsightConfig()

    # ------------------------------------------------------------------
    # 问题规划
    # ------------------------------------------------------------------
    def plan(self, question: str, days: int = 7) -> QuestionPlan:
        """问题规划：意图 + 房间 + 关键词 + 时间范围。"""
        text = str(question or "").strip()
        intent = detect_intent(text)
        route = intent if intent != Intent.UNKNOWN.value else "auto"
        room, rest = self.resolver.split_room_from_query("", text)
        tf = parse_timeframe(text, default_days=days) or resolve_range(
            days=days, default_days=self.config.default_days)
        query = strip_time_text(rest)
        for token in ("多久", "多少", "什么", "哪些", "怎么", "怎么样", "吗", "了"):
            query = query.replace(token, " ")
        query = " ".join(query.split())
        entity_ids = self.resolver.resolve_ids(room=room, query=query)
        activity = self._match_activity(text)
        return QuestionPlan(
            question=text, intent=intent, route=route, room=room, query=query,
            entity_ids=entity_ids, days=days, activity=activity, time_range=tf,
            hints=list(_ROUTE_HINTS.get(intent, [])),
            params={"has_room": bool(room), "has_query": bool(query)})

    @staticmethod
    def _match_activity(text: str) -> str:
        for key, words in (("bath", ("洗澡", "沐浴")), ("study", ("学习", "看书", "写作业")),
                           ("tv", ("看电视", "电视", "追剧")), ("sleep", ("睡觉", "睡眠", "入睡")),
                           ("cooking", ("做饭", "烹饪", "下厨"))):
            if any(w in text for w in words):
                return key
        return ""

    # ------------------------------------------------------------------
    # 问答
    # ------------------------------------------------------------------
    def ask(self, question: str, days: int = 7, route: str = "auto",
            return_hints: bool = False) -> Dict[str, Any]:
        """自然语言查询记忆：规划 -> 执行 -> 合成答案。"""
        plan = self.plan(question, days=days)
        if route and route != "auto":
            plan.route = route
        answer, data = self._execute(plan)
        payload: Dict[str, Any] = {
            "question": plan.question, "answer": answer, "route": plan.route,
            "intent": plan.intent, "plan": plan.to_dict(), "data": data,
            "room": plan.room, "query": plan.query,
            "time_range": plan.time_range.to_dict() if plan.time_range else None,
            "total": 1, "offset": 0, "has_more": False,
        }
        if return_hints:
            payload["hints"] = list(plan.hints)
        return payload

    def _execute(self, plan: QuestionPlan) -> Tuple[str, Dict[str, Any]]:
        tr = plan.time_range or resolve_range(days=plan.days)
        route = plan.route
        if route == Intent.DEVICE_USAGE.value:
            return self._answer_usage(plan, tr)
        if route == Intent.BEHAVIOR.value:
            return self._answer_behavior(plan, tr)
        if route == Intent.ANOMALY.value:
            data = self.service.anomaly_report(tr, room=plan.room, query=plan.query)
            return ("%s，共 %d 条：%s" % (data.get("summary", "异常报告"), data["total"],
                    "；".join(a["title"] for a in data["anomalies"][:3]) or "无"), data)
        if route == Intent.RHYTHM.value:
            data = self.service.rhythm(tr, room=plan.room)
            return ("你一般 %s 左右入睡、%s 左右起床（基于 %d 天样本）。" % (
                data.get("sleep") or "未知", data.get("wake") or "未知",
                data.get("samples", 0)), data)
        if route == Intent.ACTIVITY.value:
            data = self.service.infer_activities(
                tr, rooms=plan.room, activities=[plan.activity] if plan.activity else None)
            names = [a["name"] for a in data["activities"][:5]]
            return ("%s 推断到 %d 次活动：%s。" % (
                data.get("summary", ""), data["total"], "、".join(names) or "无"), data)
        if route == Intent.PERSONA.value:
            data = self.service.user_persona(days=max(plan.days, 14))
            return (data.get("persona", {}).get("summary", "暂无画像。"), data)
        hints = "；".join(plan.hints[:2])
        return ("我还不确定你想问什么。%s" % hints, {"hints": plan.hints})

    def _answer_usage(self, plan: QuestionPlan, tr: TimeRange) -> Tuple[str, Dict[str, Any]]:
        data = self.service.usage(tr, room=plan.room, query=plan.query,
                                  category=plan.category, group_by="entity")
        items = data["items"]
        if not items:
            return ("这段时间没有匹配到「%s%s」的使用记录。" % (
                plan.room, plan.query or "设备"), data)
        top = items[0]
        text = "%s ~ %s，%s 活跃 %.1f 分钟（%d 段会话，%d 天有使用）。" % (
            fmt_ts(tr.start_ts), fmt_ts(tr.end_ts), top.get("label", ""),
            top.get("active_minutes", 0.0), top.get("sessions", 0),
            top.get("days_active", 0))
        return text, data

    def _answer_behavior(self, plan: QuestionPlan, tr: TimeRange) -> Tuple[str, Dict[str, Any]]:
        data = self.service.usage(tr, room=plan.room, query=plan.query, group_by="room")
        items = data["items"]
        if not items:
            return ("这段时间没有匹配到「%s」的行为记录。" % (plan.room or plan.query), data)
        top = items[0]
        minutes = top.get("active_minutes", 0.0)
        return ("%s 在%s待了约 %.1f 分钟（约 %.1f 小时），分布在 %d 天。" % (
            fmt_ts(tr.start_ts), top.get("label", "该区域"), minutes, minutes / 60.0,
            top.get("days_active", 0)), data)
