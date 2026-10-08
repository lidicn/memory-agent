#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重启后状态字探针：只读，不写库、不打印任何明文 PII。

判据来自计划卡第 2/3 步与交接单 §五-11：R1 两列、R3 两表/新列要在新进程里跑过
init_schema 才存在。这里一律按 schema 在场性判定，不看日志顺序（docker 日志时间戳
是 flush 时刻，不是打印时刻）。
"""

import sqlite3
import sys

DB = "/data/memory_agent.db"
out = []


def cols(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")

    am = cols(conn, "agent_memories")
    out.append(("R1_feedback_question_col", "feedback_question" in am))
    out.append(("R1_feedback_comment_col", "feedback_comment" in am))
    out.append(("R3_rule_lifecycle_audit_table",
                conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='rule_lifecycle_audit'").fetchone() is not None))
    ar = cols(conn, "active_rules")
    out.append(("R3_active_rules_extra_cols", sorted(c for c in ar if c in (
        "promotion_source", "promoted_at", "promoted_by", "observation_until"))))
    out.append(("R3_active_rules_col_count", len(ar)))

    for t in ("behavior_states", "rule_trigger_history", "candidate_rules"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception as exc:
            n = f"ERR {type(exc).__name__}"
        out.append((f"rows:{t}", n))

    out.append(("rows:agent_memories", conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]))
    out.append(("mirror_dirty", conn.execute("SELECT COUNT(*) FROM agent_memories WHERE mirror_dirty=1").fetchone()[0]))
    out.append(("max_event_ts", conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]))
    out.append(("journal_mode", conn.execute("PRAGMA journal_mode").fetchone()[0]))
    out.append(("integrity_quick", conn.execute("PRAGMA integrity_check(1)").fetchone()[0]))
    conn.close()

    for k, v in out:
        print(f"{k} = {v}")

    # homesdk 运行面（裁定 §二 Q1 的 A 形态是否真的在生产在场）
    try:
        import homesdk
        print(f"homesdk = {getattr(homesdk, '__version__', 'present')}")
    except Exception as exc:
        print(f"homesdk = MISSING ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
