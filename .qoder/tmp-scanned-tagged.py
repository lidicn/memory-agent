"""只读探针：按 run() 的**同一口径**复算 scanned / tagged，判"tagged=0"是数据还是缺陷。

不调用 run()（它会写 behavior_states）；只跑 query_events + _prepare_events 这两步纯读。
"""
import sys
from collections import Counter
from datetime import timedelta

sys.path.insert(0, "/app/src")

from memory_agent.activity_inference import ActivityInferenceService
from memory_agent.config import get_config
from memory_agent.store import Store, now_local


class FakeRT:
    pass


cfg = get_config()
rt = FakeRT()
rt.config = cfg
rt.store = Store(cfg.db_path, tz_offset_hours=cfg.tz_offset_hours)
rt.insights = None
svc = ActivityInferenceService(rt)

now = now_local(cfg.tz_offset_hours)
horizon = __import__("memory_agent.activity_inference", fromlist=["x"]).rule_horizon_minutes(svc.rules)
wmin = max(int(getattr(cfg, "activity_window_minutes", 15) or 15), horizon)
deb = int(getattr(cfg, "pir_debounce_sec", 30) or 0)
start = (now.replace(microsecond=0) - timedelta(minutes=wmin)).isoformat(sep="T")
print(f"窗口 = {wmin} 分（配置 {getattr(cfg, 'activity_window_minutes', None)} / 规则跨度 {horizon}）"
      f" 去抖 {deb} 秒")

raw = svc.store.query_events(start=start, end=now.isoformat(sep="T"), order="asc", limit=5000)
print(f"query_events 返回 = {len(raw)}")
prep = svc._prepare_events([dict(e) for e in raw], deb)
print(f"_prepare_events 之后 = {len(prep)}  (= 告警里的 scanned)")
tagged = [e for e in prep if e.get("tags")]
print(f"带任一 tag 的事件 = {len(tagged)}  (= 告警里的 tagged)")
print("   tag 分布:", Counter(t for e in prep for t in e.get("tags") or []).most_common(10))
print("   domain 分布:", Counter((e.get("entity_id") or "?").split(".")[0] for e in prep).most_common(8))
RULE = {"door", "light", "climate", "computer"}
print(f"   带规则所需 tag 的 = {sum(1 for e in prep if set(e.get('tags') or []) & RULE)}")

print("\n-- 被去抖丢掉的事件里有没有规则 tag（丢错了会直接饿死规则）--")
kept_ids = {id(e) for e in prep}
raw2 = [dict(e) for e in raw]
prep2_ids = {id(e) for e in svc._prepare_events([dict(e) for e in raw], deb)}
dropped = [e for e in raw2 if e.get("tags") is None]
names = svc._friendly_names()
d_rule = 0
d_total = 0
for e in raw2:
    t = svc._tags_of(e.get("entity_id") or "", names.get(e.get("entity_id") or "", ""))
    if t & RULE:
        d_rule += 1
print(f"   原始带规则 tag 的事件 = {d_rule} / {len(raw2)}")
print("\n-- 各房间在窗口内的原始事件数（规则要 room 命中 书房/卧室）--")
print("   ", Counter(e.get("room") or "(空)" for e in raw2).most_common(8))
print("   窗口内 room 含 '卧室' 或 '书房' 的原始事件 =",
      sum(1 for e in raw2 if ("卧室" in (e.get("room") or "") or "书房" in (e.get("room") or ""))))
