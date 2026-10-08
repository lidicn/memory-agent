"""审计 20261002（`doc/审计报告/审计报告.md`）新发现项的回归锁。

覆盖：
- 新发现 1：配置热更新必须重建洞察链路的下游对象（repo / core / nl / legacy）；
- 新发现 2：`_safe_entities()` 的静默降级必须**可见**（entities_loaded 状态 + status()）；
- 新发现 3：工具登记表不允许「声称有后端、实际没有派发目标」，且导入期即校验；
- 新发现 5：schema 工具动态注册失败改为硬性失败，不再 warning 后放行残缺工具面。
"""

from __future__ import annotations

import types

import pytest

from memory_agent.insights import InsightService
from memory_agent.insights.models import InsightConfig
from memory_agent.tool_schema import (SPEC_BY_NAME, TOOL_SPECS, ToolSpec,
                                      register_simple_tools, validate_specs)


def _app_config(**overrides):
    """模拟生产侧 app Config（非 InsightConfig，带 rooms / tz_offset_hours / …）。"""
    base = dict(tz_offset_hours=8, default_days=7, default_limit=50,
                insight_cache_ttl=60.0, rooms={"客厅": ["media_player.tv"]})
    base.update(overrides)
    return types.SimpleNamespace(**base)


class _Store:
    """只实现 db_query 的最小 Store 替身。"""

    def __init__(self, rows=None, boom=False):
        self.rows = rows if rows is not None else []
        self.boom = boom
        self.calls = 0

    def db_query(self, sql, params=()):
        self.calls += 1
        if self.boom:
            raise RuntimeError("database is locked")
        return list(self.rows)


def _entity_rows():
    return [{"entity_id": "media_player.tv", "room": "客厅", "domain": "media_player",
             "attrs_json": '{"friendly_name": "客厅电视", "unit_of_measurement": ""}',
             "total": 12}]


# ── 新发现 1：热更新重建下游 ────────────────────────────────────────────────

def test_reload_config_rebuilds_repo_core_and_nl():
    store = _Store(_entity_rows())
    svc = InsightService(store, _app_config(default_days=7))
    old_repo, old_core, old_nl = svc.repo, svc.core, svc.nl

    svc.reload_config(_app_config(default_days=30))

    assert svc.config.default_days == 30
    # 下游必须换持有新 config 的实例：只改 .config 字段是过去漏掉的那半步
    assert svc.repo is not old_repo and svc.repo.config.default_days == 30
    assert svc.core is not old_core and svc.core.config is svc.config
    assert svc.nl is not old_nl and svc.nl.service is svc.core
    assert svc.nl.resolver is svc.resolver


def test_reload_config_follows_insight_cache_ttl_in_legacy():
    store = _Store()
    svc = InsightService(store, _app_config(insight_cache_ttl=60.0))
    assert svc.legacy._CACHE_TTL == 60.0

    svc.reload_config(_app_config(insight_cache_ttl=120.0))

    assert svc.legacy._CACHE_TTL == 120.0
    assert svc.legacy.config.insight_cache_ttl == 120.0


def test_reload_config_keeps_injected_repository():
    """测试注入的 repo 不归本对象所有，热更新时不应被替换掉。"""
    store = _Store()

    class _Repo:
        def __init__(self):
            self.invalidated = 0

        def list_entities(self):
            return []

        def invalidate(self):
            self.invalidated += 1

    repo = _Repo()
    svc = InsightService(store, _app_config(), repository=repo)
    svc.reload_config(_app_config(default_days=3))
    assert svc.repo is repo


def test_runtime_reload_calls_insights_reload_config():
    """runtime 里不允许再退回「只赋值 .config」的写法。"""
    import inspect

    from memory_agent import runtime

    src = inspect.getsource(runtime.AppRuntime.reload_config)
    assert "insights.reload_config(" in src
    assert "self.insights.config = " not in src


# ── 新发现 2：实体目录降级可见 ─────────────────────────────────────────────

def test_entities_loaded_true_when_catalog_reads():
    svc = InsightService(_Store(_entity_rows()), _app_config())
    assert svc.entities_loaded is True
    assert svc.entities_error == ""
    assert svc.resolver.all()


