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
