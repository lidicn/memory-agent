import json, sqlite3, sys
sys.path.insert(0, "/app/src")
from memory_agent.config import get_config
cfg = get_config()
con = sqlite3.connect("file:%s?mode=ro" % cfg.db_path, uri=True)
SQL = ("EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(b.persons_json)=1 "
       "AND json_type(b.persons_json)='array' THEN b.persons_json ELSE '[]' END) j "
       "WHERE (j.type='object' AND json_extract(j.value,'$.name')=? ) OR (j.type='string' AND j.value=?))")
rows = [(r,) for r in con.execute("SELECT persons_json FROM behavior_events")]
print("header rows=%d" % len(rows), flush=True)
names = {}
for (raw,) in rows:
    try:
        arr = json.loads(raw)
    except Exception:
        continue
    if not isinstance(arr, list):
        continue
    for p in arr:
        if isinstance(p, dict):
            nm = p.get("name")
            if isinstance(nm, str) and nm:
                names.setdefault(nm, []).append(p)
        elif isinstance(p, str) and p:
            names.setdefault(p, []).append(p)
for nm, elems in sorted(names.items()):
    py = 0
    for (raw,) in rows:
        try:
            arr = json.loads(raw)
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        for p in arr:
            pn = p.get("name") if isinstance(p, dict) else (p if isinstance(p, str) else None)
            if pn == nm:
                py += 1
                break
    sql = con.execute("SELECT COUNT(*) FROM behavior_events b WHERE " + SQL, (nm, nm)).fetchone()[0]
    tag = "OK " if py == sql else "DIFF"
    # 只报形状，不报姓名内容
    shapes = sorted({("keys=%s" % ",".join(sorted(e.keys())) if isinstance(e, dict) else "str") for e in elems})
    print("%s len=%d ascii=%s ws=%s py=%d sql=%d 元素形状=%s" % (
        tag, len(nm), nm.isascii(), (nm != nm.strip()), py, sql, shapes))
con.close()
print("names=0"  0.000000e+00n(names), flush=True)
