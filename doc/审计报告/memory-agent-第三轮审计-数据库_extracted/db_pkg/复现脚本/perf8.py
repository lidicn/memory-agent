import os, sys, time, sqlite3
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/big.db"; s=Store(DB, tz_offset_hours=8.0)
print("="*78); print("FTS5 全文检索性能"); print("="*78)
c=sqlite3.connect(DB)
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"): print("   FTS 表:", r[0])
print("   agent_memories_fts 行数:", c.execute("SELECT COUNT(*) FROM agent_memories_fts").fetchone()[0] if c.execute("SELECT name FROM sqlite_master WHERE name='agent_memories_fts'").fetchone() else "N/A")
c.close()
try:
    for kw in ("书房","灯","爸爸"):
        t0=time.time(); r=s.search_events(kw, limit=20) if hasattr(s,'search_events') else None
        dt=(time.time()-t0)*1000
        flag="🔴" if dt>500 else ("🟠" if dt>100 else "  ")
        print(f"{flag} search_events('{kw}')  {dt:>8.0f} ms  命中 {len(r) if isinstance(r,list) else r}")
except Exception as e:
    print("   search_events 不可用:", type(e).__name__, str(e)[:100])
print()
print("="*78); print("WAL 文件状态（单连接长跑是否导致 WAL 膨胀）"); print("="*78)
for ext in ("","-wal","-shm"):
    p=DB+ext
    print(f"   {os.path.basename(p):<16} {os.path.getsize(p)/1024/1024:>8.2f} MB" if os.path.exists(p) else f"   {os.path.basename(p):<16} 不存在")
print()
print("   执行 20000 次写入后观察 WAL 增长 ...")
w0=os.path.getsize(DB+"-wal")/1024/1024 if os.path.exists(DB+"-wal") else 0
t0=time.time()
for i in range(20000):
    s.insert_events([{"entity_id":f"sensor.w{i%50}","ts":"2026-09-15T10:00:00","room":"书房","domain":"sensor","new_state":"on","old_state":"off"}])
dt=time.time()-t0
w1=os.path.getsize(DB+"-wal")/1024/1024 if os.path.exists(DB+"-wal") else 0
print(f"   写入 20000 条耗时 {dt:.1f}s （{20000/dt:.0f} 条/秒）")
print(f"   WAL: {w0:.2f} MB → {w1:.2f} MB")
print()
print("   ★ 每条 insert 独立事务（自动提交），无批量合并 → 写入吞吐受限")
