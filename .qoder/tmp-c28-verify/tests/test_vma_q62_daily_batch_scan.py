"""DCD 20261004 裁6 Q6-2=A 按天分批扫描回归锁。

三条约束一个都不能省：
① 总读取量硬上限（按日分摊同一预算，不新增预算）
② 改前/改后 30 天窗耗时成对交（本锁验证功能正确性，耗时在生产实测）
③ scan_truncated 新判据（任一天命中日配额即标记，而非旧的总数>=上限）+ 可判红锁

旧实现：一次性 LIMIT scan_limit，30 天窗只覆盖前 20 小时。
新实现：每天配额 = max(1, scan_limit // n_days)，任一天命中日配额即 truncated。

时间几何全部钉在固定墙钟锚点上，**不用 `house_now()`**：原先把事件铺在
「今天 00:00 + i*10 分钟」、窗口却取 `[now-3d, now]`，于是家庭墙钟 00:00~01:30
之间跑必然有一半事件落在窗外（00:08 实跑 5/6 条、00:13 实跑判红各一次），
`n_days` 也会随当天是否已开始而 ±1，日配额跟着变。那是**运行时刻依赖**，
不是跨平台等价位——2026-10-05 00:13 本机(+8)与容器(UTC)同形判红已证。
"""

import os
import sys
from datetime import date, datetime, timedelta

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.insights.models import InsightConfig, TimeRange  # noqa: E402
from memory_agent.insights.repository import StoreRepository, _to_iso  # noqa: E402
from memory_agent.store import Store  # noqa: E402

# 固定锚点：任何机器、任何钟点跑，日键与日配额都算得出同一个数
D0 = date(2026, 4, 7)


def _win(n_days: int):
    """[D0 00:00:00, D0+n_days-1 天末] 的整日窗口（naive 家庭墙钟）。"""
    last = datetime.combine(D0 + timedelta(days=n_days - 1), datetime.min.time())
    return TimeRange(
        datetime.combine(D0, datetime.min.time()),
        last.replace(hour=23, minute=59, second=59),
    )


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


def _insert_events(st, day: date, count, room="客厅", entity="light.living"):
    """在该 day 的 00:00 起每 10 分钟一条，共 count 条（最多铺到次日 00:00 之前）。"""
    base = datetime.combine(day, datetime.min.time())
    for i in range(count):
        ts = (base + timedelta(minutes=i * 10)).isoformat(timespec="seconds")
        st.insert_events([{
            "entity_id": entity,
            "ts": ts,
            "new_state": "on" if i % 2 == 0 else "off",
            "room": room,
            "domain": "light",
            "attrs": {"friendly_name": "客厅灯"},
        }])


def _day_keys(events):
    return {_to_iso(e.ts)[:10] for e in events}


def test_daily_batch_scan_covers_multiple_days():
    """按天分批：多天窗口下**每一天**都有数据被扫到（旧实现只覆盖第一天）。"""
    st = _tmp_store()
    try:
        # 4 天，每天 5 条 = 共 20 条
        for i in range(4):
            _insert_events(st, D0 + timedelta(days=i), 5)
        # max_scan=6，n_days=4 ⇒ 日配额 = max(1, 6//4) = 1，4 天各取 1 条
        repo = _repo(st, max_scan=6)
        events = repo.load_events(_win(4))
        assert len(events) <= 6, f"总读取量 {len(events)} 超过上限 6"
        # 判据不是"有条数"，而是"每个日键都出场"——旧实现这里只会剩第一天的行
        assert _day_keys(events) == {
            "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"
        }, _day_keys(events)
    finally:
        st.close()
        os.remove(st.db_path)


def test_daily_batch_truncated_when_any_day_hits_quota():
    """新截断判据：任一天返回条数 >= 日配额即标记 truncated（即使总数远低于上限）。"""
    st = _tmp_store()
    try:
        # 第 0 天 10 条（超日配额），其余三天各 1 条；共 13 条
        _insert_events(st, D0, 10)
        for i in (1, 2, 3):
            _insert_events(st, D0 + timedelta(days=i), 1)
        # max_scan=30，n_days=4 ⇒ 日配额 = 30//4 = 7；第 0 天命中 7 条配额
        repo = _repo(st, max_scan=30)
        events = repo.load_events(_win(4))
        # 总数 10 < 30（旧判据 len>=scan_limit 会漏报）
        assert len(events) == 10, f"应取回 7+1+1+1=10 条，实际 {len(events)}"
        assert repo.last_scan_truncated is True, (
            "第 0 天命中日配额应标记 truncated（旧判据 len>=scan_limit 会漏报）"
        )
    finally:
        st.close()
        os.remove(st.db_path)


