import os, sys, sqlite3
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/data/workspace/madata/memory_worker.db",
 "HASS_SERVER":"http://127.0.0.1:18123","HASS_TOKEN":"dummy","REDIS_HOST":"127.0.0.1",
 "CHROMA_HOST":"127.0.0.1","LLM_API_URL":"http://127.0.0.1:19999/v1","LLM_API_KEY":"d","LLM_MODEL":"t"})
sys.path.insert(0,"/data/workspace/ma/src")
from memory_agent.config import get_config
from memory_agent.store import Store
from memory_agent.insights.api import InsightService
cfg = get_config(); store = Store(cfg.db_path, cfg.tz_offset_hours)
store.db_query("""CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, ts TEXT NOT NULL,
 day TEXT NOT NULL, room TEXT NOT NULL DEFAULT '', entity_id TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '',
 action TEXT NOT NULL DEFAULT '', person TEXT NOT NULL DEFAULT '', old_state TEXT, new_state TEXT, attrs_json TEXT)""")
store.db_query("DELETE FROM events")
for i in range(5):
    store.db_query("INSERT INTO events (id,ts,day,room,entity_id,domain,action,person,old_state,new_state) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"e{i}", f"2026-09-2{i} 08:00:00", f"2026-09-2{i}", "书房", "light.study_desk", "light","开灯","","off","on"))
print("events 行数:", store.db_query("SELECT COUNT(*) AS c FROM events"))
print("="*62)
svcA = InsightService(store, cfg)
print("A) InsightService(store, config)   → 实体目录 %d 条" % len(svcA._safe_entities()))
print("="*62)
svcB = InsightService(cfg, store)   # runtime.py:69 的真实写法
print("B) InsightService(config, store)   → 实体目录 %d 条   ★runtime.py:69 实际如此" % len(svcB._safe_entities()))
print("="*62)
for tag, s in (("A",svcA), ("B",svcB)):
    try:
        r = s.resolver.resolve("书房台灯")
        print(f"{tag} 解析'书房台灯' → {[x.entity_id for x in r]}")
    except Exception as e:
        print(f"{tag} resolve 异常: {type(e).__name__}: {str(e)[:80]}")
