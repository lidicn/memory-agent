"""任务表 #64：`behavior_predictor` 的改前未执行分支 + MCP 侧 weekday 边界。

编号纪律：`#NN` 指任务表编号，`§NN` 指 `审计核实与修复_20261001.md` 的节号。

改前覆盖现读（本机 Python313 + coverage 7.16.1，全量单轮，基线 HEAD `b241c7d`）：
`behavior_predictor.py` 111 语句 / 18 未执行 = **84%**，missing = `35, 49, 52-53, 81, 83, 113, 186, 256-273`。
其中 **256-273 是 `detect_pattern_deviation` 整档**（该函数 18 条语句一条没跑过，
且全仓 `src/` 无调用点——本批先补行为锁，"要不要接进告警链路"呈 DCD）。
第十五轮 §九 优先级 1 把它列为「0%~10% 覆盖」，现读对不上；那一句出自审计环境
（Python 3.10、pytest 后期 shell 不可用），不采信，本文件按 missing 清单补。

一条边界不齐是本批改码项：**weekday 的 0-6 校验只在 HTTP 侧有**。
`/api/behaviors/predict`（`api/behavior_routes.py:1011-1019`）传非 0-6 会 `error(...)`，
而 MCP 工具 `get_behavior_prediction` 原先只判 `weekday < 0`：传 7~99 不报错，
每一天都被 `_appearances_by_day` 的 weekday 过滤掉 ⇒ 扫完 5000 行事件后回一句
`arrival_prediction=None, data_days=0` 的"正常"响应（`ok:true`）。这正是 DCD 20261005 §三 Q2
「不允许静默忽略」点名的族，本批补 MCP 侧同口径守卫。
"""

import asyncio
import os
import pathlib
import sys
from datetime import datetime

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import house_time  # noqa: E402
from memory_agent.behavior_predictor import (  # noqa: E402
    _appearances_by_day, detect_pattern_deviation, house_dt, person_names,
    predict_arrival_time, predict_daily_routine, predict_post_arrival_activities)


@pytest.fixture(autouse=True)
def _house_clock_by_offset(monkeypatch):
    """与既有预测器锁同口径：把 homesdk 当家那条分支钉掉，读数只由小时偏移决定。"""
    monkeypatch.setattr(house_time, "is_active", lambda: False)
    yield


def _ev(ts, names=("Kevin",), scene=None):
    """现行形状：`persons` 是 dict 数组（`Store.list_behavior_events` 已解析）。"""
    return {"server_ts": ts, "persons": [{"name": n} for n in names],
            "action": "home", "scene": scene or ""}


# 生产是 DESC（新→旧），排序相关的用例一律按这个形状喂。
_DAYS = ["2026-10-01", "2026-09-30", "2026-09-29", "2026-09-28"]
_HOURS = {"2026-10-01": "19:15", "2026-09-30": "18:45", "2026-09-29": "19:00",
          "2026-09-28": "18:30"}


def _events():
    return [_ev(f"{d} {_HOURS[d]}:00") for d in _DAYS]


# ── 1) house_dt：坏数据只废它自己（改前 49、52-53 未执行）─────────────

@pytest.mark.parametrize("raw", ["", None, "2026-10-01", "2026-10-01T19", "not-a-date",
                                "2026-02-30 19:15:00"])
def test_house_dt_returns_none_for_unusable_text(raw):
    assert house_dt(raw) is None, f"{raw!r} 应作废该条，实得 {house_dt(raw)!r}"


def test_house_dt_converts_aware_utc_to_house_wall_clock():
    """带 `+00:00` 的那一族的 UTC 尾巴：折算后才是家里的钟，否则傍晚到家会被算成上午。"""
    dt = house_dt("2026-10-01T11:15:00+00:00", tz_offset_hours=8.0)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 10, 1, 19, 15)
    assert dt.tzinfo is None, "跨形状混排会让 sorted() 直接抛，这里必须已折成 naive"


def test_house_dt_keeps_naive_input_untouched():
    dt = house_dt("2026-10-01 19:15:00", tz_offset_hours=8.0)
    assert (dt.hour, dt.minute) == (19, 15)


