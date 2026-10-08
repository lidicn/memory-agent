import os, sys, tempfile, json
from datetime import datetime, timedelta
from types import SimpleNamespace
sys.path.insert(0, "/app/src")
sys.path.insert(0, "/tmp/tests")
from memory_agent.activity_inference import ActivityInferenceService, _parse_ts
from memory_agent.insights import InsightService
from memory_agent.store import Store

DOOR = "binary_sensor.study_door_contact"
LIGHT = "light.study_ceiling"
CLIMATE = "climate.study_ac"
PC = "switch.study_pc"

class _FakeInsights:
    def _tags_of(self, eid, name):
        return InsightService._tags_of(eid, name)

def _runtime(store):
    cfg = SimpleNamespace(tz_offset_hours=0.0, rooms={}, pir_debounce_sec=0)
    return SimpleNamespace(config=cfg, store=store, insights=_FakeInsights())

tmp = tempfile.mkdtemp(prefix="ma_diag_")
s = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
s.init_schema()

def _ev(ts, eid, room="书房", state="on"):
    s.insert_events([{"entity_id": eid, "ts": ts.isoformat(sep="T"), "room": room,
                       "domain": eid.split(".")[0], "new_state": state, "old_state": ""}])

NORMAL = [(0, DOOR, "on"), (2, DOOR, "off"), (4, LIGHT, "on"), (6, CLIMATE, "on"), (8, PC, "on")]
base = datetime(2026, 9, 1, 9, 0, 0)
for minute, eid, state in NORMAL:
    _ev(base + timedelta(minutes=minute), eid, state=state)

svc = ActivityInferenceService(_runtime(s))
events = svc._iter_events("2026-09-01T00:00:00", "2026-09-03T00:00:00")
print(f"_iter_events returned {len(events)} events")
prepared = svc._prepare_events(events, 0)
print(f"_prepare_events returned {len(prepared)} events")
for e in prepared:
    print(f"  ts={e.get('ts')} eid={e.get('entity_id')} room={e.get('room')} tags={e.get('tags')} state={e.get('state')}")

by_room_day = {}
for e in prepared:
    ts = _parse_ts(e.get("ts") or "")
    if ts is None:
        print(f"  SKIP: ts parse failed for {e.get('ts')}")
        continue
    rm = (e.get("room") or "").strip() or "未知"
    by_room_day.setdefault((rm, ts.date().isoformat()), []).append(e)
print(f"\nby_room_day keys: {list(by_room_day.keys())}")
for (rm, day), evs in by_room_day.items():
    present = {t for e in evs for t in (e.get("tags") or set())}
    print(f"  {rm}/{day}: {len(evs)} events, tags={present}")
