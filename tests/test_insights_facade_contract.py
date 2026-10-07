"""审计 P0-5 契约锁：门面必须**真的**提供生产代码访问的 insights 成员。

这类 bug 的成因不是"少了一个方法"，而是**少了也没人红**：
``_degrade`` 把异常降级成空分页、``hasattr`` + 空 ``set()`` 把"标签全空"当正常结果，
于是 Phase 4 把 ``InsightService`` 切到 ``insights/`` 包后，11 个仍在用的调用点
静默返回空（行为推断 / 模板分析 / 实体名兜底 / 缓存四条功能线），全量测试仍然绿。

本文件用三条互补断言把这条路堵死：

1. **扫描**：AST/正则扫生产源码里对 insights 对象的属性访问，逐个断言门面实例上存在；
   新增一个不存在的 ``ins.foo()`` 会直接红，不需要等到线上表现为"空结果"。
2. **反降级**：契约成员不得被 ``_degrade`` 包着——legacy 坏掉必须抛异常。
3. **行为**：用真实 Store 跑标签推断、事件分页、 decorate，证明返回的是数据而不是形状。

明确**不**做的一件事：用 ``__getattr__`` 动态转发把缺的方法"补"出来。那正是
P0-5 的藏匿机制本身——属性永远存在，静态扫描与契约测试都会被骗过去。
"""

import ast
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.api import LEGACY_CONTRACT_MEMBERS  # noqa: E402
from memory_agent.insights.utils import tags_of  # noqa: E402
from memory_agent.store import TELEMETRY_DOMAINS, Store  # noqa: E402
import memory_agent  # noqa: E402

# 扫的是「生产实际导入的那棵树」，不是测试文件旁边的 src：容器里 tests 被 docker cp
# 到 /tmp/tests，按 __file__/../src 定位会指向不存在的 /tmp/src，静态半边直接空跑。
_PKG = os.path.dirname(os.path.abspath(memory_agent.__file__))

# 生产侧对 insights 对象的属性访问：rt/runtime/self/app.insights.X 以及局部别名 ins.X
_ACCESS = re.compile(
    r"\b(?:rt|runtime|self|app)\.insights\.([a-zA-Z_][a-zA-Z0-9_]*)"
    r"|(?<![a-zA-Z_.])ins\.([a-zA-Z_][a-zA-Z0-9_]*)"
)
# insights 包自身与 legacy 不算"调用方"：前者是被测门面，后者是转发目标
_SKIP = ("insights_legacy.py", "config.py")


def _production_accesses() -> dict[str, list[str]]:
    """返回 ``{属性名: [file:line, …]}``，只统计 src/memory_agent 下的调用方文件。"""
    out: dict[str, list[str]] = {}
    for root, dirs, files in os.walk(_PKG):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py") or fn in _SKIP:
                continue
            path = os.path.join(root, fn)
            if os.path.basename(root) == "insights":  # 门面自身与仓储层
                continue
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    m = _ACCESS.search(line)
                    if not m:
                        continue
                    attr = m.group(1) or m.group(2)
                    if attr == "config":  # 取的是配置对象，不属于行为契约
                        continue
                    out.setdefault(attr, []).append(f"{fn}:{lineno}")
    return out


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="ma_contract_")
    s = Store(os.path.join(tmp, "contract.db"), tz_offset_hours=0.0)
    s.init_schema()
    yield s


def test_production_attribute_accesses_resolve_on_facade(store):
    """扫描到的每一个 insights 成员，门面实例上必须真的拿得到。"""
    svc = InsightService(store, Config())
    bad = {name: sites for name, sites in _production_accesses().items()
           if not hasattr(svc, name)}
    assert not bad, (
        "生产代码访问了 InsightService 上不存在的成员（审计 P0-5 复发）。"
        "请在 insights/api.py 显式补转发方法并登记进 LEGACY_CONTRACT_MEMBERS：\n"
        + "\n".join(f"  {k}: {'; '.join(v[:4])}" for k, v in sorted(bad.items()))
    )


def test_contract_members_are_declared_and_bound(store):
    """契约表本身不许漂移：每一项都可解析，且 legacy 有同名实现。"""
    import memory_agent.insights_legacy as L

    svc = InsightService(store, Config())
    for name in LEGACY_CONTRACT_MEMBERS:
        assert hasattr(svc, name), f"契约成员 {name} 在门面上取不到"
        if name != "store":  # store 是实例属性，不是方法
            assert hasattr(L.InsightService, name), f"legacy 无 {name}，转发无源可指"


