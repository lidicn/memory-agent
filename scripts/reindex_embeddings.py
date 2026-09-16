#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切换 embedding 模型后，从 SQLite 权威数据全量重建三个 chroma 集合。

为何需要
--------
chroma 集合在创建时就把「嵌入函数/维度」写进集合元数据；换模型后维度会变，
直接往旧集合写会维度不匹配。本脚本：删除三集合 → 用新嵌入函数重建 →
从 SQLite 重刷（behavior_history 来自每日聚合；agent_memory 来自 agent_memories 表；
arena_titles 重建后由使用方按需回填）。

运行方式（容器内）：
    docker exec -i memory-agent python3 - < scripts/reindex_embeddings.py

注意：重刷期间建议暂停写入或接受短暂不一致；behavior_history / agent_memory
在两步之内即可补齐，arena_titles 在下次被使用时自动重建。
"""

from __future__ import annotations

import os
import sys

for cand in ("/app/src", "/app", "/app/memory_agent", os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

import chromadb  # noqa: E402

from memory_agent.config import get_config  # noqa: E402
from memory_agent.store import Store  # noqa: E402
from memory_agent.history import HistoryManager  # noqa: E402
from memory_agent.agent_memory import AgentMemoryService  # noqa: E402


def main() -> None:
    cfg = get_config()
    history = HistoryManager(cfg)
    ef = history._embedding_function()
    if ef is None or ef is False:
        print("[Reindex] 未配置 embedding 端点（使用 chroma 默认模型），无需重建。")
        return

    store = Store(cfg.db_path, cfg.tz_offset_hours)
    svc = AgentMemoryService(cfg, store, history)

    # 重置连接缓存，强制后续用新嵌入函数重建集合
    history.reset_chroma()
    client = chromadb.HttpClient(host=cfg.chroma_host, port=cfg.chroma_port)
    history._client = client
    history._chroma_tried = True

    names = [
        HistoryManager.COLLECTION_NAME,
        HistoryManager.AGENT_COLLECTION,
        "arena_titles",
    ]
    for name in names:
        try:
            client.delete_collection(name)
            print(f"[Reindex] 已删除旧集合: {name}")
        except Exception as exc:
            print(f"[Reindex] 删除 {name} 跳过（可能不存在）: {exc}")
        client.get_or_create_collection(name=name, embedding_function=ef)
        print(f"[Reindex] 已用新嵌入函数重建集合: {name}")

    # ── behavior_history：从每日聚合重刷 ──
    rows = store.connect().execute(
        "SELECT DISTINCT day FROM events WHERE day IS NOT NULL ORDER BY day"
    ).fetchall()
    days = [r[0] for r in rows]
    n = history.mirror_days(days)
    print(f"[Reindex] behavior_history 重刷 {n} 条日聚合摘要（覆盖 {len(days)} 天）")

    # ── agent_memory：从 agent_memories 表重刷（跳过已撤销）──
    mems = store.list_agent_memories(limit=1_000_000)
    repop = 0
    for m in mems:
        if m.get("state") == "revoked":
            continue
        try:
            svc._upsert_mirror(m)
            repop += 1
        except Exception as exc:
            print(f"[Reindex] 记忆 {m.get('memory_id')} 重刷失败: {exc}")
    print(f"[Reindex] agent_memory 重刷 {repop}/{len(mems)} 条")

    # ── arena_titles：留空，由使用方按需回填 ──
    print("[Reindex] arena_titles 已重建为空集合，将在下次使用时自动回填。")
    print("[OK] 向量集合重建完成。")


if __name__ == "__main__":
    main()
