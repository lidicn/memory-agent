import os, sys, time
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
DB="/tmp/big.db"; s=Store(DB, tz_offset_hours=8.0)

def t(label, fn, *a, **k):
    t0=time.time(); r=fn(*a,**k); dt=(time.time()-t0)*1000
    flag="🔴" if dt>500 else ("🟠" if dt>100 else "  ")
    extra=""
    if isinstance(r,dict): extra=f" total={r.get('total')}" if 'total' in r else f" keys={len(r)}"
    elif isinstance(r,list): extra=f" 行数={len(r)}"
    print(f"{flag} {label:<42} {dt:>9.0f} ms{extra}")
    return r

print("="*78)
print("真实 Store 方法耗时实测（MCP / API 热路径）")
print("="*78)
t("query_unified_events() 无过滤（默认）", s.query_unified_events)
t("query_unified_events(room=客厅)", s.query_unified_events, room="客厅")
t("query_unified_events(room=客厅,limit=500)", s.query_unified_events, room="客厅", limit=500)
t("entity_last_seen() 全量", s.entity_last_seen)
t("entity_last_seen(指定10个实体)", s.entity_last_seen, [f"light.书房_{i}" for i in range(10)])
try: t("entity_event_counts() 全区间", s.entity_event_counts)
except Exception as e: print(f"   entity_event_counts ERR {e}")
print()
print("="*78)
print("★ 关键：query_unified_events 内部执行 2 次全量扫描")
print("  1) SELECT COUNT(*) FROM unified_events   → 全表聚合")
print("  2) SELECT ... ORDER BY server_ts LIMIT   → 全表扫描+排序")
print("="*78)
