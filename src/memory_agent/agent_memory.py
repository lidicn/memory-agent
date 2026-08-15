"""Agent 记忆服务：参与式写回向量库的安全封装。

四道保险
--------
1. 命名空间隔离：chroma 用独立 ``agent_memory`` 集合；系统种子 ``behavior_history`` 永不被碰。
2. 元数据权威源在 SQLite(``agent_memories``)，chroma 只存标量镜像(metadata)。
3. staging→live 晋升需过 条件评估 / 矛盾检测；``force`` 仅限特权会话。
4. TTL 自动过期 + 自动晋升 sweep，否则写回「能写不能用」。

检索侧（v2 #2）：chroma 只能按 ``where`` 过滤，不能按 metadata 加权，
因此 ``retrieve`` 先过取 top-k，再用 ``0.7*sim + 0.3*trust`` 在客户端重排。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from .store import now_local

AGENT_COLLECTION = "agent_memory"
AGENT_STATES = ("staging", "live", "revoked", "pending_review")


def make_activity_id(activity: str, day: str, start_ts: str, end_ts: str, room: str) -> str:
    """推断活动稳定 id（供 source_refs / promote(a) 解析）。"""
    raw = f"{activity}|{day}|{start_ts}|{end_ts}|{room}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class AgentMemoryService:
    def __init__(self, config, store, history):
        self.config = config
        self.store = store
        self.history = history

    # ── 内部工具 ──────────────────────────────────────────────────────────

    @property
    def _col(self):
        """chroma agent_memory 集合；不可用返回 None。"""
        return self.history.agent_collection

    @staticmethod
    def _to_metadata(mem: dict) -> dict:
        # 仅标量（v2 #8）：tags 只存 SQL，不放 chroma（旧版 chroma 不接受 list）
        return {
            "kind": "agent_memory",
            "state": mem["state"],
            "trust": float(mem.get("trust", 0.0)),
            "expires_at": mem.get("expires_at", ""),
            "session_id": mem.get("session_id", ""),
            "memory_id": mem.get("memory_id", ""),
            "topic_key": mem.get("topic_key", ""),
        }

    def _upsert_mirror(self, mem: dict) -> None:
        """把一条记忆同步进 chroma 镜像；失败置 mirror_dirty（不抛，v2 #9）。"""
        col = self._col
        if col is None:
            self.store.mark_mirror_dirty(mem["memory_id"], 1)
            return
        try:
            col.upsert(
                ids=[mem["memory_id"]],
                documents=[mem["text"]],
                metadatas=[self._to_metadata(mem)],
            )
            self.store.mark_mirror_dirty(mem["memory_id"], 0)
        except Exception as exc:  # pragma: no cover - 网络/序列化异常
            print(f"[AgentMemory] 镜像同步失败 {mem['memory_id']}: {exc}")
            self.store.mark_mirror_dirty(mem["memory_id"], 1)

    # ── 溯源校验（v2 #5 真溯源，非空即过 → 必须可解析）──────────────────
    def _validate_source_refs(self, source_refs: List[str]) -> Tuple[bool, List[str]]:
        # 合法前缀白名单:写入要求引用格式合法的溯源(防幻觉);
        # 真实可解析性在晋升佐证 / 人工复核阶段校验,故此处不强制实体已存在。
        VALID_PREFIXES = ("event:", "insight:", "activity:")
        invalid: List[str] = []
        for ref in source_refs:
            ref = (ref or "").strip()
            if not ref:
                invalid.append("(empty)")
                continue
            if not any(ref.startswith(p) for p in VALID_PREFIXES):
                invalid.append(ref)  # 未知前缀 = 无效
                continue
            _id = ref.split(":", 1)[1]
            if not _id.strip():
                invalid.append(ref)  # 前缀合法但 id 为空 = 无效
        return (len(invalid) == 0), invalid

    # ── 矛盾 / 重复检测（v2 #1）──────────────────────────────────────────
    def conflict_scan(self, text: str, topic_key: str, exclude_id: str = "") -> dict:
        """对一条（待晋升）记忆，查 live 同 topic_key 记忆，判重复 / 冲突。"""
        col = self._col
        out = {"available": False, "duplicate": False, "conflict": False, "neighbors": []}
        if col is None:
            return out
        try:
            res = col.query(query_texts=[text], where={"state": "live"}, n_results=5)
        except Exception as exc:  # pragma: no cover
            print(f"[AgentMemory] conflict_scan 查询失败: {exc}")
            return out
        out["available"] = True
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dup_sim = float(getattr(self.config, "agent_dup_sim", 0.92))
        con_sim = float(getattr(self.config, "agent_conflict_sim", 0.85))
        for i, mid in enumerate(ids):
            if mid == exclude_id:
                continue
            meta = metas[i] or {}
            dist = dists[i] if i < len(dists) else 1.0
            sim = 1.0 / (1.0 + max(dist, 0.0))  # l2 距离的单调相似度近似
            out["neighbors"].append(
                {"memory_id": mid, "topic_key": meta.get("topic_key", ""),
                 "similarity": round(sim, 3)}
            )
            if meta.get("topic_key") == topic_key:
                if sim >= dup_sim:
                    out["duplicate"] = True
                elif sim <= con_sim:
                    out["conflict"] = True
        return out

    # ── 写回（add_semantic_memory，默认 dry_run 自检）────────────────────
    def add_semantic_memory(
        self,
        session_id: str,
        text: str,
        tags: Optional[List[str]] = None,
        source_refs: Optional[List[str]] = None,
        ttl_days: Optional[int] = None,
        topic_key: str = "",
        dry_run: bool = True,
    ) -> dict:
        tags = tags or []
        source_refs = source_refs or []
        ttl_days = ttl_days or int(getattr(self.config, "agent_default_ttl_days", 30))

        if not text or not text.strip():
            return {"ok": False, "error": "text 不能为空", "code": 400}
        if not source_refs:
            return {"ok": False, "error": "source_refs 不能为空（参与式写回要求可溯源）", "code": 422}

        ok, invalid = self._validate_source_refs(source_refs)
        if not ok:
            return {"ok": False, "error": "source_refs 含非法引用（需 event:/insight:/activity: 前缀）",
                    "invalid_refs": invalid, "code": 422}

        tk = topic_key or (tags[0] if tags else "general")

        # 声誉回路（v2 #3）：声誉差的 session 写入即锁自动晋升
        trust_info = self.store.get_session_agent_trust(
            session_id, float(getattr(self.config, "trust_strict_threshold", -0.3))
        )
        auto_block = 1 if trust_info.get("strict") else 0

        if dry_run:
            scan = self.conflict_scan(text, tk)
            return {
                "ok": True,
                "dry_run": True,
                "will_embed": not scan.get("duplicate"),
                "conflict_scan": scan,
                "auto_promote_blocked": auto_block,
                "session_trust": trust_info,
                "message": "dry_run 未落库；preview 仅供参考",
            }

        mid = self.store.add_agent_memory(
            session_id=session_id,
            text=text,
            topic_key=tk,
            tags_json=json.dumps(tags, ensure_ascii=False),
            source_refs_json=json.dumps(source_refs, ensure_ascii=False),
            ttl_days=ttl_days,
            state="staging",
            auto_promote_blocked=auto_block,
        )
        self._upsert_mirror(self.store.get_agent_memory(mid))
        return {
            "ok": True,
            "memory_id": mid,
            "state": "staging",
            "auto_promote_blocked": auto_block,
            "message": "已写入 staging；永不自动进 live，需 promote / sweep 晋升",
        }

    # ── 晋升条件评估（v2 #1 / #6；修复：前缀归一化 + 误导性报错）────────────
    def _evaluate_promotion(self, mem: dict, corroborating_insight_id: str = "") -> Tuple[bool, str]:
        src_refs = json.loads(mem.get("source_refs_json") or "[]")
        # 归一化：裸 id 与 insight:<id> 两种传法都接受（缺陷 1：旧代码双重前缀化）
        cid = (corroborating_insight_id or "").strip()
        if cid.startswith("insight:"):
            cid = cid[len("insight:"):]
        if cid:
            # (a) 主路径：佐证 insight 必须是该记忆 source_refs 中可解析的引用且高置信
            ref = f"insight:{cid}"
            if ref not in src_refs:
                return False, "corroborating_insight_id 不在该记忆的可解析 source_refs 中"
            act = self.store.get_detected_activity(cid)
            if act is None:
                return False, "corroborating_insight_id 对应的 detected_activity 不存在"
            min_conf = float(getattr(self.config, "agent_corroborate_min_conf", 0.6))
            conf = float(act.get("confidence", 0))
            if conf < min_conf:
                return False, f"佐证置信度 {conf} 低于门槛 {min_conf}"
            return True, "corroborated_by_insight"
        # (b) 备选：跨 N 天反复观测：event: 引用的日期跨度
        event_ids = [r[len("event:"):] for r in src_refs if r.startswith("event:")]
        days = set()
        for eid in event_ids:
            ev = self.store.get_event(eid)
            if ev and ev.get("day"):
                days.add(ev["day"])
        min_days = int(getattr(self.config, "agent_promote_min_days", 2))
        if len(days) >= min_days:
            return True, f"cross_day_observations={len(days)}"
        return False, f"(a) 需提供高置信佐证 insight_id；(b) 或跨 ≥{min_days} 天观测"

    def promote_memory(
        self,
        memory_id: str,
        session_id: str = "",
        force: bool = False,
        corroborating_insight_id: str = "",
        human_override: bool = False,
    ) -> dict:
        mem = self.store.get_agent_memory(memory_id)
        if mem is None:
            return {"ok": False, "error": "memory_id 不存在"}
        if mem["state"] == "live":
            return {"ok": True, "state": "live", "message": "已是 live"}

        # 安全闸门:force 仅限特权会话(自动化 agent 不可私自放量)。
        # human_override 仅由 Web 端人工复核触发(已通过 require_user)。
        privileged = session_id in (getattr(self.config, "privileged_sessions", []) or [])
        if force and not privileged and not human_override:
            return {"ok": False, "error": "force=True 仅限特权会话（config.privileged_sessions）"}

        if not force:
            eligible, reason = self._evaluate_promotion(mem, corroborating_insight_id)
            if not eligible:
                scan = self.conflict_scan(mem["text"], mem["topic_key"], exclude_id=memory_id)
                if scan.get("duplicate"):
                    return {"ok": False, "state": mem["state"],
                            "error": "与已有 live 记忆重复，禁止晋升", "conflict_scan": scan}
                if scan.get("conflict"):
                    self.store.set_agent_memory_state(memory_id, "pending_review", mirror_dirty=1)
                    self._upsert_mirror(self.store.get_agent_memory(memory_id))
                    return {"ok": False, "state": "pending_review",
                            "error": "检测到同 topic_key 冲突，已挂起待审", "conflict_scan": scan}
                return {"ok": False, "state": mem["state"], "error": f"未满足晋升条件：{reason}"}

        self.store.set_agent_memory_state(memory_id, "live", mirror_dirty=0)
        self._upsert_mirror(self.store.get_agent_memory(memory_id))
        self.store.record_agent_feedback(memory_id, True)  # 晋升成功 → 信任 +
        return {"ok": True, "state": "live", "memory_id": memory_id,
                "message": "已晋升为 live，参与检索"}

    def revoke_memory(self, memory_id: str) -> dict:
        mem = self.store.get_agent_memory(memory_id)
        if mem is None:
            return {"ok": False, "error": "memory_id 不存在"}
        self.store.set_agent_memory_state(memory_id, "revoked", mirror_dirty=0)
        self._upsert_mirror(self.store.get_agent_memory(memory_id))
        return {"ok": True, "state": "revoked", "memory_id": memory_id}

    def rollback_agent_memory(self, session_id: str) -> dict:
        mems = self.store.list_agent_memories()
        affected = 0
        for m in mems:
            if m["session_id"] == session_id and m["state"] != "revoked":
                self.store.set_agent_memory_state(m["memory_id"], "revoked", mirror_dirty=0)
                self._upsert_mirror(m)
                affected += 1
        return {"ok": True, "affected": affected, "session_id": session_id}

    def feedback_memory(self, memory_id: str, useful: bool) -> dict:
        res = self.store.record_agent_feedback(
            memory_id, useful, float(getattr(self.config, "agent_trust_step", 0.2))
        )
        if res is None:
            return {"ok": False, "error": "memory_id 不存在"}
        return {"ok": True, **res}

    def list_agent_memories(self, state: str = "all") -> dict:
        rows = self.store.list_agent_memories(state)
        return {
            "ok": True,
            "state": state,
            "count": len(rows),
            "memories": [
                {
                    "memory_id": r["memory_id"],
                    "session_id": r["session_id"],
                    "state": r["state"],
                    "trust": r["trust"],
                    "topic_key": r["topic_key"],
                    "tags": json.loads(r["tags_json"] or "[]"),
                    "source_refs": json.loads(r["source_refs_json"] or "[]"),
                    "feedback_up": r["feedback_up"],
                    "feedback_down": r["feedback_down"],
                    "expires_at": r["expires_at"],
                    "mirror_dirty": r["mirror_dirty"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }

    def get_session_trust(self, session_id: str) -> dict:
        return {
            "ok": True,
            **self.store.get_session_agent_trust(
                session_id, float(getattr(self.config, "trust_strict_threshold", -0.3))
            ),
        }

    # ── 检索（v2 #2 re-rank）─────────────────────────────────────────────
    def retrieve(self, question: str, trust_min: Optional[float] = None, top_k: int = 5) -> List[dict]:
        col = self._col
        if col is None:
            return []
        k = max(1, min(int(getattr(self.config, "agent_retrieve_k", 20)), 50))
        where = {"state": "live"}
        if trust_min is not None:
            where["trust"] = {"$gte": trust_min}
        try:
            res = col.query(query_texts=[question], where=where, n_results=k)
        except Exception as exc:  # pragma: no cover
            print(f"[AgentMemory] 检索失败: {exc}")
            return []
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        scored = []
        for i, mid in enumerate(ids):
            meta = metas[i] or {}
            dist = dists[i] if i < len(dists) else 1.0
            sim = 1.0 / (1.0 + max(dist, 0.0))
            trust = float(meta.get("trust", 0.0))
            final = 0.7 * sim + 0.3 * ((trust + 1) / 2)
            scored.append({
                "memory_id": mid,
                "text": docs[i] if i < len(docs) else "",
                "similarity": round(sim, 3),
                "trust": round(trust, 3),
                "final_score": round(final, 3),
                "topic_key": meta.get("topic_key", ""),
            })
        scored.sort(key=lambda x: -x["final_score"])
        return scored[:top_k]

    # ── 自动晋升 sweep + reconcile（v2 #4 / #9）──────────────────────────
    def sweep_promote_candidates(self) -> dict:
        self.store.expire_overdue_agent_memories()
        candidates = self.store.list_agent_memories("staging")
        promoted = 0
        scanned = 0
        for m in candidates:
            if m.get("auto_promote_blocked"):
                continue
            scanned += 1
            eligible, _ = self._evaluate_promotion(m)
            if not eligible:
                continue
            scan = self.conflict_scan(m["text"], m["topic_key"], exclude_id=m["memory_id"])
            if scan.get("duplicate") or scan.get("conflict"):
                continue
            self.store.set_agent_memory_state(m["memory_id"], "live", mirror_dirty=0)
            self._upsert_mirror(self.store.get_agent_memory(m["memory_id"]))
            promoted += 1
        return {"ok": True, "scanned": scanned, "promoted": promoted,
                "message": "自动晋升扫描完成"}

    def reconcile(self) -> dict:
        dirty = self.store.list_dirty_agent_mirrors()
        fixed = 0
        for m in dirty:
            self._upsert_mirror(m)
            fixed += 1
        return {"ok": True, "dirty": len(dirty), "fixed": fixed}

    def sweep_and_reconcile(self) -> dict:
        return {"sweep": self.sweep_promote_candidates(), "reconcile": self.reconcile()}

    # ── 健康 / 质量 ────────────────────────────────────────────────────────
    def health(self) -> dict:
        counts = {st: len(self.store.list_agent_memories(st)) for st in AGENT_STATES}
        return {
            "ok": True,
            "states": counts,
            "mirror_dirty": len(self.store.list_dirty_agent_mirrors()),
            "chroma_available": self._col is not None,
        }
