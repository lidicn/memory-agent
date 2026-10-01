import os, sys, time, sqlite3, importlib
sys.path.insert(0,"/data/workspace/ma/src")
os.environ["JWT_SECRET"]="t"*40
os.environ["HASS_SERVER"]="http://127.0.0.1:18123"; os.environ["HASS_TOKEN"]="d"
os.environ["REDIS_HOST"]="127.0.0.1"; os.environ["CHROMA_HOST"]="127.0.0.1"
os.environ["LLM_API_URL"]="http://127.0.0.1:19999/v1"; os.environ["LLM_API_KEY"]="d"; os.environ["LLM_MODEL"]="t"

from memory_agent.identity import IdentityService, IdentityReconciler

def ha_getter(): return []   # 无 HA → 走本地对账逻辑

print("="*76)
print("identity_reconciler.reconcile 随数据量变化（启动期 await 阻塞项）")
print("="*76)
paths=[("/tmp/sc_0.db","空库"),("/tmp/sc_50000.db","5万"),("/tmp/sc_150000.db","15万"),("/tmp/sc_300000.db","30万"),("/tmp/ma30.db","30天真实")]
for path,label in paths:
    if not os.path.exists(path): print(f"   {label}: 缺文件"); continue
    n=sqlite3.connect(path).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    sz=os.path.getsize(path)/1024/1024
    os.environ["DB_PATH"]=path
    try:
        from memory_agent.store import Store
        from memory_agent.config import get_config
        # 每次重新加载 config 以应用新 DB_PATH
        import memory_agent.config as C
        importlib.reload(C)
        cfg=C.get_config()
        store=Store(cfg.db_path, cfg.tz_offset_hours)
        svc=IdentityService(cfg, store)
        rec=IdentityReconciler(svc, ha_getter, tz_offset_hours=8.0)
        t0=time.time()
        try:
            rec.reconcile(); dt=(time.time()-t0)*1000
            print(f"   {label:<10} events={n:<7} {sz:>6.1f}MB → reconcile {dt:>8.0f} ms")
        except Exception as e:
            dt=(time.time()-t0)*1000
            print(f"   {label:<10} events={n:<7} {sz:>6.1f}MB → 异常({dt:.0f}ms) {type(e).__name__}: {str(e)[:50]}")
    except Exception as e:
        print(f"   {label:<10} 构造失败 {type(e).__name__}: {str(e)[:60]}")
