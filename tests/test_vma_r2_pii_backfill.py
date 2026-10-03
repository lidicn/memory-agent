#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCD R2 回填工具的回归锁（裁定 20261001-DB六格与MA五题 §四 R2 三条硬要求逐条对锁）。

用真实 ``Store``（临时库，不碰生产盘），存量明文姓名用裸 SQL 塞进去——生产那 245 条正是
" sanitizer 之前写进来的"这一形状，走 ``add_agent_memory`` 会被入库脱敏，测不到回填。
"""

from __future__ import annotations

import glob
import importlib.util
import os
import sqlite3

from memory_agent.store import Store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "pii_backfill_r2", os.path.join(REPO, "scripts", "pii_backfill_r2.py"))
r2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r2)

NAME_A = "张三"
NAME_B = "李四"


def _seed(db_path: str) -> Store:
    """两个成员（created_at 决定 成员N 序号）+ 4 条记忆：2 条含明文姓名、1 条不含、1 条含手机号。"""
    store = Store(db_path)
    store.init_schema()
    conn = store.connect()
    conn.executemany(
        "INSERT INTO members (id, name, created_at, updated_at) VALUES (?,?,?,?)",
        [("m1", NAME_A, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
         ("m2", NAME_B, "2026-02-01T00:00:00", "2026-02-01T00:00:00")])
    rows = [
        ("a1", "sess-1", "客厅", "张三喜欢在半成品模式下看电影", "live"),
        ("a2", "sess-2", "卧室", "李四把空调调到 26 度", "staging"),
        ("a3", "sess-3", "通用", "家里整体偏安静，没有成员相关线索", "live"),
        ("a4", "sess-4", "联系", "张三的手机号是 13812345678", "staging"),
    ]
    conn.executemany(
        """INSERT INTO agent_memories
           (memory_id, session_id, text, topic_key, state, created_at, updated_at, expires_at)
           VALUES (?,?,?,?,?,'2026-09-01T00:00:00','2026-09-01T00:00:00','9999-12-31')""",
        [(mid, sess, text, topic, state) for mid, sess, topic, text, state in rows])
    conn.commit()
    return store


def _texts(store: Store) -> dict:
    return {r["memory_id"]: r["text"] for r in store.connect().execute(
        "SELECT memory_id, text FROM agent_memories ORDER BY memory_id")}


def _backup_files(db_path: str) -> list:
    return glob.glob(f"{db_path}.preR2-*.db")


def test_dry_run_counts_every_hit_and_writes_nothing(tmp_path, capsys):
    db = str(tmp_path / "ma.db")
    store = _seed(db)
    before = _texts(store)

    assert r2.main(["--db", db]) == 0
    report = capsys.readouterr().out
    assert "命中明文姓名=3 行" in report          # a1 / a2 / a4
    assert "只读模式（未带 --apply）" in report
    assert NAME_A not in report and NAME_B not in report   # 报告不复述原文
    assert _texts(store) == before                # 一条都没改
    assert _backup_files(db) == []                # 没写就不该造备份


def test_apply_is_refused_when_the_window_is_not_declared(tmp_path, capsys):
    """裁定第 2 条：停机窗由 SP 排，脚本不替人判"现在是窗口"。"""
    db = str(tmp_path / "ma.db")
    store = _seed(db)
    before = _texts(store)

    assert r2.main(["--db", db, "--apply"]) == 2
    assert "缺 --window-ok" in capsys.readouterr().out
    assert _texts(store) == before
    assert _backup_files(db) == []


def test_apply_backs_up_first_and_the_backup_still_holds_the_plaintext(tmp_path, capsys):
    """裁定第 1 条：先带时间戳备份——备份必须是"还能退回原样"的那份，不是个空壳。"""
    db = str(tmp_path / "ma.db")
    store = _seed(db)

    assert r2.main(["--db", db, "--apply", "--window-ok"]) == 0
    backups = _backup_files(db)
    assert len(backups) == 1, backups
    con = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        kept = {r[0]: r[1] for r in con.execute("SELECT memory_id, text FROM agent_memories")}
    finally:
        con.close()
    assert NAME_A in kept["a1"] and NAME_B in kept["a2"]   # 明文只在备份里活着
    assert NAME_A not in _texts(store)["a1"]               # 生产盘上已经消掉


def test_apply_masks_names_numbers_the_mirror_dirty_and_leaves_clean_rows_alone(tmp_path):
    db = str(tmp_path / "ma.db")
    store = _seed(db)
    conn = store.connect()

    assert r2.main(["--db", db, "--apply", "--window-ok"]) == 0
    texts = _texts(store)
    assert texts["a1"].startswith("成员1")                 # 序号 = members ORDER BY created_at
    assert texts["a2"].startswith("成员2")
    assert "***" in texts["a4"]                             # 同一条 sanitizer：长数字一起打码
    assert texts["a3"] == "家里整体偏安静，没有成员相关线索"  # 无关行一字不动

    dirty = {r["memory_id"]: r["mirror_dirty"] for r in conn.execute(
        "SELECT memory_id, mirror_dirty FROM agent_memories")}
    assert [dirty["a1"], dirty["a2"], dirty["a4"]] == [1, 1, 1]
    assert dirty["a3"] == 0                                 # 没改的行不该被拉去重建镜像


def test_verification_step_goes_red_if_the_sanitizer_is_bypassed(tmp_path, capsys, monkeypatch):
    """反例：脱敏没真做到时，"逐条验证"必须判红，不能报"回填完成"。"""
    db = str(tmp_path / "ma.db")
    store = _seed(db)
    monkeypatch.setattr(Store, "sanitize_feedback_text", lambda self, t: t)

    assert r2.main(["--db", db, "--apply", "--window-ok"]) == 1
    out = capsys.readouterr().out
    assert "明文姓名残留=3 行" in out
    assert "未通过" in out
    assert _texts(store)["a1"] == f"{NAME_A}喜欢在半成品模式下看电影"   # 原样留着，没假装成功


def test_roster_index_matches_the_live_sanitizer_numbering(tmp_path):
    """回填产出的 成员N 必须和新写入产出的 成员N 指同一个人（同一份名册、同一个序）。"""
    db = str(tmp_path / "ma.db")
    store = _seed(db)
    conn = store.connect()
    text = f"{NAME_A}和{NAME_B}都在客厅"
    assert r2.roster_names(conn) == [m.get("name") for m in store.list_members()]
    assert Store._sanitize_pii(text, r2.roster_names(conn)) == store.sanitize_feedback_text(text)
    assert store.sanitize_feedback_text(text) == "成员1和成员2都在客厅"


def test_hits_are_scoped_to_agent_memories_text_only(tmp_path):
    """命中判据只认 text 里的名册姓名；短于 2 字的名册项一律不算（与 _sanitize_pii 同门）。"""
    db = str(tmp_path / "ma.db")
    store = _seed(db)
    conn = store.connect()
    conn.execute("INSERT INTO members (id, name, created_at, updated_at) "
                 "VALUES ('m3','王','2026-03-01T00:00:00','2026-03-01T00:00:00')")
    conn.commit()
    hits = r2.find_hits(conn, r2.roster_names(conn))
    assert {h["memory_id"] for h in hits} == {"a1", "a2", "a4"}
    assert all(3 not in h["roster_idx"] for h in hits)       # 单字"王"不参与替换
