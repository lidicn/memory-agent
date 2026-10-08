"""窗前只读点数：命中行的状态分布 + sanitizer 真会改的行数（不写一行）。"""
import sys, collections
for cand in ("/tmp/c18snap20261005a/src", "/tmp/c18snap20261005a/scripts"):
    sys.path.insert(0, cand)
from memory_agent.store import Store
import pii_backfill_r2 as r2

store = Store()
conn = store.connect()
names = r2.roster_names(conn)
hits = r2.find_hits(conn, names)
writes = r2.plan_writes(conn, hits, store)

by_state = collections.Counter(h["state"] for h in hits)
wr_state = collections.Counter()
hit_ids = {h["memory_id"] for h in hits}
w_ids = {w[0] for w in writes}
state_of = {h["memory_id"]: h["state"] for h in hits}
for i in w_ids:
    wr_state[state_of[i]] += 1
print("[R2-read] db=", store.db_path)
print("[R2-read] total_rows=", conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0])
print("[R2-read] hits_by_state=", dict(sorted(by_state.items())), "hits_total=", len(hits))
print("[R2-read] sanitizer 真会改=", len(writes), "行  按状态=", dict(sorted(wr_state.items())))
print("[R2-read] 命中但不改（已脱敏/无变化）=", len(hits) - len(writes))
print("[R2-read] 命中行 text 合计字符数=", sum(h["chars"] for h in hits),
      " 中位长度=", sorted(h["chars"] for h in hits)[len(hits)//2] if hits else 0)
print("[R2-read] 全表 state 分布=", dict(sorted(collections.Counter(
      r[0] for r in conn.execute("SELECT state FROM agent_memories")).items())))
print("[R2-read] mirror_dirty=1 行数=", conn.execute(
      "SELECT COUNT(*) FROM agent_memories WHERE mirror_dirty=1").fetchone()[0])
print("[R2-read] 未带 --apply，本次一条都没写。")
