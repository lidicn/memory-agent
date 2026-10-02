"""Phase 5.1 离家模式状态机单元测试。"""

import sys
import os
import time
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.away_mode import AwayModeManager


class FakeStore:
    """模拟 store，用内存 dict 代替 meta 表。

    away_mode 走 Store 的 ``_db()`` / ``transaction()``（第六轮审计 CRITICAL-2：不再
    直抓共享连接），所以这里要交出的也是这两个入口；FakeStore 自己就实现
    execute/commit，直接把 self 交出去即可。
    """
    def __init__(self):
        self._meta = {}

    @contextmanager
    def _db(self):
        yield self

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=()):
        if "SELECT" in sql:
            key = params[0]
            val = self._meta.get(key)
            return FakeCursor([(val,)] if val is not None else [])
        if "INSERT OR REPLACE" in sql:
            key, val = params
            self._meta[key] = val
            return FakeCursor([])
        return FakeCursor([])

    def commit(self):
        pass


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_away_mode_lifecycle():
    """测试完整生命周期：在家 → 离家 → 在家。"""
    store = FakeStore()
    am = AwayModeManager(store, room="客厅")

    # 初始状态：在家
    assert am.is_active() == False, "初始应为在家状态"

    # no_human 事件 → 进入离家模式
    r = am.handle_event("no_human", "客厅")
    assert r["state_changed"] == True, "no_human 应触发状态变化"
    assert r["new_state"] == True, "应进入离家模式"
    assert am.is_active() == True, "状态应为离家"

    # 再次 no_human → 不重复变化
    r2 = am.handle_event("no_human", "客厅")
    assert r2["state_changed"] == False, "重复 no_human 不应重复变化"

    # face_known 事件 → 解除离家模式
    r3 = am.handle_event("face_known", "客厅")
    assert r3["state_changed"] == True, "face_known 应触发状态变化"
    assert r3["new_state"] == False, "应解除离家模式"
    assert am.is_active() == False, "状态应为在家"

    print("✅ test_away_mode_lifecycle 通过")


def test_away_alert_unknown():
    """测试离家模式下陌生人立即告警。"""
    store = FakeStore()
    am = AwayModeManager(store, room="客厅")

    # 先进入离家模式
    am.handle_event("no_human", "客厅")
    assert am.is_active() == True

    # 离家模式下 face_unknown → 告警
    r = am.handle_event("face_unknown", "客厅")
    assert r["alert"] == True, "离家模式下陌生人应告警"

    # 冷却期内不重复告警
    am._last_alert_ts = time.time()  # 模拟刚告警过
    r2 = am.handle_event("face_unknown", "客厅")
    assert r2["alert"] == False, "冷却期内不应重复告警"

    print("✅ test_away_alert_unknown 通过")


def test_room_filter():
    """测试只处理配置房间的事件。"""
    store = FakeStore()
    am = AwayModeManager(store, room="客厅")

    # 书房的 no_human 不触发离家模式
    r = am.handle_event("no_human", "书房")
    assert r["state_changed"] == False, "非配置房间不应触发"
    assert am.is_active() == False

    print("✅ test_room_filter 通过")


def test_persistence():
    """测试状态持久化（重启后恢复）。"""
    store = FakeStore()
    am1 = AwayModeManager(store, room="客厅")
    am1.handle_event("no_human", "客厅")
    assert am1.is_active() == True

    # 新建一个 manager（模拟重启），应从 DB 恢复离家状态
    am2 = AwayModeManager(store, room="客厅")
    assert am2.is_active() == True, "重启后应恢复离家状态"
    assert am2._since != "", "应恢复 since 时间"

    print("✅ test_persistence 通过")


def test_home_unknown_no_alert():
    """测试在家模式下陌生人不告警（走正常名册消除法）。"""
    store = FakeStore()
    am = AwayModeManager(store, room="客厅")

    # 在家模式下 face_unknown 不告警
    r = am.handle_event("face_unknown", "客厅")
    assert r["alert"] == False, "在家模式下陌生人不应立即告警"

    print("✅ test_home_unknown_no_alert 通过")


if __name__ == "__main__":
    test_away_mode_lifecycle()
    test_away_alert_unknown()
    test_room_filter()
    test_persistence()
    test_home_unknown_no_alert()
    print("\n🎉 全部 5 个测试通过")
