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

from memory_agent.insights.models import TimeRange, house_tz, house_ts  # noqa: E402
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


def test_fixtures_and_window_share_the_house_clock_frame():
    """量具自证（本批容器 run16 的教训）：事件 epoch 必须和 `TimeRange` 用同一个时框。

    `TimeRange.start_ts/end_ts` 走 `house_ts`（按**家庭墙钟**解释 naive），而
    `datetime(...).timestamp()` 走**机器时区**。本机与家庭同为 +8 时两种写法数值相同
    （实测都是 1791244800.0），混用不会暴露问题；容器里机器是 UTC、家庭仍是 +8，
    用例的 epoch 就整段落到窗口外——本机 5 绿、容器 5 条读成 `sessions=0`。

    所以这条锁不依赖机器时区：①窗口端点必须等于 `house_ts(同一个 naive)`（时框口径锁）；
    ②本文件用的事件 epoch 必须落进窗口；③只有当家庭偏移与机器偏移不一致时，
    两种写法才必须给出不同 epoch（这条在容器里真会执行，本机同偏移自然跳过）。
    """
    naive = datetime(2026, 10, 6, 8, 0, 0)
    tr = _tr(48, 48)
    assert tr.start_ts == house_ts(datetime(2026, 10, 4, 12, 0, 0)), tr
    assert tr.end_ts == house_ts(datetime(2026, 10, 6, 12, 0, 0)), tr
    t0 = house_ts(naive)
    assert tr.contains(t0), "用例的 epoch 落到了窗口外 ⇒ 时框不一致"
    house_off = house_tz().utcoffset(naive)
    machine_off = datetime.now().astimezone().utcoffset()
    if house_off != machine_off:
        assert t0 != naive.timestamp(), \
            "家庭偏移与机器偏移不一致时，house_ts 与 .timestamp() 必须给出不同的 epoch"


def test_unlocated_devices_is_not_a_successful_answer():
    """`ok=False` 的那一档不许被包装成成功（legacy 契约：无定位方式算用错参数）。

    改前的形状是 `_device_usage` 带着 `error` 返回、包装层再无条件 `out["ok"] = True`
    ⇒ 门面把"一个设备都没定位到"回答成一次成功查询。这条锁把方向钉死：
    未定位 = `ok=False` + `error` 点名"定位"，且 `devices` 是空表而不是全屋。
    """
    svc = _make_service({})
    out = svc.device_usage(_tr(48, 48), entity_ids=[], debounce_seconds=0)
    assert out["ok"] is False, out
    assert "定位" in str(out.get("error")), out
    assert out["devices"] == [] and out["device_count"] == 0, out
    assert out.get("hint"), "未定位必须同时给出怎么定位的下一步，不能只回一句失败"


class TestDeviceUsageBasic:
    def test_on_off_pair(self):
        """基本 on→off 配对：开 1 小时。"""
        t0 = house_ts(datetime(2026, 10, 6, 8, 0, 0))
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
        t0 = house_ts(datetime(2026, 10, 6, 8, 0, 0))
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
        t0 = house_ts(datetime(2026, 10, 6, 8, 0, 0))
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
        t0 = house_ts(datetime(2026, 10, 6, 8, 0, 0))
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
        t0 = house_ts(datetime(2026, 10, 6, 8, 0, 0))
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
        t0 = house_ts(datetime(2026, 10, 6, 8, 0, 0))
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
        t0 = house_ts(datetime(2026, 10, 5, 8, 0, 0))
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
