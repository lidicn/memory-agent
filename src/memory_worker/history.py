#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史行为门面层

重构说明
--------
原实现把每一条事件单独 ``collection.add()`` 进 Chroma，一次调用 =
一次 HTTP 请求 + 一次本地 ONNX 向量计算。上千实体回填几天数据会产生
数万条事件，耗时以小时计 —— 这是「采集不可用」的深层原因之一。

现在：
* **写**：事件批量落 SQLite（真正的主存储），Chroma 仅按「天 × 房间」
  镜像少量聚合摘要文档，供语义检索，且失败不影响采集主流程。
* **读**：区间查询、行为摘要、统计全部走 SQLite 索引。

对外方法签名保持不变（MCP 工具与 API 均依赖），返回结构亦不变。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .store import TELEMETRY_DOMAINS, Store, make_event_id, now_local, parse_ts


class HistoryManager:
    """行为历史门面：SQLite 主存储 + Chroma 语义索引。"""

    COLLECTION_NAME = "behavior_history"
    AGENT_COLLECTION = "agent_memory"  # Agent 参与式写回记忆（命名空间隔离）

    def __init__(self, config, store: Optional[Store] = None):
        self.config = config
        self.store = store or Store(config.db_path, config.tz_offset_hours)
        self._client = None
        self._collection = None
        self._chroma_error: str = ""
        self._chroma_tried = False

    # ── Chroma（可选） ───────────────────────────────────────────────────

    @property
    def collection(self):
        """惰性连接向量库。不可用时返回 None，绝不让采集或启动失败。"""
        if self._collection is not None:
            return self._collection
        if self._chroma_tried and self._chroma_error:
            return None
        self._chroma_tried = True
        try:
            import chromadb

            self._client = chromadb.HttpClient(
                host=self.config.chroma_host, port=self.config.chroma_port
            )
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME, metadata={"description": "家庭行为历史摘要"}
            )
            self._chroma_error = ""
            print(f"[History] 向量库已连接: {self.COLLECTION_NAME}")
        except Exception as exc:
            self._chroma_error = str(exc)
            self._collection = None
            print(f"[History] 向量库不可用（不影响采集）: {exc}")
        return self._collection

    @property
    def agent_collection(self):
        """Agent 记忆专用集合（命名空间隔离）。不可用时返回 None，绝不抛错。

        复用 ``collection`` 已建立的 chroma client；若 chroma 整体不可用，
        ``self._client`` 为 None，这里同样返回 None。
        """
        if self._collection is None and not self._chroma_tried:
            _ = self.collection  # 触发一次连接，建立 self._client
        if self._client is None:
            return None
        try:
            return self._client.get_or_create_collection(
                name=self.AGENT_COLLECTION,
                metadata={"description": "Agent 参与式写回记忆"},
            )
        except Exception as exc:
            print(f"[History] agent_memory 集合不可用: {exc}")
            return None

    def chroma_status(self) -> Dict[str, Any]:
        col = self.collection
        if col is None:
            return {
                "connected": False,
                "error": self._chroma_error or "未连接",
                "host": f"{self.config.chroma_host}:{self.config.chroma_port}",
            }
        try:
            return {
                "connected": True,
                "host": f"{self.config.chroma_host}:{self.config.chroma_port}",
                "documents": col.count(),
            }
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    def chroma_selftest(self, host=None, port=None) -> Dict[str, Any]:
        """向量库端到端自检：写入探针 → 语义检索 → 清理。

        只查 ``count()`` 说明不了向量库「真的在工作」—— 连接得上但写不进、
        或写得进但检索不出，都是常见故障。这里跑一次完整往返，
        把每一步的耗时和结果都摊开给用户看。

        ``host`` / ``port`` 可选：不传则用运行时配置；传入则用前端表单里的
        当前值（还没保存也能先测）。无论是否传入，都使用**临时 client**，
        绝不会被运行时惰性连接的失败缓存（``_chroma_error``）卡死——
        这正是之前「测试按钮没用」的根因：首次连失败被记住后，``self.collection``
        永远返回 None，``chroma_selftest`` 也永远走 ``col is None`` 分支直接失败。
        """
        import chromadb
        import time
        import uuid

        host = host or self.config.chroma_host
        port = port or self.config.chroma_port
        steps: List[Dict[str, Any]] = []

        def step(name: str, fn):
            begin = time.perf_counter()
            try:
                detail = fn()
                steps.append(
                    {
                        "step": name,
                        "ok": True,
                        "ms": round((time.perf_counter() - begin) * 1000, 1),
                        "detail": detail or "",
                    }
                )
                return True
            except Exception as exc:
                steps.append(
                    {
                        "step": name,
                        "ok": False,
                        "ms": round((time.perf_counter() - begin) * 1000, 1),
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                return False

        # 临时 client：隔离运行时惰性连接的失败缓存，确保测试永远用最新参数重试
        try:
            client = chromadb.HttpClient(host=host, port=port)
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "host": f"{host}:{port}",
                "error": str(exc),
                "steps": [
                    {
                        "step": "连接向量库",
                        "ok": False,
                        "ms": 0,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                ],
                "hint": "确认 chroma 容器已启动，且 chroma_host / chroma_port 配置正确",
            }

        step("连接向量库", lambda: f"{host}:{port}")

        try:
            col = client.get_or_create_collection(
                name=self.COLLECTION_NAME, metadata={"description": "家庭行为历史摘要"}
            )
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "host": f"{host}:{port}",
                "error": str(exc),
                "steps": steps,
                "hint": "连接成功但无法获取集合，可能是 chroma 版本不兼容或服务异常",
            }

        probe_id = f"__selftest__{uuid.uuid4().hex[:12]}"
        marker = uuid.uuid4().hex[:8]
        probe_text = f"忆家管家向量库自检探针 {marker}：书房 空调 开启"
        before = 0
        hit_id = ""

        def _count():
            nonlocal before
            before = col.count()
            return f"当前文档数 {before}"

        ok_all = step("读取文档数", _count)
        ok_all &= step(
            "写入探针文档",
            lambda: col.add(
                ids=[probe_id],
                documents=[probe_text],
                metadatas=[{"selftest": True, "marker": marker}],
            )
            or f"id={probe_id}",
        )

        def _query():
            nonlocal hit_id
            res = col.query(query_texts=["书房空调自检探针"], n_results=3)
            ids = (res.get("ids") or [[]])[0]
            hit_id = probe_id if probe_id in ids else (ids[0] if ids else "")
            if probe_id not in ids:
                raise RuntimeError(f"检索未命中刚写入的探针，返回 ids={ids}")
            return f"命中 {len(ids)} 条，探针排名第 {ids.index(probe_id) + 1}"

        ok_all &= step("语义检索探针", _query)
        step("清理探针", lambda: col.delete(ids=[probe_id]) or "已删除")

        return {
            "ok": ok_all,
            "connected": True,
            "host": f"{host}:{port}",
            "collection": self.COLLECTION_NAME,
            "documents": before,
            "mirror_enabled": bool(getattr(self.config, "chroma_mirror", True)),
            "steps": steps,
            "summary": (
                f"读写检索全部通过，集合 {self.COLLECTION_NAME} 现有 {before} 条文档"
                if ok_all
                else "存在失败步骤，请看 steps 明细"
            ),
        }

    def reset_chroma(self) -> None:
        self._client = None
        self._collection = None
        self._chroma_tried = False
        self._chroma_error = ""

    # ── 写入 ─────────────────────────────────────────────────────────────

    def add_events(self, events: List[Dict[str, Any]]) -> int:
        """批量写入事件（采集主路径）。返回写入条数。"""
        if not events:
            return 0
        written = self.store.insert_events(events)
        days = {e.get("day") or str(e.get("ts", ""))[:10] for e in events}
        self.store.recount_days([d for d in days if d])
        return written

    def add_event(self, event: Dict[str, Any]) -> str:
        """单条写入（兼容旧签名）。新代码请用 ``add_events``。"""
        normalized = self._normalize_legacy_event(event)
        self.add_events([normalized])
        return normalized["id"]

    def _normalize_legacy_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        raw_ts = event.get("timestamp") or event.get("ts")
        dt = parse_ts(raw_ts, self.config.tz_offset_hours) or now_local(
            self.config.tz_offset_hours
        )
        entity_id = event.get("entity_id") or event.get("device") or ""
        action = event.get("action", "")
        old_state, new_state = "", ""
        if "->" in str(action):
            old_state, _, new_state = str(action).partition("->")
        return {
            "id": make_event_id(entity_id, raw_ts or dt.isoformat()),
            "ts": dt.isoformat(sep="T"),
            "day": dt.strftime("%Y-%m-%d"),
            "room": event.get("room", ""),
            "entity_id": entity_id,
            "domain": entity_id.split(".")[0] if entity_id else "",
            "action": action,
            "person": event.get("person") or "",
            "old_state": event.get("old_state", old_state),
            "new_state": event.get("new_state", new_state),
            "attrs": event.get("params") or event.get("attrs") or {},
        }

    # ── Chroma 镜像 ──────────────────────────────────────────────────────

    def mirror_days(self, days: List[str]) -> int:
        """把指定日期的「天 × 房间」聚合摘要写入向量库。

        由采集任务在批次结束后调用（``asyncio.to_thread`` 包裹），
        失败仅记录日志，不影响已入库的事件数据。
        """
        col = self.collection
        if col is None or not days:
            return 0
        ids: List[str] = []
        docs: List[str] = []
        metas: List[Dict[str, Any]] = []
        for day in sorted(set(days)):
            start, end = f"{day}T00:00:00", f"{day}T23:59:59"
            tops = self.store.top_entities(start, end, limit=200)
            by_room: Dict[str, List[Dict[str, Any]]] = {}
            for item in tops:
                by_room.setdefault(item["room"] or "未分区", []).append(item)
            for room, items in by_room.items():
                total = sum(i["count"] for i in items)
                head = "、".join(f"{i['entity_id']}({i['count']}次)" for i in items[:12])
                ids.append(f"summary_{day}_{room}")
                docs.append(f"{day} {room} 共 {total} 次设备状态变化，主要活动：{head}")
                metas.append(
                    {"day": day, "room": room, "events": total, "kind": "daily_summary"}
                )
        if not ids:
            return 0
        try:
            col.upsert(ids=ids, documents=docs, metadatas=metas)
            return len(ids)
        except Exception as exc:
            print(f"[History] 向量库镜像失败（已忽略）: {exc}")
            return 0

    def semantic_search(
        self,
        query: str,
        n_results: int = 5,
        state: Optional[str] = None,
        kind: Optional[str] = None,
        trust_min: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        col = self.collection
        if col is None:
            return []
        where = None
        if state is not None or kind is not None or trust_min is not None:
            where = {}
            if state is not None:
                where["state"] = state
            if kind is not None:
                where["kind"] = kind
            if trust_min is not None:
                where["trust"] = {"$gte": trust_min}
        try:
            res = col.query(
                query_texts=[query],
                n_results=max(1, min(n_results, 20)),
                where=where,
            )
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            return [
                {"document": d, "metadata": m or {}} for d, m in zip(docs, metas)
            ]
        except Exception as exc:
            print(f"[History] 语义检索失败: {exc}")
            return []

    # ── 读取 ─────────────────────────────────────────────────────────────

    def _range(self, days: int) -> tuple[str, str]:
        end = now_local(self.config.tz_offset_hours)
        start = end - timedelta(days=max(1, days))
        return start.isoformat(sep="T"), end.isoformat(sep="T")

    @staticmethod
    def _to_legacy(row: Dict[str, Any]) -> Dict[str, Any]:
        import json as _json

        params: Any = {}
        if row.get("attrs_json"):
            try:
                params = _json.loads(row["attrs_json"])
            except (TypeError, ValueError):
                params = {}
        return {
            "timestamp": row.get("ts", ""),
            "person": row.get("person", ""),
            "room": row.get("room", ""),
            "device": row.get("entity_id", ""),
            "entity_id": row.get("entity_id", ""),
            "domain": row.get("domain", ""),
            "action": row.get("action", ""),
            "old_state": row.get("old_state", ""),
            "new_state": row.get("new_state", ""),
            "params": params,
        }

    def get_person_history(
        self, person: str, days: int = 7, limit: int = 500
    ) -> Dict[str, Any]:
        start, end = self._range(days)
        rows = self.store.query_events(
            start=start,
            end=end,
            person=person if person and person != "all" else None,
            limit=limit,
            order="desc",
        )
        known = self.get_all_persons()
        total = self.store.count_events(
            start, end, person=person if person and person != "all" else None
        )
        out = {
            "person": person,
            "period": f"最近{days}天",
            "range": {"start": start, "end": end},
            "total": total,
            "count": len(rows),
            "has_more": len(rows) < total,
            "total_events": len(rows),  # 兼容旧字段
            "known_persons": known,
            "events": [self._to_legacy(r) for r in rows],
        }
        if not known:
            out["notice"] = (
                "当前数据源未提供人员归属（HA 状态历史不含操作者），"
                "person 字段全为空。需要按人分析请改用 get_behavior_insights 按房间/设备维度。"
            )
        return out

    def get_all_persons(self) -> List[str]:
        return self.store.distinct_persons()

    def get_behavior_summary(
        self, days: int = 7, behavior_only: bool = True
    ) -> Dict[str, Any]:
        """行为总览。

        ``behavior_only=True``（默认）会剔除 ``sensor``/``number`` 等纯遥测域：
        功率、温湿度每分钟一条，会把小时分布拍成均匀的「电表节拍」，
        完全掩盖真实作息。需要看原始全量时显式传 ``False``。
        """
        start, end = self._range(days)
        excl = list(TELEMETRY_DOMAINS) if behavior_only else None
        total = self.store.count_events(start, end, exclude_domains=excl)
        total_raw = self.store.count_events(start, end)
        tops = self.store.top_entities(start, end, limit=20, exclude_domains=excl)
        hist = self.store.hour_histogram(start, end, exclude_domains=excl)
        by_room: Dict[str, int] = {}
        for room, buckets in hist.items():
            by_room[room] = sum(buckets)
        hourly = [0] * 24
        for buckets in hist.values():
            for i, v in enumerate(buckets):
                hourly[i] += v
        return {
            "period": f"最近{days}天",
            "days": days,
            "range": {"start": start, "end": end},
            "behavior_only": behavior_only,
            "total_events": total,
            "total_events_raw": total_raw,
            "telemetry_excluded": total_raw - total if behavior_only else 0,
            "rooms": by_room,
            "top_entities": tops,
            "hourly_distribution": hourly,
            "persons": self.get_all_persons(),
        }

    def export_history(
        self, days: int = 30, person: Optional[str] = None, limit: int = 5000
    ) -> Dict[str, Any]:
        start, end = self._range(days)
        rows = self.store.query_events(
            start=start,
            end=end,
            person=person if person and person != "all" else None,
            limit=limit,
        )
        return {
            "exported_at": now_local(self.config.tz_offset_hours).isoformat(),
            "period": f"最近{days}天",
            "person": person or "all",
            "total": len(rows),
            "events": [self._to_legacy(r) for r in rows],
        }

    def get_stats(self) -> Dict[str, Any]:
        stats = self.store.stats()
        stats["chroma"] = self.chroma_status()
        return stats

    def query_range(
        self,
        start: str,
        end: str,
        rooms: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        rows = self.store.query_events(
            start=start, end=end, rooms=rooms, entities=entities, limit=limit
        )
        return [self._to_legacy(r) for r in rows]
