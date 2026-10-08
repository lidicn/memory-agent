import os, sys, tempfile, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store

tmp=tempfile.mkdtemp(prefix="cc_"); db=os.path.join(tmp,"t.db")
s=Store(db, tz_offset_hours=0.0); s.init_schema()
print("=== 并发压测：模拟「后台采集写」+「WebUI 并发读」===")
print(f"   DB: {db}")
stop=False; errs=[]; wcount=[0]; rcount=[0]

def writer(n):
    i=0
    try:
        while not stop:
            s.insert_events([{"entity_id":f"sensor.c{i%20}","ts":"2026-09-15T10:00:00","room":"书房",
                              "domain":"sensor","new_state":"on","old_state":"off"}])
            i+=1; wcount[0]+=1
    except Exception as e:
        errs.append(("writer", type(e).__name__, str(e)[:120]))

def reader(n):
    try:
        while not stop:
            s.db_query("SELECT COUNT(*) AS c FROM events")
            rcount[0]+=1
    except Exception as e:
        errs.append(("reader", type(e).__name__, str(e)[:120]))

t0=time.time()
with ThreadPoolExecutor(max_workers=9) as ex:
    futs=[ex.submit(writer,i) for i in range(2)]+[ex.submit(reader,i) for i in range(7)]
    time.sleep(8); stop=True
    for f in futs:
        try: f.result(timeout=30)
        except Exception as e: errs.append(("join", type(e).__name__, str(e)[:120]))
dt=time.time()-t0
print(f"   持续 {dt:.1f}s | 写 {wcount[0]} 次 | 读 {rcount[0]} 次")
print(f"   异常数: {len(errs)}")
seen=set()
for who,et,msg in errs:
    k=(who,et)
    if k not in seen:
        seen.add(k); print(f"      [{who}] {et}: {msg}")
print(f"   最终入库行数: {s.db_query('SELECT COUNT(*) AS c FROM events')}")
print(f"   写入调用次数: {wcount[0]}  ← 与入库行数对比可判断丢写")
