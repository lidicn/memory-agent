import json, sqlite3, sys
from collections import Counter
sys.path.insert(0, "/app/src")
from memory_agent.config import get_config
con = sqlite3.connect("file:%s?mode=ro" % get_config().db_path, uri=True)
raws = [r[0] for r in con.execute("SELECT persons_json FROM behavior_events")]
print("rows=%d" % len(raws))
kt = Counter(); nmt = Counter()
for raw in raws:
    try:
        arr = json.loads(raw)
    except Exception as e:
        print("parsefail %r" % e); continue
    if not isinstance(arr, list):
        print("notlist %s" % type(arr).__name__); continue
    for p in arr:
        kt[type(p).__name__] += 1
        if isinstance(p, dict):
            nmt["%s:%s" % (tuple(sorted(p.keys())), type(p.get("name")).__name__)] += 1
print("元素类型=%s" % dict(kt))
for k, v in nmt.most_common(10):
    print("  %s -> %d" % (k, v))
