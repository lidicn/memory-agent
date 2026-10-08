"""把现网 behavior_events 真值喂给 behavior_predictor 的纯函数（只读，不写库、不外泄姓名）。

口径：整表 4023 行（limit=5000 的调用面等价），人名只报长度与计数。
"""
import sqlite3
import json
import traceback
from datetime import datetime

con = sqlite3.connect("file:/data/memory_agent.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row

from memory_agent.behavior_predictor import (predict_arrival_time,
                                             predict_daily_routine,
                                             predict_post_arrival_activities)

events = [dict(r) for r in con.execute(
    "SELECT server_ts, persons_json, action, scene FROM behavior_events "
    "ORDER BY server_ts DESC LIMIT 5000").fetchall()]
print("行数=%d" % len(events))

names = set()
for e in events:
    try:
        ps = json.loads(e.get("persons_json") or "[]")
    except Exception:
        continue
    for p in ps if isinstance(ps, list) else []:
        if isinstance(p, dict) and p.get("name"):
            names.add(str(p["name"]))
print("去重人名数=%d（只报长度）：%s" % (len(names), sorted(len(n) for n in names)))

print()
print("=== 1) 三个入口函数逐个跑，异常必须原样报出 ===")
for fn_name, call in (
    ("predict_arrival_time", lambda n: predict_arrival_time(events, n)),
    ("predict_post_arrival_activities", lambda n: predict_post_arrival_activities(events, n)),
    ("predict_daily_routine", lambda n: predict_daily_routine(events, n)),
):
    raised = 0
    for n in sorted(names, key=lambda x: len(x)):
        try:
            out = call(n)
        except Exception as exc:
            raised += 1
            if raised == 1:
                print("  %s / 人名长度=%d ⇒ %s: %s" % (fn_name, len(n), type(exc).__name__, exc))
    print("  %s：判红人名数=%d / %d" % (fn_name, raised, len(names)))

print()
print("=== 2) data_days 子串口径 vs 结构化口径（逐人名，只报数字）===")
for n in sorted(names, key=lambda x: len(x)):
    days_sub, days_struct = set(), set()
    for e in events:
        raw = e.get("persons_json") or ""
        ts = e.get("server_ts") or ""
        if not ts:
            continue
        if n in raw:
            days_sub.add(ts[:10])
        try:
            ps = json.loads(raw)
        except Exception:
            continue
        if any(isinstance(p, dict) and p.get("name") == n for p in (ps or [])):
            days_struct.add(ts[:10])
    marker = "  <== 不等" if days_sub != days_struct else ""
    print("  人名长度=%-3d 子串天数=%-3d 结构化天数=%-3d%s" % (len(n), len(days_sub), len(days_struct), marker))
print("PROBE_DONE")
