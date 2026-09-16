"""记忆研究员（v0.8）：定向 + 安全闸 + 定期洞察。

设计要点
--------
* LLM **只「解释/命名」已被规则提纯的信号**：复用 ``InsightService`` 的预聚合产物
  （``behavior_insights`` / ``infer_activities``），绝不直接读原始事件——这是收敛
  token 与算力的核心（对比无脑 dump 原始事件成本降约 100 倍）。
* 四轴定向坐标系（Insight Job）：``方向(direction) × 时间窗(window) × 区域(area) ×
  对象(member)``。方向限定为有限类别，不开放 LLM 自由探索。
* 立体安全闸：全局日 token 预算断路器、单 Job 单元上限（笛卡尔积爆炸防护）、
  单次调用超时、连续失败退避暂停。
* 存储分工：Job 配置落 SQLite（``insight_jobs``）；已生成洞察经 chroma 语义去重
  （``dup_sim>0.92`` 则 UPDATE）后写入 ``agent_memories``（state=staging, source=ma），
  进入人工审阅闭环，30 天自动归档（``ttl_days``）。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from typing import Any

from .store import now_local

# 方向类别（有限枚举，不开放 LLM 自由探索）
DIRECTIONS: dict[str, str] = {
    "rhythm": "作息节律",
    "anomaly": "设备异常",
    "habit": "习惯固化",
    "member_diff": "成员差异",
    "linkage": "跨设备联动",
    "energy": "能耗用量",
    "sequence": "序列模式",  # v0.9.5：有序事件序列挖掘（走确定性挖掘，不调 LLM）
}
ALL_ROOM = ""    # area 空 = 全屋
ALL_MEMBER = ""  # member 空 = 全部成员

SYSTEM_PROMPT = (
    "你是家庭行为记忆中枢的「记忆研究员」。你只负责把已经由规则算好的结构化行为数据，"
    "解释/命名成一句人类可读的洞察，并判断它是否值得作为洞察沉淀。\n"
    "以下任一种都算值得记录的洞察：\n"
    " (1) 稳定的作息/活跃规律（如活跃时段、峰值小时、最活跃房间）；\n"
    " (2) 高频出现的习惯活动（如每晚看电视、工作日居家办公）；\n"
    " (3) 设备失联 / 异常（哪怕 severity 不高）。\n"
    "你必须严格输出一个 JSON 对象："
    '{"has_insight": true 或 false, "text": "一句话洞察（≤40字，中文）", "confidence": 0.0~1.0}。\n'
    "示例：{\"has_insight\": true, \"text\": \"家庭活跃时段07:00-23:00、客厅最活跃，每晚22:00-23:00看电视\", \"confidence\": 0.7}。\n"
    "仅当数据为空或纯属噪声、完全无任何可归纳规律时，has_insight 才为 false。\n"
    "禁止编造任何未出现在给定数据中的事实。"
)


class ResearcherGates:
    """立体安全闸参数（来自 config）。"""

    def __init__(self, config: Any) -> None:
        self.enabled = bool(getattr(config, "researcher_enabled", False))
        self.daily_token_budget = int(getattr(config, "researcher_daily_token_budget", 80000))
        self.unit_cap = int(getattr(config, "researcher_unit_cap", 50))
        self.call_timeout = int(getattr(config, "researcher_call_timeout", 30))
        self.max_consecutive_failures = int(
            getattr(config, "researcher_max_consecutive_failures", 3)
        )
        self.staging_ttl_days = int(getattr(config, "researcher_staging_ttl_days", 30))


class ResearcherService:
    def __init__(self, runtime: Any) -> None:
        self.rt = runtime
        self.config = runtime.config
        self.store = runtime.store
        self.insights = runtime.insights
        self.llm = runtime.llm
        self.agent_memory = runtime.agent_memory
        self.history = runtime.history
        self.gates = ResearcherGates(self.config)
        self._consecutive_failures = 0
        self._task: Any = None

    # ── 四轴切片 ────────────────────────────────────────────────
    @staticmethod
    def _split(v: Any) -> list[str]:
        if not v:
            return []
        return [x.strip() for x in str(v).split(",") if x.strip()]

    def expand_units(self, job: dict) -> list[dict]:
        """Job 四轴 → 分析单元列表（支持轴多选，逗号分隔）。受 unit_cap 截断。"""
        directions = self._split(job.get("direction")) or list(DIRECTIONS.keys())
        areas = self._split(job.get("area")) or [ALL_ROOM]
        members = self._split(job.get("member")) or [ALL_MEMBER]
        units = [
            {"direction": d, "area": a, "member": m}
            for d in directions for a in areas for m in members
        ]
        if len(units) > self.gates.unit_cap:
            units = units[: self.gates.unit_cap]
        return units

    def _topic_key(self, unit: dict) -> str:
        return "researcher:" + ":".join([
            unit["direction"],
            unit["area"] or "all",
            unit["member"] or "all",
        ])

    # ── 预聚合（零 LLM）─────────────────────────────────────────
    def _preaggregate(self, unit: dict, job: dict) -> dict:
        days = int(job.get("window_days") or 7)
        rooms = unit["area"]  # 空=全屋
        out: dict = {"direction": unit["direction"]}
        if unit["direction"] in ("rhythm", "habit", "linkage"):
            out["behavior"] = self.insights.behavior_insights(
                days=days, rooms=rooms, behavior_only=True
            )
        if unit["direction"] in ("habit", "rhythm", "member_diff"):
            out["activities"] = self.insights.infer_activities(days=days, rooms=rooms)
        if unit["member"]:
            out["_member_focus"] = unit["member"]
        return out

    # ── 提示词构建（结构化摘要，输入有界截断）──────────────────
    def _summarize_agg(self, agg: dict) -> str:
        """把预聚合产物压缩成紧凑、信息密集的可读摘要，供 LLM 判断。

        直接 json.dumps 整段会失控（behavior 可达 60KB+），且头部截断会
        丢掉 anomalies / activities 等关键信号 → LLM 误判为无洞察。
        这里只抽取人类可读的高价值字段（异常、活跃时段、峰值、活动聚合）。
        """
        out: list[str] = []
        for k, v in agg.items():
            if k == "direction" or v is None:
                continue
            if k == "behavior" and isinstance(v, dict):
                b = v
                meta = b.get("window") or {}
                lines = [f"行为(近{meta.get('days')}天): 有效事件{b.get('total_events')}条 / 原始{b.get('total_events_raw')}条"]
                dr = b.get("daily_rhythm") or {}
                if dr.get("active_window"):
                    lines.append(f"  活跃时段: {dr['active_window']}")
                if dr.get("peak_hour") is not None:
                    lines.append(f"  峰值小时: {dr['peak_hour']}:00")
                if dr.get("active_hours"):
                    lines.append(f"  活跃小时: {dr['active_hours']}")
                if b.get("most_active_room"):
                    lines.append(f"  最活跃房间: {b.get('most_active_room')}")
                dt = b.get("daily_totals") or {}
                if dt:
                    brief = ", ".join(f"{d[5:]}:{c}" for d, c in list(dt.items())[:14])
                    lines.append(f"  每日事件量: {brief}")
                anoms = b.get("anomalies") or []
                if anoms:
                    lines.append(f"  异常({len(anoms)}条):")
                    for a in anoms[:8]:
                        if isinstance(a, dict):
                            lines.append(f"    - {a.get('title') or a.get('message') or a.get('type')} (严重={a.get('severity','')})")
                        else:
                            lines.append(f"    - {a}")
                out.append("\n".join(lines))
            elif k == "activities" and isinstance(v, dict):
                acts = v.get("activities") or []
                lines = [f"活动推断(共{len(acts)}个):"]
                by_type: dict = {}
                for a in acts:
                    by_type.setdefault(a.get("activity"), []).append(a)
                for t, items in list(by_type.items())[:12]:
                    days = sorted({i.get("day") for i in items})
                    conf = max((i.get("confidence") or 0) for i in items)
                    win = next((i.get("typical_window") for i in items if i.get("typical_window")), "")
                    lines.append(f"  - {t}: {len(items)}次, 置信{conf:.2f}, 典型{win}, 出现在{len(days)}天")
                out.append("\n".join(lines))
            else:
                s = json.dumps(v, ensure_ascii=False, default=str)
                if len(s) > 600:
                    s = s[:600] + "…"
                out.append(f"[{k}]: {s}")
        return "\n".join(out)

    def _build_prompt(self, unit: dict, agg: dict) -> str:
        dname = DIRECTIONS.get(unit["direction"], unit["direction"])
        head = f"【洞察方向：{dname}】"
        if unit["area"]:
            head += f" 区域：{unit['area']}"
        if unit["member"]:
            head += f" 对象：{unit['member']}"
        head += "\n以下为规则已算好的结构化数据（请勿当成原始事件）：\n"
        summary = self._summarize_agg(agg)
        if len(summary) > 4000:  # 输入上限兜底，超长截断（控制 token）
            summary = summary[:4000] + "\n…(已截断)"
        return head + summary

    # ── LLM 解释命名（有界）────────────────────────────────────
    async def _llm_name(self, unit: dict, agg: dict) -> dict:
        prompt = self._build_prompt(unit, agg)
        result = await asyncio.wait_for(
            self.llm.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=150,
            ),
            timeout=self.gates.call_timeout,
        )
        content = self._extract_content(result)
        token = int((result.get("usage") or {}).get("total_tokens", 0))
        backend = result.get("_backend") or {}
        return self._parse_llm(content, token, backend)

    @staticmethod
    def _extract_content(result: dict) -> str:
        # LLMRouter.chat 返回简化的 {content} 结构（非标准 OpenAI choices 结构）
        if not isinstance(result, dict):
            return ""
        if "content" in result and result.get("content") is not None:
            return str(result.get("content") or "")
        try:
            return result["choices"][0]["message"]["content"] or ""
        except Exception:
            return ""

    @staticmethod
    def _parse_llm(content: str, token: int, backend: dict | None = None) -> dict:
        text = (content or "").strip()
        try:
            obj = json.loads(text)
        except Exception:
            s, e = text.find("{"), text.rfind("}")
            if s >= 0 and e > s:
                try:
                    obj = json.loads(text[s:e + 1])
                except Exception:
                    return {"has_insight": False, "text": "", "confidence": 0.0, "token": token, "backend": backend or {}}
            else:
                return {"has_insight": False, "text": "", "confidence": 0.0, "token": token, "backend": backend or {}}
        return {
            "has_insight": bool(obj.get("has_insight", False)),
            "text": str(obj.get("text", "")).strip(),
            "confidence": float(obj.get("confidence", 0.0) or 0.0),
            "token": token,
            "backend": backend or {},
        }

    # ── 去重 + 写入 staging（统一走 mem0 式合并管线，v0.8-2）──────────
    def _dedupe_or_add(self, unit: dict, text: str, job: dict) -> tuple[str, str]:
        topic_key = self._topic_key(unit)
        tags = [
            f"direction:{unit['direction']}",
            f"area:{unit['area'] or 'all'}",
            f"member:{unit['member'] or 'all'}",
            "auto-researcher",
        ]
        refs = [f"insight:researcher:{job.get('job_id', '')}"]
        res = self.agent_memory.merge_semantic_memory(
            session_id="researcher", text=text, topic_key=topic_key,
            tags=tags, source_refs=refs, ttl_days=self.gates.staging_ttl_days,
            source="ma",
        )
        action = res.get("action", "added")
        # 统计口径归一：invalidated_added / conflict_pending 仍计为一次"写入"
        if action in ("invalidated_added", "conflict_pending"):
            action = "added"
        return res.get("memory_id", ""), action

    # ── 单 Job 运行 ─────────────────────────────────────────────
    async def run_job(self, job: dict, run_ctx: dict) -> dict:
        units = self.expand_units(job)
        started = now_local(self.config.tz_offset_hours)
        stats: dict[str, Any] = {
            "job_id": job.get("job_id"), "units_total": len(units),
            "units_processed": 0, "hits": 0, "token": 0,
            "added": 0, "updated": 0, "stopped_by_budget": False,
            "stopped_by_failure": False, "backend": {},
        }
        for unit in units:
            if run_ctx["budget_left"] <= 0:
                stats["stopped_by_budget"] = True
                break
            # 任务 C（v0.9.5）：sequence 方向走确定性序列挖掘（不调 LLM），复用安全闸
            if unit.get("direction") == "sequence":
                try:
                    area = unit.get("area") or ""
                    rooms = [area] if area else None
                    res = await asyncio.to_thread(
                        self.rt.activity.mine_sequences,
                        None, None, int(job.get("window_days") or 7),
                        2, 12, rooms,
                    )
                    cnt = int(res.get("candidates") or 0)
                    stats["units_processed"] += 1
                    stats["hits"] += cnt
                    stats["candidates"] = stats.get("candidates", 0) + cnt
                except Exception as exc:  # noqa: BLE001
                    self._consecutive_failures += 1
                    stats["error"] = str(exc)[:200]
                continue
            try:
                agg = await asyncio.to_thread(self._preaggregate, unit, job)
                llm = await self._llm_name(unit, agg)
            except Exception as exc:
                self._consecutive_failures += 1
                stats["error"] = str(exc)[:200]
                if self._consecutive_failures >= self.gates.max_consecutive_failures:
                    stats["stopped_by_failure"] = True
                    break
                continue
            self._consecutive_failures = 0
            run_ctx["budget_left"] -= llm["token"]
            stats["token"] += llm["token"]
            stats["units_processed"] += 1
            if stats.get("backend") == {} and llm.get("backend"):
                stats["backend"] = llm["backend"]
            if llm["has_insight"] and llm["text"]:
                stats["hits"] += 1
                _, action = await asyncio.to_thread(self._dedupe_or_add, unit, llm["text"], job)
                stats[action] = stats.get(action, 0) + 1
        self.store.touch_insight_job_last_run(job.get("job_id"))
        run_id = f"run_{int(time.time() * 1000)}_{job.get('job_id', '')}"
        self.store.record_researcher_run({
            "run_id": run_id, "job_id": job.get("job_id"),
            "started_at": run_ctx.get("date") or started.strftime("%Y-%m-%d"),
            "finished_at": now_local(self.config.tz_offset_hours).isoformat(),
            "token_used": stats.get("token", 0),
            "units_total": stats.get("units_total", 0),
            "units_processed": stats.get("units_processed", 0),
            "hits": stats.get("hits", 0),
            "ok": 0 if stats.get("stopped_by_failure") else 1,
            "error": str(stats.get("error", "")),
            "detail_json": json.dumps(stats, ensure_ascii=False),
        })
        return stats

    # ── 全部 Job 运行（全局日预算断路器）────────────────────────
    async def run_all(self, force: bool = False) -> dict:
        if not (self.gates.enabled or force):
            return {"skipped": "researcher_enabled=false"}
        today = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d")
        used = self.store.researcher_daily_token_used(today)
        budget_left = self.gates.daily_token_budget - used
        if budget_left <= 0:
            return {"skipped": "daily budget exhausted", "used": used}
        jobs = self.store.list_insight_jobs(enabled_only=True)
        run_ctx = {"budget_left": budget_left, "date": today}
        summary: list[dict] = []
        for job in jobs:
            stats = await self.run_job(job, run_ctx)
            summary.append(stats)
        return {
            "jobs": len(jobs),
            "budget_left": run_ctx["budget_left"],
            "detail": summary,
        }

    # ── 定期调度（asyncio 循环，按 researcher_scheduler_time）──
    async def _periodic(self) -> None:
        while True:
            try:
                now = now_local(self.config.tz_offset_hours)
                hh, mm = (self.config.researcher_scheduler_time or "03:00").split(":")
                target = now.replace(
                    hour=int(hh), minute=int(mm), second=0, microsecond=0
                )
                if target <= now:
                    target = target + timedelta(days=1)
                await asyncio.sleep(max(1, (target - now).total_seconds()))
                await self.run_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[Researcher] 周期任务异常: {exc}")
                await asyncio.sleep(3600)

    def start(self) -> None:
        if self.gates.enabled and self._task is None:
            try:
                self._task = asyncio.create_task(self._periodic())
            except RuntimeError:
                # 不在运行中的事件循环内（如测试），跳过自动调度
                pass

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
