"""审计 P0-11 的回归锁：启动自检不再逐页扫全库，且判红时先复核再回滚。

P0-11 的代价是「启动阻塞随数据量线性增长」——127.9 MB 实测
``integrity_check`` 15228.6 ms / 11699.7 ms，``quick_check`` 891.6 ms。
计时在 CI 上不可信，所以这里断言的是**执行结构**：哪些 PRAGMA 真的跑过、
备份有没有被覆盖。
"""

import os
import sqlite3
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store, _integrity_check_mode  # noqa: E402


@pytest.fixture
def db_path():
    tmp = tempfile.mkdtemp(prefix="ma_integrity_")
    yield os.path.join(tmp, "a.db")


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def _fake_pragmas(store, verdicts):
    """让指定 PRAGMA 直接返回预设结论，并记录每条语句。

    ``verdicts`` 形如 ``{"quick_check": "ok", "integrity_check": "something is wrong"}``。
    真实损坏很难在测试里稳定构造，而这里的判据本来就是"走哪条分支"，
    所以用可编程的 PRAGMA 应答替代物理损坏。
    """
    real_connect = store.connect
    ran = []

    class _ConnProxy:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *a, **kw):
            text = str(sql).strip()
            ran.append(text)
            low = text.lower()
            for name, verdict in verdicts.items():
                if low == f"pragma {name}":
                    return _Cursor(("ok",) if verdict == "ok" else (verdict,))
            return self._conn.execute(sql, *a, **kw)

        def close(self):
            # 回滚前必须真的断开句柄，否则 Windows 上 copy2 覆盖一个被占用的文件
            real = getattr(store, "_conn", None)
            if real is not None:
                real.close()
                store._conn = None
            return None

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def connect():
        return _ConnProxy(real_connect())

    store.connect = connect
    return ran


