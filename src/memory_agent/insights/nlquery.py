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
    # P2：收紧泛词——去掉单独的"多久/时长"，否则"我几点睡觉多久"这类问题被误判为设备使用
    (Intent.DEVICE_USAGE.value, r"用了多久|使用时长|用了|用过|开过多久|开了多久|开过|usage"),
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
        # P2：停用词按长度降序替换，否则"怎么"会先把"怎么样"拆成"样"，后者永远匹配不到
        for token in sorted(("多久", "多少", "什么", "哪些", "怎么", "怎么样", "吗", "了"),
                            key=len, reverse=True):
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
            # 条数在 `summary.count`、正文在每条的 `message`（legacy 的 `data["total"]`
            # 与 `a["title"]` 都不存在）。实体集走 `_answer_usage` 同一套口径：文本匹配
            # 由规划阶段的 `entity_ids` 承担，这里只负责把它转成引擎收的 `entity_id`
            # ——改前这一跳漏了，问「鱼缸水泵有什么异常」拿到的是**全屋**异常。
            data = self.service.anomaly_report(tr, room=plan.room, category=plan.category,
                                               entity_id=",".join(plan.entity_ids))
            count = int((data.get("summary") or {}).get("count") or 0)
            messages = "；".join(str(a.get("message") or "")
                                 for a in (data.get("anomalies") or [])[:3])
            return ("%s~%s 发现 %d 条异常：%s。" % (
                fmt_ts(tr.start_ts)[:10], fmt_ts(tr.end_ts)[:10],
                count, messages or "无"), data)
        if route == Intent.RHYTHM.value:
            data = self.service.rhythm(tr, room=plan.room)
            return ("你一般 %s 左右入睡、%s 左右起床（基于 %d 天样本）。" % (
                data.get("sleep") or "未知", data.get("wake") or "未知",
                data.get("samples", 0)), data)
        if route == Intent.ACTIVITY.value:
            data = self.service.infer_activities(
                tr, rooms=plan.room, activities=[plan.activity] if plan.activity else None)
            names = [a["name"] for a in data.get("activities", [])[:5]]
            # 两处原先必抛/必乱的读数：`total` 在新引擎里不存在（KeyError 被上层兜成
            # "查询失败"，这条路由从切引擎那天起就没答上来过），`summary` 是 dict，
            # 直接 %s 会把整份字典打进给用户的话术里。
            return ("%s~%s 推断到 %d 次活动：%s。" % (
                fmt_ts(tr.start_ts)[:10], fmt_ts(tr.end_ts)[:10],
                int(data.get("total_activities") or 0), "、".join(names) or "无"), data)
        if route == Intent.PERSONA.value:
            # P2：尊重用户明确指定的时间窗（如"昨天/上周"），不再强制 max(days,14)；
            # 仅当窗口不足 1 天时回退到默认 14 天画像
            span_days = int(round((tr.end_ts - tr.start_ts) / 86400.0))
            persona_days = span_days if span_days >= 1 else max(plan.days, 14)
            data = self.service.user_persona(days=persona_days)
            return (self._persona_answer(data), data)
        hints = "；".join(plan.hints[:2])
        return ("我还不确定你想问什么。%s" % hints, {"hints": plan.hints})

    @staticmethod
    def _persona_answer(data: Dict[str, Any]) -> str:
        """把 `user_persona` 的 `traits` 拼成一句人话。

        新引擎的画像里没有 `persona.summary` 这个键（legacy 才有），原先读它 ⇒
        这条路由恒答「暂无画像。」。改为直接消费引擎真给的东西：特征列表 + 读数。
        """
        traits = {str(t.get("name") or ""): str(t.get("value") or "")
                  for t in (data.get("traits") or [])}
        if not traits:
            return "暂无画像：这段时间没有可用于画像的事件。"
        total = int(data.get("total_events") or 0)
        if total <= 0:
            return "近 %s 天画像：窗口内没有事件，作息类型读作「%s」。" % (
                data.get("days", "?"), traits.get("作息类型", "无数据"))
        return "近 %s 天画像：作息类型「%s」，规律度「%s」，最常活动房间「%s」，%s。" % (
            data.get("days", "?"), traits.get("作息类型", "未知"),
            traits.get("作息规律度", "未知"), traits.get("最常活动房间", "未知"),
            traits.get("设备交互强度", "共 %d 条事件" % total))

    def _answer_usage(self, plan: QuestionPlan, tr: TimeRange) -> Tuple[str, Dict[str, Any]]:
        # 新引擎的 `usage(tr, room, category, entity_id)` 没有 `query=` / `group_by=`：
        # 文本匹配由规划阶段的 `entity_ids` 承担，分组在返回体的 `by_room`/`by_domain` 里。
        data = self.service.usage(tr, room=plan.room, category=plan.category,
                                  entity_id=",".join(plan.entity_ids))
        items = data.get("items") or []
        if not items:
            return ("这段时间没有匹配到「%s%s」的使用记录。" % (
                plan.room, plan.query or "设备"), data)
        top = items[0]
        # 口径：引擎只有事件数（`entity_stats`），没有任何时长概念，
        # 所以不许沿用 legacy 的「活跃 N 分钟」把读数编出来。
        text = "%s ~ %s，%s 共 %d 条事件（该时段 %s 个实体有使用记录，占比 %.1f%%），分布在 %d 天。" % (
            fmt_ts(tr.start_ts), fmt_ts(tr.end_ts),
            top.get("friendly_name") or top.get("entity_id", ""),
            int(top.get("count") or 0), int(data.get("total_entities") or 0),
            100.0 * float(top.get("share") or 0.0), int(top.get("active_days") or 0))
        return text, data

    def _answer_behavior(self, plan: QuestionPlan, tr: TimeRange) -> Tuple[str, Dict[str, Any]]:
        data = self.service.usage(tr, room=plan.room, category=plan.category,
                                  entity_id=",".join(plan.entity_ids))
        rows = data.get("by_room") or []
        if not rows:
            return ("这段时间没有匹配到「%s」的行为记录。" % (plan.room or plan.query), data)
        wanted = (plan.room or "").strip()
        row = next((r for r in rows if str(r.get("room") or "") == wanted), rows[0])
        return ("%s ~ %s，%s共 %d 条事件（口径：事件条数，新引擎不测算停留时长）。" % (
            fmt_ts(tr.start_ts), fmt_ts(tr.end_ts),
            row.get("room") or "该区域", int(row.get("count") or 0)), data)
