"""守卫锁：`activity_matrix` 的小时维度必须留在行里——它是全系统最忙的一张表。

**这条为什么值得钉**：Phase 4 换引擎后反复出现的缺陷族是「SQL 别名 / 返回键名 /
调用点键名对不上就静默归零，而返回值形状仍然合法」。`activity_matrix` 是这一族里
风险最高的一处：`coverage`、`anomaly_report` 规则 3（深夜占比）、`rhythm`、
`_heuristic_segments`、`user_persona` 五个下游都吃它，小时一旦被压成常量 0，
表现是"作息类型恒为早起型""深夜活动恒定误报"，而不是任何异常。

**核实结论（2026-10-04 现读，登记为阴性）**：`repository.py:345` 的别名 `hour_value`
与 `:351` 的取值键 `r.get("hour_value")` **是对得上的**，`Store.db_query` 按列名成键
（store.py:674 `dict(zip(cols, row))`）——小时维度当前正确。同文件其余聚合
（`trigger_value`/`action_value`/`state_value`/`room_value`）同样一致。
所以本文件**不是**缺陷复现，而是把"改坏立刻红"的防线补上：把别名或取值任一侧改掉，
前两条就判红；后四条钉的是下游读数（含一条"该响的必须响"的对偶档，防止修成恒不响）。

窗口口径：`rhythm`/`coverage`/`anomaly_report` 收显式 `tr`，用固定日期，跨时区容器读数
一致；`user_persona(days=)` 自己造窗口，改的是 `_now()` 这个既有测试接缝，同样不依赖"今天"。
"""

import os
import sys
import tempfile
from datetime import datetime

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import house_ts  # noqa: E402
from memory_agent.insights.parser.timeframe import resolve_range  # noqa: E402
from memory_agent.insights.repository import build_repository  # noqa: E402
from memory_agent.store import Store  # noqa: E402

# 固定日期窗口：不依赖"今天"，本机与容器（UTC）读数一致。
DAY_1 = "2026-09-21"
DAY_2 = "2026-09-22"
DAY_3 = "2026-09-23"
ENTITY = "binary_sensor.study_door"


def _tr():
    return resolve_range(start=f"{DAY_1}T00:00:00", end=f"{DAY_3}T23:59:59")


def _ev(day: str, hour: int, minute: int):
    return {"entity_id": ENTITY,
            "ts": "%sT%02d:%02d:00" % (day, hour, minute),
            "room": "书房", "domain": "binary_sensor",
            "new_state": "on", "old_state": "off",
            "attrs": {"friendly_name": "书房门磁"}}


def _spread(days, hours):
    """按 (天, 小时) 展开事件，分钟逐条错开。

    必须错开分钟：`make_event_id(entity_id, ts)` 是主键，同一实体同一秒的两条会被
    `INSERT OR IGNORE` 吞掉一条（实测：插 4 条只剩 3 行），计数类断言会因此虚低。
    """
    out = []
    for day in days:
        for i, hour in enumerate(hours):
            out.append(_ev(day, hour, minute=(i * 9 + 1) % 60))
    return out