def _mark(db_path, tag: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS marker(tag TEXT)")
    conn.execute("DELETE FROM marker")
    conn.execute("INSERT INTO marker VALUES (?)", (tag,))
    conn.commit()
    conn.close()


def _read_mark(db_path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT tag FROM marker").fetchone()
    except sqlite3.Error:
        row = None
    conn.close()
    return row[0] if row else ""


# ── 默认档位 ────────────────────────────────────────────────────────────

def test_default_mode_is_quick(db_path, monkeypatch):
    monkeypatch.delenv("MA_DB_INTEGRITY_CHECK", raising=False)
    assert _integrity_check_mode() == "quick"


def test_invalid_env_value_falls_back_to_quick(monkeypatch):
    monkeypatch.setenv("MA_DB_INTEGRITY_CHECK", "aggressive")
    assert _integrity_check_mode() == "quick"


def test_quick_green_never_touches_the_full_scan(db_path, monkeypatch):
    """quick 通过就返回：一次 PRAGMA，全库逐页扫描连问都不问。"""
    monkeypatch.delenv("MA_DB_INTEGRITY_CHECK", raising=False)
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    ran = _fake_pragmas(st, {"quick_check": "ok"})

    res = st.check_and_recover()

    assert res["checked"] is True and res["mode"] == "quick"
    assert res["pragma"] == "quick_check"
    assert res["recovered"] is False and res["error"] is None
    assert [s for s in ran if s.lower().startswith("pragma quick_check")] == \
        ["PRAGMA quick_check"], "健康库上自检被重复执行"
    assert not [s for s in ran if "integrity_check" in s.lower()], \
        f"quick 通过仍跑了逐页扫描（正是 P0-11 的阻塞源）：{ran}"
    st.close()


def test_mode_off_skips_every_pragma(db_path, monkeypatch):
    monkeypatch.setenv("MA_DB_INTEGRITY_CHECK", "off")
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    ran = _fake_pragmas(st, {"quick_check": "ok"})

    res = st.check_and_recover()

    assert res["checked"] is False and res["mode"] == "off"
    assert res["pragma"] is None and res["recovered"] is False
    assert not [s for s in ran if s.lower().startswith("pragma quick_check")], \
        f"off 档不该执行任何自检：{ran}"
    st.close()


def test_full_mode_actually_runs_the_full_scan(db_path, monkeypatch):
    """运维显式要 full 时必须真跑逐页扫描——不能被 quick 的早退吞掉。"""
    monkeypatch.setenv("MA_DB_INTEGRITY_CHECK", "full")
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    ran = _fake_pragmas(st, {"integrity_check": "ok"})

    res = st.check_and_recover()

    assert res["mode"] == "full" and res["pragma"] == "integrity_check"
    assert res["recovered"] is False and res["error"] is None
    assert [s for s in ran if "integrity_check" in s.lower()] == ["PRAGMA integrity_check"]
    assert not [s for s in ran if s.lower().startswith("pragma quick_check")], \
        f"full 档不该再付一次 quick 的代价：{ran}"
    st.close()


# ── 判红后的分支 ────────────────────────────────────────────────────────

def test_quick_red_escalates_and_keeps_the_original(db_path, monkeypatch):
    """quick 判红、full 复核通过 → 不回滚，原件留着人工排查。

    备份文件刻意造得比原件"更新"：如果代码凭 quick 的单方判断就覆盖数据，
    marker 会被写成 backup，这条用例立刻变红。
    """
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    st.close()
    _mark(db_path, "original")
    _mark(db_path + ".bak20260101-000000", "backup")

    st2 = Store(db_path, tz_offset_hours=0.0)
    ran = _fake_pragmas(st2, {"quick_check": "wrong", "integrity_check": "ok"})
    res = st2.check_and_recover()

    assert res["pragma"] == "quick_check+integrity_check"
    assert res["recovered"] is False
    assert "integrity_check 通过" in (res["error"] or "")
    assert _read_mark(db_path) == "original", "复核通过的库被备份覆盖了"
    assert any("integrity_check" in s.lower() for s in ran)
    st2.close()


def test_both_red_restores_the_newest_backup(db_path, monkeypatch):
    """quick + full 双双判红才回滚，并且按 mtime 取最新那份备份。"""
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    st.close()
    _mark(db_path, "corrupt")
    _mark(db_path + ".bak20260101-000000", "old-backup")
    _mark(db_path + ".bak20260901-000000", "new-backup")
    newest = db_path + ".bak20260901-000000"
    os.utime(db_path + ".bak20260101-000000", (1_700_000_000, 1_700_000_000))
    os.utime(newest, (1_750_000_000, 1_750_000_000))

    st2 = Store(db_path, tz_offset_hours=0.0)
    ran = _fake_pragmas(st2, {"quick_check": "wrong",
                             "integrity_check": "*** database corruption ***"})
    res = st2.check_and_recover()

    assert res["recovered"] is True
    assert res["backup_used"] == os.path.basename(newest)
    assert res["pragma"] == "integrity_check"
    assert _read_mark(db_path) == "new-backup", "回滚拿错了备份"
    st2.close()


def test_both_red_without_backup_leaves_db_alone(db_path, monkeypatch):
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    st.close()
    _mark(db_path, "original")

    st2 = Store(db_path, tz_offset_hours=0.0)
    _fake_pragmas(st2, {"quick_check": "wrong", "integrity_check": "still wrong"})
    res = st2.check_and_recover()

    assert res["recovered"] is False
    assert res["error"] == "integrity_check failed: still wrong"
    assert _read_mark(db_path) == "original"
    st2.close()


# ── 自查项：快照找得到吗 ─────────────────────────────────────────────────

def _snapshot(src_db: str, dst: str) -> None:
    """按生产口径造快照：``VACUUM INTO`` 出一个独立、可直接打开的库。"""
    src = sqlite3.connect(src_db)
    try:
        src.execute("VACUUM INTO ?", (dst,))
    finally:
        src.close()


def test_recovery_finds_backups_in_backup_dir(db_path, monkeypatch):
    """``BackupManager`` 写的是 ``backup_dir/ma-<date>.db``，恢复逻辑必须能找到它。

    原先只找 ``<db>.bak*``：两处永不相交，所谓"损坏时自动从最近备份恢复"在生产里
    一次都不会触发（而且 ``backup_enabled`` 默认关，连 ``.bak*`` 也没人产出）。
    """
    tmp = os.path.dirname(db_path)
    bdir = os.path.join(tmp, "backups")
    os.makedirs(bdir, exist_ok=True)

    st = Store(db_path, tz_offset_hours=0.0, backup_dir=bdir)
    st.init_schema()
    st.close()
    _mark(db_path, "corrupt")
    _snapshot(db_path, os.path.join(bdir, "ma-20260930.db"))
    _mark(db_path, "corrupt")                     # 快照之后再把原件弄脏
    old = db_path + ".bak20260101-000000"
    _mark(old, "ancient-bak")
    os.utime(old, (1_600_000_000, 1_600_000_000))  # 明确比 ma-*.db 旧
    os.utime(os.path.join(bdir, "ma-20260930.db"), (1_750_000_000, 1_750_000_000))

    st2 = Store(db_path, tz_offset_hours=0.0, backup_dir=bdir)
    _fake_pragmas(st2, {"quick_check": "wrong", "integrity_check": "wrong"})
    res = st2.check_and_recover()

    assert res["recovered"] is True, f"backup_dir 里的快照没被找到：{res}"
    assert res["backup_used"] == "ma-20260930.db", res
    assert _read_mark(db_path) == "corrupt", "取到的不是最新那份"
    st2.close()


def test_recovery_removes_stale_wal_and_shm(db_path, monkeypatch):
    """回滚必须一起清掉上一世的 ``-wal``/``-shm``。

    需要恢复的库恰恰是"没干净关闭过"的库，也就是最可能留着旧 WAL 的库；
    把新主文件盖上去却留下旧日志，等于让恢复自己制造下一次损坏。
    """
    st = Store(db_path, tz_offset_hours=0.0)
    st.init_schema()
    st.close()
    _mark(db_path, "corrupt")
    _snapshot(db_path, db_path + ".bak20260901-000000")
    _mark(db_path, "corrupt")
    with open(db_path + "-wal", "wb") as fh:
        fh.write(b"\x37\x7d\x06\x3a stale log")
    with open(db_path + "-shm", "wb") as fh:
        fh.write(b"stale shm")

    st2 = Store(db_path, tz_offset_hours=0.0)
    _fake_pragmas(st2, {"quick_check": "wrong", "integrity_check": "wrong"})
    res = st2.check_and_recover()

    assert res["recovered"] is True, res
    assert not os.path.exists(db_path + "-wal"), "旧 -wal 残留会被叠到新库上"
    assert not os.path.exists(db_path + "-shm"), "旧 -shm 残留"
    st2.close()

