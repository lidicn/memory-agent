"""行为分析服务

核心是**上下文压缩**：不能把上万条原始事件塞进 prompt。
先用 SQLite 聚合出紧凑的数字画像（房间×小时直方图、Top 实体、状态转移对、
按天汇总），再附少量代表性原始事件采样，整体控制在约 6k tokens 内 ——
信息密度远高于原始流水，成本与延迟也可控。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from .store import TELEMETRY_DOMAINS, Store, now_local

CATEGORIES = ("sleep", "media", "lighting", "climate", "appliance", "security", "other")

SYSTEM_PROMPT = """你是家庭行为分析专家，擅长从智能家居设备的状态变化数据中识别人的生活作息与行为模式。

你会收到一份「行为数据画像」，包含：
- 统计概览：事件总量、覆盖天数、涉及房间
- 房间×小时活跃直方图：每个房间在 0-23 点各时段的事件数
- 高频实体：触发次数最多的设备
- 状态转移对：设备 A 变化后紧接着设备 B 变化的频次与平均间隔
- 按天汇总与代表性事件采样

请识别出**一个**最显著、最有自动化价值的行为模式，并严格按下面的 JSON 结构输出。

输出要求（极其重要）：
1. 整个回答只能包含一个 ```json 代码块，块内是合法 JSON，不要在 JSON 里写注释
2. 严禁在代码块之外输出任何分析过程、方案对比、解释或说明文字 —— 直接输出 JSON 即可
3. 不要列举多个候选方案，只输出你最终选定的那一个行为模式
4. id 用小写英文与下划线，能体现语义，例如 evening_living_room_routine
5. category 只能取：sleep / media / lighting / climate / appliance / security / other
6. entities 中的 entity_id 必须来自输入数据中真实出现过的实体，不得编造
7. confidence 取 0~1 的小数，依据样本量与规律稳定性给出
8. nr_condition / nr_action 写成 Node-RED 中可直接参考的判断条件与动作描述

JSON 结构：
```json
{
  "id": "string",
  "name": "中文名称",
  "description": "一句话描述这个行为模式",
  "category": "lighting",
  "entities": [
    {"entity_id": "light.xxx", "attribute": "state", "pattern": "equals", "value": "on", "time_range": "19:00-23:00"}
  ],
  "pattern": "详细的模式描述：什么时间、什么条件下、发生什么",
  "confidence": 0.8,
  "sample_days": 7,
  "nr_condition": "Node-RED 判断条件描述",
  "nr_action": "Node-RED 执行动作描述"
}
```"""


class AnalysisService:
    MAX_TOP_ENTITIES = 25
    MAX_TRANSITIONS = 20
    MAX_SAMPLES = 40

    def __init__(self, config, store: Store, llm):
        self.config = config
        self.store = store
        self.llm = llm

    # ── 数据画像 ─────────────────────────────────────────────────────────

    def resolve_range(self, start_day: str = "", end_day: str = "", days: int = 7):
        tz = self.config.tz_offset_hours
        if start_day and end_day:
            return f"{start_day}T00:00:00", f"{end_day}T23:59:59"
        end = now_local(tz)
        start = end - timedelta(days=max(1, int(days or 7)))
        return start.isoformat(sep="T"), end.isoformat(sep="T")

    def build_digest(
        self,
        start: str,
        end: str,
        rooms: list[str] | None = None,
        behavior_only: bool = True,
    ) -> dict:
        excl = list(TELEMETRY_DOMAINS) if behavior_only else None
        total = self.store.count_events(start, end, rooms, exclude_domains=excl)
        histogram = self.store.hour_histogram(start, end, rooms, exclude_domains=excl)
        tops = self.store.top_entities(
            start, end, rooms, self.MAX_TOP_ENTITIES, exclude_domains=excl
        )
        transitions = self.store.transitions(
            start, end, rooms, limit=self.MAX_TRANSITIONS, exclude_domains=excl
        )
        daily = self.store.daily_counts_by_room(start, end, rooms)
        samples = self.store.sample_events(start, end, rooms, self.MAX_SAMPLES)

        by_day: dict[str, int] = {}
        for row in daily:
            by_day[row["day"]] = by_day.get(row["day"], 0) + row["count"]

        return {
            "range": {"start": start, "end": end},
            "behavior_only": behavior_only,
            "total_events": total,
            "days_covered": len(by_day),
            "rooms": sorted(histogram.keys()),
            "hour_histogram": histogram,
            "top_entities": tops,
            "transitions": transitions,
            "daily_totals": by_day,
            "samples": samples,
        }

    def render_digest(self, digest: dict) -> str:
        """把画像渲染为紧凑文本，比直接塞 JSON 更省 token 且更易读。"""
        lines: list[str] = []
        rng = digest["range"]
        lines.append(f"# 行为数据画像")
        lines.append(
            f"时间范围: {rng['start']} ~ {rng['end']}｜"
            f"事件总量: {digest['total_events']}｜"
            f"覆盖天数: {digest['days_covered']}｜"
            f"房间数: {len(digest['rooms'])}"
        )

        lines.append("\n## 房间 × 小时活跃直方图（0-23 点事件数）")
        for room, buckets in sorted(
            digest["hour_histogram"].items(), key=lambda kv: -sum(kv[1])
        )[:12]:
            compact = ",".join(str(v) for v in buckets)
            lines.append(f"- {room}（合计 {sum(buckets)}）: [{compact}]")

        lines.append("\n## 高频实体")
        for item in digest["top_entities"]:
            lines.append(
                f"- {item['entity_id']}（{item['room']}）触发 {item['count']} 次"
            )

        if digest["transitions"]:
            lines.append("\n## 状态转移对（A 之后紧接着 B）")
            for t in digest["transitions"]:
                lines.append(
                    f"- [{t['room']}] {t['from']} → {t['to']}："
                    f"{t['count']} 次，平均间隔 {t['avg_gap_s']:.0f} 秒"
                )

        if digest["daily_totals"]:
            lines.append("\n## 每日事件量")
            lines.append(
                "，".join(f"{d}:{c}" for d, c in sorted(digest["daily_totals"].items()))
            )

        if digest["samples"]:
            lines.append("\n## 代表性事件采样")
            for s in digest["samples"]:
                lines.append(
                    f"- {s['ts']} [{s['room']}] {s['entity_id']} "
                    f"{s['old_state']} → {s['new_state']}"
                )
        return "\n".join(lines)

    # ── Prompt ───────────────────────────────────────────────────────────

    def build_messages(
        self, digest: dict, focus: str = "", person: str = ""
    ) -> list[dict]:
        user_parts = [self.render_digest(digest)]
        if person and person != "all":
            user_parts.append(f"\n分析对象：{person}")
        if focus:
            user_parts.append(f"\n分析侧重：{focus}")
        user_parts.append(
            "\n请基于以上数据识别一个最有自动化价值的行为模式，并按要求输出 JSON。"
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

    # ── 结果解析 ─────────────────────────────────────────────────────────

    def parse_insight(self, text: str) -> dict:
        """三级容错解析：代码块 → 裸 JSON → 大括号截取。

        解析失败时不报错，而是把原文一并返回，让用户手工修正 ——
        模型偶发的格式漂移不应该让整次分析白跑。
        """
        raw = text or ""
        candidates: list[str] = []

        for match in re.finditer(r"```(?:json)?\s*(.+?)```", raw, re.S):
            candidates.append(match.group(1).strip())
        stripped = raw.strip()
        if stripped.startswith("{"):
            candidates.append(stripped)
        first, last = raw.find("{"), raw.rfind("}")
        if first != -1 and last > first:
            candidates.append(raw[first : last + 1])

        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("name"):
                return {"ok": True, "insight": self._normalize(data), "raw": raw}
        return {"ok": False, "error": "未能从回答中解析出结构化 JSON", "raw": raw}

    def _normalize(self, data: dict) -> dict:
        entities = []
        for e in data.get("entities") or []:
            if not isinstance(e, dict) or not e.get("entity_id"):
                continue
            entities.append(
                {
                    "entity_id": str(e.get("entity_id")),
                    "attribute": str(e.get("attribute") or "state"),
                    "pattern": str(e.get("pattern") or "equals"),
                    "value": str(e.get("value") or ""),
                    "time_range": str(e.get("time_range") or ""),
                }
            )
        category = str(data.get("category") or "other").lower()
        if category not in CATEGORIES:
            category = "other"
        try:
            confidence = max(0.0, min(float(data.get("confidence") or 0.0), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            sample_days = max(0, int(data.get("sample_days") or 0))
        except (TypeError, ValueError):
            sample_days = 0

        raw_id = str(data.get("id") or "").strip()
        safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw_id).strip("_").lower()
        if not safe_id:
            safe_id = f"insight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        return {
            "id": safe_id,
            "name": str(data.get("name") or "").strip(),
            "description": str(data.get("description") or "").strip(),
            "category": category,
            "entities": entities,
            "pattern": str(data.get("pattern") or "").strip(),
            "confidence": confidence,
            "sample_days": sample_days,
            "nr_condition": str(data.get("nr_condition") or "").strip(),
            "nr_action": str(data.get("nr_action") or "").strip(),
        }

    # ── 流式分析 ─────────────────────────────────────────────────────────

    async def stream_analyze(
        self,
        digest: dict,
        focus: str = "",
        person: str = "",
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[dict]:
        messages = self.build_messages(digest, focus, person)
        buffer: list[str] = []
        async for chunk in self.llm.stream_chat(
            messages, model=model, temperature=temperature, max_tokens=8192
        ):
            if chunk.get("type") == "content":
                buffer.append(chunk.get("delta", ""))
            yield chunk
        parsed = self.parse_insight("".join(buffer))
        yield {"type": "insight", "data": parsed}
