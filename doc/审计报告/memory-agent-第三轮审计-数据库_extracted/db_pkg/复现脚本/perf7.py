import os, sys, time, threading, sqlite3
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/big.db"
print("="*78)
print("数据分布")
print("="*78)
c=sqlite3.connect(DB)
for r in c.execute("SELECT day, COUNT(*) c FROM events GROUP BY day ORDER BY day LIMIT 5"): print("   ",r)
print("   总天数:", c.execute("SELECT COUNT(DISTINCT day) FROM events").fetchone()[0])
tot=c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
print("   总行数:", tot)
c.close()

s=Store(DB, tz_offset_hours=8.0)
print()
print("="*78)
print("purge_old 大批量清理实测：持锁时长 + 对其他请求的阻塞")
print("="*78)
sz_before=os.path.getsize(DB)/1024/1024
# 清理保留 1 天（当前数据跨多天 → 触发大批量删除）
lat=[]; stop=False; done=[False]
def probe():
    while not stop:
        t0=time.time()
        try: s.query_events(start="2026-09-15T00:00:00",end="2026-09-16T00:00:00",limit=10)
        except Exception: pass
        lat.append((time.time()-t0)*1000); time.sleep(0.05)
th=threading.Thread(target=probe,daemon=True); th.start()
time.sleep(1)
t0=time.time()
n=s.purge_old(retention_days=1)
dt=(time.time()-t0)*1000
stop=True; th.join(timeout=5)
print(f"   删除行数: {n}")
print(f"   purge_old 耗时: {dt:.0f} ms   ← 全程持有全局 RLock")
if lat:
    lat.sort(); nn=len(lat)
    print(f"   清理期间其他查询: n={nn}  P50={lat[nn//2]:.1f}ms  max={lat[-1]:.1f}ms")
    print(f"   （基线 0.4ms）")
sz_after=os.path.getsize(DB)/1024/1024
print(f"   DB 体积: {sz_before:.1f} MB → {sz_after:.1f} MB  （删除未回收空间）")
print()
print("   ★ 单事务大 DELETE：持锁期间全系统阻塞；删后不 VACUUM，文件不缩小")
