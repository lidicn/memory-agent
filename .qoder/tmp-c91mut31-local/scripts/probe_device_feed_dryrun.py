#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备 feed 干跑对照探针 —— 计划卡第 3 步②「实际进入 feed ≈2k/天」的首次实测。

**只在生产库的快照副本上跑**：生产连接以 `mode=ro` 打开，写入目标是 `/tmp` 下的一次性副本。
`run_once(dry_run=True)` 本身不派发副作用，但它的匹配端会写 `rule_trigger_history`，
所以任何情况都不直接把 `--db` 指到生产库上跑。

判据全部取自 `device_feed` 自己的实现（`FEED_DOMAINS` / `binary_change` / `run_once`），
不在此处另造一套口径。输出三段：

1. 逐日三道门槛的通过率（domain 白名单 → 二元翻转 → 进 feed）；
2. count 门槛（60 秒 3 次）在真实 feed 流量里的可达性——多少实体够得着；
3. 采集连续性（观察期按自然日计天数时，断档日会被当成低活跃日压低误报率）。

出境纪律：实体 id 里若含成员姓名或设备友好名，一律打码后再进读数。

用法（容器内）：
    PYTHONPATH=/app/src python scripts/probe_device_feed_dryrun.py --days 7
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

DEFAULT_DB = "/data/memory_agent.db"


def snapshot(prod_db: str) -> str:
    """生产库 -> /tmp 副本。生产连接只读打开，副本才是本次所有写动作的靶子。"""
    fd, path = tempfile.mkstemp(prefix="ma-feed-probe-", suffix=".db")
    os.close(fd)
    os.remove(path)
    src = sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return path


