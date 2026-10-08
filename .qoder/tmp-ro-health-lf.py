#!/usr/bin/env python3
"""只读健康对账（生产库，mode=ro）：integrity + agent_memories 计数 + 镜像脏行数。

不写、不建表、不 VACUUM，只用 `file:...?mode=ro` 打开。打印全部是聚合数，不含实体 id。
"""
import sqlite3

DB = "/data/memory_agent.db"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
cur = con.cursor()
try:
    ic = cur.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"integrity_check={ic}")
    print(f"journal_mode={cur.execute('PRAGMA journal_mode').fetchone()[0]}")
    n = cur.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
    print(f"agent_memories={n}")
    for topic, sql in (
        ("state_live", "SELECT COUNT(*) FROM agent_memories WHERE state='live'"),
        ("mirror_dirty", "SELECT COUNT(*) FROM agent_memories WHERE mirror_dirty=1"),
        ("has_embedding_id", "SELECT COUNT(*) FROM agent_memories WHERE chroma_id IS NOT NULL AND chroma_id<>''"),
    ):
        try:
            print(f"{topic}={cur.execute(sql).fetchone()[0]}")
        except Exception as exc:
            print(f"{topic}=SCHEMA_DIFFERS {type(exc).__name__}: {str(exc)[:60]}")
    try:
        fb = cur.execute(
            "SELECT COUNT(*) FROM agent_memories WHERE feedback_question IS NOT NULL "
            "AND feedback_question<>''"
        ).fetchone()[0]
        print(f"feedback_question_rows={fb}")
    except Exception as exc:
        print(f"feedback_question_rows=SCHEMA_DIFFERS {type(exc).__name__}")
except Exception as exc:
    print(f"PROBE_FAIL {type(exc).__name__}: {str(exc)[:120]}")
finally:
    con.close()
print("RO_HEALTH_RC=0")
