import os, sys, time, threading
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/big.db"
# 两个 Store 实例共享同一 DB 文件（模拟：同一进程内多组件 + 多进程部署）
s1=Store(DB, tz_offset_hours=8.0)
s2=Store(DB, tz_offset_hours=8.0)

print("="*78)
print("级联阻塞实测：慢查询持有锁期间，其他请求的排队延迟")
print("="*78)
lat=[]; stop=False
def slow_loop():
    while not stop:
        s1.query_unified_events()   # 689ms 的慢查询，持 RLock
def writer():
    while not stop:
        t0=time.time()
        s2.insert_events([{"entity_id":"sensor.probe","ts":"2026-09-15T10:00:00","room":"书房","domain":"sensor","new_state":"on","old_state":"off"}])
        lat.append((time.time()-t0)*1000)
        time.sleep(0.02)
print("   基线：无慢查询时单次写入延迟")
for _ in range(5):
    t0=time.time(); s2.insert_events([{"entity_id":"sensor.probe","ts":"2026-09-15T10:00:00","room":"书房","domain":"sensor","new_state":"on","old_state":"off"}]); print(f"      {(time.time()-t0)*1000:.1f} ms")
print()
print("   启动 2 个慢查询线程 + 1 个写线程，持续 8 秒 ...")
ths=[threading.Thread(target=slow_loop,daemon=True) for _ in range(2)]
ths.append(threading.Thread(target=writer,daemon=True))
for t_ in ths: t_.start()
time.sleep(8); stop=True
for t_ in ths: t_.join(timeout=20)
if lat:
    lat.sort()
    n=len(lat)
    print(f"   写请求数 {n}")
    print(f"     平均 {sum(lat)/n:>8.1f} ms")
    print(f"     P50   {lat[n//2]:>8.1f} ms")
    print(f"     P95   {lat[int(n*0.95)]:>8.1f} ms")
    print(f"     P99   {lat[int(n*0.99)]:>8.1f} ms")
    print(f"     最大   {lat[-1]:>8.1f} ms")
print()
print("   ★ 单连接+RLock 架构下，慢查询的耗时会被完整叠加到排队请求上")
