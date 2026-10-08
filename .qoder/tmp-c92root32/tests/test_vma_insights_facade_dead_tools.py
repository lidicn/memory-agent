"""门面（insights/api.py）死工具与入参吞没的回归锁。

发现的现场（容器内对生产 /app/src 实测，见 审计报告 §十六）：
- `define_activity` 门面签名是 `(name, rule, tags, room)`，而 MCP 按 ToolSpec 登记的
  8 个参数**位置传参**调用 → 先 `TypeError`；把签名喂对又撞
  `BehaviorService` 没有 `activities` 属性 → `AttributeError`。两层都被 `_degrade`
  吞成 `ok:false`，工具从来没成功过一次（`activity_rules` 里一条都没落）。
- `infer_activities(activities=...)` 这个 ToolSpec 声明的入参收进来就丢，调用方
  缩小范围的意图被静默忽略，拿到的仍是全量。

DCD 20261004 §三.6 裁6 Q2=A 之后，签名/入库/套用三段都通了，本文件随之分两层：
形状与入库（8 参数、ToolSpec 对齐、写进 `activity_rules`）继续锁死；回执文案从
「尚未套用」改为「下次生效」，并由 `test_registered_rule_is_read_by_the_current_engine`
核对现行引擎**真的**读了这张表——文案与读数必须同向，否则又是一次过度承诺。
"""

import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402
from memory_agent.tool_schema import SPEC_BY_NAME  # noqa: E402


def _svc():
    """真实 Store + 真实 Config：legacy 的 define_activity 会读 config.rooms，桩子喂不出这条路径。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    return st, InsightService(st, Config())


def test_define_activity_accepts_the_positional_shape_mcp_actually_uses():
    """MCP 用 8 个位置参调用；门面必须按 ToolSpec 的形状接下来并真的入库。"""
    st, svc = _svc()
    try:
        out = svc.define_activity("nap", "卧室", ["presence"], 12, 15, 1, 0.6, "午觉")
        assert out.get("ok") is True, out
        assert out.get("rule", {}).get("name") == "nap", out

        rules = st.list_activity_rules()
        assert len(rules) == 1, rules
        r = rules[0]
        assert r["name"] == "nap" and r["room"] == "卧室"
        assert r["tags"] == ["presence"], r
        assert (r["start_hour"], r["end_hour"], r["min_events"]) == (12, 15, 1), r
        assert abs(r["confidence"] - 0.6) < 1e-9, r
        assert r["enabled"] is True, r
    finally:
        st.close()
        os.remove(st.db_path)


def test_facade_define_activity_signature_matches_the_registered_toolspec():
    """签名漂移是这次事故的根因，直接把 ToolSpec 的参数表钉成判据。"""
    spec = SPEC_BY_NAME["define_activity"]
    spec_params = [p.name for p in spec.params]
    facade_params = list(inspect.signature(InsightService.define_activity).parameters)[1:]
    assert facade_params == spec_params, (facade_params, spec_params)


def test_define_activity_receipt_promise_matches_what_the_engine_now_reads():
    """裁6 Q2=A 之后，回执的两侧都不许过头：

    - 不能退回旧话「自动套用」却不写清套用的是哪张表、按什么口径；
    - 也不能把 legacy 的覆盖预检告警（`coverage_warning`）从 message 里抹掉——
      门面代写回执时整段替换 message，曾把「缺实体类型」这句诊断吞掉。
    """
    st, svc = _svc()
    try:
        out = svc.define_activity("nap", "卧室", ["presence"], 12, 15, 1, 0.6, "")
        msg = out.get("message") or ""
        assert out.get("ok") is True, out
        assert "activity_rules" in msg, msg
        assert "infer_activities 起生效" in msg, msg
        assert "自动套用" not in msg, msg
        assert "尚未套用" not in msg, msg
        assert out["rule"]["rule_id"] in msg, msg

        # 空库里 presence 无实体 → legacy 给的是诊断，门面必须原样带到 message 末尾
        assert out.get("coverage_warning"), out
        assert out["coverage_warning"] in msg, msg
    finally:
        st.close()
        os.remove(st.db_path)


def test_registered_rule_is_read_by_the_current_engine():
    """回执说「下次生效」，就必须真能核对到「现行引擎读了这条规则」。

    这一条是「注册了没人读」那起事故的反向锁：把规则写进 `activity_rules` 后，
    `infer_activities` 的 `rule_sources.activity_rules_table` 必须把它计进来。
    """
    st, svc = _svc()
    try:
        svc.define_activity("nap", "卧室", ["presence"], 12, 15, 1, 0.6, "")
        out = svc.infer_activities(start="2026-01-01T00:00:00", end="2026-01-01T23:59:59")
        assert out["rule_sources"]["activity_rules_table"] == 1, out
    finally:
        st.close()
        os.remove(st.db_path)


def _seed_events(st):
    st.insert_events([
        # 日间一段（13 点）+ 晚间一段（21 点）：中间留白 >=3h，两条片段可区分
        {"entity_id": "sensor.day_probe", "ts": "2026-01-01T13:00:00",
         "new_state": "on", "room": "客厅", "day": "2026-01-01"},
        {"entity_id": "sensor.day_probe", "ts": "2026-01-01T13:30:00",
         "new_state": "off", "room": "客厅", "day": "2026-01-01"},
        {"entity_id": "sensor.night_probe", "ts": "2026-01-01T21:00:00",
         "new_state": "on", "room": "客厅", "day": "2026-01-01"},
        {"entity_id": "sensor.night_probe", "ts": "2026-01-01T21:40:00",
         "new_state": "off", "room": "客厅", "day": "2026-01-01"},
    ])


def test_infer_activities_honors_the_activities_whitelist():
    """`activities` 是声明过的入参，必须真的缩小结果集，而不是收进就丢。"""
    st, svc = _svc()
    try:
        _seed_events(st)
        full = svc.infer_activities(start="2026-01-01T00:00:00", end="2026-01-01T23:59:59")
        names = {a.get("name") for a in full.get("activities", [])}
        assert "日间活动" in names and "晚间活动" in names, full

        one = svc.infer_activities(start="2026-01-01T00:00:00", end="2026-01-01T23:59:59",
                                   activities=["日间活动"])
        assert [a.get("name") for a in one.get("activities", [])] == ["日间活动"], one

        # 逗号字符串形状（ToolSpec 把该参数登记成 string）也要生效
        two_csv = svc.infer_activities(start="2026-01-01T00:00:00", end="2026-01-01T23:59:59",
                                       activities="日间活动,晚间活动")
        assert {a.get("name") for a in two_csv.get("activities", [])} == {"日间活动", "晚间活动"}

        # 白名单填了不存在的名字 → 空集（不能退化成"忽略过滤返回全量"）
        none_hit = svc.infer_activities(start="2026-01-01T00:00:00", end="2026-01-01T23:59:59",
                                        activities=["不存在的活动"])
        assert none_hit.get("activities") == [], none_hit
    finally:
        st.close()
        os.remove(st.db_path)
