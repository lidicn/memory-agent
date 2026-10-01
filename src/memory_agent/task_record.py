"""持久意图 + 周期归档：Phase 5.3（简化版）。

设计：
- task 表：持久任务定义（如"每日离家时段统计"）
- task_record 表：任务执行记录，日/周/月归档
- 唯一索引幂等：重复触发不产生重复记录
"""

from __future__ import annotations

from typing import Any


# ── 建表 SQL（由 store 初始化时执行）─────────────────────────────────────────────

CREATE_TASK_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    period       TEXT NOT NULL DEFAULT 'daily',  -- daily / weekly / monthly
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

CREATE_TASK_RECORD_TABLE = """
CREATE TABLE IF NOT EXISTS task_records (
    record_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    period_key   TEXT NOT NULL,  -- 如 2026-09-18 / 2026-W38 / 2026-09
    data_json    TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    UNIQUE(task_id, period_key)  -- 幂等：同任务同周期只一条
);
"""


def upsert_task_record(
    store: Any,
    task_id: str,
    period_key: str,
    data: dict,
    now_str: str,
) -> int:
    """幂等写入任务记录：同 task_id+period_key 已存在则更新，不存在则插入。"""
    import json
    data_json = json.dumps(data, ensure_ascii=False)
    conn = store.connect()
    with store._lock:
        # 先查是否存在
        row = conn.execute(
            "SELECT record_id FROM task_records WHERE task_id=? AND period_key=?",
            (task_id, period_key),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE task_records SET data_json=? WHERE record_id=?",
                (data_json, row["record_id"]),
            )
            return int(row["record_id"])
        cur = conn.execute(
            """INSERT INTO task_records(task_id, period_key, data_json, created_at)
               VALUES(?,?,?,?)""",
            (task_id, period_key, data_json, now_str),
        )
        return int(cur.lastrowid)


def archive_daily_records(store: Any, task_id: str, day: str) -> dict:
    """日归档：把某天的记录汇总成周/月归档。"""
    conn = store.connect()
    with store._lock:
        rows = conn.execute(
            "SELECT * FROM task_records WHERE task_id=? AND period_key LIKE ?",
            (task_id, f"{day[:7]}%"),
        ).fetchall()
    total = len(rows)
    return {"task_id": task_id, "month": day[:7], "total_records": total}
