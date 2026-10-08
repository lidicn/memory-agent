import sys
sys.path.insert(0, "/app/src")
from memory_agent.store import Store

s = Store("/data/memory_agent.db")
eid = "event.hannto_cn_587939217_lager_opc_usage_change_e_5_9"

rows = s.query_events(None, "2026-09-29T00:00:00", entities=[eid], limit=3, order="desc")
print("prior events:")
for r in rows:
    print(f"  ts={r.get('ts')} state={r.get('new_state')}")

rows2 = s.query_events("2026-09-29T00:00:00", "2026-10-06T00:00:00", entities=[eid], limit=5, order="asc")
print(f"window events: {len(rows2)}")
for r in rows2:
    print(f"  ts={r.get('ts')} state={r.get('new_state')}")
