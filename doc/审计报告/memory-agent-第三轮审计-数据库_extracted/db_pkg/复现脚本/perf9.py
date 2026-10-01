import os, sys, time, threading, sqlite3
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/big.db"; s=Store(DB, tz_offset_hours=8.0)
print("="*78)
print("稳定性硬伤验证：_lock 是 RLock，无超时、不可中断")
print("="*78)
# 模拟：某线程持锁后卡住（如采集大事务 / 外部 IO 阻塞在锁内）
holder_stop=[False]
def holder():
    with s._lock:
        time.sleep(6)      # 持锁 6 秒
        holder_stop[0]=True
threading.Thread(target=holder,daemon=True).start()
time.sleep(0.5)
res={}
def victim():
    t0=time.time()
    try:
        s.query_events(start="2026-09-15T00:00:00",end="2026-09-16T00:00:00",limit=10)
        res['ok']=(time.time()-t0)*1000
    except Exception as e:
        res['err']=f"{type(e).__name__}: {str(e)[:80]} @ {(time.time()-t0)*1000:.0f}ms"
tv=threading.Thread(target=victim,daemon=True); tv.start()
tv.join(timeout=15)
if tv.is_alive():
    print("   🔴 受害线程 15 秒后仍在等待 —— 无超时保护，永久挂起")
else:
    print("   受害线程结果:", res)
print()
print("   ★ RLock 无 timeout 参数；持锁线程若被阻塞，其余线程无限排队，")
print("     且不计入 sqlite3.connect(timeout=30.0) 的保护范围。")
print()
print("="*78)
print("二次确认：purge_old 后磁盘空间是否可回收")
print("="*78)
sz0=os.path.getsize(DB)/1024/1024
cnt=sqlite3.connect(DB).execute("SELECT COUNT(*) FROM events").fetchone()[0]
print(f"   当前 events 行数: {cnt}   DB 体积: {sz0:.1f} MB")
print("   （上一步 purge_old 删除 50 万行后体积 198 MB 未变 → 空间未释放给文件系统）")
print("   执行 VACUUM 对比 ...")
c=sqlite3.connect(DB); t0=time.time(); c.execute("VACUUM"); c.close()
sz1=os.path.getsize(DB)/1024/1024
print(f"   VACUUM 后: {sz1:.1f} MB  (耗时 {time.time()-t0:.1f}s)")
print(f"   → 可回收 {sz0-sz1:.1f} MB")
