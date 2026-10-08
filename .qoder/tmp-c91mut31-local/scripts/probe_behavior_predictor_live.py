"""只读量具：现网 behavior_events 真值 × 行为预测器，出境只报计数与人名长度。

用途：#46 那族「键名对不上就静默归零」的缺陷在**生产数据**上重新量一遍——
`Store.list_behavior_events` 早已把 `persons_json` 弹出换成 `persons`，而预测器读的是旧键，
现网表现为三个入口恒空、`/api/behaviors/predict` 照样 `ok:true`。

口径：整表按调用面等价的 `LIMIT 5000` 取，行形状与门面一致（用 `Store._deserialize_persons`
这个纯静态方法还原，不建 Store、不连写库）。连接一律 `mode=ro`。

跑法（`PYTHONPATH` 指向被测代码树，容器里即快照的 src）：
`PYTHONPATH=/tmp/<snap>/src:/tmp/pylibs python -u scripts/probe_behavior_predictor_live.py`
"""

import json
import sqlite3
from collections import Counter

DB = "file:/data/memory_agent.db?mode=ro"

from memory_agent.behavior_predictor import (  # noqa: E402
    predict_arrival_time, predict_daily_routine, predict_post_arrival_activities)
from memory_agent.store import Store  # noqa: E402  只用其静态方法，不实例化

con = sqlite3.connect(DB, uri=True)
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT server_ts, persons_json, action, scene FROM behavior_events "
    "ORDER BY server_ts DESC LIMIT 5000").fetchall()]
print("行数=%d" % len(rows))

shapes = Counter()
day_shape: dict[str, set] = {}


def _shape(ts: str) -> str:
    if not ts:
        return "empty"
    return "aware" if ("+" in ts[10:] or ts.endswith("Z")) else "naive"


for r in rows:
    ts = str(r.get("server_ts") or "")
    shapes[_shape(ts)] += 1
    if len(ts) >= 10:
        day_shape.setdefault(ts[:10], set()).add(_shape(ts))
mixed_days = [d for d, s in day_shape.items() if len(s) > 1]

print()
print("=== A. server_ts 形状分布 ===")
for shape, n in shapes.most_common():
    print("  %-6s n=%d" % (shape, n))
print("  同一自然日混用两种形状的天数=%d %s" % (len(mixed_days), sorted(mixed_days)[:5]))

# 门面形状：persons 是解析好的 dict 数组，persons_json 已不存在
events = [{
    "server_ts": r.get("server_ts"),
    "persons": Store._deserialize_persons(r.get("persons_json")),
    "action": r.get("action"),
    "scene": r.get("scene"),
} for r in rows]

names = sorted({
    p["name"] for e in events for p in e["persons"]
    if isinstance(p, dict) and p.get("name")
}, key=lambda x: (len(x), x))


def _is_str_list(raw):
    try:
        ps = json.loads(raw or "[]")
    except Exception:
        return False
    return isinstance(ps, list) and any(isinstance(p, str) for p in ps)


print()
print('=== B. 人名字符串元素（旧格式 ["Kevin"]）在库里是否存在 ===')
print("  含字符串元素的人数=%d / 去重人名数=%d（只报长度）：%s" % (
    sum(1 for r in rows if _is_str_list(r.get("persons_json"))),
    len(names), sorted(len(n) for n in names)))

print()
print("=== C. 三个入口逐个跑：非空读数 / 不抛异常 ===")
for fn_name, call in (
    ("predict_arrival_time", lambda n: predict_arrival_time(events, n)),
    ("predict_post_arrival_activities", lambda n: predict_post_arrival_activities(events, n)),
    ("predict_daily_routine", lambda n: predict_daily_routine(events, n)),
):
    nonempty = raised = 0
    first_err = ""
    for n in names:
        try:
            out = call(n)
        except Exception as exc:
            raised += 1
            first_err = first_err or "%s: %s" % (type(exc).__name__, exc)
            continue
        if out:
            nonempty += 1
    print("  %-32s 非空=%d/%d 判红=%d %s" % (fn_name, nonempty, len(names), raised, first_err))

print()
print("=== D. data_days 结构化口径（每人：出现天数）===")
for n in names:
    routine = predict_daily_routine(events, n)
    arrival = routine.get("arrival") or {}
    print("  人名长度=%-3d data_days=%-3d arrival.sample_days=%-3d predicted_hour=%s" % (
        len(n), routine.get("data_days", -1), arrival.get("sample_days", -1),
        arrival.get("predicted_hour", "-")))

print()
print("=== E. 时区折算是否改变读数（offset 8 vs 0）===")
changed = 0
for n in names:
    h8 = (predict_arrival_time(events, n, tz_offset_hours=8.0) or {}).get("predicted_hour")
    h0 = (predict_arrival_time(events, n, tz_offset_hours=0.0) or {}).get("predicted_hour")
    if h8 is not None and h0 is not None and h8 != h0:
        changed += 1
print("  读数随偏移改变的人数=%d / %d（>0 证明 `tz_offset_hours` 不是装饰参数）" % (changed, len(names)))
print("PROBE_DONE")
