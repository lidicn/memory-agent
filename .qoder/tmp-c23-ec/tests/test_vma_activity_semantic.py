"""MA-裁6「活动识别迁移缺口」的回归锁（DCD 20261004 §三.6）。

裁定四条，逐条对应本文件的用例：
- **Q1=A** 语义活动回归：legacy 的规则判定迁回新框架，时段启发式降级为
  「无标签设备兜底」的补充输出（每条带 `source`: semantic | heuristic）；
- **Q2=A** `define_activity` 回 ToolSpec 的 8 参数、注册走 `upsert_activity_rule`，
  并让**现行引擎真的读** `activity_rules`（这条以前是注册了没人读）；
- **Q3=A** 硬排除实体在新引擎的 `activity_matrix` 里被剔除，返回体带
  「已排除 N 个实体」的可见字段；
- **Q4（认可）** 门面每切一个方法到新实现，必须留 legacy vs 新实现的
  「返回键集合 + 语义枚举值集合 + 旁挂依赖（规则表/排除表）」三项对比读数，
  缺一项判红。
"""

import inspect
import os
import sys
from datetime import datetime, timedelta

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.activity import BUILTIN_ACTIVITIES  # noqa: E402
from memory_agent.insights.models import (InsightConfig, TimeRange, house_now,
                                          house_ts)  # noqa: E402
from memory_agent.insights.repository import StoreRepository  # noqa: E402
from memory_agent.insights.service import BehaviorService  # noqa: E402
from memory_agent.store import Store  # noqa: E402

DAY = "2026-01-05"


def _tmp_store():
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "ma_c6_%d.db" % os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    st = Store(tmp, tz_offset_hours=8.0)
    st.init_schema()
    return st


def _ev(eid, ts, state, room, name, domain=None):
    return {"entity_id": eid, "ts": ts, "new_state": state, "room": room,
            "domain": domain or eid.split(".")[0],
            "attrs": {"friendly_name": name}}


def _bath_events(st, day=DAY, hour=21):
    """洗澡的两个信号：卫生间存在传感器 20 分钟 + 卫生间热水器一段。"""
    st.insert_events([
        _ev("binary_sensor.bath_presence", "%sT%02d:00:00" % (day, hour), "on", "卫生间", "卫生间存在传感器"),
        _ev("binary_sensor.bath_presence", "%sT%02d:20:00" % (day, hour), "off", "卫生间", "卫生间存在传感器"),
        _ev("water_heater.bath_heater", "%sT%02d:01:00" % (day, hour), "on", "卫生间", "卫生间热水器"),
        _ev("water_heater.bath_heater", "%sT%02d:19:00" % (day, hour), "off", "卫生间", "卫生间热水器"),
    ])


def _svc(st):
    """事件先落库再建门面：EntityResolver 的目录来自 events 聚合。"""
    return InsightService(st, Config())


def _rows(out):
    return out.get("activities") or []


# ---------------------------------------------------------------- Q1 语义回归
def test_semantic_rule_fires_on_the_live_path():
    """内置语义规则（洗澡）必须真的出现在结果里——以前只有时段标签。"""
    st = _tmp_store()
    try:
        _bath_events(st)
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        assert out.get("ok") is True, out
        bath = [a for a in _rows(out) if a.get("activity") == "bath"]
        assert bath, out
        hit = bath[0]
        assert hit["source"] == "semantic", hit
        assert hit["name"] == "洗澡" and hit["room"] == "卫生间", hit
        assert hit["minutes"] >= 8.0, hit
        assert hit["confidence"] >= 0.55, hit
        assert hit["evidence"] and "热水器" in hit["evidence"], hit
        assert hit["day"] == DAY, hit
        assert hit["signals"] and hit["signals"][0]["satisfied"] is True, hit
    finally:
        st.close()
        os.remove(st.db_path)