def test_house_dt_uses_iana_house_tz_when_homesdk_active(monkeypatch):
    """改前 35 行未执行：homesdk 当家时按名取时区，**忽略**传入的小时偏移。"""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(house_time, "is_active", lambda: True)
    monkeypatch.setattr(house_time, "house_tz", lambda: ZoneInfo("Asia/Shanghai"))
    dt = house_dt("2026-10-01T13:15:00+00:00", tz_offset_hours=-5.0)
    assert (dt.hour, dt.minute) == (21, 15), "按 IANA 名折算，不能被小时偏移带跑"


# ── 2) person_names：形状两代都要认（改前 81、83 未执行）──────────────

def test_person_names_accepts_a_bare_dict():
    assert person_names({"persons": {"name": "Kevin"}}) == ["Kevin"]


@pytest.mark.parametrize("persons", [None, 42, "not json", b"\xff\xfe", {"name": ""},
                                     ["  "], [{"name": None}], [3.14]])
def test_person_names_degrades_to_empty(persons):
    """坏数据只废它自己：两个调用点都没有 try/except，这里抛出就是整条请求 500。"""
    assert person_names({"persons": persons}) == []


def test_person_names_prefers_parsed_column_over_legacy_json():
    """`persons` 在位就不回退读 `persons_json`（门面已把后者弹出）。"""
    ev = {"persons": [{"name": "Kevin"}], "persons_json": '[{"name": "Ghost"}]'}
    assert person_names(ev) == ["Kevin"]


# ── 3) weekday 过滤（改前 113 未执行）─────────────────────────────────

def test_appearances_by_day_filters_on_house_weekday():
    events = _events()
    target = datetime(2026, 9, 28).weekday()
    by_day = _appearances_by_day(events, "Kevin", 8.0, target)
    assert list(by_day) == ["2026-09-28"], list(by_day)
    assert _appearances_by_day(events, "Kevin", 8.0, None).keys() == {*_DAYS}


def test_predict_arrival_time_with_a_weekday_that_has_too_few_days():
    """单一日只 1 天 ⇒ 不足 min_days 如实回 None，而不是拿别的星期几凑数。"""
    target = datetime(2026, 9, 28).weekday()
    assert predict_arrival_time(_events(), "Kevin", weekday=target) is None
    one = predict_arrival_time(_events(), "Kevin", weekday=target, min_days=1)
    assert one["predicted_hour"] == pytest.approx(18.5)
    assert one["sample_days"] == 1


def test_predict_arrival_time_uses_all_days_when_weekday_is_none():
    res = predict_arrival_time(_events(), "Kevin")
    assert res["sample_days"] == 4
    assert res["range"][0] <= res["predicted_hour"] <= res["range"][1]


# ── 4) 到家后活动：样本天数门槛（改前 186 未执行）─────────────────────

def test_post_arrival_activities_needs_three_days():
    assert predict_post_arrival_activities(_events()[:2], "Kevin") == []


def test_post_arrival_activities_counts_windows_not_events():
    """同一窗口内去重：一个窗口里两条同名活动只记 1 次，ratio 分母是窗口数。"""
    events = [
        _ev("2026-10-01 19:15:00", scene="watch_tv"),
        _ev("2026-10-01 19:20:00", scene="watch_tv"),   # 同窗口同名 ⇒ 去重
        _ev("2026-10-01 19:17:00", scene="kitchen_light"),
        _ev("2026-09-30 18:45:00", scene="watch_tv"),
        _ev("2026-09-29 19:00:00", scene="watch_tv"),
        _ev("2026-09-28 18:30:00", scene="vacuum"),
    ]
    res = predict_post_arrival_activities(events, "Kevin", window_min=30, top_n=5)
    top = {r["activity"]: r for r in res}
    assert top["watch_tv"]["frequency"] == 3, "10-01 窗口内同名两条只算 1 次，四个窗口里三个有它"
    assert top["watch_tv"]["ratio"] == pytest.approx(3 / 4, abs=0.01), "分母是窗口数，不是事件数"
    assert top["kitchen_light"]["frequency"] == 1
    assert top["vacuum"]["frequency"] == 1, "09-28 那个窗口只有 vacuum"
    assert res[0]["activity"] == "watch_tv", "按频次降序"


