"""vMA-1.3 unified_events VIEW 契约测试（真实部署库的现读面）。

验证：
1. VIEW 存在
2. VIEW 行数 = 三源之和 - 过滤行（behavior_events status!='ok'）
3. 字段语义正确（source 分布、event_type 非空）

本文件面向 **MA_TEST_DB 指向的在役库**，因此它天然是和采集器并发读的：
确定性契约（建库/播种/手工 UNION ALL 对拍）在 `test_vma13_unified_events_contract.py`，
这里只保证一件事——现役库的视图与源表口径对得上，且**不因并发写入而假红**。

三张源表在持续写入，跨语句的等式比较必须出自同一条 SELECT：SQLite 的读快照
以语句为边界。容器回归 c9 的 `1003987 != 1004477` 就是这么来的——四条独立 COUNT
之间被插进了几百行，等式破的不是契约，是读法。
"""
import sqlite3
import os
import sys
import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))


DB_PATH = os.environ.get("MA_TEST_DB", "/data/memory_agent.db")

#: 视图与三源的计数，全部塞进**一条**语句。
#: 顺带把 source 的取值域也读出来：`NOT IN (三源)` 计数为 0 等价于
#: 「GROUP BY 没吐出第四个值」，但不需要第二条语句。
_COUNTS_SQL = """
SELECT
    (SELECT COUNT(*) FROM unified_events)                           AS view_total,
    (SELECT COUNT(*) FROM events)                                   AS device_total,
    (SELECT COUNT(*) FROM behavior_events WHERE status='ok')        AS vision_ok,
    (SELECT COUNT(*) FROM perception_events)                        AS perception_total,
    (SELECT COUNT(*) FROM unified_events WHERE source='device')     AS view_device,
    (SELECT COUNT(*) FROM unified_events WHERE source='vision')     AS view_vision,
    (SELECT COUNT(*) FROM unified_events
       WHERE source='perception')                                   AS view_perception,
    (SELECT COUNT(*) FROM unified_events
       WHERE source NOT IN ('device','vision','perception'))        AS view_other_source
"""


def _counts(conn) -> dict:
    """一次读快照内的八项计数。多一条语句就多一个竞窗口，所以只有一条。"""
    row = conn.execute(_COUNTS_SQL).fetchone()
    keys = ("view_total", "device_total", "vision_ok", "perception_total",
            "view_device", "view_vision", "view_perception", "view_other_source")
    return dict(zip(keys, row))


@pytest.fixture(scope="module")
def conn():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"DB not found: {DB_PATH}")
    c = sqlite3.connect(DB_PATH)
    # 该测试面向真实部署库（MA_TEST_DB）：若文件不是 memory-agent 库
    # （缺源表 events，例如本地碰巧存在的空/无关 sqlite 文件），跳过而非误报。
    src_tables = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('events','behavior_events','perception_events')"
    ).fetchall()}
    if not {"events", "behavior_events", "perception_events"} <= src_tables:
        c.close()
        pytest.skip(f"{DB_PATH} is not a memory-agent store DB (missing source tables)")
    yield c
    c.close()