def test_entity_failure_is_visible_not_silent():
    """契约要求查询不外抛，但失败状态必须能被 /api/health 看到。"""
    svc = InsightService(_Store(boom=True), _app_config())
    assert svc.entities_loaded is False
    assert svc.entities_error.startswith("RuntimeError:")
    assert svc.status()["entities_loaded"] is False
    # 仍然降级成空结果而不是异常（兼容契约）
    assert svc.entity_catalog()["entities"] == []


def test_reload_config_retries_the_entity_catalog():
    store = _Store(boom=True)
    svc = InsightService(store, _app_config())
    assert svc.entities_loaded is False

    store.boom = False
    store.rows = _entity_rows()
    svc.reload_config(_app_config(default_days=30))

    assert svc.entities_loaded is True
    assert svc.entities_error == ""
    assert svc.resolver.all()


def test_runtime_health_exposes_insights_block():
    import inspect

    from memory_agent import runtime

    assert '"insights": self.insights.status(),' in inspect.getsource(runtime.AppRuntime.health)


# ── 新发现 3 + 5：schema 登记表一致性与注册失败硬抛 ────────────────────────

def test_current_tool_spec_table_is_consistent():
    validate_specs()          # 不应抛
    for name in ("get_device_usage_summary", "get_room_behavior_summary",
                 "get_member_daily_pattern"):
        spec = SPEC_BY_NAME[name]
        # 这三条只有 MCP 手写函数体，不能再声称有 rt.store.<method> 派发目标
        assert spec.service == "static", name
        assert spec.generated is False, name


@pytest.mark.parametrize("mutate,keyword", [
    ({"service": "store", "method": ""}, "method 为空"),
    ({"service": "static", "method": "", "generated": True}, "generated=True"),
    ({"service": "static", "method": "", "expose": ("mcp", "builtin")}, "派发目标"),
])
def test_validate_specs_rejects_self_contradictory_entries(mutate, keyword):
    bad = ToolSpec(name="x_bad", summary="", description="", group="g",
                   params=[], service="insights", method="ok_method",
                   expose=("mcp",), generated=False)
    for k, v in mutate.items():
        setattr(bad, k, v)
    with pytest.raises(ValueError) as exc:
        validate_specs([bad])
    assert keyword in str(exc.value)


def test_validate_specs_rejects_duplicate_names():
    dup = ToolSpec(name="dup", summary="", description="", group="g",
                   params=[], service="insights", method="m", expose=("mcp",))
    with pytest.raises(ValueError, match="重复登记"):
        validate_specs([dup, dup])


class _FakeMCP:
    def __init__(self, fail=False):
        self.fail = fail
        self.registered = []

    def tool(self, *a, **kw):
        outer = self

        def deco(fn):
            if outer.fail:
                raise TypeError("annotation not supported")
            outer.registered.append(fn.__name__)
            return fn
        return deco


def _generated_mcp_names():
    return [s.name for s in TOOL_SPECS if s.generated and "mcp" in s.expose]


def test_register_simple_tools_registers_generated_subset():
    names = _generated_mcp_names()[:2]
    mcp = _FakeMCP()
    registered = register_simple_tools(mcp, (lambda: None), names=names)
    assert sorted(registered) == sorted(names)
    assert sorted(mcp.registered) == sorted(names)


def test_register_simple_tools_raises_on_registration_failure():
    mcp = _FakeMCP(fail=True)
    with pytest.raises(RuntimeError, match="schema 工具注册失败"):
        register_simple_tools(mcp, (lambda: None), names=_generated_mcp_names()[:1])


def test_register_simple_tools_raises_on_unmatched_name():
    """请求了一个根本不会被注册的名字 = 目录与实现割裂，同样启动期红。"""
    with pytest.raises(RuntimeError, match="未被注册"):
        register_simple_tools(_FakeMCP(), (lambda: None), names=["no_such_tool"])


def test_mcp_server_no_longer_swallows_registration_errors():
    import inspect

    from memory_agent import mcp_server

    src = inspect.getsource(mcp_server)
    assert "注册 schema 工具失败" not in src
