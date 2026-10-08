"""只读复算行为推断：为什么 behavior_states 生产 0 行。

不调 run()（它会落库），也不起 Runtime（装配太重）——用 fake runtime 驱动，
只复刻「读取 → 打标签 → 匹配」三段，纯统计不写。
"""
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, "/app/src")
from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.config import get_config                           # noqa: E402
from memory_agent.store import Store, now_local                      # noqa: E402

cfg = get_config()


class FakeRT:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.insights = None


store = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
svc = ActivityInferenceService(FakeRT(cfg, store))

now = now_local(cfg.tz_offset_hours)
start = (now - timedelta(hours=24)).isoformat(timespec="seconds")
end = now.isoformat(timespec="seconds")

raw = store.query_events(start=start, end=end, order="asc", limit=5000)
print("query_events_24h(limit5000) =", len(raw))
if raw:
    print("ts_span =", raw[0].get("ts"), "→", raw[-1].get("ts"))
print("room_values =", Counter((e.get("room") or "<empty>") for e in raw).most_common(6))

prep = svc._prepare_events(raw, int(getattr(cfg, "pir_debounce_sec", 30) or 0))
tags = Counter()
for e in prep:
    t = e.get("tags") or set()
    tags["<none>" if not t else "|".join(sorted(t))] += 1
print("prepared =", len(prep))
print("tag_distribution =", tags.most_common(8))
print("domain_distribution =", Counter((e.get("domain") or "<empty>") for e in prep).most_common(8))

by_room: dict[str, list] = {}
for e in prep:
    by_room.setdefault((e.get("room") or "").strip() or "未知", []).append(e)
print("rooms =", {k: len(v) for k, v in list(by_room.items())[:8]})

for rule in svc.rules:
    total = 0
    per_room = {}
    for room, evs in by_room.items():
        if not svc._room_ok(room, rule):
            continue
        h = svc._match_rule(evs, rule)
        total += len(h)
        if h:
            per_room[room] = len(h)
    print(f"RULE {rule.get('name')!r} rooms={rule.get('room')} "
          f"steps={len(rule.get('steps') or [])} conf={rule.get('confidence')} "
          f"hits={total} per_room={per_room}")

print("activity_conf_threshold =", getattr(cfg, "activity_conf_threshold", 0.6))
print("behavior_states_total =", store.connect().execute(
    "select count(*) from behavior_states").fetchone()[0])