def test_store_attribute_is_the_injected_store(store):
    """``templates.py`` 直接 ``ins.store.query_events(...)``：必须是同一个对象。"""
    assert InsightService(store, Config()).store is store


def test_contract_members_are_not_silently_degraded(store):
    """反降级断言：legacy 坏掉时契约成员必须抛，不许返回空信封。

    这是 P0-5 的核心教训——``_degrade`` 让 11 个坏调用点在测试与 UI 上表现成
    "今天没有数据"，而不是"代码坏了"。
    """
    svc = InsightService(store, Config())
    svc.legacy = object()  # 模拟 legacy 缺失/方法被改名
    # 属性查找先于参数绑定：legacy 上没有这个名字时一定抛 AttributeError。
    # 各方法按门面签名给够实参，避免 TypeError 掩盖真正的断言。
    args_by_name = {"name_map": (), "decorate": ([],), "_parse": ("",),
                    "_fallback_name": ("x",), "_tags_of": ("x", "")}
    for name in LEGACY_CONTRACT_MEMBERS:
        if name == "store":
            continue
        with pytest.raises(AttributeError):
            getattr(svc, name)(*args_by_name.get(name, ("x",)))


def test_tags_of_no_longer_needs_an_insights_instance(store):
    """行为推断的标签不得再依赖注入对象；``insights=None`` 也要有标签。

    旧实现 ``hasattr(self.insights, "_tags_of")`` 不成立时 ``return set()``，
    于是所有序列规则的 tag 条件恒不匹配——表现为"没有任何行为"而非报错。
    """
    rt = SimpleNamespace(config=SimpleNamespace(rooms={}, tz_offset_hours=0.0),
                         store=store, insights=None)
    svc = ActivityInferenceService(rt)
    assert svc._tags_of("binary_sensor.study_door_contact", "") == tags_of(
        "binary_sensor.study_door_contact", "")
    assert svc._tags_of("switch.desk_lamp", "") == {"appliance"}
    assert svc._tags_of("light.bedroom_main", "") == {"light"}


def test_iter_all_events_pages_and_honours_exclude_domains(store):
    """分页取数必须是真分页：跨 5000 行上限的数据不能只剩最早一段。"""
    svc = InsightService(store, Config())
    base = datetime(2026, 9, 1, 8, 0, 0)
    rows = []
    for i in range(12):
        ts = (base + timedelta(minutes=i)).isoformat()
        rows.append({"entity_id": f"binary_sensor.room_{i}_pir", "ts": ts,
                     "room": "书房", "domain": "binary_sensor",
                     "new_state": "on", "old_state": "off"})
    for i in range(3):  # 遥测域：应当被 exclude_domains 挡掉
        rows.append({"entity_id": f"sensor.power_{i}", "ts": base.isoformat(),
                     "room": "书房", "domain": "sensor",
                     "new_state": str(i), "old_state": ""})
    store.insert_events(rows)
    got = svc._iter_all_events(base.isoformat(),
                               (base + timedelta(days=1)).isoformat(),
                               max_rows=10000, rooms=["书房"], order="asc",
                               exclude_domains=list(TELEMETRY_DOMAINS))
    assert len(got) == 12, f"分页/过滤结果异常：{len(got)} 行（应 12 行行为事件）"


def test_decorate_attaches_friendly_names_and_room(store):
    """``mcp_server`` 用 ``ins.decorate`` 补友好名：返回必须逐行带这两个字段。"""
    svc = InsightService(store, Config())
    ts = datetime(2026, 9, 1, 8, 0, 0).isoformat()
    store.insert_events([{"entity_id": "binary_sensor.study_door_contact",
                          "ts": ts, "room": "书房", "domain": "binary_sensor",
                          "new_state": "on", "old_state": "off"}])
    raw = svc._iter_all_events(ts, (datetime(2026, 9, 2) ).isoformat(), max_rows=100)
    out = svc.decorate(raw)
    assert len(out) == 1
    assert out[0]["room"] == "书房"
    assert out[0]["friendly_name"]  # 空壳降级时这里是 ""，正是当初线上表现


