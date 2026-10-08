"""run_analysis_template 引擎测试。

不依赖真实数据库 / LLM：用桩 rt 模拟 runtime，验证
- 模板模型（metric/default_days/interpretation）序列化往返
- run_template 按 metric 正确分发到对应算力并返回结构化结果
- _build_summary 对四种 metric 的解读话术
- run_analysis_template 已在 MCP 注册、route_question 指向 plan_question
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.templates import (  # noqa: E402
    BehaviorInsight,
    EntityQuery,
    run_template,
)


# ── 桩 ─────────────────────────────────────────────────────────────────────

class _FakeInsights:
    """模拟 InsightService 的算力方法，返回固定结构，便于断言分发逻辑。"""

    def resolve_range(self, days=7, start="", end=""):
        return "2026-08-01T00:00:00", "2026-08-08T00:00:00", {
            "start": "2026-08-01T00:00:00",
            "end": "2026-08-08T00:00:00",
            "days": 7,
            "timezone": "UTC+8",
        }

    def name_map(self):
        return {}

    def _fallback_name(self, eid):
        return eid

    def _usage_one(self, entity_id, start_iso, end_iso, allow_on, debounce, include_timeline, time_range=""):
        return {
            "entity_id": entity_id,
            "total_on_human": "1小时",
            "sessions": 2,
            "daily_average_human": "0.5小时",
            "total_seconds": 3600,
            "timeline": [{"ts": start_iso}] if include_timeline else [],
        }

    def _usage_by_attr(self, entity_id, attribute, value, pattern, start_iso, end_iso, debounce=60, include_timeline=True, time_range=""):
        return {
            "entity_id": entity_id,
            "attribute": attribute,
            "value": value,
            "total_on_human": "30分钟",
            "sessions": 1,
            "daily_average_human": "30分钟",
            "total_seconds": 1800,
            "timeline": [{"ts": start_iso}] if include_timeline else [],
        }

    def _count_by_filter(self, entity_id, attribute, value, pattern, start_iso, end_iso):
        return {
            "entity_id": entity_id,
            "attribute": attribute,
            "value": value,
            "match_count": 5,
            "by_day_count": {"2026-08-05": 3, "2026-08-06": 2},
        }

    def _numeric_sum(self, rt, ins, eq, start_iso, end_iso):
        return {
            "entity_id": eq.entity_id,
            "unit": "L",
            "liters": 12.5,
            "count": 3,
            "total_value": 12.5,
            "by_day_value": {"2026-08-05": 6.0, "2026-08-06": 6.5},
        }


class _FakeTemplates:
    def __init__(self, tpl):
        self._tpl = tpl

    def get(self, template_id):
        return self._tpl if self._tpl.id == template_id else None

    def list_all(self):
        return [self._tpl]


class _FakeRT:
    def __init__(self, tpl):
        self.templates = _FakeTemplates(tpl)
        self.insights = _FakeInsights()


def _tpl(metric, attribute="", value="", pattern="exact", interpretation=""):
    return BehaviorInsight(
        id="tpl_x",
        name="测试模板",
        description="desc",
        category="media",
        entities=[EntityQuery(entity_id="sensor.test", attribute=attribute, pattern=pattern, value=value, metric=metric)],
        default_days=7,
        interpretation=interpretation,
    )


# ── 序列化 ─────────────────────────────────────────────────────────────────

def test_template_model_roundtrip():
    t = _tpl("duration", attribute="state", value="on", interpretation="{name}：{total_human}")
    d = t.to_dict()
    assert d["entities"][0]["metric"] == "duration"  # metric 挂在 EntityQuery 上
    assert d["default_days"] == 7
    assert d["interpretation"] == "{name}：{total_human}"
    t2 = BehaviorInsight.from_dict(d)
    assert t2.default_days == 7
    assert t2.interpretation == t.interpretation
    assert t2.entities[0].metric == "duration"


# ── 分发 ───────────────────────────────────────────────────────────────────

def test_run_template_duration_state():
    rt = _FakeRT(_tpl("duration", attribute="state", value="on"))
    out = run_template(rt, "tpl_x", days=7)
    assert out["ok"] is True
    r = out["entities"][0]["result"]
    assert r["total_seconds"] == 3600
    assert "累计 1小时" in out["summary_text"]


def test_run_template_duration_attr():
    rt = _FakeRT(_tpl("duration", attribute="source", value="xbox"))
    out = run_template(rt, "tpl_x")
    assert out["ok"]
    assert out["entities"][0]["result"]["attribute"] == "source"


def test_run_template_count():
    rt = _FakeRT(_tpl("count", attribute="state", value="motion"))
    out = run_template(rt, "tpl_x")
    assert out["entities"][0]["result"]["match_count"] == 5
    assert "命中 5 次" in out["summary_text"]


def test_run_template_numeric_sum(monkeypatch):
    from memory_agent import templates as tpl_mod

    def _fake_num(rt, ins, eq, start_iso, end_iso):
        return {
            "entity_id": eq.entity_id,
            "unit": "mL",
            "total_liters": 12.5,
            "count": 3,
            "total_value": 12500,
            "by_day_value": {"2026-08-05": 6.0, "2026-08-06": 6.5},
        }

    monkeypatch.setattr(tpl_mod, "_numeric_sum", _fake_num)
    rt = _FakeRT(_tpl("numeric_sum"))
    out = run_template(rt, "tpl_x")
    assert "共 12.5 升（3 次）" in out["summary_text"]


def test_run_template_interpretation_format():
    rt = _FakeRT(_tpl("duration", attribute="state", value="on", interpretation="{name}（{window}）：{body}"))
    out = run_template(rt, "tpl_x")
    assert "测试模板（" in out["summary_text"]
    assert "累计 1小时" in out["summary_text"]


def test_run_template_empty_entities():
    t = BehaviorInsight(id="tpl_empty", name="空", description="", category="media", entities=[], default_days=7)
    rt = _FakeRT(t)
    out = run_template(rt, "tpl_empty")
    assert out["ok"] and out["entities"] == []


def test_run_template_not_found():
    rt = _FakeRT(_tpl("duration"))
    out = run_template(rt, "nope")
    assert out["ok"] is False and "模板不存在" in out["error"]


# ── 注册一致性（轻量，可离线）──────────────────────────────────────────────

def test_run_analysis_template_registered_in_spec():
    from memory_agent.tool_schema import SPEC_BY_NAME
    assert "run_analysis_template" in SPEC_BY_NAME
    spec = SPEC_BY_NAME["run_analysis_template"]
    assert "mcp" in spec.expose
    param_names = [p.name for p in spec.params]
    assert "template_id" in param_names
    assert spec.params[param_names.index("template_id")].required is True


def test_route_question_maps_to_plan_question():
    from memory_agent.tool_schema import SPEC_BY_NAME
    assert SPEC_BY_NAME["route_question"].method == "plan_question"


mcp_server_mod = None
try:
    import memory_agent.mcp_server as mcp_server_mod  # noqa: E402
except Exception:  # pragma: no cover - 本地无 mcp 时跳过
    mcp_server_mod = None

skip_mcp = pytest.mark.skipif(
    mcp_server_mod is None, reason="mcp 不可用，跳过 MCP 注册检查"
)


@skip_mcp
def test_run_analysis_template_function_registered():
    # run_analysis_template 是 _build_server() 内的 @mcp.tool() 嵌套函数，
    # 不在模块级；验证源码中有定义且 spec 已登记即可。
    import inspect
    src = inspect.getsource(mcp_server_mod)
    assert "def run_analysis_template(" in src
    from memory_agent.tool_schema import SPEC_BY_NAME
    assert "run_analysis_template" in SPEC_BY_NAME


# ── 内置「空调时长」聚合模板（ADM 联动计划第 4 步 ②）────────────────────────

def _builtin(template_id: str):
    from memory_agent.templates import BUILTIN_INSIGHTS
    return next((t for t in BUILTIN_INSIGHTS if t.id == template_id), None)


def test_builtin_ac_runtime_template_shape():
    t = _builtin("ac_runtime_daily")
    assert t is not None, "计划卡 ② 要求内置「空调时长」聚合模板"
    assert t.category == "climate"
    assert len(t.entities) >= 2, "多房间汇总才有意义，单实体不算聚合"
    for eq in t.entities:
        assert eq.entity_id.startswith("climate."), eq.entity_id
        assert eq.metric == "duration"
        assert eq.attribute == "state"
        # value 留空才走 OFF_STATES 判定；写死 "cool" 会把 heat/auto/dry 模式漏掉
        assert eq.value in ("", None), eq.value
        # 不裁剪时段：00:00-23:59 会每天丢掉 23:59-00:00 这段运行时长
        assert eq.time_range == "", eq.time_range
        assert eq.logical_id, "必须带逻辑引用，HA 重登导致实体漂移时才能自愈"
    assert "{window}" in t.interpretation
    # 多实体模板若只用 {total_human}，话术会退化成「只报第一台」
    assert "{body}" in t.interpretation, t.interpretation


def test_builtin_ac_runtime_template_roundtrip_and_builtin_protection(tmp_path):
    from memory_agent.templates import BehaviorInsight, TemplateManager
    t = _builtin("ac_runtime_daily")
    t2 = BehaviorInsight.from_dict(t.to_dict())
    assert [e.logical_id for e in t2.entities] == [e.logical_id for e in t.entities]
    assert [e.value for e in t2.entities] == ["" for _ in t.entities]
    assert [e.time_range for e in t2.entities] == ["" for _ in t.entities]
    mgr = TemplateManager(str(tmp_path))
    assert mgr.is_builtin("ac_runtime_daily") is True
    assert mgr.get("ac_runtime_daily") is not None
    assert mgr.delete("ac_runtime_daily") is False, "内置模板不可删"


def test_run_template_ac_runtime_aggregates_every_room():
    t = _builtin("ac_runtime_daily")
    rt = _FakeRT(t)
    out = run_template(rt, "ac_runtime_daily")
    assert out["ok"] is True
    assert len(out["entities"]) == len(t.entities)
    assert out["summary_text"].count("累计 1小时") == len(t.entities)
    assert "空调运行情况" in out["summary_text"]
    assert out["template"]["id"] == "ac_runtime_daily"


def test_run_template_ac_runtime_prefers_logical_ref_resolution():
    """身份层给出新实体时，模板必须用新实体而不是写死的 entity_id。"""
    t = _builtin("ac_runtime_daily")
    mapping = {
        eq.logical_id: [f"climate.migrated_{i}"]
        for i, eq in enumerate(t.entities)
    }
    rt = _FakeRT(t)
    rt.identity = _FakeIdentity(mapping)
    out = run_template(rt, "ac_runtime_daily")
    used = [e["entity_id"] for e in out["entities"]]
    assert used == [f"climate.migrated_{i}" for i in range(len(t.entities))]
    assert all(e["stale"] is False for e in out["entities"])


class _FakeIdentity:
    def __init__(self, mapping):
        self._mapping = mapping

    def resolve(self, ref):
        eids = self._mapping.get(ref) or []
        return (list(eids), None) if eids else ([], "unresolved")
