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
import logging
from typing import List, Optional, Tuple

from .store import safe_json_loads

AGENT_COLLECTION = "agent_memory"
AGENT_STATES = ("staging", "live", "revoked", "pending_review")


def make_activity_id(activity: str, day: str, start_ts: str, end_ts: str, room: str) -> str:
    """推断活动稳定 id（供 source_refs / promote(a) 解析）。"""
    raw = f"{activity}|{day}|{start_ts}|{end_ts}|{room}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class AgentMemoryService:
    logger = logging.getLogger("memory_agent.agent_memory")

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
        # v0.5：source 标记记忆来源（ma=本服务原生 / butler=豆包管家生态），
        # 用于检索侧按来源过滤，隔离低置信 LLM 摘要对高置信行为事实的污染。
        return {
            "kind": "agent_memory",
            "state": mem["state"],
            "trust": float(mem.get("trust", 0.0)),
            "source": mem.get("source", "ma"),
            "expires_at": mem.get("expires_at", ""),
            "session_id": mem.get("session_id", ""),
            "memory_id": mem.get("memory_id", ""),
            "topic_key": mem.get("topic_key", ""),
            # WO-MA-016: member_id 写入镜像 metadata，使向量路 where 过滤生效
            "member_id": mem.get("member_id", ""),
        }

    def _upsert_mirror(self, mem: dict) -> None:
        """把一条记忆同步进 chroma 镜像；失败置 mirror_dirty（不抛，v2 #9）。"""
        mid = mem.get("memory_id")
        if not mid:
            # 防御：空 memory_id 无法 upsert 到 Chroma，跳过（不应发生，多为垃圾数据）
            self.logger.warning("镜像同步跳过：memory_id 为空 (state=%s, text=%.40s)",
                                mem.get("state"), mem.get("text", ""))
            return
        col = self._col
        if col is None:
            self.store.mark_mirror_dirty(mid, 1)
            return
        try:
            col.upsert(
                ids=[mem["memory_id"]],
                documents=[mem["text"]],
                metadatas=[self._to_metadata(mem)],
            )
            self.store.mark_mirror_dirty(mid, 0)
        except Exception as exc:  # pragma: no cover - 网络/序列化异常
            # WO-MA-016: 静默失败改可观测
            self.logger.warning("镜像同步失败 %s: %s", mid, exc)
            self.store.mark_mirror_dirty(mid, 1)

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
            # 生产实测：这行曾只打出 "HTTPStatusError.__init__() missing 2 required
            # keyword-only arguments"。2026-10-04 容器复现拿到全量 traceback：抛点是
            # chromadb 的兜底包装 `CollectionCommon.py:93`——`raise type(e)(msg)` 用单参重建
            # 异常，而 httpx≥0.27 的 `HTTPStatusError.__init__` 是关键字专用
            # `(message, *, request, response)`，于是真实状态码在重新抛出这一步被 TypeError 顶掉。
            # 根因不是 chromadb×httpx 的接口不兼容，而是**任何**多参构造的异常穿过这层包装都会被吞；
            # MA 侧已把嵌入端点失败换成单参可构造的 `history.EmbeddingEndpointError`（状态码进消息），
            # 这一层的可观测性补强保留：仍能打出类型与 cause，供未被 MA 覆盖的第三方异常定位。
            cause = exc.__cause__ or exc.__context__
            detail = f"{type(exc).__name__}: {exc}"
            if cause is not None and cause is not exc:
                detail += f" | cause={type(cause).__name__}: {cause}"
            print(f"[AgentMemory] conflict_scan 查询失败: {detail}")
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
        source: str = "ma",
        merge: bool = True,
        valid_from: str = "",
        observed_at: str = "",
        member_id: str = "",  # WO-MA-005: 成员归属
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
            # HI-38 修复：不整批拒写，过滤掉非法引用，只保留合法的
            valid_refs = [r for r in source_refs if r and r.strip() and r.strip() not in invalid]
            if not valid_refs:
                return {"ok": False, "error": "source_refs 全部非法（需 event:/insight:/activity: 前缀）",
                        "invalid_refs": invalid, "code": 422}
            import logging
            logging.getLogger(__name__).warning(f"source_refs 过滤掉 {len(invalid)} 条非法引用: {invalid}")
            source_refs = valid_refs

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

        # v0.8-2：默认走 mem0 式合并管线（相似→UPDATE / 矛盾且新更可信→INVALIDATE+ADD）
        if merge:
            return self.merge_semantic_memory(
                session_id=session_id, text=text, topic_key=tk, tags=tags,
                source_refs=source_refs, ttl_days=ttl_days, source=source,
                trust=trust_info.get("trust", 0.0),
                valid_from=valid_from, observed_at=observed_at,
                member_id=member_id,
                auto_promote_blocked=auto_block,  # P1-13: merge 路径同样携带 blocked 标志
            )

        mid = self.store.add_agent_memory(
            session_id=session_id,
            text=text,
            topic_key=tk,
            tags_json=json.dumps(tags, ensure_ascii=False),
            source_refs_json=json.dumps(source_refs, ensure_ascii=False),
            ttl_days=ttl_days,
            state="staging",
            auto_promote_blocked=auto_block,
            source=source,
            member_id=member_id,
        )
        self._upsert_mirror(self.store.get_agent_memory(mid))
        return {
            "ok": True,
            "memory_id": mid,
            "state": "staging",
            "source": source,
            "auto_promote_blocked": auto_block,
            "message": "已写入 staging；永不自动进 live，需 promote / sweep 晋升",
        }

    # ── mem0 式合并写入（v0.8-2：ADD / UPDATE / INVALIDATE 分支）─────────────
    def merge_semantic_memory(
        self,
        session_id: str,
        text: str,
        topic_key: str = "",
        tags: Optional[List[str]] = None,
        source_refs: Optional[List[str]] = None,
        ttl_days: Optional[int] = None,
        source: str = "ma",
        trust: float = 0.0,
        valid_from: str = "",
        observed_at: str = "",
        member_id: str = "",  # WO-MA-005: 成员归属
        auto_promote_blocked: int = 0,  # P1-13: merge 路径同样消费 blocked 标志
    ) -> dict:
        """v0.8-2 mem0 式记忆操作：写入前向量近邻召回同 topic 旧记忆，按相似度分支。

        分支（dup_sim 默认 0.92，conflict_sim 默认 0.85）：
          sim < dup_sim                  → ADD（新 staging）
          sim ≥ dup_sim 且兼容/补充      → UPDATE 旧（保留演变链 prev_id）
          矛盾 & 旧是 live               → 新进 pending_review（**不自动 INVALIDATE live**，安全闸）
          矛盾 & 旧非 live（staging 等）  → INVALIDATE 旧 + ADD 新（prev_id=旧，新更可信）

        安全闸：live 是高置信已晋升事实，绝不自动撤；与之矛盾只挂起待人工裁决。
        chroma 不可用时退化为纯 ADD（不合并），与 add_semantic_memory 降级一致。
        """
        tags = tags or []
        source_refs = source_refs or []
        ttl_days = ttl_days or int(getattr(self.config, "agent_default_ttl_days", 30))
        if not text or not text.strip():
            return {"ok": False, "error": "text 不能为空", "code": 400}
        if not source_refs:
            return {"ok": False, "error": "source_refs 不能为空（参与式写回要求可溯源）", "code": 422}
        ok, invalid = self._validate_source_refs(source_refs)
        if not ok:
            return {"ok": False, "error": "source_refs 含非法引用",
                    "invalid_refs": invalid, "code": 422}

        tk = topic_key or (tags[0] if tags else "general")
        tags_json = json.dumps(tags, ensure_ascii=False)
        refs_json = json.dumps(source_refs, ensure_ascii=False)
        col = self._col
        if col is None:
            # chroma 不可用 → 退化纯 ADD，mirror 稍后由 reconcile 补
            mid = self.store.add_agent_memory(
                session_id=session_id, text=text, topic_key=tk,
                tags_json=tags_json, source_refs_json=refs_json,
                ttl_days=ttl_days, state="staging", source=source,
                member_id=member_id, auto_promote_blocked=auto_promote_blocked,
            )
            return {"ok": True, "action": "added", "memory_id": mid, "state": "staging",
                    "similarity": 0.0, "source": source, "note": "chroma 不可用，未合并",
                    "auto_promote_blocked": auto_promote_blocked}

        # 近邻召回：有 topic_key 则同 topic 过滤（更准），否则全局最近
        where = {"topic_key": tk} if tk != "general" else {}
        try:
            res = col.query(query_texts=[text], where=where, n_results=1)
        except Exception as exc:  # pragma: no cover
            print(f"[AgentMemory] merge 召回失败: {exc}")
            res = None
        nearest_id, nearest_sim, nearest_state = "", 0.0, ""
        if res:
            ids = (res.get("ids") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            if ids:
                nearest_id = ids[0]
                d = dists[0] if dists else 1.0
                nearest_sim = 1.0 / (1.0 + max(float(d), 0.0))
                nearest_state = (metas[0] or {}).get("state", "")

        dup_sim = float(getattr(self.config, "agent_dup_sim", 0.92))
        conflict_sim = float(getattr(self.config, "agent_conflict_sim", 0.85))

        # 分支 1：无相似（低于矛盾阈值）→ ADD 新
        if not nearest_id or nearest_sim < conflict_sim:
            mid = self.store.add_agent_memory(
                session_id=session_id, text=text, topic_key=tk,
                tags_json=tags_json, source_refs_json=refs_json,
                ttl_days=ttl_days, state="staging", source=source,
                member_id=member_id, auto_promote_blocked=auto_promote_blocked,
            )
            self._upsert_mirror(self.store.get_agent_memory(mid))
            return {"ok": True, "action": "added", "memory_id": mid, "state": "staging",
                    "similarity": round(nearest_sim, 3), "source": source,
                    "auto_promote_blocked": auto_promote_blocked}

        # 分支 2：相似（≥ dup_sim）→ 兼容/补充 → UPDATE 旧（保留演变链）
        if nearest_state == "live":
            if nearest_sim >= dup_sim:
                self.store.merge_update_agent_memory(
                    nearest_id, text, tags_json, refs_json,
                    prev_id=nearest_id, ttl_days=ttl_days)
                self._upsert_mirror(self.store.get_agent_memory(nearest_id))
                return {"ok": True, "action": "updated", "memory_id": nearest_id,
                        "state": "live", "similarity": round(nearest_sim, 3), "source": source}
            # 分支 3：与 live 矛盾 → 新进 pending_review（不自动 INVALIDATE live，安全闸）
            mid = self.store.add_agent_memory(
                session_id=session_id, text=text, topic_key=tk,
                tags_json=tags_json, source_refs_json=refs_json,
                ttl_days=ttl_days, state="pending_review", source=source,
                member_id=member_id, auto_promote_blocked=auto_promote_blocked)
            self._upsert_mirror(self.store.get_agent_memory(mid))
            return {"ok": True, "action": "conflict_pending", "memory_id": mid,
                    "state": "pending_review", "conflict_with": nearest_id,
                    "similarity": round(nearest_sim, 3), "source": source}

        # 分支 4：旧是 staging/pending_review（未进 live）
        if nearest_sim >= dup_sim:
            self.store.merge_update_agent_memory(
                nearest_id, text, tags_json, refs_json,
                prev_id=nearest_id, ttl_days=ttl_days)
            self._upsert_mirror(self.store.get_agent_memory(nearest_id))
            return {"ok": True, "action": "updated", "memory_id": nearest_id,
                    "state": nearest_state, "similarity": round(nearest_sim, 3), "source": source}
        # 矛盾且新更可信（时间更新）→ INVALIDATE 旧 + ADD 新（prev_id=旧）
        # v0.9 时间有效性：旧记忆 valid_to = 新记忆 valid_from（时间切片，演变可回溯）
        from .store import now_local as _now_local
        now_iso = _now_local(self.config.tz_offset_hours).isoformat(sep="T")
        new_valid_from = valid_from or observed_at or now_iso
        self.revoke_memory(nearest_id)
        self.store.close_agent_memory_validity(nearest_id, new_valid_from)
        mid = self.store.add_agent_memory(
            session_id=session_id, text=text, topic_key=tk,
            tags_json=tags_json, source_refs_json=refs_json,
            ttl_days=ttl_days, state="staging", source=source, prev_id=nearest_id,
            valid_from=new_valid_from, observed_at=observed_at or new_valid_from,
            member_id=member_id, auto_promote_blocked=auto_promote_blocked)  # P1-13: 分支4同样携带 blocked 标志
        self._upsert_mirror(self.store.get_agent_memory(mid))
        return {"ok": True, "action": "invalidated_added", "memory_id": mid,
                "state": "staging", "invalidated": nearest_id,
                "valid_from": new_valid_from, "valid_to": "",
                "similarity": round(nearest_sim, 3), "source": source}

    # ── 晋升条件评估（v2 #1 / #6；修复：前缀归一化 + 误导性报错）────────────
    def _evaluate_promotion(self, mem: dict, corroborating_insight_id: str = "") -> Tuple[bool, str]:
        src_refs = safe_json_loads(mem.get("source_refs_json"), [])
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
        # (c) researcher 自动分析产出的记忆，source_refs 含 insight:researcher:，
        #     本身就是 LLM 多轮分析的结论，直接视为已佐证。
        if any(r.startswith("insight:researcher:") for r in src_refs):
            return True, "researcher_generated"
        return False, f"(a) 需提供高置信佐证 insight_id；(b) 或跨 ≥{min_days} 天观测；(c) researcher 产出"

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

        # 安全闸门（WO-MA-002 / P0-5 修复）：force=True 仅限 human_override。
        # human_override 仅由 Web 端人工复核触发(已通过 require_user)，硬编码为 True。
        # 已移除 privileged_sessions 机制：session_id 由调用方自报，填特权 id 即绕过护栏，
        # 属于「身份由调用方自报」根因。MCP/API 调用方不能 force，只能走正常晋升条件。
        if force and not human_override:
            return {"ok": False, "error": "force=True 仅限 Web 端人工复核（human_override），MCP/API 调用方不可 force"}

        if not force:
            # 护栏永远优先于"条件达标"：重复 / 矛盾无论是否满足晋升条件都不应进 live。
            # 旧逻辑把 conflict_scan 放进 `if not eligible` 分支，导致满足跨日条件的记忆
            # 直接跳过护栏被晋升（去重/矛盾失效）。
            scan = self.conflict_scan(mem["text"], mem["topic_key"], exclude_id=memory_id)
            if scan.get("duplicate"):
                return {"ok": False, "state": mem["state"],
                        "error": "与已有 live 记忆重复，禁止晋升", "conflict_scan": scan}
            if scan.get("conflict"):
                self.store.set_agent_memory_state(memory_id, "pending_review", mirror_dirty=1)
                self._upsert_mirror(self.store.get_agent_memory(memory_id))
                return {"ok": False, "state": "pending_review",
                        "error": "检测到同 topic_key 冲突，已挂起待审", "conflict_scan": scan}
            eligible, reason = self._evaluate_promotion(mem, corroborating_insight_id)
            if not eligible:
                return {"ok": False, "state": mem["state"], "error": f"未满足晋升条件：{reason}"}

        # P1-20: human_override 强制晋升时记录审计（谁在什么时候绕过信任度模型晋升了哪条记忆）
        if human_override:
            try:
                self.store.log_mcp_audit(
                    token_name="human_override",
                    tool="promote_memory",
                    scope="write",
                    duration_ms=0.0,
                    ok=True,
                    error="",
                    origin=f"force_promote:{memory_id}",
                )
            except Exception:  # noqa: BLE001 —— 审计失败不影响主流程
                pass
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
                # 必须用更新后的记录 re-fetch 再镜像，否则 chroma 里仍是旧状态，
                # 导致已撤销记忆继续出现在 live 检索 / 冲突检测中（漏写镜像）。
                self._upsert_mirror(self.store.get_agent_memory(m["memory_id"]))
                affected += 1
        return {"ok": True, "affected": affected, "session_id": session_id}

    def feedback_memory(self, memory_id: str, useful: bool, comment: str = "",
                        question: str = "") -> dict:
        res = self.store.record_agent_feedback(
            memory_id, useful, float(getattr(self.config, "agent_trust_step", 0.2)),
            comment=comment, question=question,
        )
        if res is None:
            return {"ok": False, "error": "memory_id 不存在"}
        return {"ok": True, **res}

    def list_negative_feedback(self, limit: int = 50) -> dict:
        """vMA-2.0 门取料：带问题文本的 👎 明细（已入库脱敏，姓名→成员N）。"""
        rows = self.store.list_negative_feedback(limit)
        # 取料口交给 vMA-2.0 门的每一行都必须能还原成 badcase（有 id、有当初的问题文本）
        usable = all(str(r.get("memory_id") or "") and str(r.get("feedback_question") or "")
                     for r in rows)
        return {"ok": usable, "count": len(rows), "items": rows}

    def list_agent_memories(self, state: str = "all", source: str = "",
                             member_id: str = "") -> dict:
        # vMA-1.2.2: fail-closed —— 空 member_id 只返回公共记忆（member_id=''），
        # 不返回任何特定成员的记忆，与 retrieve 路口径一致
        rows = self.store.list_agent_memories(state, source, member_id=member_id or "",
                                              exact_member=True)
        return {
            "ok": True,
            "state": state,
            "source": source or "all",
            "member_id": member_id or "all",
            "count": len(rows),
            "memories": [
                {
                    "memory_id": r["memory_id"],
                    "session_id": r["session_id"],
                    "text": r["text"],
                    "state": r["state"],
                    "trust": r["trust"],
                    "source": r.get("source", "ma"),
                    "member_id": r.get("member_id", ""),
                    "topic_key": r["topic_key"],
                    "tags": safe_json_loads(r["tags_json"], []),
                    "source_refs": safe_json_loads(r["source_refs_json"], []),
                    "prev_id": r.get("prev_id", ""),
                    "valid_from": r.get("valid_from", ""),
                    "valid_to": r.get("valid_to", ""),
                    "observed_at": r.get("observed_at", ""),
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

    # ── 检索（v0.8-4 混合检索：向量 + FTS5 关键词融合重排）──────────────
    def retrieve(self, question: str, trust_min: Optional[float] = None, top_k: int = 5,
                 source: str = "", as_of: str = "", member_id: str = "") -> List[dict]:
        """WO-ADM-001 R-60：加 member_id 过滤，修复 butler→MA 成员归属丢失。"""
        col = self._col
        k = max(1, min(int(getattr(self.config, "agent_retrieve_k", 20)), 50))
        merged: dict = {}

        # 第一路：向量语义召回
        if col is not None:
            # WO-MA-016: 多键 where 必须用 $and（Chroma 要求 where 只能有一个 operator）
            where_conditions = [{"state": "live"}]
            if trust_min is not None:
                where_conditions.append({"trust": {"$gte": trust_min}})
            # v0.5：按来源过滤（如只召回本服务原生记忆，或只召回管家生态记忆以隔离低置信摘要）
            if source:
                where_conditions.append({"source": source})
            # vMA-1.2.2: member_id fail-closed —— 空 member_id 只召回公共记忆，不返回任何特定成员的记忆
            if member_id:
                where_conditions.append({"member_id": member_id})
            else:
                where_conditions.append({"member_id": ""})
            where = {"$and": where_conditions} if len(where_conditions) > 1 else where_conditions[0]
            try:
                res = col.query(query_texts=[question], where=where, n_results=k)
                ids = (res.get("ids") or [[]])[0]
                dists = (res.get("distances") or [[]])[0]
                docs = (res.get("documents") or [[]])[0]
                metas = (res.get("metadatas") or [[]])[0]
                for i, mid in enumerate(ids):
                    meta = metas[i] or {}
                    dist = dists[i] if i < len(dists) else 1.0
                    sim = 1.0 / (1.0 + max(dist, 0.0))
                    merged[mid] = {
                        "memory_id": mid,
                        "text": docs[i] if i < len(docs) else "",
                        "similarity": sim,
                        "trust": float(meta.get("trust", 0.0)),
                        "source": meta.get("source", "ma"),
                        "member_id": meta.get("member_id", ""),
                        "topic_key": meta.get("topic_key", ""),
                        "fts": 0.0,
                    }
            except Exception as exc:  # pragma: no cover
                # WO-MA-016: 静默失败改可观测
                self.logger.warning("向量检索失败: %s", exc)

        # 第二路：FTS5 关键词召回（专名/设备名/房间名，向量语义易漏）
        try:
            fts_rows = self.store.search_agent_memories_fts(question, limit=k, state="live")
        except Exception:
            fts_rows = []
        for r in fts_rows:
            mid = r["memory_id"]
            if source and (r.get("source") or "ma") != source:
                continue
            if trust_min is not None and float(r.get("trust", 0.0)) < trust_min:
                continue
            # WO-ADM-001 R-60：FTS 路按成员归属过滤（SQL 层不支持，结果层过滤）
            # vMA-1.2.2: member_id fail-closed —— 空 member_id 只保留公共记忆
            if member_id:
                if (r.get("member_id") or "") != member_id:
                    continue
            else:
                if (r.get("member_id") or "") != "":
                    continue
            if mid in merged:
                merged[mid]["fts"] = 1.0
            else:
                merged[mid] = {
                    "memory_id": mid,
                    "text": r.get("text", ""),
                    "similarity": 0.0,
                    "trust": float(r.get("trust", 0.0)),
                    "source": r.get("source", "ma"),
                    "member_id": r.get("member_id", ""),
                    "topic_key": r.get("topic_key", ""),
                    "fts": 1.0,
                }

        # 融合重排：语义为主 + 关键词增强 + 信任微调
        scored = []
        for m in merged.values():
            trust = float(m.get("trust", 0.0))
            sim = float(m.get("similarity", 0.0))
            fts = float(m.get("fts", 0.0))
            final = 0.6 * sim + 0.25 * fts + 0.15 * ((trust + 1) / 2)
            scored.append({
                "memory_id": m["memory_id"],
                "text": m["text"],
                "similarity": round(sim, 3),
                "trust": round(trust, 3),
                "source": m["source"],
                "member_id": m.get("member_id", ""),
                "topic_key": m["topic_key"],
                "fts_hit": bool(fts),
                "final_score": round(final, 3),
                "schema": "ma-recall/1",  # vMA-1.2.2: 召回返回加 schema 版本字段
            })
        # v0.9 时间有效性：as_of 给定时按 valid_from/valid_to 过滤（该时刻是否成立）
        if as_of:
            kept = []
            for m in scored:
                rec = self.store.get_agent_memory(m["memory_id"]) or {}
                vf = rec.get("valid_from") or ""
                vt = rec.get("valid_to") or ""
                m["valid_from"], m["valid_to"] = vf, vt
                if (not vf or vf <= as_of) and (not vt or as_of <= vt):
                    kept.append(m)
            scored = kept
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
