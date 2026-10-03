#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DCD R2：``agent_memories.text`` 存量明文姓名回填（裁定 20261001-DB六格与MA五题 §四 R2）。

裁定给了三条硬要求，逐字对应本脚本的三个动作：
  1. **先做带时间戳的库备份** → 写到 ``<db>.preR2-<墙钟戳>.db``，备份文件自己必须
     ``PRAGMA integrity_check`` 通过；备份失败或校验非 ok 一律不进写路径（没有退路就不动手）。
  2. **安排在停机窗口** → 必须显式带 ``--window-ok``。不带就只做只读点数，一条都不改。
     窗口是 SP 排的，脚本不替人决定"现在是不是停机窗"。
  3. **回填后逐条验证** → 全表重扫名册姓名，命中即 RC=1（残留 = 这次回填没做成，别当成功报）。

脱敏走的是**生产写入用的同一条 sanitizer**（``Store.sanitize_feedback_text`` → ``_sanitize_pii``），
不在这里另造一套规则：另造的存量口径会和增量长期不一致。

报告只打 memory_id / 命中名册序号 / 文本长度 / 前后 sha8，**不打印任何原文**——明文姓名正是
这次要消掉的东西，把它抄进日志等于换个地方继续泄露。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for cand in ("/app/src", os.path.normpath(os.path.join(_HERE, "..", "src"))):
    if os.path.isdir(cand) and cand not in sys.path:
        sys.path.insert(0, cand)

from memory_agent.store import Store, now_local  # noqa: E402


