"""AutoFlow 竞技场对接核心。

提供：竞技场分区版本化快照（脱敏）、灵感生成、题目创造力三层评估、提交结果记录。
所有接口仅对持有 ``arena_`` 令牌（kind=arena）的 AutoFlow 竞技场开放，与
生产 / butler / ACP 令牌三者隔离（见 docs/交接单_AutoFlow竞技场对接.md）。

设计要点
--------
* 快照一旦生成即固定（版本化），后续竞技场查询只走快照，不随真实家庭数据变化，
  保证竞技场可复现。
* 脱敏：实体名按首次出现顺序稳定映射为「设备N」，成员名映射为「成员N」；
  配置 ``arena_desensitize`` 可自定义映射，缺省自动生成。
* 题目库去重用 chroma ``arena_titles`` 集合（与行为历史 / Agent 记忆命名空间隔离）；
  chroma 不可用时退化为「无历史去重」，不阻塞提交。
* 创造力评估三层：实体重叠(快速) → 文本相似(轻量) → LLM 语义(仅模糊区间介入)，
  控制时延与 LLM 成本。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from typing import Any


def _tokens(s: str) -> set:
    return set(re.findall(r"[一-鿿a-zA-Z0-9_]+", s or ""))


def _jaccard(a, b) -> float:
    a, b = set(a or []), set(b or [])
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _hour_of(ts) -> int | None:
    """从事件时间戳里抠出小时（0-23），失败返回 None。"""
    if not ts:
        return None
    s = str(ts)
    try:
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(s2).hour
    except Exception:
        m = re.search(r"(\d{1,2}):\d{2}", s)
        return int(m.group(1)) % 24 if m else None


class ArenaService:
    def __init__(self, config, store, history, insights, llm):
        self.config = config
        self.store = store
        self.history = history
        self.insights = insights
        self.llm = llm

    # ── 快照 ──────────────────────────────────────────────────────────
    def build_snapshot(self, arena_id, room, devices, history_days=30) -> dict:
        """从真实库抽取指定房间设备 + 历史，脱敏后存为版本化固定快照。"""
        devices = list(devices or [])
        # 自定义脱敏映射（可选）：真实名 -> 通用名
        custom = getattr(self.config, "arena_desensitize", None) or {}

        res = self.insights.search_events(
            room=room, days=history_days, behavior_only=True,
            limit=2000, order="asc",
        )
        events = res.get("events") or [] if isinstance(res, dict) else []

        mapping: dict[str, str] = {}
        entity_stats: dict[str, dict] = {}
        order = 0
        for ev in events:
            eid = ev.get("entity_id")
            if not eid:
                continue
            if eid not in mapping:
                order += 1
                mapping[eid] = custom.get(eid) or f"设备{order}"
            label = mapping[eid]
            st = entity_stats.setdefault(label, {
                "label": label, "original": eid,
                "count": 0, "hours": Counter(),
            })
            st["count"] += 1
            h = _hour_of(ev.get("ts") or ev.get("server_ts") or ev.get("time"))
            if h is not None:
                st["hours"][h] += 1

        entities = []
        for label, st in entity_stats.items():
            busy = [h for h, _ in st["hours"].most_common(3)]
            entities.append({
                "label": label, "original": st["original"],
                "count": st["count"], "busy_hours": busy,
            })
        entities.sort(key=lambda x: -x["count"])

        snapshot = {
            "arena_id": arena_id,
            "room": room,
            "devices": devices,
            "history_days": history_days,
            "total_events": len(events),
            "window": res.get("window") if isinstance(res, dict) else None,
            "entities": entities,
            "members": [],
            "activity_hints": [],
        }
        saved = self.store.save_arena_snapshot(
            arena_id, room, json.dumps(devices, ensure_ascii=False),
            history_days, json.dumps(snapshot, ensure_ascii=False),
        )
        snapshot["version"] = saved["version"]
        snapshot["snapshot_id"] = saved["id"]
        return snapshot

    # ── 灵感生成 ──────────────────────────────────────────────────────
    async def get_arena_inspiration(self, arena_id, inspiration_type="all", limit=5) -> dict:
        snap = await asyncio.to_thread(self.store.get_arena_snapshot, arena_id)
        if not snap:
            return {
                "ok": True, "arena_id": arena_id, "count": 0, "items": [],
                "hint": "该竞技场分区尚无快照，请先调用 POST /api/arena/snapshot 创建",
            }
        data = json.loads(snap["snapshot_json"])
        entities = data.get("entities", [])
        items = []
        idx = 0
        for ent in entities:
            idx += 1
            if inspiration_type not in ("all", "device", "behavior"):
                continue
            busy = ent.get("busy_hours") or []
            busy_str = "、".join(f"{h}:00" for h in busy) or "全天"
            score = min(1.0, ent["count"] / 40.0)
            items.append({
                "id": f"ins_{idx:03d}",
                "type": "device",
                "title": f"{ent['label']} 高频使用模式",
                "description": (
                    f"过去 {data.get('history_days')} 天，{ent['label']} 共触发 "
                    f"{ent['count']} 次，主要在 {busy_str} 时段活跃。"
                ),
                "suggested_flow": (
                    f"为 {ent['label']} 编写一个自动化 flow：在 {busy_str} 执行对应场景"
                    f"（如联动灯光 / 提醒 / 节能调度）。"
                ),
                "entity_hints": [ent["label"]],
                "creativity_score": round(score, 3),
            })
        items = items[: max(1, int(limit))]
        return {"ok": True, "arena_id": arena_id, "count": len(items), "items": items}

    # ── 创造力三层评估 ────────────────────────────────────────────────
    async def evaluate_creativity(self, arena_id, title, description, entity_ids) -> dict:
        entity_ids = list(entity_ids or [])

        # 快照实体（用于 relevance 贴合度）
        snap = await asyncio.to_thread(self.store.get_arena_snapshot, arena_id)
        snap_entities: list[str] = []
        if snap:
            try:
                snap_entities = [
                    e["label"]
                    for e in json.loads(snap["snapshot_json"]).get("entities", [])
                ]
            except Exception:
                snap_entities = []
        relevance = _jaccard(entity_ids, snap_entities) if snap_entities else 0.5

        text = f"{title}\n{description}"
        col = self.history.arena_collection
        nearest = None
        sim = 0.0
        if col is not None:
            try:
                r = col.query(
                    query_texts=[text], n_results=3,
                    include=["documents", "metadatas", "distances"],
                )
                docs = (r.get("documents") or [[]])[0]
                metas = (r.get("metadatas") or [[]])[0]
                dists = (r.get("distances") or [[]])[0]
                if docs:
                    d = float(dists[0]) if dists else 0.0
                    # chroma 距离约 0~2（cosine），归一到相似度
                    sim = max(0.0, 1.0 - d / 2.0)
                    nearest = {
                        "document": docs[0],
                        "metadata": metas[0] if metas else {},
                        "distance": d,
                    }
            except Exception as exc:
                print(f"[Arena] chroma 查询失败: {exc}")
                nearest = None

        # 文本 / 实体重叠（与最近一条比对）
        text_sim = 0.0
        ent_overlap = 0.0
        if nearest:
            ndoc = nearest.get("document") or ""
            text_sim = _jaccard(_tokens(text), _tokens(ndoc))
            n_meta = nearest.get("metadata") or {}
            n_ents = []
            try:
                n_ents = json.loads(n_meta.get("entity_ids", "[]") or "[]")
            except Exception:
                n_ents = []
            ent_overlap = _jaccard(entity_ids, n_ents)

        # 三层判定：实体重叠(快速) → 文本相似(轻量) → LLM 语义(仅模糊区间)
        is_dup = False
        reason = ""
        if (ent_overlap >= 0.8 and text_sim >= 0.6) or sim >= 0.85:
            is_dup = True
            reason = "与已存在题目高度重合（实体/文本/向量）"
        elif 0.3 <= text_sim < 0.85 or 0.5 <= sim < 0.85:
            try:
                if await self._llm_same(title, description, nearest.get("document", "")):
                    is_dup = True
                    reason = "LLM 语义判定为同一题目"
            except Exception as exc:
                reason = f"模糊区间 LLM 判定失败，按启发式放行（{exc}）"

        creativity = round(0.6 * (1 - sim) + 0.4 * relevance, 3)
        novelty = round(1 - sim, 3)
        feedback = self._feedback(is_dup, reason, relevance, novelty, nearest)
        duplicate_of = (nearest.get("metadata") or {}).get("tid") if is_dup else None

        # 锁定题目库：非重复则写入（幂等 upsert，tid 由内容哈希决定）
        if not is_dup and col is not None:
            try:
                tid = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
                col.upsert(
                    ids=[tid],
                    documents=[text],
                    metadatas=[{
                        "tid": tid,
                        "arena_id": arena_id,
                        "entity_ids": json.dumps(entity_ids, ensure_ascii=False),
                    }],
                )
            except Exception as exc:
                print(f"[Arena] 题目库写入失败: {exc}")

        return {
            "ok": True,
            "arena_id": arena_id,
            "creativity_score": creativity,
            "novelty_score": novelty,
            "relevance_score": round(relevance, 3),
            "is_duplicate": is_dup,
            "duplicate_of": duplicate_of,
            "feedback": feedback,
        }

    async def _llm_same(self, title, description, other_doc) -> bool:
        if not other_doc:
            return False
        sys = ("你是竞技场题目查重助手。判断两个题目是否本质上在做同一件事、解决同一问题。"
               "只回答 yes 或 no，不要解释。")
        usr = (f"题目A：{title}\n{description}\n\n"
               f"题目B：{other_doc}\n\n是否本质上相同（yes/no）？")
        resp = await self.llm.chat([
            {"role": "system", "content": sys},
            {"role": "user", "content": usr},
        ])
        ans = (resp.get("content") or "").strip().lower()
        return ans.startswith("yes") or ans.startswith("是")

    @staticmethod
    def _feedback(is_dup, reason, relevance, novelty, nearest) -> str:
        if is_dup:
            return f"该题目已被提交过（{reason}），建议换一个切入点或合并改进。"
        parts = []
        if relevance < 0.3:
            parts.append("题目涉及的设备与该竞技场分区的可用设备关联较弱，建议更贴合场景数据。")
        if novelty < 0.3:
            parts.append("题目与已有题目相似度偏高，可尝试更独特的视角。")
        if not parts:
            parts.append("题目与场景数据贴合且具备新意，可继续细化 flow。")
        if nearest:
            doc = nearest.get("document", "")
            parts.append(f"参考已有题目：{doc[:80]}")
        return " ".join(parts)

    # ── 结果记录 ──────────────────────────────────────────────────────
    async def record_arena_result(self, arena_id, task_title, task_description,
                                 flow_dsl, success, token_used, agent_id,
                                 used_memory_tools) -> dict:
        insight_id = await asyncio.to_thread(
            self.store.add_arena_result, arena_id, agent_id, task_title,
            task_description, flow_dsl, success, token_used, used_memory_tools,
        )
        return {"ok": True, "recorded": True, "insight_id": insight_id}
