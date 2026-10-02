"""第七轮审计（关节与连接处）回归锁。

审计包：``doc/审计报告/memory-agent-第七轮审计-关节与连接处*.zip``
本轮六个发现全部落在组件之间的接缝上，因此这里的测试也只盯接缝：
配置 → 组件、MA → Chroma、容器时钟 → 家庭墙钟、HTTP 连接 → 复用、脏数据 → 单条跳过。
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sys
import threading
import time
import types
from datetime import datetime
from types import SimpleNamespace

import pytest

from memory_agent import runtime as runtime_mod
from memory_agent.backup import BackupManager
from memory_agent.ha_client import HAClient, _CLIENT_POOL
from memory_agent.rule_engine import ActiveRuleEngine
from memory_agent.semantic_dedup import SemanticDeduplicator


# ── 测试替身 ────────────────────────────────────────────────────────────────

def _app_config(**overrides):
    base = dict(
        tz_offset_hours=8.0,
        default_days=7,
        default_limit=50,
        chroma_host="127.0.0.1",
        chroma_port=8000,
        embedding_base_url="http://127.0.0.1:8000/v1",
        embedding_model="bge-m3",
        embedding_api_key="",
        db_path=":memory:",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeConn:
    """记录 execute 参数，返回预置行。"""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        outer = self

        class _R:
            def fetchall(self_inner):
                return outer._rows

            def fetchone(self_inner):
                return outer._rows[0] if outer._rows else None

        return _R()


class _FakeStore:
    def __init__(self, rows, tz_offset_hours=8.0):
        self._rows = rows
        self.tz_offset_hours = tz_offset_hours
        self._lock = threading.RLock()
        self.conn = _FakeConn(rows)

    def _db(self):
        outer = self

        class _Ctx:
            def __enter__(self_inner):
                return outer.conn

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()

    def connect(self):
        return self.conn


# ── CRITICAL-1：配置热更新的 5 条漏掉的接缝 ─────────────────────────────────

def test_reload_config_repoints_the_five_components_that_were_missed():
    """reload_config 必须把新 config 交给这 5 个组件，否则设置页保存只是「看起来生效」。"""
    src = inspect.getsource(runtime_mod.AppRuntime.reload_config)
    for attr in ("ha_assist", "backup", "semantic_dedup", "activity", "researcher"):
        assert f"self.{attr}.config = self.config" in src, f"reload_config 漏了 {attr}"


def test_activity_and_researcher_read_the_tz_offset_at_call_time():
    """reload 只重指向 config 就足够，前提是这两个组件在调用时读 tz_offset_hours，而不是构造期快照。"""
    from memory_agent import activity_inference, researcher

    for mod in (activity_inference, researcher):
        assert "self.config.tz_offset_hours" in inspect.getsource(mod), mod.__name__


def test_reload_config_resets_the_semantic_dedup_embedding_probe():
    src = inspect.getsource(runtime_mod.AppRuntime.reload_config)
    assert "self.semantic_dedup.reset_embedding_probe()" in src


# ── CRITICAL-1 姊妹处：embedding 探针「试一次就定终身」 ─────────────────────

def test_reset_embedding_probe_allows_a_retry_after_the_endpoint_is_fixed():
    dedup = SemanticDeduplicator(_app_config(embedding_base_url="", embedding_model=""))
    assert dedup._get_embedding_fn() is None
    assert dedup._embedding_tried is True
    # 未配置端点 → 已定终身：不重置就永远不会再试
    assert dedup._get_embedding_fn() is None
    dedup.reset_embedding_probe()
    assert dedup._embedding_tried is False
    dedup.config = _app_config()
    assert dedup._get_embedding_fn() is not None


def test_reset_embedding_probe_keeps_the_injected_function():
    sentinel = lambda texts: [[0.0]]  # noqa: E731
    dedup = SemanticDeduplicator(config=None, embedding_fn=sentinel)
    dedup.reset_embedding_probe()
    assert dedup._embedding_fn is sentinel
    assert dedup._get_embedding_fn() is sentinel


# ── CRITICAL-2：Chroma 首次失败不再被永久缓存 ───────────────────────────────

def _history_with_failing_chroma(monkeypatch, boom=True):
    from memory_agent.history import HistoryManager

    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, host, port):
            calls["n"] += 1
            if boom:
                raise RuntimeError("connection refused")

        def get_or_create_collection(self, **kwargs):
            return object()

    fake = types.ModuleType("chromadb")
    fake.HttpClient = _FakeClient
    monkeypatch.setitem(sys.modules, "chromadb", fake)
    hm = HistoryManager(_app_config(), store=SimpleNamespace())
    return hm, calls


def test_chroma_failure_only_freezes_a_cooldown(monkeypatch):
    hm, calls = _history_with_failing_chroma(monkeypatch, boom=True)
    assert hm.collection is None
    assert calls["n"] == 1                     # 首帧即尝试（没有「比 uptime 更长的冷却」）
    assert hm.collection is None
    assert calls["n"] == 1                     # 冻结期内不打爆日志
    hm._chroma_retry_after = time.monotonic() - 1.0   # 冷却到期
    assert hm.collection is None
    assert calls["n"] == 2                     # 到期后重新尝试 → 服务恢复即自愈


def test_chroma_recovers_and_clears_the_error_state(monkeypatch):
    hm, calls = _history_with_failing_chroma(monkeypatch, boom=True)
    assert hm.collection is None
    assert hm._chroma_error
    hm._chroma_retry_after = time.monotonic() - 1.0
    # chroma 起来了：同一份代码路径现在应当成功
    monkeypatch.setitem(
        sys.modules, "chromadb",
        types.SimpleNamespace(HttpClient=lambda host, port: types.SimpleNamespace(
            get_or_create_collection=lambda **kw: object())),
    )
    assert hm.collection is not None
    assert hm._chroma_error == ""
    assert hm._chroma_retry_after == 0.0


def test_permanent_tried_flag_is_gone():
    """旧形态（`self._chroma_tried` + error 永久短路）不允许复活；注释里提一次不碍事。"""
    from memory_agent.history import HistoryManager

    assert not hasattr(HistoryManager, "_chroma_tried")
    for node in ast.walk(ast.parse(inspect.getsource(HistoryManager))):
        if isinstance(node, ast.Attribute):
            assert node.attr != "_chroma_tried", f"history.py:{node.lineno} 又读回 _chroma_tried"


# ── HIGH：家庭墙钟 vs 容器 UTC（跨天窗口） ──────────────────────────────────

LOCAL_TIME_MODULES = [
    "backup.py", "causal_scanner.py", "candidate_promotion.py", "daily_profile.py",
    "perception_ingest.py", "summary_queries.py", "mcp_server.py", "runtime.py",
    "activity_inference.py", "researcher.py", "api/behavior_routes.py",
    "api/collect_routes.py", "api/insight_routes.py", "api/vision_routes.py",
]


def _bare_now_calls(path: pathlib.Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "now" and not node.args and not node.keywords):
            value = node.func.value
            name = getattr(value, "id", "") or getattr(getattr(value, "value", None), "id", "")
            if name == "datetime":
                out.append(node.lineno)
    return out


@pytest.mark.parametrize("rel", LOCAL_TIME_MODULES)
def test_local_time_modules_never_read_the_container_clock(rel):
    """这些模块的时间一律与 day/server_ts 列（家庭墙钟）比对，裸 datetime.now() 会让
    UTC 16:00–24:00 这 8 小时窗口里的事件被归到另一个「天」。"""
    src_root = pathlib.Path(memory_agent_root())
    hits = _bare_now_calls(src_root / rel)
    assert not hits, f"{rel} 仍有裸 datetime.now(): {hits}"


def memory_agent_root() -> str:
    import memory_agent

    return str(pathlib.Path(memory_agent.__file__).parent)


def test_backup_stamp_follows_the_house_clock_not_the_container(monkeypatch):
    """UTC 16:30（北京 00:30）的备份要打成「明天」的文件，否则轮转与排查对不上账。"""
    import memory_agent.store as store_mod

    fixed_utc = datetime(2026, 10, 2, 16, 30, 0)

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            if tz is None:
                return fixed_utc
            from datetime import timedelta, timezone

            shifted = fixed_utc + timedelta(hours=tz.utcoffset(None).total_seconds() / 3600)
            return shifted.replace(tzinfo=tz)

    monkeypatch.setattr(store_mod, "datetime", _FakeDateTime)
    assert BackupManager(_app_config(tz_offset_hours=8))._date_stamp() == "20261003"
    assert BackupManager(_app_config(tz_offset_hours=0))._date_stamp() == "20261002"


def test_attribution_window_starts_from_the_house_day(monkeypatch):
    """_fetch_attribution_events 的 `day >= ?` 必须以家庭墙钟为锚，取不到昨天就整批空转。"""
    import memory_agent.store as store_mod
    from memory_agent.mcp_server import _fetch_attribution_events

    fixed_utc = datetime(2026, 10, 2, 16, 30, 0)

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            if tz is None:
                return fixed_utc
            from datetime import timedelta

            return fixed_utc + timedelta(hours=tz.utcoffset(None).total_seconds() / 3600)

    monkeypatch.setattr(store_mod, "datetime", _FakeDateTime)
    store = _FakeStore(rows=[])
    _fetch_attribution_events(store, days=3)
    _sql, params = store.conn.calls[0]
    # 容器 UTC 16:30 = 家庭墙钟次日 00:30：窗口锚点必须跟着跨天。
    # 墙钟口径 2026-10-03 - 3 天 = 09-30；若误用容器时钟会得到 09-29，整天数据消失。
    assert params == ("2026-09-30",)


# ── HIGH：HA 连接复用 ───────────────────────────────────────────────────────

def test_ha_client_reuses_one_pooled_connection():
    ha = HAClient(SimpleNamespace(hass_server="http://192.0.2.1:8123/", hass_token="t1",
                                  tz_offset_hours=8.0))
    with ha._session() as first:
        pass
    with ha._session() as second:
        pass
    assert first is second                      # 同一 (地址, 令牌) → 同一条连接池
    assert not first.is_closed                  # with 退出只「还」不「拆」


def test_ha_client_pool_is_keyed_by_endpoint_and_token():
    ha_a = HAClient(SimpleNamespace(hass_server="http://192.0.2.1:8123", hass_token="t1",
                                    tz_offset_hours=8.0))
    ha_b = HAClient(SimpleNamespace(hass_server="http://192.0.2.1:8123", hass_token="t2",
                                    tz_offset_hours=8.0))
    with ha_a._session() as a, ha_b._session() as b:
        assert a is not b                       # 换令牌不能复用旧凭证的连接
    assert len(_CLIENT_POOL) >= 2


def test_ha_client_no_longer_builds_a_client_per_request():
    src = inspect.getsource(HAClient)
    assert "with httpx.Client()" not in src


# ── MEDIUM：一条脏记录不许打断整批 ──────────────────────────────────────────

def _rule_row(rule_id: str, condition: str = '{"kind": "motion"}') -> dict:
    return {
        "rule_id": rule_id,
        "condition_json": condition,
        "action_json": '{"action": "notify"}',
        "trigger_json": "{}",
        "enabled": 1,
        "rule_type": "static",
        "created_at": "2026-10-01T00:00:00",
    }


def test_rule_engine_keeps_the_good_rules_when_one_row_is_corrupt(caplog):
    engine = ActiveRuleEngine(_FakeStore(rows=[
        _rule_row("rule_ok_1"),
        _rule_row("rule_dirty", condition='{"kind": "motion"'),   # 截断的 JSON
        _rule_row("rule_ok_2"),
    ]))
    rules = engine.list_rules()
    assert [r["rule_id"] for r in rules] == ["rule_ok_1", "rule_ok_2"]
    assert "rule_dirty" in caplog.text


def test_corrupt_condition_is_not_promoted_to_the_wildcard_bucket():
    """坏 condition 退化成 {} 会让规则命中全屋所有事件——必须整条跳过，不能兜底空字典。"""
    engine = ActiveRuleEngine(_FakeStore(rows=[_rule_row("rule_dirty", condition="not json")]))
    engine._rebuild_index()
    assert "rule_dirty" not in engine._rules_cache
    assert all("rule_dirty" not in ids for ids in engine._kind_index.values())


def test_rule_get_returns_none_for_an_unreadable_row():
    engine = ActiveRuleEngine(_FakeStore(rows=[_rule_row("rule_dirty", condition="[1, 2]")]))
    assert engine.get_rule("rule_dirty") is None


def test_llm_answer_cache_bad_payload_degrades_to_a_miss():
    from memory_agent.api.llm_routes import _cache_blob

    assert _cache_blob("{'answer': 'x'}") is None       # 单引号不是 JSON
    assert _cache_blob("123") is None                   # 非对象一律按未命中
    assert _cache_blob(json.dumps({"answer": "ok"})) == {"answer": "ok"}


def test_pattern_metadata_bad_columns_only_drop_that_template():
    pytest.importorskip("chromadb")   # patterns 在模块层 import chromadb（本机无该依赖时跳过，容器内跑）
    from memory_agent.patterns import _json_object

    assert _json_object("", "condition") == {}                    # 缺列 = 无限制条件
    assert _json_object('{"room": "客厅"}', "condition") == {"room": "客厅"}
    assert _json_object("{broken", "condition", "p-1") is None
    assert _json_object("[1, 2]", "action", "p-2") is None


def test_acp_packed_event_with_bad_json_does_not_raise():
    from memory_agent.acp_server import _parse_packed

    ev, data = _parse_packed("event: backend\ndata: {oops")
    assert ev == "backend"
    assert data == {}                       # 这条事件空转，SSE 流继续


def test_explain_insight_reads_source_refs_through_the_safe_parser():
    from memory_agent.insights_legacy import InsightService

    src = inspect.getsource(InsightService.explain_insight)
    assert "safe_json_loads(mem.get(\"source_refs_json\")" in src
    assert "json.loads(mem.get(\"source_refs_json\")" not in src