def _hour_map(rows):
    return {int(r["hour"]): int(r["count"]) for r in rows}


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_matrix_")
    s = Store(os.path.join(tmp, "matrix.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


@pytest.fixture
def facade(store):
    return InsightService(store, Config())


# ── 1) 本体：小时必须留在行里 ────────────────────────────────────────────

def test_activity_matrix_preserves_the_hour_dimension(store):
    """三天 × 三个时刻的事件，矩阵里就要出现这三个 hour，而不是全 0。"""
    store.insert_events([_ev(DAY_1, 8, 1), _ev(DAY_1, 19, 2), _ev(DAY_1, 23, 3),
                         _ev(DAY_2, 8, 1), _ev(DAY_2, 19, 2), _ev(DAY_3, 8, 1),
                         _ev(DAY_3, 19, 2)])
    rows = build_repository(store, Config()).activity_matrix(_tr())
    assert rows, "矩阵不应为空"
    assert sorted({int(r["hour"]) for r in rows}) == [8, 19, 23], (
        f"小时维度失真：{sorted({int(r['hour']) for r in rows})}")


def test_activity_matrix_day_count_and_domain_keys_stay_correct(store):
    """同一条 SQL 的其余键：day / hour / domain / count 要逐格聚合到位。"""
    store.insert_events(_spread((DAY_1, DAY_2), (19, 19, 8)))
    rows = build_repository(store, Config()).activity_matrix(_tr())
    by_cell = {(r["day"], r["hour"], r["domain"]): r["count"] for r in rows}
    assert by_cell.get((DAY_1, 19, "binary_sensor")) == 2, by_cell
    assert by_cell.get((DAY_1, 8, "binary_sensor")) == 1, by_cell
    assert by_cell.get((DAY_2, 19, "binary_sensor")) == 2, by_cell
    assert len(by_cell) == 4, f"两天 × (19 点 / 8 点) 应为 4 格，实得 {len(by_cell)}"


# ── 2) 下游读数：五个消费方里最容易被无声改坏的四处 ──────────────────────

def test_rhythm_hourly_buckets_are_not_collapsed_into_midnight(facade):
    """`rhythm` 的 24 格分布要落在真实时刻上（小时被压成 0 时 23 格恒空）。"""
    facade.store.insert_events(_spread((DAY_1, DAY_2, DAY_3), (8, 19, 19, 22)))
    out = facade.core.rhythm(_tr())
    assert out.get("ok"), f"rhythm 自身失败：{out}"
    hourly = _hour_map(out["hourly"])
    assert hourly.get(0) == 0, f"0 点不该有事件，实得 {hourly.get(0)}"
    assert hourly.get(19) == 6, f"19 点应有 6 条（3 天 ×2），实得 {hourly.get(19)}"
    assert out["peaks"]["top_hours"][0]["hour"] == 19, (
        f"峰值小时应为 19，实得 {out['peaks']['top_hours']}")


def test_persona_rhythm_label_follows_the_evening_peak(facade, monkeypatch):
    """`user_persona` 的作息类型由峰值小时推：傍晚峰不能被判成「早起型」。

    `user_persona` 只收 `days`、窗口自己按 `_now()` 造，所以钉住那个既有接缝，
    不让用例随日历漂移失效。
    """
    facade.store.insert_events(_spread((DAY_1, DAY_2, DAY_3), (13, 19, 19, 20)))
    monkeypatch.setattr(facade.core, "_now",
                        lambda: house_ts(datetime(2026, 9, 24, 12, 0, 0)))
    out = facade.core.user_persona(days=4)
    assert out.get("ok"), f"user_persona 自身失败：{out}"
    assert out["total_events"] == 12, f"4 天窗口应覆盖 12 条，实得 {out['total_events']}"
    assert out["rhythm"]["peak_hour"] == 19, (
        f"峰值小时应为 19，实得 {out['rhythm']['peak_hour']}")
    label = next(t["value"] for t in out["traits"] if t["name"] == "作息类型")
    assert label == "日间活跃型", f"傍晚峰的作息类型应为「日间活跃型」，实得「{label}」"


def test_anomaly_night_rule_does_not_fire_on_daytime_only_events(facade):
    """规则 3 判的是「深夜 00-06 占比 >15%」：全白天数据不该报深夜异常。

    小时若被折进 0 点，深夜占比恒 100% ⇒ 任何家每天都"深夜活动异常"。
    """
    facade.store.insert_events(_spread((DAY_1, DAY_2, DAY_3), (9, 10, 11, 15)))
    out = facade.core.anomaly_report(_tr())
    assert out.get("ok"), f"anomaly_report 自身失败：{out}"
    kinds = {a["type"] for a in out["anomalies"]}
    assert "night_activity" not in kinds, (
        "白天事件被判成深夜活动：%s" % [a["message"] for a in out["anomalies"]
                                        if a["type"] == "night_activity"])


def test_anomaly_night_rule_fires_when_night_share_is_really_high(facade):
    """同一条规则的正向档：真·深夜数据必须报出来。

    与上一条成对——只留"不该响"那一条，恒不响的实现同样能全绿。
    """
    facade.store.insert_events(_spread((DAY_1, DAY_2, DAY_3), (1, 2, 3, 4)))
    out = facade.core.anomaly_report(_tr())
    kinds = {a["type"] for a in out["anomalies"]}
    assert "night_activity" in kinds, "凌晨 1-4 点的全部事件应触发深夜活动规则"


def test_coverage_hourly_matches_the_event_hours(facade):
    """`coverage` 的 `hourly` 与 `peak_hours` 也吃这张表。"""
    facade.store.insert_events(_spread((DAY_1, DAY_2), (7, 18, 18)))
    out = facade.core.coverage(_tr())
    hourly = _hour_map(out["hourly"])
    assert hourly.get(0) == 0
    assert hourly.get(18) == 4, f"18 点应有 4 条（2 天 ×2），实得 {hourly.get(18)}"
    assert out["peak_hours"][0] == 18, f"峰值小时应为 18，实得 {out['peak_hours']}"