def _sha8(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


def roster_names(conn: sqlite3.Connection) -> list:
    """脱敏名册，序号 = ``members ORDER BY created_at`` 的位置（与 ``Store.list_members`` 同序）。

    序号必须与线上 sanitizer 一致，否则回填产出的 ``成员N`` 会跟新写入的 ``成员N`` 指不同的人。
    """
    return [r[0] for r in conn.execute(
        "SELECT name FROM members ORDER BY created_at").fetchall()]


def find_hits(conn: sqlite3.Connection, names: list) -> list:
    """text 里含名册姓名（≥2 字，与 ``_sanitize_pii`` 的门同一口径）的行。"""
    effective = [str(n or "").strip() for n in names]
    rows = conn.execute(
        "SELECT memory_id, text, state FROM agent_memories "
        "WHERE text IS NOT NULL AND text != '' ORDER BY memory_id").fetchall()
    hits = []
    for r in rows:
        text = r["text"]
        idx = [i for i, n in enumerate(effective, 1) if len(n) >= 2 and n in text]
        if idx:
            hits.append({"memory_id": r["memory_id"], "state": r["state"],
                         "roster_idx": idx, "chars": len(text),
                         "before_sha": _sha8(text)})
    return hits


def count_leftover(conn: sqlite3.Connection, names: list) -> int:
    """验证步：回填后全表还剩几行明文姓名（不含 sha 之外的任何内容）。"""
    return len(find_hits(conn, names))


def make_backup(db_path: str) -> tuple:
    """带时间戳的 ``sqlite3`` 在线备份 + 备份自身完整性校验。返回 (路径, sha8)。"""
    stamp = now_local(8.0).strftime("%Y%m%d-%H%M%S")
    target = f"{db_path}.preR2-{stamp}.db"
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    dst = sqlite3.connect(target, timeout=30.0)
    try:
        src.backup(dst)
    finally:
        dst.commit()
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        src.close()
    if check != "ok":
        raise RuntimeError(f"备份完整性校验未通过：{check}（备份留在 {target} 供取证）")
    with open(target, "rb") as fh:
        return target, _sha8(fh.read(4096) + str(os.path.getsize(target)).encode())


def plan_writes(conn: sqlite3.Connection, hits: list, store: Store) -> list:
    """对命中行跑生产 sanitizer，返回真正会变（new != old）的 ``(memory_id, new_text)``。"""
    out = []
    for h in hits:
        old = conn.execute("SELECT text FROM agent_memories WHERE memory_id=?",
                           (h["memory_id"],)).fetchone()["text"]
        new = store.sanitize_feedback_text(old)
        if new != old:
            out.append((h["memory_id"], new))
    return out


def apply_backfill(store: Store, writes: list) -> int:
    """单事务写回。只改 text / updated_at，并置 mirror_dirty=1 让向量镜像逐条自愈。"""
    ts = now_local(store.tz_offset_hours).isoformat(timespec="seconds")
    with store.transaction() as conn:
        for memory_id, new_text in writes:
            conn.execute(
                "UPDATE agent_memories SET text=?, updated_at=?, mirror_dirty=1 "
                "WHERE memory_id=?", (new_text, ts, memory_id))
    return len(writes)


def reconcile_mirrors(store: Store) -> dict:
    """逐条重新镜像（不是全量重建）：R2 备注里的 12 分钟冷启动只在镜像整体不可信时才需要。"""
    from memory_agent.config import get_config
    from memory_agent.history import HistoryManager
    from memory_agent.agent_memory import AgentMemoryService
    cfg = get_config()
    svc = AgentMemoryService(cfg, store, HistoryManager(cfg))
    before = len(store.list_dirty_agent_mirrors())
    res = svc.reconcile()
    return {"dirty_before": before, "reconcile": res,
            "dirty_after": len(store.list_dirty_agent_mirrors())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DCD R2 存量明文姓名回填（默认只读点数）")
    ap.add_argument("--db", default="", help="库路径，缺省取 Config.db_path")
    ap.add_argument("--apply", action="store_true", help="真写（必须先备份成功）")
    ap.add_argument("--window-ok", action="store_true",
                    help="确认当前处于 SP 排的停机窗口；不传则拒绝 --apply")
    ap.add_argument("--reconcile", action="store_true", help="写完后逐条重刷向量镜像")
    ap.add_argument("--max-report", type=int, default=20)
    args = ap.parse_args(argv)

    store = Store(args.db) if args.db else Store()
    conn = store.connect()
    names = roster_names(conn)
    total_rows = conn.execute("SELECT COUNT(*) FROM agent_memories").fetchone()[0]
    hits = find_hits(conn, names)

    print(f"[R2] db={store.db_path}")
    print(f"[R2] 名册成员={len(names)}（有效姓名≥2字 {sum(1 for n in names if len(str(n or '').strip()) >= 2)} 个）"
          f" agent_memories={total_rows} 行")
    print(f"[R2] 命中明文姓名={len(hits)} 行；按状态分布 "
          f"{sorted({h['state'] for h in hits})}")
    for h in hits[:max(0, args.max_report)]:
        print(f"    {h['memory_id']} state={h['state']} 命中序号={h['roster_idx']} "
              f"长度={h['chars']} before_sha={h['before_sha']}")
    if len(hits) > args.max_report:
        print(f"    …其余 {len(hits) - args.max_report} 行省略（--max-report 调）")

    if not args.apply:
        print("[R2] 只读模式（未带 --apply）：一条都没改。")
        return 0
    if not args.window_ok:
        print("[R2] 拒绝写入：裁定第 2 条要求停机窗口，缺 --window-ok 视为窗口未开。")
        return 2

    target, sha = make_backup(store.db_path)
    print(f"[R2] 备份完成 {os.path.basename(target)} sha8={sha}（裁定第 1 条）")

    writes = plan_writes(conn, hits, store)
    written = apply_backfill(store, writes)
    print(f"[R2] 写回 {written} 行（命中 {len(hits)} 行，其余 sanitizer 判定无变化；mirror_dirty 已置 1）")

    if args.reconcile:
        try:
            print(f"[R2] 镜像自愈：{reconcile_mirrors(store)}")
        except Exception as exc:
            print(f"[R2] ⚠️ 镜像自愈失败（SQLite 已是脱敏态，向量面仍留明文）：{type(exc).__name__}: {exc}")
            print("[R2]    必须在窗口内补跑 --reconcile 或全量重建，否则 R2 只算做了一半。")

    # 裁定第 3 条：逐条验证，不留明文
    conn = store.connect()
    leftover = count_leftover(conn, roster_names(conn))
    print(f"[R2] 验证：回填后再扫全表，明文姓名残留={leftover} 行")
    if leftover:
        print("[R2] ❌ 未通过：仍有明文姓名，按裁定这不算完成。")
        return 1
    print("[R2] ✅ 通过：全表已无明文成员姓名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