def test_post_arrival_activities_top_n_truncates():
    res = predict_post_arrival_activities(_events(), "Kevin", top_n=1)
    assert len(res) == 1


# ── 5) detect_pattern_deviation：改前整档未执行（256-273）──────────────

def test_deviation_unknown_when_prediction_is_missing():
    assert detect_pattern_deviation({}, 19.0, "Kevin") == {"deviation": "unknown", "confidence": 0.0}
    assert detect_pattern_deviation({"predicted_hour": 18.5, "range": []}, 19.0, "Kevin") \
        == {"deviation": "unknown", "confidence": 0.0}
    assert detect_pattern_deviation(None, 19.0, "Kevin")["deviation"] == "unknown"


def test_deviation_normal_inside_the_band():
    predicted = {"predicted_hour": 18.5, "range": [17.5, 19.5]}
    res = detect_pattern_deviation(predicted, 19.2, "Kevin")
    assert res["deviation"] == "normal" and res["within_range"] is True
    assert res["delta_hours"] == pytest.approx(0.7, abs=1e-9)
    assert "severity" not in res, "在带内不给严重度，别造出一个假的档"


@pytest.mark.parametrize("actual,direction,severity", [
    (19.0, "late", "mild"),       # 越上界 0.2h，距中位数 0.5h < 1.0
    (19.8, "late", "moderate"),   # 距中位数 1.3h
    (20.6, "late", "severe"),     # 距中位数 2.1h
    (17.9, "early", "mild"),      # 越下界 0.3h，距中位数 0.6h
    (16.0, "early", "severe"),    # 距中位数 2.5h
])
def test_deviation_direction_and_severity(actual, direction, severity):
    """严重度按**距中位数**分档（不是距带边界），方向按越界侧——两条口径在这里各锁一次。"""
    predicted = {"predicted_hour": 18.5, "range": [18.2, 18.8]}
    res = detect_pattern_deviation(predicted, actual, "Kevin")
    assert res["deviation"] == direction
    assert res["severity"] == severity
    assert res["within_range"] is False
    assert res["predicted_range"] == [18.2, 18.8]
    assert res["delta_hours"] == pytest.approx(actual - 18.5, abs=1e-9)


# ── 6) daily_routine 的组合口径（回归既有结论，不给它留无人锁的档）────

def test_daily_routine_shape():
    res = predict_daily_routine(_events(), "Kevin")
    assert res["person"] == "Kevin"
    assert res["data_days"] == 4
    assert res["arrival"] is not None and res["arrival"]["sample_days"] == 4
    # 离家取当天最晚（max），不是"循环最后一次赋值"（DESC 下会取到当天最早）。
    assert res["departure"]["predicted_hour"] == pytest.approx(19.0, abs=0.3)


# ── 7) MCP 侧 weekday 边界（本批改码项）───────────────────────────────

def _tools_by_name():
    from memory_agent import mcp_server as ms
    server = getattr(ms, "mcp_server", None)
    if server is None:
        return None, ms
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", None) if manager else None
    return (tools if isinstance(tools, dict) else None), ms


class _Store:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def list_behavior_events(self, limit=None):
        self.calls += 1
        return self.rows


def _fake_runtime(rows):
    config = type("Cfg", (), {"tz_offset_hours": 8.0})()
    return type("RT", (), {"store": _Store(rows), "config": config})()


def test_mcp_get_behavior_prediction_rejects_out_of_range_weekday(monkeypatch):
    """改前红：weekday=99 不报错，扫完 5000 行回一句"没有那天"——与 HTTP 侧 400 不齐。"""
    tools, ms = _tools_by_name()
    if tools is None:
        pytest.skip("MCP SDK 不可用或版本过旧，无法取到已注册工具函数")
    tool = tools.get("get_behavior_prediction")
    assert tool is not None, "get_behavior_prediction 不在已注册工具表里"

    rt = _fake_runtime(_events())
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)
    for bad in (7, 99, -2):
        res = asyncio.run(tool.fn(person="Kevin", weekday=bad))
        assert res.get("ok") is False, f"weekday={bad} 应拒绝，实得 {res}"
        assert "weekday" in str(res.get("error", "")), res
    assert rt.store.calls == 0, "越界 weekday 不该把 5000 行事件扫一遍再回空"