def test_daily_batch_not_truncated_when_no_day_hits_quota():
    """无天命中日配额时 truncated=False（对照组：防判据恒 True）。"""
    st = _tmp_store()
    try:
        # 4 天，每天 2 条 = 共 8 条；日配额 = 30//4 = 7，2 < 7
        for i in range(4):
            _insert_events(st, D0 + timedelta(days=i), 2)
        repo = _repo(st, max_scan=30)
        events = repo.load_events(_win(4))
        assert len(events) == 8, f"应返回全部 8 条，实际 {len(events)}"
        assert repo.last_scan_truncated is False, "无天命中配额不应标记 truncated"
    finally:
        st.close()
        os.remove(st.db_path)


def test_single_day_window_falls_back_to_legacy_behavior():
    """单天窗口退化为旧的单次查询（n_days<=1 时不分批），上限与判据都按旧口径。"""
    st = _tmp_store()
    try:
        _insert_events(st, D0 + timedelta(days=1), 10)
        repo = _repo(st, max_scan=5)
        one_day = TimeRange(
            datetime.combine(D0 + timedelta(days=1), datetime.min.time()),
            datetime.combine(D0 + timedelta(days=1), datetime.min.time())
            + timedelta(hours=23, minutes=59, seconds=59),
        )
        events = repo.load_events(one_day)
        assert len(events) == 5, f"单天窗口应取满 scan_limit=5，实际 {len(events)}"
        assert repo.last_scan_truncated is True, "单天窗口命中上限应标记 truncated"
    finally:
        st.close()
        os.remove(st.db_path)


def test_total_read_volume_hard_cap_respected():
    """约束①：总读取量硬上限 = scan_limit，按天分摊不新增预算。"""
    st = _tmp_store()
    try:
        # 10 天，每天 100 条 = 共 1000 条
        for d in range(10):
            _insert_events(st, D0 + timedelta(days=d), 100)
        # max_scan=50，n_days=10 ⇒ 日配额 = 5，10 天各取 5 条 = 共 50 条
        repo = _repo(st, max_scan=50)
        events = repo.load_events(_win(10))
        assert len(events) == 50, (
            f"总读取量应正好等于硬上限 50，实际 {len(events)}（按天分摊不得新增预算）"
        )
        assert repo.last_scan_truncated is True, "每天命中配额 5 应标记 truncated"
    finally:
        st.close()
        os.remove(st.db_path)


def test_scan_truncated_is_thread_local_on_a_shared_repository():
    """截断位是「本次切片的属性」，不是仓库实例的属性。

    `StoreRepository` 一个 facade 一个实例（`build_repository`），MCP/HTTP 并发与
    runtime 周期任务会在不同线程同时调 `load_events`。判据若挂在实例上，
    A 线程就会读到 B 线程刚写入的截断位 —— 对外 `scan_truncated` 张冠李戴，
    而 DCD 裁6 Q6-2 约束③要的就是这个字段可信。
    """
    import threading

    st = _tmp_store()
    try:
        # 第 0 天 10 条（会命中日配额），第 1~3 天各 1 条（不会命中）
        _insert_events(st, D0, 10)
        for i in (1, 2, 3):
            _insert_events(st, D0 + timedelta(days=i), 1)

        repo = _repo(st, max_scan=30)
        busy_tr = _win(4)                                    # 4 天，日配额 7，第 0 天 10 条
        quiet_last = datetime.combine(D0 + timedelta(days=3), datetime.min.time())
        quiet_tr = TimeRange(
            quiet_last.replace(hour=0, minute=0, second=0),
            quiet_last.replace(hour=23, minute=59, second=59),
        )                                                      # 单看第 3 天，1 条 < 配额

        loaded_busy = threading.Event()
        loaded_quiet = threading.Event()
        seen = {}

        def _busy():
            repo.load_events(busy_tr)
            loaded_busy.set()
            loaded_quiet.wait(5)
            seen["busy"] = repo.last_scan_truncated

        def _quiet():
            loaded_busy.wait(5)
            repo.load_events(quiet_tr)
            loaded_quiet.set()
            seen["quiet"] = repo.last_scan_truncated

        t1 = threading.Thread(target=_busy)
        t2 = threading.Thread(target=_quiet)
        t1.start(); t2.start(); t1.join(10); t2.join(10)

        assert seen == {"busy": True, "quiet": False}, seen
    finally:
        st.close()
        os.remove(st.db_path)
