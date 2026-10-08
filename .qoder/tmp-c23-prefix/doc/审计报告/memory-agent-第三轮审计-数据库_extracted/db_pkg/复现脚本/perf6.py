import os, sys, time, threading
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/big.db"
s=Store(DB, tz_offset_hours=8.0)   # ★ 生产真实形态：全局单例 Store
print("="*78)
print("同实例锁竞争实测（生产形态：rt.store 全局单例，所有组件共享一把 RLock）")
print("="*78)
print("   基线：无竞争时")
for lbl,fn in (("快查询 query_events",lambda: s.query_events(start="2026-09-15T00:00:00",end="2026-09-16T00:00:00",limit=50)),
               ("写入 insert_events",lambda: s.insert_events([{"entity_id":"sensor.p","ts":"2026-09-15T10:00:00","room":"书房","domain":"sensor","new_state":"on","old_state":"off"}]))):
    ts=[]
    for _ in range(5):
        t0=time.time(); fn(); ts.append((time.time()-t0)*1000)
    print(f"      {lbl:<24} {sum(ts)/len(ts):>7.1f} ms")
print()
lat=[]; qlat=[]; stop=False
def slow():
    while not stop: s.query_unified_events()      # 689ms 慢查询
def fast_q():
    while not stop:
        t0=time.time(); s.query_events(start="2026-09-15T00:00:00",end="2026-09-16T00:00:00",limit=50)
        qlat.append((time.time()-t0)*1000); time.sleep(0.01)
def writer():
    while not stop:
        t0=time.time()
        s.insert_events([{"entity_id":"sensor.p","ts":"2026-09-15T10:00:00","room":"书房","domain":"sensor","new_state":"on","old_state":"off"}])
        lat.append((time.time()-t0)*1000); time.sleep(0.01)
print("   启动 1 慢查询 + 1 快查询 + 1 写线程，持续 10 秒 ...")
for f in (slow, fast_q, writer):
    threading.Thread(target=f,daemon=True).start()
time.sleep(10); stop=True; time.sleep(0.5)
def stat(name,arr):
    if not arr: print(f"   {name}: 无样本"); return
    arr.sort(); n=len(arr)
    print(f"   {name:<10} n={n:<5} 平均{sum(arr)/n:>8.1f}ms  P50{arr[n//2]:>8.1f}ms  P95{arr[int(n*0.95)]:>8.1f}ms  max{arr[-1]:>8.1f}ms")
print()
stat("写请求",lat); stat("快查询",qlat)
print()
print("   timeout=30.0 为 SQLite 层锁等待上限；RLock 排队不计入该超时")
