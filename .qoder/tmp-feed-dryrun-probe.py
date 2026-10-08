"""设备 feed 第 3 步②验收的干跑对照：三道降噪门槛各自的通过率。

判据全部取自 `device_feed` 自己的实现（FEED_DOMAINS / binary_change / run_once），
不另造口径。**只在生产库的快照副本上跑**——生产连接以 mode=ro 打开，
写入目标是 /tmp 的副本，绝不碰生产库。
"""
import json
import sqlite3
import sys

SRC = "/tmp/vs30/src"
if SRC not in sys.path:
    sys.path.insert(0, SRC)

PROD = "/data/memory_agent.db"
SNAP = "/tmp/feed_probe.db"

src = sqlite3.connect(f"file:{PROD}?mode=ro", uri=True)
dst = sqlite3.connect(SNAP)
src.backup(dst)
dst.close()
src.close()

from memory_agent.config import Config          # noqa: E402
from memory_agent.device_feed import (           # noqa: E402
    DEVICE_TRIGGER, FEED_DOMAINS, DeviceEventFeed, WINDOW_SECONDS, MIN_COUNT)
from memory_agent.rule_engine import ActiveRuleEngine  # noqa: E402
from memory_agent.store import Store             # noqa: E402

cfg = Config.load()
tz = float(getattr(cfg, "tz_offset_hours", 8) or 8)
st = Store(SNAP, tz_offset_hours=tz)

conn = sqlite3.connect(SNAP)
conn.row_factory = sqlite3.Row

# ── 选一个"过完了整天"的日期：最后一个 hour=23 有数据的自然日 ──
day = conn.execute(
    "SELECT day FROM events WHERE substr(ts, 12, 2)='23' GROUP BY day ORDER BY day DESC LIMIT 1"
).fetchone()
if day is None:
    print("NO_FULL_DAY")
    sys.exit(0)
day = str(day["day"])

day_total = int(conn.execute(
    "SELECT COUNT(*) FROM events WHERE day=?", (day,)).fetchone()[0])
by_domain = {r["domain"]: int(r["c"]) for r in conn.execute(
    "SELECT domain, COUNT(*) AS c FROM events WHERE day=? GROUP BY domain ORDER BY c DESC",
    (day,))}

feed = DeviceEventFeed(st, ActiveRuleEngine(st), config=cfg)

scanned = kept = matched = dispatched = logged_only = 0
truncated_hours = []
errors = []
tag_seen = set()
for h in range(24):
    start = f"{day}T{h:02d}:00:00"
    end = f"{day}T23:59:59" if h == 23 else f"{day}T{h+1:02d}:00:00"
    res = feed.run_once(start=start, end=end, dry_run=True)
    scanned += int(res["scanned"])
    kept += int(res["kept"])
    matched += int(res["matched"])
    dispatched += int(res["dispatched"])
    logged_only += int(res["logged_only"])
    errors += list(res["errors"])
    if res["truncated"]:
        truncated_hours.append((start, int(res["scanned"])))
    obs = feed.observed_tags(start, end)
    tag_seen |= set(obs["tags"])
    if not obs["complete"]:
        errors.append(f"observed_tags truncated {start}")

active_rules = len(conn.execute("SELECT 1 FROM active_rules").fetchall())
candidates = int(conn.execute("SELECT COUNT(*) FROM candidate_rules").fetchone()[0])
history_rows = int(conn.execute("SELECT COUNT(*) FROM rule_trigger_history").fetchone()[0])

out = {
    "day": day,
    "day_total_events_all_domains": day_total,
    "by_domain": by_domain,
    "feed_domains_whitelist": list(FEED_DOMAINS),
    "gate1_scanned_whitelist": scanned,
    "gate2_kept_binary": kept,
    "gate1_pass_share": round(scanned / day_total, 4) if day_total else None,
    "gate2_pass_share": round(kept / scanned, 4) if scanned else None,
    "overall_pass_share": round(kept / day_total, 4) if day_total else None,
    "matched": matched,
    "dispatched": dispatched,
    "logged_only": logged_only,
    "truncated_hours": truncated_hours,
    "errors": errors[:5],
    "error_count": len(errors),
    "observed_tags": sorted(tag_seen),
    "active_rules": active_rules,
    "candidate_rules": candidates,
    "rule_trigger_history_rows": history_rows,
    "trigger_written_into_rules": DEVICE_TRIGGER,
    "window_seconds": WINDOW_SECONDS,
    "min_count": MIN_COUNT,
    "prod_device_feed_enabled": bool(getattr(cfg, "device_feed_enabled", False)),
    "prod_device_feed_dry_run": bool(getattr(cfg, "device_feed_dry_run", True)),
    "prod_device_feed_interval_seconds": int(getattr(cfg, "device_feed_interval_seconds", 3600)),
}
print(json.dumps(out, ensure_ascii=False, indent=2))
st.close()
