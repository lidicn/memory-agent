"""只读探针：确认 2 字中文词检索为 0 是 trigram 的分词下限，不是索引坏。"""
import os, sqlite3, sys
sys.path.insert(0, "/app/src")
DB = "/data/memory_agent.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

print("-- config 表里记录的分词器 --")
for r in conn.execute("SELECT * FROM agent_memories_fts_config"):
    print("  ", r)

print("\n-- MATCH：词长按 2/3/4 递增 --")
for q in ("客厅", "客厅最", "客厅最活", "卧室多设", "冒烟自测"):
    try:
        n = [r[0] for r in conn.execute(
            "SELECT rowid FROM agent_memories_fts WHERE agent_memories_fts MATCH ? LIMIT 3", (q,))]
        print(f"   {q!r} (len={len(q)}) -> {n}")
    except Exception as exc:
        print(f"   {q!r} (len={len(q)}) -> 抛 {type(exc).__name__}: {exc}")

print("\n-- 带引号短语（包装层的真实形状）--")
for q in ('"客厅"', '"客厅最"', '"卧室多设"'):
    try:
        n = [r[0] for r in conn.execute(
            "SELECT rowid FROM agent_memories_fts WHERE agent_memories_fts MATCH ? LIMIT 3", (q,))]
        print(f"   {q!r} -> {n}")
    except Exception as exc:
        print(f"   {q!r} -> 抛 {type(exc).__name__}: {exc}")

print("\n-- trigram 的 LIKE 加速路径（2 字能否走通）--")
try:
    n = [r[0] for r in conn.execute(
        "SELECT rowid FROM agent_memories_fts WHERE text LIKE '%客厅%' LIMIT 3")]
    print(f"   LIKE '%客厅%' -> {n}")
except Exception as exc:
    print(f"   LIKE 抛 {type(exc).__name__}: {exc}")

print("\n-- 走生产包装层 search_agent_memories_fts --")
from memory_agent.store import Store
st = Store(DB, tz_offset_hours=8)
for q in ("客厅", "客厅最", "卧室", "冒烟自测记忆", "researcher"):
    for stt in ("staging", "live"):
        rows = st.search_agent_memories_fts(q, limit=3, state=stt)
        print(f"   search({q!r}, state={stt!r}) -> {len(rows)} 条")
conn.close()
