"""只读探针：#27 的收尾读数——activity 状态字是否真的开口了。

不改任何数据。先取 /api/health 的 activity 块，再用同一份 status() 判"0 产出"能不能被看见。
"""
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, "/app/src")

PORT = os.environ.get("MA_PORT", "")
CAND = [f"http://127.0.0.1:{p}/api/health" for p in (PORT, "8000", "8080", "8090", "7860") if p]
got = None
for url in CAND:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            got = json.loads(r.read().decode())
        print(f"GET {url} -> 200")
        break
    except Exception as exc:
        print(f"GET {url} -> {type(exc).__name__}: {exc}")

if got is not None:
    print("\n-- /api/health 的 activity 块 --")
    print(json.dumps(got.get("activity", {"__missing__": True}), ensure_ascii=False, indent=2))
    print("\n-- health 顶层键 --")
    print(sorted(got.keys()))

print("\n-- 活库状态字 --")
conn = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
for tbl in ("behavior_states", "behavior_events", "active_rules", "candidate_rules"):
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"   {tbl}: {n} 行")
    except Exception as exc:
        print(f"   {tbl}: 抛 {exc}")
try:
    rows = conn.execute(
        "SELECT state, COUNT(*) FROM candidate_rules GROUP BY state").fetchall()
    print("   candidate_rules by state:", rows)
except Exception as exc:
    print("   candidate_rules 分组抛", exc)
try:
    mx = conn.execute("SELECT MAX(ts) FROM behavior_events").fetchone()[0]
    print("   最近一条 behavior_event ts =", mx)
except Exception as exc:
    print("   behavior_events.max(ts) 抛", exc)
conn.close()
