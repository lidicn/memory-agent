#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切换 embedding 模型后，从 SQLite 权威数据全量重建三个 chroma 集合。

P2-2 修复：不再先删后建（中途失败向量库被清空且不回滚），改为先建 *_new → 重刷 → 成功后原子切换。
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
        print("[Reindex] 未配置 embedding 端点，无需重建。")
        return

    store = Store(cfg.db_path, cfg.tz_offset_hours)
    svc = AgentMemoryService(cfg, store, history)

    history.reset_chroma()
    client = chromadb.HttpClient(host=cfg.chroma_host, port=cfg.chroma_port)
    history._client = client
    history._chroma_tried = True

    names = [
        HistoryManager.COLLECTION_NAME,
        HistoryManager.AGENT_COLLECTION,
        "arena_titles",
    ]
    errors = []

    # 第一步：建 *_new 集合（不碰旧库）
    for name in names:
        new_name = f"{name}_new"
        try:
            client.get_or_create_collection(name=new_name, embedding_function=ef)
            print(f"[Reindex] 已创建新集合: {new_name}")
        except Exception as exc:
            errors.append(f"创建 {new_name} 失败: {exc}")
            print(f"[Reindex] 创建 {new_name} 失败: {exc}")

    if errors:
        print(f"[FAIL] 共 {len(errors)} 个错误，保留旧库，未做切换")
        sys.exit(1)

    # behavior_history：重刷到 new
    rows = store.connect().execute(
        "SELECT DISTINCT day FROM events WHERE day IS NOT NULL ORDER BY day"
    ).fetchall()
    days = [r[0] for r in rows]
    old_collection = HistoryManager.COLLECTION_NAME
    HistoryManager.COLLECTION_NAME = f"{old_collection}_new"
    try:
        n = history.mirror_days(days)
        print(f"[Reindex] behavior_history_new 重刷 {n} 条（覆盖 {len(days)} 天）")
    except Exception as exc:
        errors.append(f"behavior_history 重刷失败: {exc}")
        print(f"[Reindex] behavior_history 重刷失败: {exc}")
    finally:
        HistoryManager.COLLECTION_NAME = old_collection

    # agent_memory：重刷到 new（跳过已撤销）
    mems = store.list_agent_memories(limit=1_000_000)
    repop = 0
    old_agent_collection = HistoryManager.AGENT_COLLECTION
    HistoryManager.AGENT_COLLECTION = f"{old_agent_collection}_new"
    try:
        for m in mems:
            if m.get("state") == "revoked":
                continue
            try:
                svc._upsert_mirror(m)
                repop += 1
            except Exception as exc:
                errors.append(f"记忆 {m.get('memory_id')} 重刷失败: {exc}")
                print(f"[Reindex] 记忆 {m.get('memory_id')} 重刷失败: {exc}")
        print(f"[Reindex] agent_memory_new 重刷 {repop}/{len(mems)} 条")
    finally:
        HistoryManager.AGENT_COLLECTION = old_agent_collection

    if errors:
        print(f"[FAIL] 共 {len(errors)} 个错误，保留旧库，未做切换")
        sys.exit(1)

    # 第二步：原子切换（删旧 + rename new→旧）
    for name in names:
        new_name = f"{name}_new"
        try:
            client.delete_collection(name)
            print(f"[Reindex] 已删除旧集合: {name}")
        except Exception as exc:
            print(f"[Reindex] 删除 {name} 跳过（可能不存在）: {exc}")
        try:
            client.rename_collection(new_name, name)
            print(f"[Reindex] 已切换: {new_name} -> {name}")
        except Exception as exc:
            errors.append(f"重命名 {new_name} 失败: {exc}")
            print(f"[Reindex] 重命名 {new_name} 失败: {exc}")

    if errors:
        print(f"[FAIL] 切换后发现 {len(errors)} 个错误")
        sys.exit(1)

    print("[Reindex] arena_titles 已重建为空集合，将在下次使用时自动回填。")
    print("[OK] 向量集合重建完成。")


if __name__ == "__main__":
    main()
