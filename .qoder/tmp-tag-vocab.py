"""只读探针：本居设备事件能带出哪些规则 tag（#27 剩余阻塞的证据）。

不写任何数据；只打印 domain/tag/计数的聚合，不打印实体名。
"""
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, "/app/src")

from memory_agent.config import get_config
from memory_agent.insights.utils import tags_of
from memory_agent.store import Store, now_local

cfg = get_config()
store = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)

rooms = getattr(cfg, "rooms", None) or {}
print(f"-- config.rooms：房间数 = {len(rooms)}")
names = {}
for payload in rooms.values():
    for eid, info in ((payload or {}).get("entities") or {}).items():
        if isinstance(info, dict):
            names[eid] = info.get("friendly_name") or ""
print(f"   entity_id→friendly_name 映射条数 = {len(names)}")

RULE_TAGS = {"door", "light", "climate", "computer"}
now = now_local(cfg.tz_offset_hours)

for label, mins in (("周期任务真实窗口(25min)", 25), ("近 24h", 24 * 60)):
    start = (now - timedelta(minutes=mins)).isoformat(sep="T")
    evs = store.query_events(start=start, end=now.isoformat(sep="T"),
                             order="asc", limit=5000)
    dom = Counter()
    tag = Counter()
    hit = 0
    for e in evs:
        eid = e.get("entity_id") or ""
        dom[eid.split(".")[0] if "." in eid else "(无 entity_id)"] += 1
        t = tags_of(eid, names.get(eid, ""))
        for x in t:
            tag[x] += 1
        if t & RULE_TAGS:
            hit += 1
    print(f"\n-- {label}：{len(evs)} 事件 --")
    print(f"   domain 分布: {dom.most_common(10)}")
    print(f"   tag 分布   : {tag.most_common(12)}")
    print(f"   带规则所需 tag(door/light/climate/computer) 的事件数 = {hit} / {len(evs)}")

print("\n-- 规则要求的房间关键词 vs 本居房间名 --")
for kw in ("书房", "study", "主卧室", "卧室", "bedroom"):
    print(f"   {kw!r:9} 命中 = {[r for r in rooms if kw in r]}")

print("\n-- 事件表里的 room 取值（前 10）--")
evs = store.query_events(start=(now - timedelta(hours=24)).isoformat(sep="T"),
                         end=now.isoformat(sep="T"), order="desc", limit=5000)
print("   ", Counter((e.get("room") or "(空)") for e in evs).most_common(10))