def test_view_exists(conn):
    """VIEW 必须存在。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='unified_events'"
    ).fetchone()
    assert row is not None, "unified_events VIEW does not exist"


def test_row_count_contract(conn):
    """VIEW 行数 = events + behavior_events(status='ok') + perception_events。"""
    c = _counts(conn)
    expected = c["device_total"] + c["vision_ok"] + c["perception_total"]
    assert c["view_total"] == expected, (
        f"VIEW row count mismatch: {c['view_total']} != {expected} "
        f"(events={c['device_total']}, vision_ok={c['vision_ok']}, "
        f"perception={c['perception_total']})"
    )
    # 视图自身的分片必须闭合，否则「总数对得上」可能只是两处漏行相互抵消
    assert c["view_total"] == (
        c["view_device"] + c["view_vision"] + c["view_perception"] + c["view_other_source"]
    )


def test_source_distribution(conn):
    """source 只能是 device/vision/perception，且与三源行数对应（同一条语句读全）。"""
    c = _counts(conn)

    assert c["view_other_source"] == 0, (
        f"{c['view_other_source']} rows have source outside device/vision/perception"
    )

    # 各 source 行数与源表一致
    assert c["view_device"] == c["device_total"], (
        f"device 分支漏行/多行: {c['view_device']} != {c['device_total']}"
    )
    assert c["view_vision"] == c["vision_ok"], (
        f"vision 分支漏行/多行: {c['view_vision']} != {c['vision_ok']}"
    )
    assert c["view_perception"] == c["perception_total"], (
        f"perception 分支漏行/多行: {c['view_perception']} != {c['perception_total']}"
    )


class _Rows:
    """execute() 的返回值形状：取完再插，所以先把行物化。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _CollectorWritingBetweenReads:
    """在每条 SELECT 之后往 events 插一行——等价于在役库里不知停歇的采集器。

    包装本身不是被测对象：它只保证「语句之间世界会变」这一件事在测试里必然发生，
    于是旧的读法每次都红、新的读法每次都绿，不再靠运气。
    """

    FOUR_SEPARATE_COUNTS = (
        "SELECT COUNT(*) FROM unified_events",
        "SELECT COUNT(*) FROM events",
        "SELECT COUNT(*) FROM behavior_events WHERE status='ok'",
        "SELECT COUNT(*) FROM perception_events",
    )

    def __init__(self, real: sqlite3.Connection) -> None:
        self.real = real
        self.writes = 0

    def execute(self, sql, params=()):
        rows = self.real.execute(sql, params).fetchall()
        if sql.strip().upper().startswith("SELECT"):
            self.writes += 1
            self.real.execute(
                "INSERT INTO events(id, ts, day, room, entity_id, action) "
                "VALUES (?, '2026-09-28T00:00:00', '2026-09-28', '', 'switch.race', 'on')",
                ("race-%d" % self.writes,),
            )
            self.real.commit()
        return _Rows(rows)


def test_count_contract_survives_writes_between_reads(tmp_path):
    """c9 假红的确定性复现：视图与三源的计数必须出自同一条语句。

    容器实测（2026-10-05 回归）：`1003987 != 1004477 (events=996833, vision_ok=3719,
    perception=3925)`——四条独立 COUNT 之间采集器又写了几百行。用真实 Store 建库、
    每条 SELECT 后插一行，两件事一起钉住：`_counts` 恒等（一条语句），旧的读法恒破。
    """
    from memory_agent.store import Store

    store = Store(str(tmp_path / "race.db"), tz_offset_hours=0.0)
    store.init_schema()
    conn = store.connect()
    conn.execute(
        "INSERT INTO events(id, ts, day, room, entity_id, action) "
        "VALUES ('d1', '2026-09-28T08:00:00', '2026-09-28', '客厅', 'light.a', 'on')"
    )
    conn.commit()

    busy = _CollectorWritingBetweenReads(conn)

    c = _counts(busy)
    assert c["view_total"] == c["device_total"] + c["vision_ok"] + c["perception_total"], (
        f"单条语句读出的计数竟然不等：{c}"
    )

    legacy = [busy.execute(sql).fetchone()[0] for sql in busy.FOUR_SEPARATE_COUNTS]
    assert legacy[0] != legacy[1] + legacy[2] + legacy[3], (
        f"四条独立 COUNT 在并发写入下必须判红（这是 c9 的那次假红），实际 {legacy} 相等 "
        "⇒ 写入包装没生效，本锁失去意义"
    )


def test_event_type_not_empty(conn):
    """event_type 不应为空字符串。"""
    null_count = conn.execute(
        "SELECT COUNT(*) FROM unified_events WHERE event_type IS NULL OR event_type = ''"
    ).fetchone()[0]
    assert null_count == 0, f"{null_count} rows have empty event_type"


def test_device_event_type_format(conn):
    """device 的 event_type 应包含 ':'（entity:action 格式）。"""
    sample = conn.execute(
        "SELECT event_type FROM unified_events WHERE source='device' LIMIT 100"
    ).fetchall()
    if sample:
        no_colon = sum(1 for (r,) in sample if ":" not in r)
        assert no_colon == 0, f"{no_colon}/100 device event_type missing ':' separator"
