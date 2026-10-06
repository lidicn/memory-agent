"""回归锁：裁5 追加 **Q-B** / Q3-4——新引擎自己的读法要回显时间窗 `window`。

**钉的是什么**：legacy 的每个读法都把「这批数覆盖哪一段」放在顶层 `window`
（`resolve_range` 给的 meta），而 Phase 4 的新引擎只给自己算出来的数：
`core.usage` / `core.device_health` / `core.behavior_insights` 三条实测**都没有 `window`**。
消费方读到一串数却无从核对口径，这与本轮在抓的那族缺陷同源——形状合法、键名对不上、
什么也不报（#24/#46/#47/#48/Q3-1）。Q3-4 要的「legacy 的分页/窗口键在新引擎有对应物」
就是这一格。

**每条都配「该不响」的对偶档**（裁6 §三.4）：
- 正向锁「三个读法都带 window 且等于 `tr.to_dict()`」；对偶档断言**窗口口径不是编的**
  （`start_ts <= end_ts`、`days` 落在请求区间内），否则一个恒返回固定窗的实现也能绿。
- 降级路径也要带 `window`：这条的反面是「出错时顺手把口径也弄丢」，那时消费方连
  「哪一段的数据取不到」都答不出。
- `tr`  unusable 时 `_window_echo` 必须返回**空 dict**而不是抛、也不是造一个假窗口——
  缺窗要看得见是缺窗。
- 不发明键：`window` 的键集合必须是 `TimeRange.to_dict()` 给的那些，不许顺手加
  legacy 有而本层算不出的 `timezone`（宁缺勿造，造出来就是假口径）。

三条都是新引擎内部路径（`core.*`），按裁5 Q1=A 对外仍走 legacy 门面体，
所以这批锁**不改变任何出境载荷**。`coverage`/`data_quality` 两条今天就对外——
DCD `decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md:74`（Q-A=甲，把 `window`
定为洞察类读数的**通用回显键**，点名这两格）已裁，属**已裁未落**，本件按裁定把两格纳进
同一批参数化锁（任务表 #73）。超出裁定点名的 `compare_insights`/`plan_question`
没有顺手加：那属裁定适用范围问题，已另呈 DCD。
"""

import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import EntityInfo, TimeRange  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DAY_1 = "2026-09-21"
DAY_3 = "2026-09-23"
START = "%sT00:00:00" % DAY_1
END = "%sT23:59:59" % DAY_3
LIGHT_MAIN = "light.living_main"
CLIMATE_AC = "climate.living_ac"
CATALOG = [
    EntityInfo(entity_id=LIGHT_MAIN, friendly_name="客厅主灯", room="客厅", domain="light"),
    EntityInfo(entity_id=CLIMATE_AC, friendly_name="客厅空调", room="客厅", domain="climate"),
]


def _ev(entity_id, day, minute, state, domain):
    return {"entity_id": entity_id, "ts": "%sT10:%02d:00" % (day, minute), "room": "客厅",
            "domain": domain, "new_state": state, "old_state": "x",
            "attrs": {"friendly_name": entity_id.split(".")[-1]}}


def _dataset():
    rows = []
    for day in (DAY_1, DAY_3):
        rows += [_ev(LIGHT_MAIN, day, i, ("on" if i % 2 else "off"), "light")
                 for i in range(4)]
        rows += [_ev(CLIMATE_AC, day, i, "on", "climate") for i in range(2)]
    return rows


