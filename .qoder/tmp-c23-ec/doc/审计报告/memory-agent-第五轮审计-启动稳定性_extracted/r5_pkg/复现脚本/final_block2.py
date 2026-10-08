import os, sys, time, asyncio
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/ma30.db"
s=Store(DB, tz_offset_hours=8.0); s.connect()   # check_same_thread=False
SLOW=("SELECT entity_id, MAX(room) AS room, MAX(domain) AS domain, "
      "MAX(ts) AS last_ts, COUNT(*) AS total FROM events "
      "GROUP BY entity_id ORDER BY entity_id LIMIT 5000")
def q(): return s.db_query(SLOW)
t0=time.time(); q(); base=(time.time()-t0)*1000
print(f"基线：entity_catalog 类查询 {base:.0f} ms（events 30 万行，30 天库）\n")

async def heartbeat(stop, ticks):
    while not stop[0]:
        ticks.append(time.time()); await asyncio.sleep(0.005)

async def run(mode):
    stop=[False]; ticks=[]
    hb=asyncio.create_task(heartbeat(stop,ticks))
    await asyncio.sleep(0.05)
    t0=time.time()
    if mode=="correct":
        await asyncio.to_thread(q)     # 正确：卸载线程池
    else:
        q()                            # MA 现状：async 内直接同步
    dt=(time.time()-t0)*1000
    stop[0]=True
    try: await hb
    except Exception: pass
    n=len(ticks)
    print(f"  {mode:<9} 查询 {dt:>6.0f} ms | 期间事件循环调度 {n:>4} 次  {'✓ 正常并发' if n>0 else '✗ 完全冻结'}")

print("="*74)
print("实测：同一 360ms 级 DB 查询，两种写法对并发能力的影响")
print("="*74)
asyncio.run(run("correct"))
asyncio.run(run("blocking"))
print()
print("  ★ blocking 期间，所有 WebUI / MCP / ACP / 采集请求全部停滞")
print("  ★ MA 有 32 处此类调用；vision/tv 路径已正确使用 to_thread → 是 DB 路径遗漏")
