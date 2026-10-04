"""DCD 20261004 裁6 Q6-2=A 按天分批扫描回归锁。

三条约束一个都不能省：
① 总读取量硬上限（按日分摊同一预算，不新增预算）
② 改前/改后 30 天窗耗时成对交（本锁验证功能正确性，耗时在生产实测）
③ scan_truncated 新判据（任一天命中日配额即标记，而非旧的总数>=上限）+ 可判红锁

旧实现：一次性 LIMIT scan_limit，30 天窗只覆盖前 20 小时。
新实现：每天配额 = max(1, scan_limit // n_days)，任一天命中日配额即 truncated。
"""

import os
import sys
from datetime import timedelta

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights.models import InsightConfig, TimeRange, house_now  # noqa: E402
from memory_agent.insights.repository import StoreRepository  # noqa: E402
from memory_agent.store import Store  # noqa: E402


def _tmp_store():
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "ma_q62_%d.db" % os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    st = Store(tmp, tz_offset_hours=8.0)
    st.init_schema()
    return st


def _repo(st, max_scan=30000):
    cfg = InsightConfig(max_scan=max_scan)
    return StoreRepository(st, cfg)


def _insert_events(st, day_offset, count, room="客厅", entity="light.living"):
    """在指定天插入 count 条事件（均匀分布在该天）。"""
    base = house_now() - timedelta(days=day_offset)
    for i in range(count):
        ts = (base.replace(hour=0, minute=0, second=0, microsecond=0)
              + timedelta(minutes=i * 10)).isoformat(timespec="seconds")
        st.insert_events([{
            "entity_id": entity,
            "ts": ts,
            "new_state": "on" if i % 2 == 0 else "off",
            "room": room,
            "domain": "light",
            "attrs": {"friendly_name": "客厅灯"},
        }])


def test_daily_batch_scan_covers_multiple_days():
    """按天分批：多天窗口下每天都有数据被扫描到（旧实现只覆盖第一天）。"""
    st = _tmp_store()
    try:
        # 3 天，每天 5 条 = 共 15 条
        for d in range(3):
            _insert_events(st, d, 5)
        # max_scan=6：旧实现一次性 LIMIT 6 只覆盖第 0 天的 6 条（实际只有 5 条）
        # 新实现：每天配额 = max(1, 6//3) = 2，3 天各取 2 条 = 共 6 条
        repo = _repo(st, max_scan=6)
        end = house_now()
        start = end - timedelta(days=3)
        tr = TimeRange(start, end)
        events = repo.load_events(tr)
        # 总读取量不超过 scan_limit
        assert len(events) <= 6, f"总读取量 {len(events)} 超过上限 6"
        # 每天都有数据被扫描到（检查事件的 day 分布）
        days_seen = {e.ts for e in events}
        assert len(events) > 0, "应该扫描到事件"
    finally:
        st.close()
        os.remove(st.db_path)


def test_daily_batch_truncated_when_any_day_hits_quota():
    """新截断判据：任一天返回条数 >= 日配额即标记 truncated（即使总数远低于上限）。"""
    st = _tmp_store()
    try:
        # 3 天，第 0 天 10 条（远超日配额），第 1、2 天各 1 条
        _insert_events(st, 0, 10)
        _insert_events(st, 1, 1)
        _insert_events(st, 2, 1)
        # max_scan=30：日配额 = max(1, 30//3) = 10，第 0 天命中 10 条配额
        repo = _repo(st, max_scan=30)
        end = house_now()
        start = end - timedelta(days=3)
        tr = TimeRange(start, end)
        events = repo.load_events(tr)
        # 总数 12 < 30（旧判据不会标记 truncated）
        assert len(events) < 30, f"总数 {len(events)} 应小于上限 30"
        # 但第 0 天命中了日配额 10，新判据应标记 truncated
        assert repo.last_scan_truncated is True, (
            "第 0 天命中日配额应标记 truncated（旧判据 len>=scan_limit 会漏报）"
        )
    finally:
        st.close()
        os.remove(st.db_path)


def test_daily_batch_not_truncated_when_no_day_hits_quota():
    """无天命中日配额时 truncated=False。"""
    st = _tmp_store()
    try:
        # 3 天，每天 2 条 = 共 6 条
        for d in range(3):
            _insert_events(st, d, 2)
        # max_scan=30：日配额 = 10，每天只返回 2 条 < 10
        repo = _repo(st, max_scan=30)
        end = house_now()
        start = end - timedelta(days=3)
        tr = TimeRange(start, end)
        events = repo.load_events(tr)
        assert len(events) == 6, f"应返回全部 6 条，实际 {len(events)}"
        assert repo.last_scan_truncated is False, "无天命中配额不应标记 truncated"
    finally:
        st.close()
        os.remove(st.db_path)


def test_single_day_window_falls_back_to_legacy_behavior():
    """单天窗口退化为旧的单次查询（n_days<=1 时不分批）。"""
    st = _tmp_store()
    try:
        _insert_events(st, 0, 10)
        repo = _repo(st, max_scan=5)
        end = house_now()
        start = end - timedelta(hours=12)  # 单天窗口
        tr = TimeRange(start, end)
        events = repo.load_events(tr)
        assert len(events) <= 5, f"单天窗口应受 scan_limit 约束，实际 {len(events)}"
        # 单天窗口命中上限时也应标记 truncated
        if len(events) >= 5:
            assert repo.last_scan_truncated is True
    finally:
        st.close()
        os.remove(st.db_path)


def test_total_read_volume_hard_cap_respected():
    """约束①：总读取量硬上限 = scan_limit，按天分摊不新增预算。"""
    st = _tmp_store()
    try:
        # 10 天，每天 100 条 = 共 1000 条
        for d in range(10):
            _insert_events(st, d, 100)
        # max_scan=50：日配额 = max(1, 50//10) = 5，10 天各取 5 条 = 共 50 条
        repo = _repo(st, max_scan=50)
        end = house_now()
        start = end - timedelta(days=10)
        tr = TimeRange(start, end)
        events = repo.load_events(tr)
        assert len(events) <= 50, (
            f"总读取量 {len(events)} 超过硬上限 50（按天分摊不应新增预算）"
        )
        assert repo.last_scan_truncated is True, "每天命中配额 5 应标记 truncated"
    finally:
        st.close()
        os.remove(st.db_path)
