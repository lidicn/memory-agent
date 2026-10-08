"""生产库上的 handler 形状与 legacy/门面口径对账（只读，不打印任何凭据）。

在容器内以 /app/src（生产代码）+ /data/memory_agent.db（生产库）运行：

    ssh lidicn@192.168.2.200 'docker exec -w /app -e PYTHONPATH=/app/src \\
        memory-agent python /tmp/probe_insights_prod_shape.py'

只读：只用 query/统计接口，不写库、不打印实体内容以外的数据，载荷读数只到键名层级。
"""
from collections import Counter

from memory_agent.config import get_config
from memory_agent.insights import InsightService as Facade
from memory_agent.insights_legacy import InsightService as Legacy
from memory_agent.store import Store

cfg = get_config()
st = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
fac = Facade(st, cfg)
leg = Legacy(cfg, st)
try:
    n_ent = len(fac.resolver.all(False))
except Exception as exc:
    n_ent = f"<{type(exc).__name__}>"
print(f"db={cfg.db_path} rooms_config={len(cfg.rooms or {})} "
      f"entities_loaded={fac.entities_loaded} entities={n_ent}")


def brief(out, *listkeys):
    if not isinstance(out, dict):
        return f"<{type(out).__name__}>"
    n = None
    for k in listkeys:
        v = out.get(k)
        if isinstance(v, list):
            n = len(v)
            break
    return (f"ok={out.get('ok')} total={out.get('total')} count={out.get('count')} "
            f"n={n} err={str(out.get('error'))[:120]} keys={sorted(out)[:9]}")


HANDLER = [
    ("mcp:1116 get_entity_catalog", "entity_catalog",
     ("客厅", "", "", "", True, 7), {}),
    ("mcp:1137 get_behavior_insights", "behavior_insights", (7, "", True, "", ""), {}),
    ("mcp:1164 get_device_usage", "device_usage",
     ("", "客厅", "", "", 7, "", "", "", 5, True), {}),
    ("mcp:1334 search_events", "search_events", (), {
        "room": "客厅", "category": "", "domain": "", "query": "", "entity_id": "",
        "state": "", "days": 7, "start": "", "end": "", "limit": 200, "offset": 0,
        "order": "desc", "behavior_only": True, "summarize": False}),
    ("mcp:1446 get_device_health", "device_health", ("", "", "", 7, 3, True), {}),
    ("llm_routes:539 device_usage(eid)", "device_usage",
     ("light.livingroom_light", "", "", "", 7, "", ""), {}),
    ("arena:70 search_events(order/behavior_only)", "search_events", (), {
        "days": 7, "limit": 50, "order": "desc", "behavior_only": True}),
    ("researcher:115 behavior_insights(rooms)", "behavior_insights", (), {
        "days": 7, "rooms": "", "behavior_only": True}),
]

print("\n=== A. handler 现有形状打在生产门面上 ===")
for tag, method, a, kw in HANDLER:
    try:
        out = getattr(fac, method)(*a, **kw)
        print(f"[{tag}] {brief(out, 'entities', 'events', 'items', 'devices')}")
    except Exception as exc:
        print(f"[{tag}] 外抛 {type(exc).__name__}: {str(exc)[:120]}")

print("\n=== B. 同一生产库，门面按自家形状 vs legacy 按自家形状 ===")
PAIRS = [
    ("entity_catalog", dict(room="客厅"), dict(room="客厅", only_enabled=True)),
    ("search_events", dict(days=7, room="客厅", limit=50),
     dict(days=7, room="客厅", limit=50)),
    ("device_usage", dict(days=7, room="客厅"), dict(days=7, room="客厅")),
    ("behavior_insights", dict(days=7, room="客厅"), dict(days=7, rooms="客厅")),
    ("device_health", dict(days=7), dict(days=7)),
]
for method, fkw, lkw in PAIRS:
    try:
        fo = getattr(fac, method)(**fkw)
    except Exception as exc:
        fo = {"ok": False, "error": f"外抛 {type(exc).__name__}: {exc}"}
    try:
        lo = getattr(leg, method)(**lkw)
    except Exception as exc:
        lo = {"ok": False, "error": f"外抛 {type(exc).__name__}: {exc}"}
    print(f"[{method}] 门面 {brief(fo, 'entities', 'events', 'items', 'devices')}")
    print(f"{' ' * len(method)}  legacy {brief(lo, 'entities', 'events', 'items', 'devices', 'healthy')}")
    if isinstance(fo, dict) and isinstance(lo, dict):
        only_f = sorted(set(fo) - set(lo))
        only_l = sorted(set(lo) - set(fo))
        print(f"        键差 门面独有={only_f} legacy独有={only_l}")

print("\n=== C. 门面对 days 的处理（同一 room，days=1 vs 30）===")
for d in (1, 30):
    fo = fac.search_events(days=d, limit=5)
    lo = leg.search_events(days=d, limit=5)
    print(f" days={d:>2} 门面 total={fo.get('total')} window={str(fo.get('time_range'))[:38]} | "
          f"legacy total={lo.get('total')} window={str(lo.get('window'))[:38]}")

print("\n=== D. 门面里被接收但未使用的过滤位 ===")
f_all = fac.search_events(days=7, limit=3)
f_room = fac.search_events(days=7, room="客厅", limit=3)
f_cat = fac.search_events(days=7, category="climate", limit=3)
f_q = fac.search_events(days=7, query="灯", limit=3)
print(f" 门面 total: 无过滤={f_all.get('total')} room=客厅={f_room.get('total')} "
      f"category=climate={f_cat.get('total')} query=灯={f_q.get('total')}")
l_all = leg.search_events(days=7, limit=3)
l_room = leg.search_events(days=7, room="客厅", limit=3)
l_cat = leg.search_events(days=7, category="climate", limit=3)
l_q = leg.search_events(days=7, query="灯", limit=3)
print(f" legacy total: 无过滤={l_all.get('total')} room=客厅={l_room.get('total')} "
      f"category=climate={l_cat.get('total')} query=灯={l_q.get('total')}")
st.close()
