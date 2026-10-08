"""去掉 `day = ?` 之后，`day BETWEEN` / 窗口 `ts BETWEEN` 还在不在索引这一侧？

上一格（tmp-c37-eqp.py）量到：单分支带 `day = ?` 走 `idx_events_day`（一天扫 24 遍全天），
不带则走 `idx_events_ts` 且**行集逐字相同**。但真实分支不是裸的 ts 范围——`_event_where`
还会带进 `day BETWEEN` 与窗口 `ts BETWEEN`，后者是首/末日**半截窗**的夹紧条件，删了会越窗。
这一格量四种组合的 EQP + 耗时 + 行集是否同一批，用来决定分支 WHERE 的确切形状。

只读：EXPLAIN QUERY PLAN + SELECT。
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
tr = resolve_range(days=30)
start_iso, end_iso, start_day, end_day = StoreRepository._bounds(tr)
DAY = "2026-10-04"
HOURS = ["%02d" % h for h in range(24)]

# 四种分支 WHERE 形状：conds 里的 "?" 由 kind 决定绑什么
VARIANTS = {
    "V1 现状（day BETWEEN + ts BETWEEN + day=? + ts 范围）": ("day_window", "day_eq"),
    "V2 去 day=?（day BETWEEN + ts BETWEEN + ts 范围）": ("day_window", "no_day_eq"),
    "V3 去 day 谓词（ts BETWEEN + ts 范围）": ("no_day", "no_day_eq"),
    "V4 只留 ts 范围（窗口夹紧靠 ts 范围自己）": ("bare", "no_day_eq"),
}


def build(kind_day, kind_eq):
    branches, params = [], []
    for hh in HOURS:
        lo = DAY + "T" + hh + ":00:00"
        hi = DAY + "T" + "%02d" % (int(hh) + 1) + ":00:00"
        conds, bp = [], []
        if kind_day == "day_window":
            conds.append("day BETWEEN ? AND ?")
            bp += [start_day, end_day]
        if kind_day != "bare":
            conds.append("ts BETWEEN ? AND ?")
            bp += [start_iso, end_iso]
        if kind_eq == "day_eq":
            conds.append("day = ?")
            bp.append(DAY)
        conds.append("ts >= ?")
        conds.append("ts < ?")
        bp += [lo, hi]
        branches.append("SELECT * FROM (SELECT " + COLS + " FROM events WHERE "
                        + " AND ".join(conds)
                        + " ORDER BY ts ASC, id ASC LIMIT ? OFFSET ?)")
        bp += [41, 0]
        params.extend(bp)
    return " UNION ALL ".join(branches), params


def ids_of(rows):
    return {r[0] for r in rows}


print("db=%s 窗口=%s→%s（日键 %s→%s）DAY=%s" % (cfg.db_path, start_iso, end_iso,
                                                start_day, end_day, DAY))
baseline = None
for label, (kind_day, kind_eq) in VARIANTS.items():
    sql, params = build(kind_day, kind_eq)
    plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, params)]
    t0 = time.perf_counter()
    rows = con.execute(sql, params).fetchall()
    ms = round((time.perf_counter() - t0) * 1000, 1)
    got = ids_of(rows)
    if baseline is None:
        baseline = got
        same = "（基准）"
    else:
        same = "与基准同一批=%s 差集 %d/%d" % (
            got == baseline, len(baseline - got), len(got - baseline))
    days = {r[2] for r in rows}
    print("\n%s\n  耗时=%sms 行数=%s 日键=%s %s\n  EQP=%s" % (label, ms, len(rows),
                                                              sorted(days), same, plan))
con.close()
print("\nVARIANTS_DONE")
