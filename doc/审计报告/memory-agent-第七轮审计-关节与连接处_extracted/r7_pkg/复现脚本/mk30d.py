import os, sys, sqlite3, random, time, json
sys.path.insert(0,"/data/workspace/ma/src")
os.environ.setdefault("JWT_SECRET","t"*40)
from memory_agent.store import Store

DB="/tmp/ma30.db"
for f in (DB, DB+"-wal", DB+"-shm"):
    if os.path.exists(f): os.remove(f)
s=Store(DB, tz_offset_hours=8.0)
t0=time.time(); s.init_schema(); print(f"init_schema（空库）: {(time.time()-t0)*1000:.0f} ms")
conn=sqlite3.connect(DB); conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA synchronous=NORMAL")
random.seed(2026)
# 30 天，按真实家庭规模：每天 ~10000 条 → 30 万条
rooms=["客厅","书房","主卧","次卧","厨房","卫生间","阳台","玄关"]
ents=[]
for r in rooms:
    for d in ("light","binary_sensor","switch","climate","sensor","media_player","fan","cover","vacuum"):
        for i in range(5): ents.append((f"{d}.{r}_{i}", r, d))
print(f"实体数 {len(ents)}  生成 30 天 × ~10000 条/天 = 30 万条 ...")
base=time.time()-30*86400
rows=[]
for i in range(300000):
    eid,room,dom=random.choice(ents)
    off=random.randint(0,30*86400-1)
    ts=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(base+off))
    day=ts[:10]
    rows.append((f"e{i}", ts, day, room, eid, dom, random.choice(["on","off","开","关","打开","关闭",""]),
                 random.choice(["","爸爸","妈妈","孩子"]), "off","on",'{"x":1}'))
t0=time.time()
conn.executemany("INSERT INTO events (id,ts,day,room,entity_id,domain,action,person,old_state,new_state,attrs_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
conn.commit()
print(f"events 入库 {(time.time()-t0):.1f}s → {conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]} 行")
# behavior_events 视觉 6 万
rows=[]
for i in range(60000):
    off=random.randint(0,30*86400-1)
    ts=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(base+off))
    rows.append((ts, None, ts[:10], random.choice(rooms), None,
                 json.dumps([{"name":random.choice(["爸爸","妈妈","孩子"]),"confidence":0.85}], ensure_ascii=False),
                 1, random.choice(["看电视","做饭","睡觉","工作","洗澡","读书"]), "场景", 0.85, None, "face", None, None, None, "ok"))
conn.executemany("INSERT INTO behavior_events (server_ts,device_ts,day,room,camera_src,persons_json,count,action,scene,confidence,appearance_json,trigger,vlm_latency_ms,snapshot_path,raw_response,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
conn.commit()
# perception_events 8 万
rows=[(None, f"p{i}", base+random.randint(0,30*86400), None, "edge_ai",
       random.choice(["face_known","motion","cry","fall"]), random.choice(rooms), None, 0.8, "{}", "{}") for i in range(80000)]
rows=[(None, f"p{i}", base+random.randint(0,30*86400),
       time.strftime("%Y-%m-%d", time.localtime(base+random.randint(0,30*86400))), "edge_ai",
       random.choice(["face_known","motion","cry","fall"]), random.choice(rooms), None, 0.8, "{}", "{}") for i in range(80000)]
conn.executemany("INSERT INTO perception_events (id,event_id,server_ts,day,source,kind,room,entity_id,confidence,payload_json,raw_event_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
conn.commit()
conn.execute("ANALYZE"); conn.commit(); conn.close()
print(f"\nDB 体积: {os.path.getsize(DB)/1024/1024:.1f} MB （30 天规模）")
for t in ("events","behavior_events","perception_events"):
    c=sqlite3.connect(DB); print(f"   {t:<20} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>8} 行"); c.close()
