"""回归锁：`get_data_coverage` 的 handler docstring 只能承诺载荷里真的存在的键。

**钉的是什么**：MCP handler 的 docstring 就是客户端看到的工具描述，是被**注入进 prompt 的那句话**。
本轮运行时键 diff 量出它承诺了「每天的 `has_data` 标记」与「first/last 有数据的日期」，
而切到新引擎之后载荷里既没有 `has_data`（逐日格子叫 `empty`，`service.py:463-464`），
也没有"有数据的首末日"（`start_day/end_day` 是**窗口边界**，`service.py:442/:475-476`）。
这与 `route_question` 的 `recommended_tool` 是同一族缺陷：**实现改了、文案没跟着改，
模型照文案读就不存在**（台账 §四十五 / §四十七 第 三 节）。

**为什么用 AST 读源码不 import `mcp_server`**：本机 mcp SDK 版本不匹配时整个模块导不进来
（`MCPServer` 不可导入 ⇒ 模块属性上没有 handler），锁会在**最该跑它的环境里静默 skip**。
文案是源码的一部分，AST 取它不需要运行时。

**每条都配「该不响」的对偶**：
- 正向锁「文案点名的键都在载荷里」；反例是"载荷键越写越多、文案随便挑几个"——所以第 2 条
  锁的是**死键不许回到文案**（`has_data`/`first_day_with_data`/`last_day_with_data`）。
- 第 3 条锁逐日标记的**名字本身**（`empty` 在、`has_data` 不在）。
- 第 4 条是这一族里最容易再次混过去的一格：故意把窗口起点落在**没有数据的那一天**，
  断言 `start_day` 仍是窗口边界。若哪天有人"顺手"把 `start_day` 改成首个有数据日，
  这条会红——因为那时文案里"窗口边界 ≠ 数据边界"这句说明就又不成立。
"""

import ast
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import EntityInfo  # noqa: E402
from memory_agent.store import Store  # noqa: E402

_MCP_SOURCE = os.path.join(_SRC, "memory_agent", "mcp_server.py")

DAY_EMPTY_START = "2026-09-20"
DAY_1 = "2026-09-21"
DAY_2 = "2026-09-22"
DAY_3 = "2026-09-23"
LIGHT_MAIN = "light.living_main"
CLIMATE_AC = "climate.living_ac"
CATALOG = [
    EntityInfo(entity_id=LIGHT_MAIN, friendly_name="客厅主灯", room="客厅", domain="light"),
    EntityInfo(entity_id=CLIMATE_AC, friendly_name="客厅空调", room="客厅", domain="climate"),
]

#: 文案里以反引号点名、且必须真的存在于载荷的顶层键（`empty` 在逐日格子里，单独锁第 3 条）
PROMISED_TOP_LEVEL = ("missing_days", "start_day", "end_day", "day_coverage",
                      "hour_coverage", "peak_hours")
#: 切换之后已经不存在、文案也不许再承诺的 legacy 键
DEAD_KEYS = ("has_data", "first_day_with_data", "last_day_with_data")


def _ev(entity_id, day, minute, state, domain):
    return {"entity_id": entity_id, "ts": "%sT10:%02d:00" % (day, minute), "room": "客厅",
            "domain": domain, "new_state": state, "old_state": "x",
            "attrs": {"friendly_name": entity_id.split(".")[-1]}}


def _dataset():
    """09-20 无事件（窗口边界日）、09-21 有、09-22 无（缺口日）、09-23 有。"""
    rows = []
    for day in (DAY_1, DAY_3):
        rows += [_ev(LIGHT_MAIN, day, i, ("on" if i % 2 else "off"), "light")
                 for i in range(4)]
        rows += [_ev(CLIMATE_AC, day, i, "on", "climate") for i in range(2)]
    return rows