def test_mcp_get_behavior_prediction_still_serves_valid_weekday(monkeypatch):
    """反例锁：守卫不能把 -1（全部）与 0-6 的正常路径一起拦掉。"""
    tools, ms = _tools_by_name()
    if tools is None:
        pytest.skip("MCP SDK 不可用或版本过旧，无法取到已注册工具函数")
    tool = tools.get("get_behavior_prediction")
    rt = _fake_runtime(_events())
    monkeypatch.setattr(ms, "get_runtime", lambda: rt)

    res = asyncio.run(tool.fn(person="Kevin", weekday=-1))
    assert res.get("ok") is True, res
    assert res["weekday"] is None and res["arrival_prediction"] is not None

    target = datetime(2026, 9, 28).weekday()
    res2 = asyncio.run(tool.fn(person="Kevin", weekday=target))
    assert res2.get("ok") is True, res2
    assert res2["arrival_prediction"] is None, "单日不足 min_days=3，如实回空而不是凑数"
    assert res2["weekday"] == target


# ── 8) DCD 20261006 §六 Q5=乙′：未接入 + 与在役路径重叠，登记成会红的锁 ─────

_SRC_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "memory_agent"


def _src_sites(token):
    """按**字面 token** 数 src 里的出现行（含 def 行本身，所以"未接入"= 只剩定义那一行）。"""
    hits = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if token in line:
                hits.append(f"{path.name}:{lineno}")
    return hits


def test_detect_pattern_deviation_is_not_wired_while_daily_profile_is():
    """Q5=乙′：本函数在 src 里**没有任何调用点**（只剩 `:246` 那一行 def），
    而在役已有一条等价的偏离检测：`daily_profile.check_return_time_anomaly`
    （中位数 + MAD，`abs(now−median) > 2σ`，由 `get_return_time_profile():127` 带出去，
    HTTP `behavior_routes.py` 与 `livingroom_ai.py` 消费）。

    两套并存 = 两套基线公式 + 两套阈值，"到家时间算不算 anomaly"是产品语义，
    接线触发条件 = 先裁定并存还是归一（本裁定 §七.6）。
    负控在同一条用例里：同一把尺量在役那条必须非零，否则"没有调用点"只是量具坏了。
    """
    assert _src_sites("detect_pattern_deviation") == ["behavior_predictor.py:246"]
    wired = _src_sites("check_return_time_anomaly")
    assert len(wired) >= 2, f"负控失效：在役的偏离检测都量不出调用点（{wired}）"


def test_deviation_ignores_the_person_argument():
    """Q5 顺带登记（本裁定 §六）：`person` 形参完全未读（`:246-252`）⇒ 换任何人名结果一字不差。
    将来要按成员分档（不同人不同容差）必须先动这条锁并留痕。"""
    predicted = {"predicted_hour": 18.5, "range": [17.5, 19.5]}
    baseline = detect_pattern_deviation(predicted, 20.6, "Kevin")
    for who in ("Alice", "", None):
        assert detect_pattern_deviation(predicted, 20.6, who) == baseline


def test_deviation_does_not_wrap_across_midnight():
    """Q5 顺带登记（本裁定 §六）：小时做**线性比较**，跨午夜的带（下界 > 上界）永远判不进带，
    且偏离量按 `|actual − predicted_hour|` 算 ⇒ 带 [23.0, 1.0]、实际 23:30 被读成
    "晚了 23.5 小时、severe"，而真实偏离是 0.5 小时。环形距离（`min(d, 24−d)`）接线前必须补。"""
    predicted = {"predicted_hour": 0.0, "range": [23.0, 1.0]}
    res = detect_pattern_deviation(predicted, 23.5, "Kevin")
    assert res["deviation"] == "late" and res["severity"] == "severe"
    assert res["delta_hours"] == pytest.approx(23.5, abs=1e-9)
    assert res["within_range"] is False, "跨午夜的带判不进（现状登记）"
