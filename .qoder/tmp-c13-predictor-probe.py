import sqlite3, json, re, collections

con = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def q(sql, *args):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


print("=== A. behavior_events.server_ts 的形状分布（口径：是否带时区偏移）===")
rows = q("""
SELECT CASE WHEN server_ts LIKE '%+%' OR server_ts LIKE '%Z' THEN 'aware'
            WHEN server_ts IS NULL OR server_ts='' THEN 'empty'
            ELSE 'naive' END AS shape,
       COUNT(*) AS n,
       MIN(server_ts) AS min_ts, MAX(server_ts) AS max_ts
FROM behavior_events GROUP BY shape
""")
for r in rows:
    print("  %-6s n=%-8s %s → %s" % (r["shape"], r["n"], r["min_ts"], r["max_ts"]))
shapes = {r["shape"]: r["n"] for r in rows}
mixed = shapes.get("aware", 0) > 0 and shapes.get("naive", 0) > 0
print("  ⇒ 同日混合 naive+aware 的可能性：两种形状同时存在=%s" % mixed)

print()
print("=== B. 同一自然日内是否真的混用两种形状（这才是 sorted() 崩溃的前提）===")
day_rows = q("""
SELECT substr(server_ts,1,10) AS day,
       SUM(CASE WHEN server_ts LIKE '%+%' OR server_ts LIKE '%Z' THEN 1 ELSE 0 END) AS aware,
       SUM(CASE WHEN server_ts NOT LIKE '%+%' AND server_ts NOT LIKE '%Z'
                 AND server_ts IS NOT NULL AND server_ts<>'' THEN 1 ELSE 0 END) AS naive
FROM behavior_events GROUP BY day HAVING aware>0 AND naive>0
""")
print("  混用日数=%d（前 5 天）：%s" % (len(day_rows), day_rows[:5]))

print()
print("=== C. 从 Python 侧直接复现 sorted() 的判据（只读，不写库）===")
sample = q("SELECT server_ts FROM behavior_events WHERE server_ts<>'' ORDER BY server_ts DESC LIMIT 200")
from datetime import datetime
naive, aware = [], []
for r in sample:
    try:
        dt = datetime.fromisoformat(r["server_ts"])
    except ValueError:
        continue
    (aware if dt.tzinfo else naive).append(dt)
print("  近 200 条样本：naive=%d aware=%d" % (len(naive), len(aware)))
if naive and aware:
    try:
        sorted(naive + aware)
        print("  sorted() 未抛错（形状可比较）")
    except TypeError as exc:
        print("  sorted() TypeError: %s  ⇒ 崩溃前提成立" % exc)
else:
    print("  样本只有一种形状 ⇒ 现网暂无崩溃前提（口径：近 200 条 desc）")

print()
print("=== D. data_days 的子串口径 vs 结构化口径差多少 ===")
evs = q("SELECT server_ts, persons_json FROM behavior_events WHERE persons_json IS NOT NULL AND persons_json<>'' LIMIT 60000")
names = collections.Counter()
for e in evs:
    try:
        ps = json.loads(e["persons_json"])
    except Exception:
        continue
    for p in ps if isinstance(ps, list) else []:
        if isinstance(p, dict) and p.get("name"):
            names[str(p["name"])] += 1
uniq = [n for n, _ in names.items()]
print("  出现过的人名（去重计数，不列全名）=%d 个" % len(uniq))
bad = []
for n in uniq:
    days_sub, days_struct = set(), set()
    for e in evs:
        raw = e["persons_json"] or ""
        if not e["server_ts"]:
            continue
        if n in raw:
            days_sub.add(e["server_ts"][:10])
        try:
            ps = json.loads(raw)
        except Exception:
            continue
        if any(isinstance(p, dict) and p.get("name") == n for p in (ps or [])):
            days_struct.add(e["server_ts"][:10])
        else:
            pass
    if days_sub != days_struct:
        bad.append((n, len(days_sub), len(days_struct)))
print("  子串口径与结构化口径**不等**的人名数=%d" % len(bad))
for b in bad[:5]:
    print("    人名长度=%d 子串天数=%d 结构化天数=%d" % (len(b[0]), b[1], b[2]))

print()
print("=== E. list_behavior_events 的 limit=5000 会不会把预测窗口切掉 ===")
total = q("SELECT COUNT(*) AS n FROM behavior_events")[0]["n"]
distinct_days = q("SELECT COUNT(DISTINCT substr(server_ts,1,10)) AS d FROM behavior_events")[0]["d"]
recent = q("""
SELECT COUNT(*) AS n, MIN(substr(server_ts,1,10)) AS lo, MAX(substr(server_ts,1,10)) AS hi
FROM (SELECT server_ts FROM behavior_events ORDER BY server_ts DESC LIMIT 5000)
""")[0]
print("  表总行数=%d 总天数=%d ｜ limit=5000 取到的最近窗口：%d 条（%s → %s）" % (total, distinct_days, recent["n"], recent["lo"], recent["hi"]))
print("  ⇒ 5000 条窗口覆盖天数=%d" % q("SELECT COUNT(DISTINCT substr(server_ts,1,10)) AS d FROM (SELECT server_ts FROM behavior_events ORDER BY server_ts DESC LIMIT 5000)")[0]["d"])
print("PROBE_DONE")
