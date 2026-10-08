"""A5 那格 25.82× 的真身：分支里那条 `day = ?` 是不是让规划器放弃了 ts 范围索引。

全程 EXPLAIN QUERY PLAN + SELECT，只读、不建 schema、不写一行。生产库口径。

量三件事：
  1. `day = ? AND ts >= ? AND ts < ?`（现状）与 `ts >= ? AND ts < ?`（去冗余谓词）的
     EQP 与单分支/整日 24 格 UNION ALL 各自耗时；
  2. 两种写法的**结果集是否同一批行**（按 id 集合比）——去谓词的前提是等价，不是"看起来快"；
  3. `day` 与 `substr(ts,1,10)` 在全表/窗口内有多少行不一致——这一格决定 `day = ?`
     到底是冗余谓词，还是它其实在筛掉"ts 与 day 不同日"的行（那就是语义，不能去）。
"""
import sqlite3
import time

from memory_agent.config import get_config
from memory_agent.insights.parser.timeframe import resolve_range
from memory_agent.insights.repository import StoreRepository

COLS = ("id, ts, day, room, entity_id, domain, action, person, "
        "old_state, new_state, attrs_json")
cfg = get_config()
con = sqlite3.connect(cfg.db_path)
con.row_factory = sqlite3.Row
tr = resolve_range(days=30)
DAY = "2026-10-04"          # 窗口内密度最高的一档（44,573 真值那量级）


def eqp(sql, params):
    return [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params)]


def timed(sql, params):
    t0 = time.perf_counter()
    rows = con.execute(sql, params).fetchall()
    return round((time.perf_counter() - t0) * 1000, 1), rows


def hour_bounds(hh):
    lo = DAY + "T" + hh + ":00:00"
    hi = DAY + "T" + "%02d" % (int(hh) + 1) + ":00:00"
    return lo, hi


print("db=%s" % cfg.db_path)
# 窗口边界走读路径同一把尺（`_bounds`），别在这里另起一套 ISO 口径
start_iso, end_iso, start_day, end_day = StoreRepository._bounds(tr)
print("window=%s → %s（日键 %s → %s）" % (start_iso, end_iso, start_day, end_day))

print("\n=== 1 单分支 EQP（LIMIT 41）===")
A = ("SELECT " + COLS + " FROM events WHERE day = ? AND ts >= ? AND ts < ? "
     "ORDER BY ts ASC, id ASC LIMIT ?")
B = ("SELECT " + COLS + " FROM events WHERE ts >= ? AND ts < ? "
     "ORDER BY ts ASC, id ASC LIMIT ?")
lo, hi = hour_bounds("20")
print("WITH_DAY :", eqp(A, [DAY, lo, hi, 41]))
print("NO_DAY   :", eqp(B, [lo, hi, 41]))
print("indexes  :", [r[1] for r in con.execute("PRAGMA index_list(events)")])

print("\n=== 2 整日 24 格 UNION ALL 耗时（现状 vs 去 day）===")
for label, tpl in (("with_day", "day = ? AND ts >= ? AND ts < ?"),
                   ("no_day", "ts >= ? AND ts < ?")):
    branches, params = [], []
    for hh in ["%02d" % h for h in range(24)]:
        l, h = hour_bounds(hh)
        branches.append("SELECT * FROM (SELECT '" + hh + "' AS hour_bucket, " + COLS +
                        " FROM events WHERE " + tpl + " ORDER BY ts ASC, id ASC LIMIT ? OFFSET ?)")
        params += ([DAY, l, h] if tpl.startswith("day") else [l, h]) + [41, 0]
    sql = " UNION ALL ".join(branches)
    ms, rows = timed(sql, params)
    ids = {r["id"] for r in rows}
    per_bucket = {}
    for r in rows:
        per_bucket[r["hour_bucket"]] = per_bucket.get(r["hour_bucket"], 0) + 1
    print("%s: 耗时=%sms 行数=%s 格数=%s 每格行数(min/max)=%s/%s id集合哈希=%s" % (
        label, ms, len(rows), len(per_bucket), min(per_bucket.values()),
        max(per_bucket.values()), hash(frozenset(ids)) & 0xffffffff))
    globals()[label] = ids

print("结果集是否同一批行：%s（差集 with-no=%s / no-with=%s）" % (
    with_day == no_day, len(with_day - no_day), len(no_day - with_day)))

print("\n=== 3 day 与 substr(ts,1,10) 的一致性 ===")
for label, cond, args in (("全表", "", []),
                          ("30天窗口", "WHERE ts BETWEEN ? AND ?",
                           [tr.start.isoformat(), tr.end.isoformat()])):
    t0 = time.perf_counter()
    total, mismatch = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN day != substr(ts,1,10) THEN 1 ELSE 0 END) "
        "FROM events " + cond, args).fetchone()
    print("%s: 行数=%s day!=ts前10位的行数=%s（耗时 %sms）" % (
        label, total, mismatch, round((time.perf_counter() - t0) * 1000, 1)))

print("\n=== 4 若无索引可用，去掉窗口下界看看规划器还有别的选项吗 ===")
print("EQP(ts BETWEEN 整窗 + day, 无 LIMIT)：",
      eqp("SELECT COUNT(*) FROM events WHERE day = ? AND ts BETWEEN ? AND ?",
          [DAY, DAY + "T00:00:00", DAY + "T23:59:59.9999"]))
con.close()
print("EQP_DONE")
