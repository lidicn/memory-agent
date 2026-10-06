"""Q-B 异名切换：core.device_usage 新实现单元测试。

验证 BehaviorService.device_usage 与 legacy 契约一致：
- on/off 状态配对
- prior 事件（窗口前已开 → 从窗口起点计）
- 窗口末未闭合截断到 window_end
- 去抖（短于 debounce_seconds 的片段丢弃）
- 返回结构完整性（devices/total_on_seconds/duty_cycle 等）
"""
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.insights.models import TimeRange  # noqa: E402
from memory_agent.insights.service import BehaviorService  # noqa: E402


class FakeRepository:
    """模拟 StoreRepository：按 entity_id 返回预设事件。"""

    def __init__(self, events_by_entity: Dict[str, List[Dict[str, Any]]]):
        self._events = events_by_entity
        self.last_scan_truncated = False

    def load_events(self, tr: Any, entity_ids: Any = None, **kwargs) -> List[Any]:
        out = []
        eids = set(entity_ids or [])
        for eid in eids:
            for ev in self._events.get(eid, []):
                ts = ev["ts"]
                if tr.start_ts <= ts <= tr.end_ts:
                    out.append(SimpleNamespace(
                        ts=ts, entity_id=eid, state=ev.get("state", ""),
                        friendly_name=ev.get("friendly_name", ""),
                        room=ev.get("room", ""), domain=ev.get("domain", ""),
                        unit="", attributes={}))
        out.sort(key=lambda e: e.ts)
        return out


class FakeResolver:
    def resolve(self, **kwargs):
        return []

    def rooms(self, only_enabled=True):
        return []


class FakeAdapter:
    def meta(self, eid: str) -> Dict[str, str]:
        return {"friendly_name": eid, "room": "客厅", "category": "light", "domain": "light"}


def _make_service(events: Dict[str, List[Dict[str, Any]]]) -> BehaviorService:
    repo = FakeRepository(events)
    resolver = FakeResolver()
    config = SimpleNamespace(default_days=7, default_limit=100, noise_ratio_cap=0.6, cache_ttl=300)
    svc = BehaviorService(repo, resolver, config)
    svc._adapter = FakeAdapter()
    return svc


def _tr(start_hours_ago: float = 24, duration_hours: float = 24) -> TimeRange:
    end = datetime(2026, 10, 6, 12, 0, 0)
    start = end - timedelta(hours=start_hours_ago)
    actual_end = start + timedelta(hours=duration_hours)
    return TimeRange(start, actual_end, "test")