def full_days(conn, limit: int) -> list[str]:
    """取"有 23 时段事件"的最近 N 个自然日（没跑完的一天不拿来算日均）。"""
    rows = conn.execute(
        "SELECT day FROM events WHERE substr(ts, 12, 2)='23' GROUP BY day "
        "ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
    return sorted(str(r[0]) for r in rows)


def mask_factory(store, names: dict):
    """实体 id 打码：命中成员姓名或设备友好名的部分换成 **。"""
    tokens = {str(m.get("name") or "") for m in store.list_members() if m.get("name")}
    tokens |= {str(v or "") for v in names.values() if v}
    ordered = sorted((t for t in tokens if len(t) >= 2), key=len, reverse=True)

    def mask(eid: str) -> str:
        low = eid.lower()
        for token in ordered:
            if token.lower() in low:
                return eid.replace(token, "**")
        return eid
    return mask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="生产库路径（只读快照的源）")
    ap.add_argument("--days", type=int, default=7, help="干跑对照的自然日数")
    ap.add_argument("--keep-snapshot", action="store_true", help="保留 /tmp 副本便于复跑")
    args = ap.parse_args()

    from memory_agent.config import Config
    from memory_agent.device_feed import FEED_DOMAINS, DeviceEventFeed, binary_change, tags_of
    from memory_agent.rule_engine import ActiveRuleEngine
    from memory_agent.store import Store

    snap = snapshot(args.db)
    cfg = Config.load()
    store = Store(snap, tz_offset_hours=float(getattr(cfg, "tz_offset_hours", 8) or 8))
    conn = sqlite3.connect(snap)
    feed = DeviceEventFeed(store, ActiveRuleEngine(store), config=cfg)
    names = feed._name_map()
    mask = mask_factory(store, names)

    days = full_days(conn, max(1, args.days))
    per_day: list[dict] = []
    kept_rows: list[tuple[str, str]] = []      # (entity_id, ts)
    for day in days:
        total = int(conn.execute(
            "SELECT COUNT(*) FROM events WHERE day=?", (day,)).fetchone()[0])
        sensor = int(conn.execute(
            "SELECT COUNT(*) FROM events WHERE day=? AND domain='sensor'", (day,)).fetchone()[0])
        scanned = kept = truncated = matched = dispatched = logged = 0
        errors: list[str] = []
        for hour in range(24):
            # 探针自己的窗口一律 `hh:00:00 ~ hh:59:59`：`ts BETWEEN` 两端闭区间，
            # 用"下一小时整点"当终点会把整点那一秒的事件在相邻两窗各数一次——
            # 那正是 `e3901f9` 在水位线路径上修掉的形状，探针不能自己再犯一遍。
            start = f"{day}T{hour:02d}:00:00"
            end = f"{day}T{hour:02d}:59:59"
            res = feed.run_once(start=start, end=end, dry_run=True)
            scanned += int(res["scanned"])
            kept += int(res["kept"])
            truncated += int(bool(res["truncated"]))
            matched += int(res["matched"])
            dispatched += int(res["dispatched"])
            logged += int(res["logged_only"])
            errors += list(res["errors"])
        for row in conn.execute(
                "SELECT entity_id, ts, old_state, new_state FROM events WHERE day=? "
                "AND domain IN ({}) ORDER BY ts".format(
                    ",".join("?" * len(FEED_DOMAINS))), (day, *FEED_DOMAINS)):
            if binary_change({"entity_id": row[0], "old_state": row[2],
                              "new_state": row[3]}):
                kept_rows.append((str(row[0]), str(row[1])))
        per_day.append({
            "day": day,
            "events_total": total,
            "sensor_share": round(sensor / total, 4) if total else None,
            "gate1_domain_whitelist": scanned,
            "gate2_binary_flip": kept,
            "feed_share": round(kept / total, 5) if total else None,
            "truncated_windows": truncated,
            "matched": matched,
            "dispatched": dispatched,
            "logged_only": logged,
            "errors": errors[:3],
            "error_count": len(errors),
        })

    # ── count 门槛（60 秒 / 3 次）的真实可达性 ──
    by_entity: dict[str, list[float]] = {}
    for eid, ts in kept_rows:
        try:
            by_entity.setdefault(eid, []).append(datetime.fromisoformat(ts).timestamp())
        except ValueError:
            continue
    max_in_window = {}
    for eid, stamps in by_entity.items():
        stamps.sort()
        best = i = 0
        for j in range(len(stamps)):
            while stamps[j] - stamps[i] >= 60:
                i += 1
            best = max(best, j - i + 1)
        max_in_window[eid] = best
    reachable = sorted(eid for eid, cnt in max_in_window.items() if cnt >= 3)

    # ── 采集连续性：整天 / 断档小时 ──
    continuity = []
    for row in conn.execute(
            "SELECT day, COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT substr(ts,12,2)) "
            "FROM events GROUP BY day ORDER BY day DESC LIMIT 10"):
        continuity.append({"day": str(row[0]), "events": int(row[1]),
                           "first_ts": str(row[2]), "last_ts": str(row[3]),
                           "hours_seen": int(row[4])})

    print(json.dumps({
        "db_source": args.db,
        "snapshot": snap,
        "days_measured": len(per_day),
        "per_day": per_day,
        "feed_domains_whitelist": list(FEED_DOMAINS),
        "daily_feed_mean": (round(sum(d["gate2_binary_flip"] for d in per_day)
                                  / max(1, len(per_day)), 1)),
        "count_gate": {
            "window_seconds": 60, "min_count": 3,
            "entities_in_feed": len(by_entity),
            "entities_reachable": len(reachable),
            "reachable_entity_ids_masked": [mask(e) for e in reachable][:20],
            "top_max_per_60s": {mask(k): v for k, v in sorted(
                max_in_window.items(), key=lambda kv: -kv[1])[:10]},
        },
        "tags_in_feed": sorted({t for eid in by_entity
                                for t in tags_of(eid, names.get(eid, ""))}),
        "active_rules": int(conn.execute("SELECT COUNT(*) FROM active_rules").fetchone()[0]),
        "candidate_rules": int(conn.execute(
            "SELECT COUNT(*) FROM candidate_rules").fetchone()[0]),
        "prod_switches": {
            "device_feed_enabled": bool(getattr(cfg, "device_feed_enabled", False)),
            "device_feed_dry_run": bool(getattr(cfg, "device_feed_dry_run", True)),
            "device_feed_interval_seconds": int(
                getattr(cfg, "device_feed_interval_seconds", 3600)),
        },
        "collection_continuity": continuity,
    }, ensure_ascii=False, indent=2))

    store.close()
    if args.keep_snapshot:
        print(f"# snapshot kept at {snap}", file=sys.stderr)
    else:
        os.remove(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