@pytest.fixture
def svc():
    tmp = tempfile.mkdtemp(prefix="ma_cov_doc_")
    store = Store(os.path.join(tmp, "cov_doc.db"), tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(_dataset())
    facade = InsightService(store, Config())
    facade.resolver.refresh(CATALOG)
    yield facade


def _handler_doc(func_name):
    """AST 取 `@mcp.tool()` 里那个 handler 的 docstring，不依赖 mcp SDK 能否导入。"""
    with open(_MCP_SOURCE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=_MCP_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            doc = ast.get_docstring(node)
            assert doc, "%s 没有 docstring，工具描述对客户端是空的" % func_name
            return doc
    raise AssertionError("mcp_server.py 里找不到 async def %s" % func_name)


def test_coverage_docstring_only_promises_keys_the_payload_has(svc):
    """文案点名的每个顶层键，载荷里都得真的在——否则模型会去读一个不存在的格子。"""
    doc = _handler_doc("get_data_coverage")
    tr = svc._tr("%sT00:00:00" % DAY_EMPTY_START, "%sT23:59:59" % DAY_3)
    out = svc.core.coverage(tr)

    missing = [k for k in PROMISED_TOP_LEVEL if k not in out]
    assert not missing, "docstring 承诺但载荷没有的键：%s" % (missing,)
    unmentioned = [k for k in PROMISED_TOP_LEVEL if k not in doc]
    assert not unmentioned, "docstring 漏掉了它本该说明的键：%s" % (unmentioned,)
    assert "`days[]`" in doc, "文案没写逐日明细那格叫什么，消费方只能猜"


def test_coverage_docstring_does_not_resurrect_dead_legacy_keys():
    """这条例子是"该不响"的对偶：切换后不存在的 legacy 键，不许被文案重新许诺。"""
    doc = _handler_doc("get_data_coverage")
    for dead in DEAD_KEYS:
        assert dead not in doc, (
            "docstring 又承诺了 %r —— 新引擎载荷里没有该键"
            "（逐日标记叫 `empty`，窗口边界叫 `start_day`/`end_day`）" % (dead,))


def test_per_day_marker_is_named_empty_not_has_data(svc):
    """逐日那格的标记名字锁死：`empty` 在、`has_data` 不在，且值真的是空日才 True。"""
    tr = svc._tr("%sT00:00:00" % DAY_EMPTY_START, "%sT23:59:59" % DAY_3)
    days = svc.core.coverage(tr)["days"]
    by_day = {d["day"]: d for d in days}

    assert set(by_day) == {DAY_EMPTY_START, DAY_1, DAY_2, DAY_3}
    for day, row in by_day.items():
        assert "empty" in row, "%s 那格没有 `empty` 标记：%r" % (day, sorted(row))
        assert "has_data" not in row, "%s 那格还留着 legacy 的 `has_data`" % day
    assert by_day[DAY_1]["empty"] is False and by_day[DAY_1]["events"] > 0
    assert by_day[DAY_EMPTY_START]["empty"] is True and by_day[DAY_2]["empty"] is True


def test_start_day_is_the_window_bound_not_the_first_day_with_data(svc):
    """窗口起点故意落在无数据日：`start_day` 必须仍是边界，`missing_days` 才说"没数据"。

    这条防的是把 `start_day` 当 legacy `first_day_with_data` 的替代来"顺手修好"——
    那会让文案与载荷又一次对不上，而且是静默的。
    """
    tr = svc._tr("%sT00:00:00" % DAY_EMPTY_START, "%sT23:59:59" % DAY_3)
    out = svc.core.coverage(tr)

    assert out["start_day"] == DAY_EMPTY_START
    assert out["end_day"] == DAY_3
    assert out["missing_days"] == [DAY_EMPTY_START, DAY_2]
    first_with_data = next(d["day"] for d in out["days"] if not d["empty"])
    assert out["start_day"] != first_with_data, (
        "`start_day` 被改成了首个有数据日——它按裁5 Q-B 是窗口边界，两者不许混用")