class TestDeviceUsageBasic:
    def test_on_off_pair(self):
        """基本 on→off 配对：开 1 小时。"""
        t0 = datetime(2026, 10, 6, 8, 0, 0).timestamp()
        events = {"light.test": [
            {"ts": t0, "state": "on"},
            {"ts": t0 + 3600, "state": "off"},
        ]}
        svc = _make_service(events)
        tr = _tr(48, 48)
        out = svc.device_usage(tr, entity_ids=["light.test"], debounce_seconds=0)
        assert out["ok"] is True
        dev = out["devices"][0]
        assert dev["sessions"] == 1
        assert dev["total_on_seconds"] == pytest.approx(3600, abs=1)
        assert dev["switch_on_count"] == 1
        assert dev["switch_off_count"] == 1

    def test_prior_event_open(self):
        """窗口前已开 → 从窗口起点计，窗口内 off 闭合。"""
        t0 = datetime(2026, 10, 6, 8, 0, 0).timestamp()
        # prior 事件在窗口前，state=on
        prior_ts = t0 - 3600
        events = {"light.test": [
            {"ts": prior_ts, "state": "on"},
            {"ts": t0 + 1800, "state": "off"},  # 窗口内开了 30 分钟
        ]}
        svc = _make_service(events)
        tr = TimeRange(
            datetime(2026, 10, 6, 7, 0, 0),
            datetime(2026, 10, 6, 12, 0, 0), "test")
        out = svc.device_usage(tr, entity_ids=["light.test"], debounce_seconds=0)
        dev = out["devices"][0]
        # 从窗口起点(7:00)到 off(8:30) = 1.5 小时
        assert dev["total_on_seconds"] == pytest.approx(5400, abs=1)
        assert dev["sessions"] == 1

    def test_unclosed_truncated(self):
        """窗口末未闭合 → 截断到 window_end。"""
        t0 = datetime(2026, 10, 6, 8, 0, 0).timestamp()
        events = {"light.test": [
            {"ts": t0, "state": "on"},
            # 没有 off 事件
        ]}
        svc = _make_service(events)
        tr = TimeRange(
            datetime(2026, 10, 6, 7, 0, 0),
            datetime(2026, 10, 6, 12, 0, 0), "test")
        out = svc.device_usage(tr, entity_ids=["light.test"], debounce_seconds=0)
        dev = out["devices"][0]
        # 从 8:00 到 window_end 12:00 = 4 小时
        assert dev["total_on_seconds"] == pytest.approx(14400, abs=1)
        assert dev["sessions"] == 1

    def test_debounce(self):
        """去抖：短于 debounce_seconds 的片段丢弃。"""
        t0 = datetime(2026, 10, 6, 8, 0, 0).timestamp()
        events = {"light.test": [
            {"ts": t0, "state": "on"},
            {"ts": t0 + 2, "state": "off"},  # 2 秒，应被去抖
            {"ts": t0 + 10, "state": "on"},
            {"ts": t0 + 3610, "state": "off"},  # 1 小时，保留
        ]}
        svc = _make_service(events)
        tr = _tr(48, 48)
        out = svc.device_usage(tr, entity_ids=["light.test"], debounce_seconds=5)
        dev = out["devices"][0]
        assert dev["sessions"] == 1
        assert dev["total_on_seconds"] == pytest.approx(3600, abs=1)

    def test_empty_entity(self):
        """无事件设备 → 返回 0，不崩溃。"""
        svc = _make_service({})
        tr = _tr(24, 24)
        out = svc.device_usage(tr, entity_ids=["light.nonexistent"], debounce_seconds=0)
        assert out["ok"] is True
        assert out["device_count"] == 1
        dev = out["devices"][0]
        assert dev["total_on_seconds"] == 0
        assert dev["sessions"] == 0

    def test_return_structure(self):
        """返回结构包含所有契约字段。"""
        t0 = datetime(2026, 10, 6, 8, 0, 0).timestamp()
        events = {"light.test": [
            {"ts": t0, "state": "on"},
            {"ts": t0 + 3600, "state": "off"},
        ]}
        svc = _make_service(events)
        tr = _tr(48, 48)
        out = svc.device_usage(tr, entity_ids=["light.test"], include_timeline=True)
        # 顶层字段
        for key in ["ok", "window", "device_count", "total_on_seconds",
                     "total_on_human", "devices"]:
            assert key in out, f"缺少顶层字段 {key}"
        # 设备字段
        dev = out["devices"][0]
        for key in ["entity_id", "friendly_name", "room", "domain",
                     "sessions", "switch_on_count", "switch_off_count",
                     "total_on_seconds", "total_on_human",
                     "avg_session_seconds", "avg_session_human",
                     "longest_session_human", "daily_average_human",
                     "duty_cycle_percent", "by_day_seconds",
                     "raw_event_count", "timeline"]:
            assert key in dev, f"缺少设备字段 {key}"

    def test_multiple_entities_sorted(self):
        """多设备按 total_on_seconds 降序排列。"""
        t0 = datetime(2026, 10, 6, 8, 0, 0).timestamp()
        events = {
            "light.a": [{"ts": t0, "state": "on"}, {"ts": t0 + 3600, "state": "off"}],
            "light.b": [{"ts": t0, "state": "on"}, {"ts": t0 + 7200, "state": "off"}],
        }
        svc = _make_service(events)
        tr = _tr(48, 48)
        out = svc.device_usage(tr, entity_ids=["light.a", "light.b"], debounce_seconds=0)
        assert out["devices"][0]["entity_id"] == "light.b"  # 2 小时 > 1 小时
        assert out["devices"][1]["entity_id"] == "light.a"

    def test_duty_cycle(self):
        """duty_cycle = total_on / window_span * 100。"""
        t0 = datetime(2026, 10, 5, 8, 0, 0).timestamp()
        events = {"light.test": [
            {"ts": t0, "state": "on"},
            {"ts": t0 + 3600, "state": "off"},
        ]}
        svc = _make_service(events)
        # 窗口 10 小时（完全在过去），开 1 小时 → 10%
        tr = TimeRange(
            datetime(2026, 10, 5, 8, 0, 0),
            datetime(2026, 10, 5, 18, 0, 0), "test")
        out = svc.device_usage(tr, entity_ids=["light.test"], debounce_seconds=0)
        dev = out["devices"][0]
        assert dev["duty_cycle_percent"] == pytest.approx(10.0, abs=0.1)
