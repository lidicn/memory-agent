import json, sqlite3, sys
from collections import Counter
sys.path.insert(0, "/app/src")
from memory_agent.config import get_config
cfg = get_config()
con = sqlite3.connect("file:%s?mode=ro" % cfg.db_path, uri=True)
print("sqlite_version=%s" % sqlite3.sqlite_version)
try:
    print("json_each_smoke=%s" % con.execute("SELECT COUNT(*) FROM json_each('[{\"name\":\"a\"},\"b\"]')").fetchone()[0])
except Exception as e:
    print("json_each_smoke=FAIL %r" % e)
n = con.execute("SELECT COUNT(*) FROM behavior_events").fetchone()[0]
empty = con.execute("SELECT COUNT(*) FROM behavior_events WHERE persons_json IN ('[]','')").fetchone()[0]
invalid = con.execute("SELECT COUNT(*) FROM behavior_events WHERE json_valid(persons_json)=0").fetchone()[0]
notarr = con.execute("SELECT COUNT(*) FROM behavior_events WHERE json_valid(persons_json)=1 AND json_type(persons_json)<>'array'").fetchone()[0]
print("behavior_events 行数=%s persons空=%s JSON非法=%s 合法但非数组=%s" % (n, empty, invalid, notarr))
types = Counter()
nokey = 0
for (raw,) in con.execute("SELECT persons_json FROM behavior_events"):
    try:
        arr = json.loads(raw)
    except Exception:
        continue
    if not isinstance(arr, list):
        continue
    for p in arr:
        types[type(p).__name__] += 1
        if isinstance(p, dict) and "name" not in p:
            nokey += 1
        if isinstance(p, dict) and not (p.get("name") or ""):
            nokey += 1
print("元素类型直方图=%s 无有效 name 的元素=%s" % (dict(types), nokey))
# SQL 口径 vs Python 口径一致性（只报数量，不报姓名）
SQL = ("EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(b.persons_json)=1 "
       "AND json_type(b.persons_json)='array' THEN b.persons_json ELSE '[]' END) j "
       "WHERE (j.type='object' AND json_extract(j.value,'$.name')=? ) OR (j.type='string' AND j.value=?))")
names = set()
for (raw,) in con.execute("SELECT persons_json FROM behavior_events"):
    try:
        arr = json.loads(raw)
    except Exception:
        continue
    if isinstance(arr, list):
        for p in arr:
            if isinstance(p, dict) and p.get("name"):
                names.add(str(p["name"]))
            elif isinstance(p, str) and p:
                names.add(p)
mism = 0
checked = 0
for nm in sorted(names):
    py = 0
    for (raw,) in con.execute("SELECT persons_json FROM behavior_events"):
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
    checked += 1
    if sql != py:
        mism += 1
print("姓名数=%s 比对口径不一致的姓名数=%s" % (checked, mism))
con.close()