def test_rule_window_anchors_at_house_midnight_not_the_tr_start():
    """查询区间从 half-day 开始时，同一段活动只能判一次。

    `split_days` 返回的是**裁剪后**的日段，起点通常不是零点；旧写法把裁剪起点当
    零点喂给 `_window_range`，于是 `window=(0,24)` 变成「17:00 → 次日 17:00」，
    相邻两天的窗口重叠，一次洗澡被判成两次（`by_name` 计数翻倍、NL 问答报多一次）。
    """
    st = _tmp_store()
    try:
        # 11:00 落在「次日」，而查询区间从前一日 17:00 开始：正是重叠的那一档
        _bath_events(st, day="2026-01-06", hour=11)
        svc = _svc(st)
        out = svc.infer_activities(start="2026-01-05T17:00:00", end="2026-01-06T17:00:00")
        bath = [a for a in _rows(out) if a.get("activity") == "bath"]
        assert len(bath) == 1, bath
        assert bath[0]["day"] == "2026-01-06", bath[0]
        assert out["summary"]["by_name"].get("洗澡") == 1, out["summary"]
    finally:
        st.close()
        os.remove(st.db_path)


def test_hour_segment_heuristic_survives_as_fallback_output():
    """时段启发式不许消失，但必须标明自己是「兜底」而不是语义结论。"""
    st = _tmp_store()
    try:
        st.insert_events([
            _ev("sensor.unlabeled_probe", "%sT13:00:00" % DAY, "on", "客厅", "无名探头"),
            _ev("sensor.unlabeled_probe", "%sT13:30:00" % DAY, "off", "客厅", "无名探头"),
            _ev("sensor.unlabeled_probe", "%sT21:00:00" % DAY, "on", "客厅", "无名探头"),
            _ev("sensor.unlabeled_probe", "%sT21:40:00" % DAY, "off", "客厅", "无名探头"),
        ])
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        names = {a.get("name") for a in _rows(out)}
        assert "日间活动" in names and "晚间活动" in names, out
        assert all(a.get("source") == "heuristic" for a in _rows(out)), out
        assert out["summary"]["heuristic"] == len(_rows(out))
        assert out["summary"]["semantic"] == 0, out
    finally:
        st.close()
        os.remove(st.db_path)


