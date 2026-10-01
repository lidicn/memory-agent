import os, sys, tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store
from memory_agent.activity_inference import ActivityInferenceService
from memory_agent.insights_legacy import InsightService as Legacy

class FixedInsights:   # 用 legacy 的正确实现
    def _tags_of(self, eid, name): return Legacy._tags_of(eid, name)

def mkrt(store, insights):
    cfg=SimpleNamespace(tz_offset_hours=0.0,rooms={},activity_window_minutes=120,
        pir_debounce_sec=30,activity_conf_threshold=0.6)
    return SimpleNamespace(config=cfg,store=store,insights=insights)

def build():
    tmp=tempfile.mkdtemp(prefix="fx_"); s=Store(os.path.join(tmp,"t.db"),tz_offset_hours=0.0); s.init_schema()
    base=datetime(2026,9,15,10,0,0); t=lambda m:(base+timedelta(minutes=m)).isoformat()
    for eid,st,m in [("binary_sensor.study_door_contact","on",0),("binary_sensor.study_door_contact","off",2),
                     ("light.study_ceiling","on",4),("climate.study_ac","cool",6),("switch.study_pc","on",8)]:
        s.insert_events([{"entity_id":eid,"ts":t(m),"room":"书房","domain":eid.split(".")[0],"new_state":st,"old_state":""}])
    return s, t

print("="*64)
print("A) 现状：insights 无 _tags_of（生产真实情况）")
print("="*64)
s1,t1 = build()
class Broken: pass
res1 = ActivityInferenceService(mkrt(s1, Broken())).run(start=t1(-1), end=t1(30))
print(f"   tags 全空 → persisted={res1['persisted']} candidates={res1['candidates']} detail={res1['detail']}")

print()
print("="*64)
print("B) 修复后：insights 提供正确 _tags_of（legacy 实现）")
print("="*64)
s2,t2 = build()
res2 = ActivityInferenceService(mkrt(s2, FixedInsights())).run(start=t2(-1), end=t2(30))
print(f"   tags 正确 → persisted={res2['persisted']} candidates={res2['candidates']}")
for d in res2['detail']: print("      detail:", d)
rows = s2.list_behavior_states()
print(f"   落库行为状态: {len(rows)} 条")
for r in rows: print("      ", {k:r.get(k) for k in ('activity','room','confidence')})
print()
print("★ 同一份输入数据，唯一变量是 _tags_of 是否可用")
