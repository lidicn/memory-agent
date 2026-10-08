import json, sqlite3, sys
sys.path.insert(0, "/app/src")
from memory_agent.config import get_config
con = sqlite3.connect("file:%s?mode=ro" % get_config().db_path, uri=True)
SQL = ("EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(b.persons_json)=1 "
       "AND json_type(b.persons_json)='array' THEN b.persons_json ELSE '[]' END) j "
       "WHERE (j.type='object' AND json_extract(j.value,'$.name')=?) OR (j.type='text' AND j.value=?))")
rows = [r[0] for r in con.execute("SELECT persons_json FROM behavior_events")]
names = []
for raw in rows:
    try:
        arr = json.loads(raw)
    except Exception:
        continue
    if not isinstance(arr, list):
        continue
    for p in arr:
        nm = p.get("name") if isinstance(p, dict) else (p if isinstance(p, str) else None)
        if isinstance(nm, str) and nm and nm not in names:
            names.append(nm)
print("names_len=%d" % len(names), flush=True)
for nm in names:
    py = 0
    for raw in rows:
        try:
            arr = json.loads(raw)
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        hit = False
        for p in arr:
            pn = p.get("name") if isinstance(p, dict) else (p if isinstance(p, str) else None)
            if pn == nm:
                hit = True
                break
        if hit:
            py += 1
    sql = con.execute("SELECT COUNT(*) FROM behavior_events b WHERE " + SQL, (nm, nm)).fetchone()[0]
    print("%s len=%d ws=%s ascii=%s py=%d sql=%d" % (
        "OK  " if py == sql else "DIFF", len(nm), nm != nm.strip(), nm.isascii(), py, sql), flush=True)