def test_resolve_range_returns_legacy_shape(store):
    """``resolve_range`` 必须返回 ``(start, end, meta)`` 三元组，不是分页信封。"""
    svc = InsightService(store, Config())
    start, end, meta = svc.resolve_range(days=7)
    assert isinstance(meta, dict) and start < end
    datetime.fromisoformat(start)
    datetime.fromisoformat(end)


def test_no_contract_member_is_wrapped_by_degrade():
    """静态复核：转发方法源码里没有 ``@_degrade``，也不得新增。

    运行时断言 ``test_contract_members_are_not_silently_degraded`` 已经能抓，
    这条只是把原因写在失败信息里，便于改代码的人一眼看懂规则。
    """
    path = os.path.join(_PKG, "insights", "api.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    decorated = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            names = []
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                names.append(getattr(target, "id", getattr(target, "attr", "")))
            decorated[node.name] = names
    leaked = {k: v for k, v in decorated.items()
              if k in LEGACY_CONTRACT_MEMBERS and "_degrade" in v}
    assert not leaked, f"契约成员被 _degrade 静默化（P0-5 复发）：{leaked}"


# ── 门面切换丢键：`get_data_quality` 的 `agent_memory` 那一格 ─────────────────
# ToolSpec（`tool_schema.py:446`）与 handler 摘要（`mcp_server.py:519`）都写着
# "…+ agent 记忆镜像缺口（mirror_dirty）"，legacy 原形 `insights_legacy.py:3403`
# 也确实输出 `agent_memory`；门面切到 `core.data_quality()` 之后那块整格没了。
# 这条与 P0-5 同源：丢了也没人红，所以判据落在"承诺的键必须在输出里"。

_HEALTH = {"ok": True, "states": {"live": 5}, "mirror_dirty": 2, "chroma_available": True}


def _patch_runtime(monkeypatch, agent_memory):
    from memory_agent import runtime as rt_mod
    rt = SimpleNamespace(agent_memory=agent_memory)
    monkeypatch.setattr(rt_mod, "get_runtime", lambda: rt)


def test_get_data_quality_carries_the_agent_memory_block(store, monkeypatch):
    _patch_runtime(monkeypatch, SimpleNamespace(health=lambda: dict(_HEALTH)))
    out = InsightService(store, Config()).get_data_quality(days=7)
    assert "score" in out, f"主块被打成降级信封：{sorted(out)}"
    assert out["agent_memory"]["mirror_dirty"] == 2


def test_unavailable_agent_memory_is_a_visible_failure_not_an_absent_key(store, monkeypatch):
    """拿不到就明说"查不到"，不许整块缺席——缺席与"镜像很干净"读起来一样。"""
    for agent in (None, object()):
        _patch_runtime(monkeypatch, agent)
        out = InsightService(store, Config()).get_data_quality(days=7)
        assert out["agent_memory"] == {"ok": False, "error": "agent_memory 不可用"}
        assert "score" in out


def test_agent_memory_blowing_up_does_not_degrade_the_quality_page(store, monkeypatch):
    """附属块炸掉时只丢附属块：整页降级会把电量倒流/心跳/陈旧那些读数一起抹掉。"""
    def boom():
        raise RuntimeError("runtime 还没起")

    _patch_runtime(monkeypatch, None)
    from memory_agent import runtime as rt_mod
    monkeypatch.setattr(rt_mod, "get_runtime", boom)
    out = InsightService(store, Config()).get_data_quality(days=7)
    assert out["agent_memory"]["ok"] is False
    assert "score" in out


# ── DCD 20261007 §二 裁乙：温控环比维度回到新引擎（门面注入 climate_provider）────
#
# 缺陷形状与被替代方一模一样：修复 #9 加的温控环比在 Phase 4 门面切换时被洗掉——
# `api.py` 的 `get_behavior_insights` 改走 `core.compare_insights`，而 `_compare_insights`
# 只算活动量，已迁好的 `insights/utils.py aggregate_climate_sessions / compare_climate`
# 只剩 legacy 反向 import。全量测试照绿，因为**没人读过那一格**。
#
# 所以判据不能只钉键名：键在、数恒空是同一场事故的另一种表现。这里三条真库读数锁
# （与 legacy 逐字对账 / 具体数字 / 两窗口分别取数）加三条形状锁
# （引擎不碰 self.legacy / 每个构造点都注入 / 降级只丢这一块）一起上。

SERVICE_PY = os.path.join(_PKG, "insights", "service.py")
API_PY = os.path.join(_PKG, "insights", "api.py")
# legacy `compare_climate` 的产出键，裁定要求逐字沿用。
LEGACY_CLIMATE_KEYS = {"current", "previous", "delta_hours", "delta_avg_setpoint_c"}

_CLIMATE_ROOMS = {
    "卧室": {"enabled": True, "entities": {
        "climate.bedroom_ac": {"name": "卧室空调", "domain": "climate"},
        "light.bedroom": {"name": "卧室灯", "domain": "light"},
    }},
}


def _climate_events():
    """当前窗口一组 2 小时会话、前一窗口一组 3 小时会话，各带设定/室温。

    放在 -2 天与 -9 天是**窗口交叠区**：legacy 的环比窗口对齐自然日边界，引擎按
    `now` 滚动切 7×86400 秒，两侧各留两天余量才能保证两边读到同一批事件——
    否则"与 legacy 对账"那条锁会随时钟点漂红。
    """
    from memory_agent.insights.models import house_now
    now = house_now()
    rows = []
    for offset, on_h, off_h, setpoint, room_temp in ((2, 10, 12, 24.0, 27.5),
                                                     (9, 10, 13, 22.0, 26.0)):
        day = now - timedelta(days=offset)
        on_ts = day.replace(hour=on_h, minute=0, second=0, microsecond=0)
        off_ts = day.replace(hour=off_h, minute=0, second=0, microsecond=0)
        attrs = '{"temperature": %s, "current_temperature": %s}' % (setpoint, room_temp)
        rows.append({"entity_id": "climate.bedroom_ac", "ts": on_ts.isoformat(timespec="seconds"),
                     "room": "卧室", "old_state": "off", "new_state": "cool", "attrs_json": attrs})
        rows.append({"entity_id": "climate.bedroom_ac", "ts": off_ts.isoformat(timespec="seconds"),
                     "room": "卧室", "old_state": "cool", "new_state": "off", "attrs_json": attrs})
        rows.append({"entity_id": "light.bedroom", "ts": on_ts.isoformat(timespec="seconds"),
                     "room": "卧室", "old_state": "off", "new_state": "on",
                     "attrs_json": '{"friendly_name": "卧室灯"}'})
    return rows


@pytest.fixture
def climate_live():
    """真库 + 真事件：返回 (Store, 门面)。温控环比必须有读数可对，形状锁赢不了这个。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    st.insert_events(_climate_events())
    cfg = Config()
    cfg.rooms = _CLIMATE_ROOMS
    try:
        yield st, InsightService(st, cfg)
    finally:
        st.close()
        try:
            os.remove(path)
        except OSError:
            pass


def test_climate_comparison_is_the_legacy_block_verbatim(climate_live):
    """同库同钟跑 legacy 与引擎，两块的**键集合与读数**逐字相同。

    裁定口径"消费 legacy 读数 + 键名沿用 legacy"——这条就是它的可执行形式。
    """
    from memory_agent.insights_legacy import InsightService as LegacyInsightService
    st, svc = climate_live
    engine = svc.core.compare_insights(compare_days=7)["climate_comparison"]
    # 用门面自己交给 legacy 的那份原始 Config（`raw_config`），两边读的才是同一套房间表。
    legacy = LegacyInsightService(svc.raw_config, st).get_behavior_insights(7)["climate_comparison"]
    assert set(engine) == LEGACY_CLIMATE_KEYS, sorted(engine)
    assert set(legacy) == LEGACY_CLIMATE_KEYS, sorted(legacy)
    assert engine == legacy, f"引擎块与 legacy 块不一致：\nengine={engine}\nlegacy={legacy}"


def test_climate_comparison_carries_the_measured_numbers(climate_live):
    """读数锁：2h/3h 两窗口会话、设定 24 vs 22 —— 键在但数恒空也算丢维度。"""
    block = climate_live[1].core.compare_insights(compare_days=7)["climate_comparison"]
    assert block.get("ok") is None, f"温控块被降级了：{block}"
    assert block["current"]["sessions"] == 1 and block["previous"]["sessions"] == 1, block
    assert block["current"]["hours"] == 2.0 and block["previous"]["hours"] == 3.0, block
    assert block["delta_hours"] == -1.0, block
    assert block["current"]["avg_setpoint_c"] == 24.0, block
    assert block["delta_avg_setpoint_c"] == 2.0, block


def test_climate_comparison_reads_each_window_from_the_provider(climate_live):
    """两窗口分别取数：provider 收到的是各自的 start_iso/end_iso。

    反例锁——把 provider 换成"永远取全屋大窗口"的实现，`delta_hours` 立刻归零，
    这条判据就是为那个形状准备的（照抄一个窗口 = 环比永远是 0，比缺键更难发现）。
    """
    from memory_agent.insights.service import BehaviorService
    _, svc = climate_live
    seen = []

    def spy(start_iso, end_iso):
        seen.append((start_iso, end_iso))
        return svc._climate_sessions_for_window(start_iso, end_iso)

    out = BehaviorService(svc.repo, svc.resolver, svc.config,
                          climate_provider=spy).compare_insights(compare_days=7)
    assert len(seen) == 2, f"provider 该被调两次（当前 + 前一窗口），实得 {seen}"
    assert seen[0] != seen[1], f"两次取数用了同一个窗口：{seen}"
    assert out["climate_comparison"]["delta_hours"] == -1.0, out["climate_comparison"]
    assert seen[1][1] == seen[0][0], f"两窗口不连续（前窗 end 应等于当前窗 start）：{seen}"


def test_engine_never_touches_self_legacy():
    """形状锁（裁乙的"不直接读 self.legacy"）：引擎源码里 `self.legacy` 出现次数必须为 0。

    这条是前瞻锁——今天没有，写它是为了让"顺手改成 self.legacy.xxx()"当场红，
    那正是把 legacy 生命周期再拖长一档的起点。
    """
    with open(SERVICE_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=SERVICE_PY)
    hits = [(n.lineno, n.attr) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "self" and n.attr == "legacy"]
    assert not hits, f"BehaviorService 直接读了 self.legacy：{hits}"


def test_every_facade_construction_injects_the_climate_provider():
    """形状锁：门面里每一处 `BehaviorService(...)` 都必须带 `climate_provider=`。

    构造点有两处（`__init__` 与 `reload_config`）。热更新那条漏注入的后果是
    "改一次配置，温控环比从此永久消失"——上一轮就是这么洗掉修复 #9 的。
    """
    with open(API_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=API_PY)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "BehaviorService"]
    assert calls, "门面里找不到 BehaviorService 的构造点——注入点搬走了，请同步更新本锁"
    missing = [n.lineno for n in calls
               if "climate_provider" not in {kw.arg for kw in n.keywords}]
    assert not missing, f"这些构造点没注入 climate_provider，温控环比会静默降级：{missing}"
    assert hasattr(InsightService, "_climate_sessions_for_window"), \
        "provider 指向的门面方法不存在（写错的属性名在构造时才炸，先在这里钉住）"


def test_missing_provider_is_a_visible_failure_not_an_absent_block(store):
    """没注入回调时那一格必须明说"未注入"，不许整块缺席——与 agent_memory 那条同口径。"""
    from memory_agent.insights.service import BehaviorService
    svc = InsightService(store, Config())
    out = BehaviorService(svc.repo, svc.resolver, svc.config).compare_insights(compare_days=7)
    assert out["ok"] is True, out
    assert out["climate_comparison"] == {"ok": False, "error": "climate_provider 未注入"}, out


def test_blowing_up_provider_degrades_only_the_climate_block(store):
    """取数炸了只丢附属块：活动环比的读数不该被温控块拖下水（否则修一个维度坏一页）。"""
    from memory_agent.insights.service import BehaviorService
    svc = InsightService(store, Config())

    def boom(start_iso, end_iso):
        raise RuntimeError("legacy 挂了")

    out = BehaviorService(svc.repo, svc.resolver, svc.config,
                          climate_provider=boom).compare_insights(compare_days=7)
    assert out["ok"] is True, out
    assert "total_events" in out["current"], out
    block = out["climate_comparison"]
    assert block["ok"] is False and "legacy 挂了" in block["error"], block


def test_degraded_envelope_still_carries_the_climate_block(store):
    """整页降级（`compare_days` 非法）时那一格也要在，且自称是降级——不是安静消失。"""
    svc = InsightService(store, Config())
    out = svc.core.compare_insights(compare_days=-1)
    assert out["ok"] is False, out
    assert out["climate_comparison"]["ok"] is False, out
    assert "降级" in out["climate_comparison"]["error"], out
