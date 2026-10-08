"""P4a 行为预测的现行数据形状回归锁（审计报告 §5.4-5「补 behavior_predictor 功能覆盖」）。

三条判据全部来自现网只读实测（探针 `.qoder/tmp-c13-predictor-live.py`，口径见回执）：

1. **门面已经不给 `persons_json`**：`Store.list_behavior_events`（store.py:2378）把
   `persons_json` **弹出**并换成解析好的 `persons`（dict 数组）。而预测器三个入口读的是
   `ev.get("persons_json") or "[]"` ⇒ 每条事件都读成"无人员" ⇒ `predict_arrival_time`
   永远 `None`、`predict_daily_routine` 永远全空、`data_days` 永远 0，
   而 HTTP 面照样 `ok:true`。这正是本项目栽过两次的「键名对不上就静默归零」。
2. **`_deserialize_persons` 允许字符串元素本身即姓名**（旧格式 `["Kevin"]`，store.py:586），
   预测器对 dict 数组写死 `p.get("name")` ⇒ 撞上就是 `AttributeError`，
   而两个调用点（behavior_routes.py:855、mcp_server.py:2736）都没有 try/except ⇒ 直接 500。
3. **`server_ts` 现网两种形状混用**：2026-09-18 同日 44 条 `+00:00` 与 309 条 naive
   ⇒ `predict_post_arrival_activities` 的 `sorted(dts)` 抛
   `can't compare offset-naive and offset-aware datetimes`。
4. **`departure` 顺序依赖**：靠"循环里最后一次赋值"取当天最晚，而门面是 DESC ⇒ 报成当天最早。
5. **`data_days` 拿人名做子串搜索**：短名被长名吃掉（现网实测两名 13 天 vs 1 天）。

UTC 那 44 条不是"少 8 小时"这种小偏差：它把傍晚到家折算成上午，直接掉出
「中午后首次出现 = 到家」的判据窗口。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import house_time  # noqa: E402
from memory_agent.behavior_predictor import (predict_arrival_time,  # noqa: E402
                                             predict_daily_routine,
                                             predict_post_arrival_activities)


@pytest.fixture(autouse=True)
def _house_clock_by_offset(monkeypatch):
    """本锁测的是「按小时偏移折算家庭墙钟」这条兜底路径，把它钉住。

    homesdk 当家时 `house_tz()` 会按 IANA 名换算并忽略传入偏移（契约 §四 口径，另有既有锁覆盖），
    那会让这台机器装没装 homesdk、环境变量里有没有 TZ 都改变读数。
    """
    monkeypatch.setattr(house_time, "is_active", lambda: False)
    yield


def _ev(ts, persons):
    """`persons` 原样塞进去：形状本身就是被测对象，不做任何预制。"""
    return {"server_ts": ts, "persons": persons, "action": "home", "scene": ""}


def _ev_json(ts, persons_json):
    """旧形状：`persons_json` 是字符串列（P1-4 之前 store 给的就是这个）。"""
    return {"server_ts": ts, "persons_json": persons_json, "action": "home", "scene": ""}


# ------------------------------------------------ 1. 现行形状不许读成"无人员"
def test_current_store_shape_is_not_blind():
    """`list_behavior_events` 给的是 `persons`（dict 数组）——必须照样算得出预测。"""
    events = [
        _ev("2026-09-16T19:00:00", [{"name": "K", "confidence": 0.9}]),
        _ev("2026-09-17T19:30:00", [{"name": "K", "confidence": 0.9}]),
        _ev("2026-09-18T18:00:00", [{"name": "K", "confidence": 0.9}]),
        _ev("2026-09-19T19:10:00", [{"name": "其他人", "confidence": 0.9}]),
    ]
    out = predict_arrival_time(events, "K", min_days=3)
    assert out is not None, "现行形状被读成零 ⇒ predict_arrival_time 恒 None"
    assert out["sample_days"] == 3, out
    assert 18.0 <= out["predicted_hour"] <= 19.6, out


def test_legacy_persons_json_string_still_works():
    """旧形状（字符串列）不许因为换新键而倒退——两代形状都要认。"""
    events = [
        _ev_json("2026-09-16T19:00:00", '[{"name": "K", "confidence": 0.9}]'),
        _ev_json("2026-09-17T19:30:00", '[{"name": "K", "confidence": 0.9}]'),
        _ev_json("2026-09-18T18:00:00", '[{"name": "K", "confidence": 0.9}]'),
    ]
    out = predict_arrival_time(events, "K", min_days=3)
    assert out is not None and out["sample_days"] == 3, out


# ------------------------------------------------ 2. 旧格式字符串元素即姓名
def test_plain_string_person_entries_are_counted():
    """`_deserialize_persons` 认 `["K"]` 这种旧格式（字符串元素本身就是姓名）。

    预测器原先只认 dict：一条 `["K"]` 就把整个调用打成 AttributeError，而调用点没有兜底。
    """
    events = [
        _ev("2026-09-16T19:00:00", ["K"]),
        _ev("2026-09-17T19:30:00", ["K"]),
        _ev("2026-09-18T18:00:00", [{"name": "K"}]),
    ]
    out = predict_arrival_time(events, "K", min_days=3)
    assert out is not None and out["sample_days"] == 3, out


def test_malformed_persons_row_does_not_zero_the_batch():
    """一条坏数据只该废掉它自己，不许把整批一起归零。"""
    events = [
        _ev("2026-09-16T19:00:00", [{"name": "K"}]),
        _ev("2026-09-17T19:30:00", "not-a-list"),
        _ev_json("2026-09-18T18:00:00", "{坏了"),
        _ev("2026-09-19T19:00:00", [{"name": "K"}, None, 7]),
        _ev("2026-09-20T19:00:00", [{"name": ""}]),
        _ev("2026-09-21T18:30:00", [{"name": "K"}]),
    ]
    out = predict_arrival_time(events, "K", min_days=3)
    # 09-17（裸字符串不是数组）、09-18（JSON 解不出）、09-20（姓名是空串）各自作废；
    # 09-19 里 None/7 被跳过，同一条上的 "K" 仍然算数。
    assert out is not None and out["sample_days"] == 3, out


# ------------------------------------------------ 3. tz 形状与家庭墙钟
def test_mixed_naive_and_aware_same_day_does_not_crash():
    """现网 2026-09-18 同日既有 naive 又有 `+00:00`，`sorted()` 必须还能跑。"""
    events = [
        _ev("2026-09-16T19:00:00", [{"name": "K"}]),
        _ev("2026-09-16T13:00:00+00:00", [{"name": "K"}]),   # 家庭墙钟 21:00，同一天
        _ev("2026-09-17T19:30:00", [{"name": "K"}]),
        _ev("2026-09-18T18:00:00", [{"name": "K"}]),
    ]
    acts = predict_post_arrival_activities(events, "K", window_min=60)
    assert isinstance(acts, list), acts
    routine = predict_daily_routine(events, "K")
    assert routine["arrival"] is not None, routine


def test_aware_timestamps_are_measured_on_the_house_clock():
    """`11:00+00:00` 在 +8 是傍晚 19:00：按 UTC 小时算会掉进"上午"，到家判据整个错位。"""
    events = [
        _ev("2026-09-16T11:00:00+00:00", [{"name": "K"}]),
        _ev("2026-09-17T11:00:00+00:00", [{"name": "K"}]),
        _ev("2026-09-18T11:00:00+00:00", [{"name": "K"}]),
        _ev("2026-09-19T19:00:00", [{"name": "K"}]),
    ]
    at8 = predict_arrival_time(events, "K", min_days=3, tz_offset_hours=8.0)
    assert at8 is not None and at8["predicted_hour"] == 19.0, at8
    # 同一批数据换时区必须换读数：证明 `tz_offset_hours` 不是收下就丢的装饰参数。
    # 偏移 0 时那三天只剩 11:00 的上午出现，掉出午后判据窗口 ⇒ 走"当天最晚"兜底 ⇒ 11.0。
    at0 = predict_arrival_time(events, "K", min_days=3, tz_offset_hours=0.0)
    assert at0 is not None and at0["predicted_hour"] == 11.0, at0


# ------------------------------------------------ 4. data_days 的口径
def test_data_days_counts_structured_names_not_substrings():
    """`data_days` 原先用「人名字符串出现在 persons_json 里」数天数 ⇒ 短名被长名吃掉。

    "明" 会匹配 "小明" 的每一条。现网实测两个名出现子串口径与结构化口径不等
    （其中一个 13 天 vs 1 天）。返回体里这个数是要给消费方看的，口径必须说清。
    """
    events = [
        _ev("2026-09-16T19:00:00", [{"name": "小明"}]),
        _ev("2026-09-17T19:00:00", [{"name": "小明"}]),
        _ev("2026-09-18T19:00:00", [{"name": "明"}]),
        _ev("2026-09-19T19:00:00", [{"name": "明"}]),
        _ev("2026-09-20T19:00:00", [{"name": "明"}]),
    ]
    routine = predict_daily_routine(events, "明")
    assert routine["data_days"] == 3, (
        "data_days 用了子串口径：把「小明」的 2 天也算进「明」 ⇒ %r" % routine["data_days"])
    assert routine["arrival"]["sample_days"] == 3, routine


# ------------------------------------------------ 5. 顺序无关 + 家庭日切
def test_departure_is_the_last_appearance_not_the_first():
    """`departure` 原先靠"循环里最后一次赋值"取当天最晚，而门面是 DESC ⇒ 赋到最后拿到的是当天最早那次。

    现网 `list_behavior_events` 就是 `ORDER BY server_ts DESC`（store.py:2369），所以这条不是假想敌：
    同一批数据按现网顺序喂进去，离家预测会报成早 8 点。
    """
    days = ["2026-09-16", "2026-09-17", "2026-09-18"]
    events = []
    for day in days:
        events.append(_ev(f"{day}T22:30:00", [{"name": "K"}]))   # DESC：晚的在前
        events.append(_ev(f"{day}T08:00:00", [{"name": "K"}]))
    routine = predict_daily_routine(events, "K")
    assert routine["departure"] is not None, routine
    assert routine["departure"]["predicted_hour"] == 22.5, routine["departure"]
    assert routine["departure"]["sample_days"] == 3, routine["departure"]


def test_utc_event_crossing_midnight_lands_on_the_house_day():
    """`09-16T20:00+00:00` 在家里是 17 日凌晨 4 点：按天聚合要用换算后的日子，不是字符串前缀。

    截前缀会凭空多出"09-16 这天午后没出现 ⇒ 兜底取 04:00"这种假样本日，把中位数往下拽。
    """
    events = [
        _ev("2026-09-16T20:00:00+00:00", [{"name": "K"}]),   # 家庭墙钟 09-17 04:00
        _ev("2026-09-17T19:00:00", [{"name": "K"}]),
        _ev("2026-09-18T19:00:00", [{"name": "K"}]),
        _ev("2026-09-19T19:00:00", [{"name": "K"}]),
    ]
    out = predict_arrival_time(events, "K", min_days=3, tz_offset_hours=8.0)
    assert out is not None, out
    assert out["sample_days"] == 3, f"按字符串前缀切日会数出 4 天 ⇒ {out}"
    assert out["predicted_hour"] == 19.0, out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