@pytest.fixture
def core():
    tmp = tempfile.mkdtemp(prefix="ma_window_")
    store = Store(os.path.join(tmp, "window.db"), tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(_dataset())
    svc = InsightService(store, Config())
    svc.resolver.refresh(CATALOG)
    yield svc.core, svc


@pytest.fixture
def svc_facade():
    """对外那两条读法要经过门面，所以这格用的是 `InsightService`（`core` 那批用的是 `core`）。"""
    tmp = tempfile.mkdtemp(prefix="ma_window_facade_")
    store = Store(os.path.join(tmp, "window_facade.db"), tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(_dataset())
    svc = InsightService(store, Config())
    svc.resolver.refresh(CATALOG)
    yield svc


def _tr(svc):
    return svc._tr(START, END)


@pytest.mark.parametrize("name", ("usage", "device_health", "behavior_insights",
                                "coverage", "data_quality"))
def test_core_reads_echo_the_window_they_scanned(core, name):
    """三个读法都给出 `window`，且逐字等于本次 tr 的口径（不是另算一个近似窗）。"""
    engine, svc = core
    tr = _tr(svc)
    out = getattr(engine, name)(tr)
    assert out.get("ok") is True
    assert "window" in out, "%s 的返回体没有 window，消费方无从核对口径" % name
    assert out["window"] == tr.to_dict()


@pytest.mark.parametrize("name", ("usage", "device_health", "behavior_insights",
                                "coverage", "data_quality"))
def test_window_is_the_requested_span_not_a_fabricated_one(core, name):
    """对偶档：窗口必须是请求的那一段——恒返回一个固定窗的实现过不了这一格。"""
    engine, svc = core
    tr = _tr(svc)
    win = getattr(engine, name)(tr)["window"]
    assert win["start_ts"] <= win["end_ts"]
    assert win["start"] == tr.to_dict()["start"]
    assert 1.0 <= float(win["days"]) <= 3.0          # 请求 3 天，读数不许漂成 7 天/0 天
    assert set(win) <= {"start", "end", "start_ts", "end_ts", "days", "label"}


def test_window_survives_the_degrade_path(core):
    """降级载荷仍带 window：出错时把口径一起弄丢，消费方连「哪一段取不到」都答不出。"""
    engine, svc = core
    tr = _tr(svc)

    class _Broken(object):
        def entity_stats(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

        def entity_catalog(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

        def behavior_summary(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

        def day_counts(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

        def activity_matrix(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

        def quality_counts(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

        def sample_rows(self, *a, **kw):
            raise RuntimeError("注入的取数故障")

    engine.repo = _Broken()
    for name in ("usage", "device_health", "behavior_insights",
                 "coverage", "data_quality"):
        out = getattr(engine, name)(tr)
        assert out.get("ok") is False, "%s 故障被吞成了成功" % name
        assert "window" in out, "%s 的降级信封丢了窗口口径" % name
        assert out["window"] == tr.to_dict()


def test_unusable_tr_yields_empty_window_not_an_invented_one(core):
    """tr 取不到口径时回空 dict：不抛、不省略键、也不造一个假窗口。"""
    engine, _svc = core

    class _NoRange(object):
        pass

    assert engine._window_echo(_NoRange()) == {}
    assert engine._window_echo(None) == {}


def test_window_is_a_copy_of_the_time_range_payload(core):
    """回显的是副本：调用方改 `window` 不许写回 TimeRange，反过来也不许共享可变对象。"""
    engine, svc = core
    tr = _tr(svc)
    win = engine.usage(tr)["window"]
    assert win is not tr.to_dict()
    win["start"] = "被改过"
    assert tr.to_dict()["start"] != "被改过"


def test_time_range_to_dict_shape_stays_the_contract(core):
    """`window` 的键集合来自 `TimeRange.to_dict()`——本层算不出的键（如 legacy 的
    `timezone`）宁可没有，也不许在这里凭空补一个值。"""
    engine, svc = core
    tr = TimeRange(start=svc._tr(START, END).start, end=svc._tr(START, END).end)
    assert set(tr.to_dict()) == {"start", "end", "start_ts", "end_ts", "days", "label"}
    assert set(engine.usage(_tr(svc))["window"]) == set(tr.to_dict())


def test_outward_facade_reads_carry_the_window(svc_facade):
    """对外的两条读法（`get_data_coverage` / `get_data_quality`）也必须带口径。

    裁5 Q-A 的甲口径点名的就是这两格，而它们**今天就对外**：MCP 侧
    `mcp_server.py:1625` / `mcp_server.py:2614` 直接把门面返回体整包给模型，
    门面少一格，模型那头就无从核对"这批数覆盖哪一段"。
    """
    svc = svc_facade
    cov = svc.data_coverage(days=7)
    dq = svc.get_data_quality(days=30)
    assert cov.get("ok") is True and dq.get("ok") is True
    for name, out in (("data_coverage", cov), ("get_data_quality", dq)):
        assert "window" in out, "%s 对外读数没有 window" % name
        assert set(out["window"]) == {"start", "end", "start_ts", "end_ts", "days", "label"}
    assert cov["window"]["days"] == 7.0 or int(cov["window"]["days"]) == 7
