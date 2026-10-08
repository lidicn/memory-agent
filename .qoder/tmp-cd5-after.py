"""裁5 落地后的工作树读数：17 个 insights 工具按 dispatch 形状打一遍。"""
import dataclasses
import inspect
import os
import sys
import tempfile

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent.config import Config
from memory_agent.insights import InsightService
from memory_agent.store import Store
from memory_agent.tool_schema import TOOL_SPECS

ROOMS = {
    "书房": {"enabled": True, "entities": {"light.shufang_desk": {"name": "书房台灯", "domain": "light"}}},
    "客厅": {"enabled": True, "entities": {"media_player.living_tv": {"name": "客厅电视", "domain": "media_player"}}},
}

fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.remove(path)
st = Store(path, tz_offset_hours=8.0)
st.init_schema()
rows = []
for eid, room in (("light.shufang_desk", "书房"), ("media_player.living_tv", "客厅")):
    for h in (9, 21):
        for s, m in (("on", 0), ("off", 30)):
            rows.append({"entity_id": eid, "ts": f"2026-01-01T{h:02d}:{m:02d}:00",
                         "new_state": s, "room": room, "day": "2026-01-01",
                         "attrs_json": '{"friendly_name": "%s"}' % eid})
st.insert_events(rows)
st.insert_behavior_event({"server_ts": "2026-01-01T15:10:00", "day": "2026-01-01",
                          "room": "书房", "persons": [{"name": "张三"}], "count": 1,
                          "action": "坐在桌前使用电脑", "scene": "书房有人使用电脑"})
svc = InsightService(st, dataclasses.replace(Config(), rooms=ROOMS))

BIND = ("unexpected keyword argument", "positional arguments", "has no attribute",
        "takes from", "required positional")


def kwargs_for(spec):
    out = {}
    for p in spec.params:
        if p.required:
            out[p.name] = "书房" if p.name == "room" else "x"
        elif p.default is not None:
            out[p.name] = p.default
    return out


for s in [t for t in TOOL_SPECS if t.service == "insights" and t.method]:
    kw = kwargs_for(s)
    try:
        r = getattr(svc, s.method)(**kw)
    except TypeError as exc:
        print(f"[RAISE] {s.name:28} {type(exc).__name__}: {exc}")
        continue
    err = (r or {}).get("error") if isinstance(r, dict) else None
    dead = bool(err) and any(b in str(err) for b in BIND)
    shape = sorted(r.keys())[:6] if isinstance(r, dict) else type(r).__name__
    print(f"{'[DEAD]' if dead else '[OK] '} {s.name:28} err={str(err)[:70]!r} keys={shape}")

print("--- 专项：query_behavior_events 走 VLM 表 + member 过滤")
one = svc.query_behavior_events(room="书房", days=3650)
print("all:", {k: one.get(k) for k in ("ok", "count", "has_more", "limit", "offset")},
      "persons:", [e.get("persons") for e in one.get("events", [])])
hit = svc.query_behavior_events(room="书房", member="张三", days=3650)
print("member=张三:", {k: hit.get(k) for k in ("ok", "count")},
      [e.get("persons") for e in hit.get("events", [])])
miss = svc.query_behavior_events(room="书房", member="李四", days=3650)
print("member=李四:", {k: miss.get(k) for k in ("ok", "count")})
bad = svc.query_behavior_events(room="不存在房间")
print("未匹配房间:", {k: bad.get(k) for k in ("ok", "error")})
print("门面代键在否:", all(k in one for k in ("time_range", "offset", "limit", "has_more")),
      "有没有伪造 total:", "total" in one)

print("--- 专项：get_last_event 两代键")
g = svc.get_last_event(entity_id="light.shufang_desk", transition="off")
print("legacy 键:", {k: g.get(k) for k in ("ok", "entity_id", "ts", "old_state", "new_state")})
print("兼容键:", {k: g.get(k) for k in ("event", "total", "offset", "limit", "has_more")})
g2 = svc.get_last_event("light.shufang_desk", None, None, "off", 3650)  # handler 的位置形状
print("位置形状:", g2.get("ok"), g2.get("ts"))
g3 = svc.get_last_event(entity_id="light.unknown_eid", transition="off")
print("查无:", {k: g3.get(k) for k in ("ok", "error", "event", "total")})

print("--- 专项：get_events 的 total 语义与序列化")
e = svc.get_events(days=3650, limit=2, offset=0)
print("page:", {k: e.get(k) for k in ("total", "offset", "limit", "has_more",
                                      "truncated", "scan_limit", "total_exact")})
print("首条类型:", type(e["events"][0]).__name__)

print("--- 专项：新引擎空指向的四条现在能不能出数据")
c = svc.climate_sessions(room="客厅", days=3650)
print("climate_sessions:", {k: c.get(k) for k in ("ok", "total", "total_sessions", "has_more", "error")})
w = svc.water_purifier_usage(start="2026-01-01T00:00:00", end="2026-01-02T00:00:00")
print("water_purifier:", {k: w.get(k) for k in ("ok", "days", "error")}, "type=", type(w).__name__)
q = svc.data_quality_issues(start="2026-01-01T00:00:00", end="2026-01-02T00:00:00")
print("data_quality_issues:", {k: q.get(k) for k in ("total", "issues", "error")})
x = svc.explain_insight("不存在的-id")
print("explain_insight:", {k: x.get(k) for k in ("ok", "found", "total", "error")})
