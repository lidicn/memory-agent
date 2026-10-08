"""设备 feed 干跑对照 v2：连续 7 个整日的三门槛通过率 + count 门槛（60s/3 次）的真实可达性。

只在生产库快照副本上跑（生产连接 mode=ro，写入目标是 /tmp 副本）。
"""
import json
import sqlite3
import sys

SRC = "/tmp/vs30/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

PROD = "/data/memory_agent.db"
SNAP = "/tmp/feed_probe2.db"

src = sqlite3.connect(f"file:{PROD}?mode=ro", uri=True)
dst = sqlite3.connect(SNAP)
src.backup(dst)
dst.close()
src.close()

from memory_agent.config import Config                    # noqa: E402
from memory_agent.device_feed import (                     # noqa: E402
    FEED_DOMAINS, DeviceEventFeed, binary_change, tags_of)
from memory_agent.rule_engine import ActiveRuleEngine      # noqa: E402
from memory_agent.store import Store                       # noqa: E402

cfg = Config.load()
tz = float(getattr(cfg, "tz_offset_hours", 8) or 8)
st = Store(SNAP, tz_offset_hours=tz)
conn = sqlite3.connect(SNAP)
conn.row_factory = sqlite3.Row

days = [str(r["day"]) for r in conn.execute(
    "SELECT day FROM events WHERE substr(ts, 12, 2)='23' GROUP BY day "
    "ORDER BY day DESC LIMIT 7")]
feed = DeviceEventFeed(st, ActiveRuleEngine(st), config=cfg)
names = feed._name_map()

per_day = []
all_kept = []
for day in sorted(days):
    day_total = int(conn.execute(
        "SELECT COUNT(*) FROM events WHERE day=?", (day,)).fetchone()[0])
    sensor = int(conn.execute(
        "SELECT COUNT(*) FROM events WHERE day=? AND domain='sensor'", (day,)).fetchone()[0])
    scanned = kept = 0
    truncated = 0
    for h in range(24):
        start = f"{day}T{h:02d}:00:00"
        end = f"{day}T23:59:59" if h == 23 else f"{day}T{h+1:02d}:00:00"
        res = feed.run_once(start=start, end=end, dry_run=True)
        scanned += int(res["scanned"])
        kept += int(res["kept"])
        if res["truncated"]:
            truncated += 1
    for row in conn.execute(
            "SELECT * FROM events WHERE day=? AND domain IN ({}) "
            "ORDER BY ts".format(",".join("?" * len(FEED_DOMAINS))),
            (day, *FEED_DOMAINS)):
        if binary_change(dict(row)):
            all_kept.append(dict(row))
    per_day.append({
        "day": day,
        "total": day_total,
        "sensor_share": round(sensor / day_total, 4) if day_total else None,
        "whitelist_scanned": scanned,
        "binary_kept": kept,
        "overall_share": round(kept / day_total, 5) if day_total else None,
        "truncated_hours": truncated,
    })

# count 门槛（window_seconds=60 / min_count=3）在真实 kept 流量里的可达性
by_entity: dict[str, list] = {}
for r in all_kept:
    by_entity.setdefault(str(r.get("entity_id") or ""), []).append(str(r.get("ts") or ""))

def epoch(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()

max_in_window = {}
for eid, ts_list in by_entity.items():
    ts_sorted = sorted(ts_list)
    best = 0
    i = 0
    for j in range(len(ts_sorted)):
        while epoch(ts_sorted[j]) - epoch(ts_sorted[i]) >= 60:
            i += 1
        best = max(best, j - i + 1)
    max_in_window[eid] = best

reachable = sorted([e for e, c in max_in_window.items() if c >= 3])

# 出境面纪律：实体 id 里若含成员姓名，一律打码后再进读数（姓名是本仓的 PII 红线）
_names_for_mask = {str(m.get("name") or "") for m in st.list_members() if m.get("name")}
_names_for_mask |= {str(v or "") for v in names.values() if v}
_masked = sorted((n for n in _names_for_mask if len(n) >= 2), key=len, reverse=True)

def mask(eid: str) -> str:
    low = eid.lower()
    for n in _masked:
        if n.lower() in low:
            return eid.replace(n, "**")
    return eid

max_window_masked = {mask(k): v for k, v in sorted(
    max_in_window.items(), key=lambda kv: -kv[1])[:15]}
out = {
    "days": per_day,
    "days_count": len(per_day),
    "kept_rows_collected": len(all_kept),
    "sum_of_run_once_kept": sum(d["binary_kept"] for d in per_day),
    "distinct_entities_in_feed": len(by_entity),
    "max_events_per_entity_within_60s": max_window_masked,
    "entities_reachable_by_count_gate_3in60s": len(reachable),
    "reachable_entity_ids_masked": [mask(e) for e in reachable][:20],
    "tags_in_feed": sorted({t for eid in by_entity for t in tags_of(eid, names.get(eid, ""))}),
    "active_rules": int(conn.execute("SELECT COUNT(*) FROM active_rules").fetchone()[0]),
    "candidate_rules": int(conn.execute("SELECT COUNT(*) FROM candidate_rules").fetchone()[0]),
}
print(json.dumps(out, ensure_ascii=False, indent=2))
st.close()
