"""学习闭环 · SQLite 持久化（学习信号 / 参数变更审计 / 参数现值）。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Sequence

from learning_models import FeedbackKind, FeedbackSignal, ReasonCode, SubjectType
from learning_optimizer import ParamAdjustment

from .store import safe_json_loads


def _safe_json_loads(raw, default=None):
    """NEW-P1-1：安全解析 JSON，失败返回默认值（委托 store 版本，坏数据留 WARNING）。"""
    return safe_json_loads(raw, default)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS learning_feedback (
    feedback_id     TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    subject_type    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    valence         REAL NOT NULL,
    confidence      REAL NOT NULL,
    member_id       TEXT,
    room            TEXT,
    reason          TEXT NOT NULL,
    repeat_count    INTEGER NOT NULL DEFAULT 1,
    context_json    TEXT NOT NULL DEFAULT '{}',
    context_dropped INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON learning_feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_subject ON learning_feedback(subject_type, subject_id);

CREATE TABLE IF NOT EXISTS learning_adjustment (
    adjustment_id TEXT PRIMARY KEY,
    param         TEXT NOT NULL,
    old_value     REAL NOT NULL,
    new_value     REAL NOT NULL,
    reason        TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    mode          TEXT NOT NULL,
    verdict       TEXT,
    rolled_back   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    evaluated_at  TEXT
);

CREATE TABLE IF NOT EXISTS learning_param_state (
    param      TEXT PRIMARY KEY,
    value      REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


class LearningStore:
    """薄仓储层；核心逻辑保持纯函数，I/O 全部收敛在这里。"""

    def __init__(self, db_path: str = "memoryagent.db") -> None:
        # NEW-P0-1：加 timeout + busy_timeout，与主库 store.py 对齐，防并发写 database is locked
        # 第六轮审计 CRITICAL-2：连接是本实例的单例共享对象，FastAPI 会在多个线程里调用它；
        # execute 与 commit 之间无锁 → 甲线程的 commit 会把乙线程未写完的半截事务一并提交。
        # 与 Store._db()/Store.transaction() 同一口径，锁住"语句 + 提交"整段。
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    @contextmanager
    def _db(self):
        """持锁连接区；正常退出提交、异常回滚（与 Store.transaction() 同语义）。
        只读语句也走这里：无待提交事务时 commit() 是空操作，换取单一入口。
        """
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception as rb_exc:
                    # 回滚失败说明连接本身已断；记录后仍上抛原始异常
                    logging.getLogger(__name__).warning(
                        "[LearningStore] 事务回滚失败: %s", rb_exc)
                raise

    # -- 反馈信号 ---------------------------------------------------------
    def insert_signals(self, signals: Sequence[FeedbackSignal]) -> int:
        rows = [
            (
                s.feedback_id, s.kind.value, s.subject_type.value, s.subject_id,
                s.valence, s.confidence, s.member_id, s.room, s.reason.value,
                s.repeat_count, json.dumps(dict(s.context), ensure_ascii=False),
                1 if s.context_dropped else 0, _iso(s.created_at),
            )
            for s in signals
        ]
        with self._db() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO learning_feedback VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
            )
        return len(rows)

    def list_signals(self, start: datetime, end: datetime) -> list[FeedbackSignal]:
        with self._db() as conn:
            cur = conn.execute(
                "SELECT * FROM learning_feedback WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
                (_iso(start), _iso(end)),
            )
            rows = cur.fetchall()
        out: list[FeedbackSignal] = []
        for row in rows:
            (
                fid, kind, subject_type, subject_id, valence, confidence,
                member_id, room, reason, repeat_count, context_json, dropped, created_at,
            ) = row
            out.append(
                FeedbackSignal(
                    feedback_id=fid,
                    kind=FeedbackKind(kind),
                    subject_type=SubjectType(subject_type),
                    subject_id=subject_id,
                    valence=valence,
                    confidence=confidence,
                    member_id=member_id,
                    room=room,
                    reason=ReasonCode(reason),
                    repeat_count=repeat_count,
                    context=_safe_json_loads(context_json, {}),
                    context_dropped=bool(dropped),
                    created_at=_parse_dt(created_at),
                )
            )
        return out

    # -- 参数变更审计 -----------------------------------------------------
    def insert_adjustment(self, adj: ParamAdjustment) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO learning_adjustment VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    adj.adjustment_id, adj.param, adj.old_value, adj.new_value, adj.reason,
                    json.dumps(list(adj.evidence), ensure_ascii=False), adj.mode, adj.verdict,
                    1 if adj.rolled_back else 0, _iso(adj.created_at), None,
                ),
            )

    def list_adjustments(self, limit: int = 100) -> list[ParamAdjustment]:
        with self._db() as conn:
            cur = conn.execute(
                "SELECT * FROM learning_adjustment ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
        out: list[ParamAdjustment] = []
        for row in rows:
            (
                aid, param, old_v, new_v, reason, evidence_json,
                mode, verdict, rolled_back, created_at, _evaluated_at,
            ) = row
            out.append(
                ParamAdjustment(
                    adjustment_id=aid, param=param, old_value=old_v, new_value=new_v,
                    reason=reason, evidence=tuple(_safe_json_loads(evidence_json, [])), mode=mode,
                    created_at=_parse_dt(created_at), verdict=verdict,
                    rolled_back=bool(rolled_back),
                )
            )
        return out

    def mark_evaluated(self, adjustment_id: str, verdict: str, now: datetime) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE learning_adjustment SET verdict = ?, evaluated_at = ? WHERE adjustment_id = ?",
                (verdict, _iso(now), adjustment_id),
            )

    def mark_rolled_back(self, adjustment_id: str) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE learning_adjustment SET rolled_back = 1 WHERE adjustment_id = ?", (adjustment_id,)
            )

    # -- 参数现值 ---------------------------------------------------------
    def param_values(self) -> dict[str, float]:
        with self._db() as conn:
            cur = conn.execute("SELECT param, value FROM learning_param_state")
            return {row[0]: float(row[1]) for row in cur.fetchall()}

    def set_param(self, param: str, value: float, now: datetime) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO learning_param_state(param, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(param) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (param, float(value), _iso(now)),
            )

    def last_adjusted_at(self) -> dict[str, datetime]:
        with self._db() as conn:
            cur = conn.execute("SELECT param, MAX(created_at) FROM learning_adjustment GROUP BY param")
            return {row[0]: _parse_dt(row[1]) for row in cur.fetchall()}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
