import os, sys, json
from datetime import timedelta
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))
from memory_agent.config import Config
from memory_agent.insights import InsightService
from memory_agent.insights.models import house_now
from memory_agent.store import Store

tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "ma_c6_dbg.db")
if os.path.exists(tmp):
    os.remove(tmp)
st = Store(tmp, tz_offset_hours=8.0)
st.init_schema()


def ev(eid, ts, state, room, name, domain=None):
    return {"entity_id": eid, "ts": ts, "new_state": state, "room": room,
            "domain": domain or eid.split(".")[0], "attrs": {"friendly_name": name}}


anchor = (house_now() - timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
st.insert_events([
    ev("binary_sensor.bath_presence", anchor.isoformat(), "on", "卫生间", "卫生间存在传感器"),
    ev("binary_sensor.bath_presence", (anchor + timedelta(minutes=20)).isoformat(), "off", "卫生间", "卫生间存在传感器"),
    ev("water_heater.bath_heater", (anchor + timedelta(minutes=1)).isoformat(), "on", "卫生间", "卫生间热水器"),
    ev("water_heater.bath_heater", (anchor + timedelta(minutes=19)).isoformat(), "off", "卫生间", "卫生间热水器"),
])
svc = InsightService(st, Config())
tr = svc._tr(None, None, days=1)
print("TR", tr.start.isoformat(), tr.end.isoformat(), tr.start_ts, tr.end_ts)
print("SPLIT", tr.split_days())
out = svc.core.infer_activities(tr)
for r in out.get("activities", []):
    print("ROW", json.dumps({k: r.get(k) for k in ("activity", "name", "day", "source", "start_ts", "end_ts", "start_hour", "end_hour", "events", "minutes")}, ensure_ascii=False))
print("META", json.dumps({k: out.get(k) for k in ("summary", "rule_sources", "activity_types", "total_activities")}, ensure_ascii=False))
print("NL", json.dumps(svc.ask_memory("最近洗澡了吗", days=1).get("answer"), ensure_ascii=False))
st.close()
os.remove(tmp)