def test_semantic_and_heuristic_rows_share_the_readable_keys():
    """两类行必须能被同一份消费代码读：day/activity/name/时段/置信度/证据齐全。"""
    st = _tmp_store()
    try:
        _bath_events(st)
        st.insert_events([
            _ev("light.living", "%sT19:00:00" % DAY, "on", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT19:30:00" % DAY, "off", "客厅", "客厅灯", "light"),
        ])
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        rows = _rows(out)
        assert {a["source"] for a in rows} == {"semantic", "heuristic"}, rows
        for a in rows:
            for key in ("activity", "name", "day", "start_hour", "end_hour",
                        "start_ts", "end_ts", "confidence", "evidence",
                        "typical_window", "source", "custom"):
                assert key in a, (key, a)
    finally:
        st.close()
        os.remove(st.db_path)


# ---------------------------------------------------------------- Q2 规则表
def test_engine_reads_the_activity_rules_table():
    """define_activity 落的规则必须被现行引擎读到并套用（以前是注册了没人读）。"""
    st = _tmp_store()
    try:
        st.upsert_activity_rule({"name": "午睡", "room": "卧室", "tags": ["presence"],
                                 "start_hour": 12, "end_hour": 15, "min_events": 2,
                                 "confidence": 0.8, "note": "周末午睡"})
        st.insert_events([
            _ev("binary_sensor.bedroom_presence", "%sT13:00:00" % DAY, "on", "卧室", "卧室人体存在"),
            _ev("binary_sensor.bedroom_presence", "%sT13:10:00" % DAY, "off", "卧室", "卧室人体存在"),
            _ev("binary_sensor.bedroom_presence", "%sT14:00:00" % DAY, "on", "卧室", "卧室人体存在"),
            _ev("binary_sensor.bedroom_presence", "%sT14:10:00" % DAY, "off", "卧室", "卧室人体存在"),
        ])
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        nap = [a for a in _rows(out) if a.get("activity") == "午睡"]
        assert nap, out
        assert nap[0]["custom"] is True and nap[0]["source"] == "semantic", nap
        assert nap[0]["rule"] == "午睡", nap
        # 用户声明的置信度原样生效，不被引擎算出来的数覆盖
        assert abs(nap[0]["confidence"] - 0.8) < 1e-9, nap
        assert out["rule_sources"]["activity_rules_table"] == 1, out
        assert out["rule_sources"]["custom_applied"] == 1, out
        assert out["rule_sources"]["builtin"] == len(BUILTIN_ACTIVITIES), out
    finally:
        st.close()
        os.remove(st.db_path)


def test_disabled_rule_rows_are_not_applied():
    """`enabled=0` 的规则不得进入引擎（引擎读的是 enabled_only=True）。"""
    st = _tmp_store()
    try:
        st.upsert_activity_rule({"name": "午睡", "room": "卧室", "tags": ["presence"],
                                 "start_hour": 12, "end_hour": 15, "min_events": 1,
                                 "confidence": 0.8, "enabled": False})
        st.insert_events([
            _ev("binary_sensor.bedroom_window", "%sT13:00:00" % DAY, "on", "卧室", "卧室窗台"),
            _ev("binary_sensor.bedroom_window", "%sT13:10:00" % DAY, "off", "卧室", "卧室窗台"),
        ])
        assert st.list_activity_rules(enabled_only=True) == [], st.list_activity_rules()
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        assert [a for a in _rows(out) if a.get("activity") == "午睡"] == [], out
        assert out["rule_sources"]["activity_rules_table"] == 0, out
    finally:
        st.close()
        os.remove(st.db_path)


def test_rule_tag_condition_filters_events_instead_of_being_decorative():
    """`tags_json` 是判定条件，不是展示标签：没有对应标签的规则必须命中 0 条。

    Q4 读数②在生产库上量到的假命中：一条要求 `nonexistent_tag` 的规则（房间为空）
    因为引擎只看「房间 + 频次」，把全屋事件判成了它。legacy 一侧一直是按标签求交
    （insights_legacy.py:2782），所以这条是新引擎搬运时丢的语义。
    """
    st = _tmp_store()
    try:
        st.upsert_activity_rule({"name": "有据活动", "room": "卧室", "tags": ["presence"],
                                 "start_hour": 12, "end_hour": 15, "min_events": 1})
        st.upsert_activity_rule({"name": "无据活动", "room": "卧室",
                                 "tags": ["nonexistent_tag"],
                                 "start_hour": 12, "end_hour": 15, "min_events": 1})
        st.insert_events([
            _ev("binary_sensor.bedroom_presence", "%sT13:00:00" % DAY, "on", "卧室", "卧室人体存在"),
            _ev("binary_sensor.bedroom_presence", "%sT13:10:00" % DAY, "off", "卧室", "卧室人体存在"),
        ])
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        names = {a.get("activity") for a in _rows(out)}
        assert "有据活动" in names, out
        assert "无据活动" not in names, out
        # 规则确实被读进来了（不是靠"没加载"蒙对）
        assert out["rule_sources"]["activity_rules_table"] == 2, out
        rule = svc.core._rule_from_row({"name": "无据活动", "room": "卧室",
                                        "tags": ["nonexistent_tag"], "min_events": 1})
        assert rule.require_tags == ["nonexistent_tag"], rule.to_dict()
    finally:
        st.close()
        os.remove(st.db_path)


def test_activities_input_reaches_the_engine_instead_of_being_dropped():
    """`activities` 必须在推断前参与规则筛选（Q1 的入参半边）。"""
    st = _tmp_store()
    try:
        _bath_events(st)
        st.insert_events([
            _ev("light.living", "%sT19:00:00" % DAY, "on", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT19:30:00" % DAY, "off", "客厅", "客厅灯", "light"),
            # 一条独立可命中的内置规则（学习：书房存在 ≥30 分钟）。过滤失效时它一定会冒出来，
            # 只靠 bath 自身无法区分「筛过」和「没筛」。
            _ev("binary_sensor.study_presence", "%sT13:00:00" % DAY, "on", "书房", "书房存在传感器"),
            _ev("binary_sensor.study_presence", "%sT13:40:00" % DAY, "off", "书房", "书房存在传感器"),
        ])
        svc = _svc(st)
        tr = svc._tr(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        assert "activities" in inspect.signature(BehaviorService.infer_activities).parameters
        # nlquery.py 就是按关键字传这个参数的，签名不收时整条路由静默失败
        out = svc.core.infer_activities(tr, rooms="", activities=["bath"])
        assert {a["source"] for a in _rows(out)} == {"semantic"}, out
        assert {a["activity"] for a in _rows(out)} == {"bath"}, out
        assert "study" not in out["activity_types"], out
        assert out["rule_sources"]["selected"] == 1, out
        assert out["filters"]["activities"] == ["bath"], out
        # 对照：不加过滤时同一份数据会多出「学习」，证明上一条断言不是恒真
        wide = svc.core.infer_activities(tr, rooms="")
        assert "study" in wide["activity_types"], wide
    finally:
        st.close()
        os.remove(st.db_path)


def test_nl_activity_route_answers_instead_of_degrading():
    """route=activity 这条自然语言链路：以前 `data["total"]` 必 KeyError，被兜成"查询失败"。"""
    st = _tmp_store()
    try:
        anchor = house_now() - timedelta(hours=6)
        anchor = anchor.replace(minute=0, second=0, microsecond=0)
        st.insert_events([
            _ev("binary_sensor.bath_presence", anchor.isoformat(), "on", "卫生间", "卫生间存在传感器"),
            _ev("binary_sensor.bath_presence", (anchor + timedelta(minutes=20)).isoformat(),
                "off", "卫生间", "卫生间存在传感器"),
            _ev("water_heater.bath_heater", (anchor + timedelta(minutes=1)).isoformat(),
                "on", "卫生间", "卫生间热水器"),
            _ev("water_heater.bath_heater", (anchor + timedelta(minutes=19)).isoformat(),
                "off", "卫生间", "卫生间热水器"),
        ])
        svc = _svc(st)
        out = svc.ask_memory("最近洗澡了吗", days=1)
        answer = out.get("answer") or ""
        assert out.get("route") == "activity", out
        assert "查询失败" not in answer, out
        assert "洗澡" in answer, answer
        assert "1 次活动" in answer, answer
        assert "{" not in answer, answer   # summary 是 dict，不许再整份打进话术
    finally:
        st.close()
        os.remove(st.db_path)


# ---------------------------------------------------------------- Q3 硬排除
def test_hard_exclusion_drops_events_and_is_visible():
    """signal_exclusions 的 exclude 行必须真的从 activity_matrix 里剔掉，且数量可见。"""
    st = _tmp_store()
    try:
        st.insert_events([
            _ev("switch.noisy_humidifier", "%sT20:00:00" % DAY, "on", "客厅", "加湿器缺水"),
            _ev("switch.noisy_humidifier", "%sT20:05:00" % DAY, "off", "客厅", "加湿器缺水"),
            _ev("switch.noisy_humidifier", "%sT20:10:00" % DAY, "on", "客厅", "加湿器缺水"),
            _ev("switch.noisy_humidifier", "%sT20:15:00" % DAY, "off", "客厅", "加湿器缺水"),
            _ev("light.living", "%sT20:20:00" % DAY, "on", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT20:40:00" % DAY, "off", "客厅", "客厅灯", "light"),
        ])
        st.upsert_signal_exclusion("switch.noisy_humidifier", scope="all", reason="垄断型噪声")
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        assert out["excluded_entities"]["count"] == 1, out
        assert out["excluded_entities"]["entity_ids"] == ["switch.noisy_humidifier"], out
        assert out["excluded_entities"]["sources"]["signal_exclusions"] == 1, out
        # 剩下的事件数就是未被排除的那两条：被排除实体一条都不许留
        assert sum(a["events"] for a in _rows(out)) == 2, out
        assert all("加湿器" not in (a.get("evidence") or "") for a in _rows(out)), out
    finally:
        st.close()
        os.remove(st.db_path)


def test_classification_exclusions_do_not_drop_events():
    """`is_automation` / `not_automation` 是分类标注，不是排除——误当排除会抹掉正常设备。"""
    st = _tmp_store()
    try:
        st.insert_events([
            _ev("light.living", "%sT20:20:00" % DAY, "on", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT20:40:00" % DAY, "off", "客厅", "客厅灯", "light"),
        ])
        st.upsert_signal_exclusion("light.living", scope="presence",
                                   exclusion_type="is_automation")
        svc = _svc(st)
        out = svc.infer_activities(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        assert out["excluded_entities"]["count"] == 0, out
        assert sum(a["events"] for a in _rows(out)) == 2, out
    finally:
        st.close()
        os.remove(st.db_path)


def test_rule_sources_reports_the_scan_cap_that_limits_the_semantic_side():
    """语义一侧受 `max_scan` 截断必须自报四个数：真值 / 扫了多少 / 上限 / 是否可能被截。

    生产库 30 天窗实测（`scripts/probe_activity_coverage_gap.py`）：切片 30,000 条只覆盖窗口
    前 20 小时，全窗口真值 956,988 条——没有这几个数，"语义产出比兜底少"就分不清是规则不
    匹配设备命名，还是被上限饿死。兜底一侧走 `activity_matrix`（SQL 聚合，不受上限约束）。
    """
    st = _tmp_store()
    try:
        _bath_events(st)
        st.insert_events([
            _ev("light.living", "%sT19:00:00" % DAY, "on", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT19:10:00" % DAY, "off", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT19:20:00" % DAY, "on", "客厅", "客厅灯", "light"),
            _ev("light.living", "%sT19:30:00" % DAY, "off", "客厅", "客厅灯", "light"),
        ])
        day_window = dict(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)

        capped = InsightConfig()
        capped.max_scan = 6
        rs = InsightService(st, capped).infer_activities(**day_window)["rule_sources"]
        assert rs["events_total"] == 8, rs
        assert rs["events_scanned"] == 6, rs
        assert rs["scan_limit"] == 6, rs
        assert rs["scan_truncated"] is True, rs

        full = InsightConfig()
        full.max_scan = 50
        rs2 = InsightService(st, full).infer_activities(**day_window)["rule_sources"]
        assert rs2["events_total"] == 8 and rs2["events_scanned"] == 8, rs2
        assert rs2["scan_truncated"] is False, rs2
    finally:
        st.close()
        os.remove(st.db_path)


def test_not_in_drops_only_the_named_entities():
    """硬排除只点掉被点名的实体；空值列（room/domain 为 `''`）不许被连带误伤。

    两半各咬一处：
    - SQL 形状：排除集为空 ⇒ 一条 WHERE 都不加（否则 `NOT IN ()` 是语法错误）；
      非空 ⇒ 裸 `NOT IN`，**不带** `OR col IS NULL`——events 的 entity_id/room/domain
      是 `NOT NULL DEFAULT ''`，NULL 分支永远走不到，留着只会掩盖真问题。
    - 读取行为：`activity_matrix` 里被点名实体消失，room 为 `''` 的另一条保留。
    """
    where, params = [], []
    StoreRepository._add_not_in(where, params, "entity_id", [])
    assert where == [] and params == [], (where, params)

    StoreRepository._add_not_in(where, params, "entity_id", "light.a, light.b")
    assert where == ["entity_id NOT IN (?,?)"], where
    assert params == ["light.a", "light.b"], params

    st = _tmp_store()
    try:
        st.insert_events([
            {"entity_id": "sensor.no_room", "ts": "%sT20:00:00" % DAY, "new_state": "on",
             "domain": "sensor", "attrs": {"friendly_name": "无房间探头"}},
            _ev("light.living", "%sT20:20:00" % DAY, "on", "客厅", "客厅灯", "light"),
        ])
        svc = _svc(st)
        tr = svc._tr(start="%sT00:00:00" % DAY, end="%sT23:59:59" % DAY)
        all_rows = svc.repo.activity_matrix(tr)
        excluded = svc.repo.activity_matrix(tr, exclude_entity_ids=["light.living"])
        assert sum(r["count"] for r in all_rows) == 2, all_rows
        assert sum(r["count"] for r in excluded) == 1, excluded
        assert {r["domain"] for r in excluded} == {"sensor"}, excluded
    finally:
        st.close()
        os.remove(st.db_path)


# ---------------------------------------------------------------- Q4 读数
def test_three_readings_legacy_vs_new_are_all_present():
    """裁6 Q4：返回键集合 / 语义枚举值集合 / 旁挂依赖，三项读数缺一项判红。"""
    st = _tmp_store()
    try:
        _bath_events(st)
        st.upsert_activity_rule({"name": "午睡", "room": "卧室", "tags": ["presence"],
                                 "start_hour": 12, "end_hour": 15, "min_events": 2,
                                 "confidence": 0.8})
        st.upsert_signal_exclusion("switch.noisy_humidifier", scope="all")
        svc = _svc(st)
        start, end = "%sT00:00:00" % DAY, "%sT23:59:59" % DAY
        new = svc.infer_activities(start=start, end=end)
        old = svc.legacy.infer_activities(start=start, end=end)

        # ① 返回键集合：legacy 的三条承诺键在新返回体里必须同名存在
        key_reading = {"legacy": sorted(old), "new": sorted(new),
                       "missing": sorted(set(old) - set(new))}
        assert {"total_activities", "activity_types", "window"} <= set(new), key_reading

        # ② 语义枚举值集合：两侧都能算出活动类型集，差集如实可核对
        enum_reading = {"legacy_types": old["activity_types"],
                        "new_types": new["activity_types"]}
        assert isinstance(new["activity_types"], list) and "bath" in new["activity_types"], enum_reading

        # ③ 旁挂依赖：规则表 / 排除表的数必须等于库里的真值
        assert new["rule_sources"]["activity_rules_table"] == \
            len(st.list_activity_rules(enabled_only=True)), new["rule_sources"]
        assert new["excluded_entities"]["count"] == len(
            [x for x in st.list_signal_exclusions(include_revoked=False)
             if x["exclusion_type"] == "exclude"]), new["excluded_entities"]
    finally:
        st.close()
        os.remove(st.db_path)


def test_split_days_cuts_at_house_wall_clock_midnight():
    """语义引擎按天推窗口，日界必须落在家庭墙钟零点。

    容器机器时区是 UTC、家庭墙钟 +8：旧写法 `datetime.fromtimestamp` 把日界切在
    UTC 零点（= 家庭 08:00），跨零点的睡眠段被劈成两段、`day_key` 又按家庭墙钟归日，
    两侧口径不一致。取「家庭凌晨 02:00 起」这种 UTC 上还在前一天的起点，
    旧写法会多切出一段并把日期标签写错一天——本用例因此在容器里对旧实现判红
    （本机 +8 与家庭墙钟同侧，改回旧写法也判不出红，属等价位，须由容器权威跑）。
    """
    tr = TimeRange(datetime(2026, 1, 5, 2, 0), datetime(2026, 1, 6, 3, 0))
    days = tr.split_days()
    assert [d for d, _s, _e in days] == ["2026-01-05", "2026-01-06"], days
    assert abs(days[0][1] - house_ts(datetime(2026, 1, 5, 2, 0))) < 1e-6, days
    assert abs(days[0][2] - house_ts(datetime(2026, 1, 6, 0, 0))) < 1e-6, days
    assert abs(days[1][1] - house_ts(datetime(2026, 1, 6, 0, 0))) < 1e-6, days
    assert abs(days[1][2] - house_ts(datetime(2026, 1, 6, 3, 0))) < 1e-6, days
