r"""回归锁：对外文案点名的键，必须是**真跑一遍之后**载荷里真的存在的键。

**钉的是什么**：任务表 #75 用一次全量扫描（AST 走四个对外面：`@mcp.tool()` handler docstring、
ToolSpec 的 summary/description/pitfall/params、随包 SKILL.md、人读 user_manual.js）量出三条
"实现改了、文案没跟着改"的活口：

| 对外文案原先承诺 | 载荷里真的叫 | 判据来源 |
|---|---|---|
| `get_data_quality`：`data_quality_issues` | `issues`（+ `checks[]`） | `insights/service.py:1276 data_quality` 走新引擎 |
| `ask_memory`：`semantic_hints`/`agent_memory_hints` | `hints`（仅 `return_hints=True` 时） | `insights/nlquery.py:118-133 ask()` |
| `route_question`：`recommended_tool` | 没有这个键；能读的是 `route`/`intent` | `insights/api.py:839 plan_question` → `QuestionPlan.to_dict()` |

三个 legacy 名只在 `insights_legacy.py` 里作为键存在（本轮实测：全仓 grep 除文案外无活口）。
按 DCD 20261005 裁5 Q-B 的方向——**切的是文案，不是往出境载荷里加键**（加键要走载荷键名登记）。

**为什么每条都配一个"该不响"的对偶**：
- `data_quality_issues` 在 `get_device_health` 的文案里**留着是对的**：裁5 Q1=A 把 device_health
  整条交回 legacy，legacy 真的产出该键（`insights_legacy.py:1711`）。所以第 3 条把这格钉住，
  防止下一个人在"全仓清零死键"的冲动下把它也改掉——那才是真把文案和载荷改拧了。
- 第 6 条锁 `plan_question` 的载荷里**不许**凭空长出 `recommended_tool`：本族的修法只有改文案。
- 第 8 条锁"文案里列举的取值"本身：pitfall 现在写的是意图名清单，清单必须等于 `Intent` 枚举真值，
  否则修一条假承诺换来的是另一条假承诺。
- 第 9、10 条是扫描短名单的最后一行：`get_climate_sessions` 文案里的 `current_temperature`
  被判成"键只活在 legacy"，实测那是**输入侧 attrs 名**（`insights_legacy.py:1794` 读它 ⇒
  会话格子给 `room_temp_c`）。第 9 条锁这个区分，第 10 条锁文案许诺的那条温度链真的跑得通
  ——`room_temp_c`/`setpoint_c`/`sessions_with_temperature` 此前在 tests/ 里出现 **0** 次。

**为什么用 AST 读源码、不 import `mcp_server`**：本机 mcp SDK 版本不匹配时整个模块导不进来
（`MCPServer` 不可导入 ⇒ 模块属性上没有 handler），锁会在**最该跑它的环境里静默 skip**（台账 §四十八）。
"""

import ast
import os
import re
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import EntityInfo, Intent  # noqa: E402
from memory_agent.store import Store  # noqa: E402

_MCP_SOURCE = os.path.join(_SRC, "memory_agent", "mcp_server.py")
_SPEC_SOURCE = os.path.join(_SRC, "memory_agent", "tool_schema.py")

DAY_1 = "2026-09-21"
DAY_2 = "2026-09-22"
PC = "binary_sensor.study_pc"
LIGHT = "light.study_lamp"
CATALOG = [
    EntityInfo(entity_id=PC, friendly_name="书房电脑", room="书房", domain="binary_sensor"),
    EntityInfo(entity_id=LIGHT, friendly_name="书房台灯", room="书房", domain="light"),
]

AC = "climate.master_ac"
AC_CATALOG = [EntityInfo(entity_id=AC, friendly_name="主卧空调", room="主卧", domain="climate")]
#: (相对分钟, new_state, attrs 温度对)。四条分支都要踩到：OPEN 建会话、非 OPEN 非 CLOSED
#: （状态判不了但温度仍采信）、显式 off 闭合、以及"只有设定温度没有室温"的那一路。
CLIMATE_LEG = [
    (0, "off", {}),
    (10, "cool", {"temperature": 26.0, "current_temperature": 29.5}),
    (40, "cool", {"temperature": 25.5, "current_temperature": 28.0}),
    (70, "unknown", {"temperature": 25.0, "current_temperature": 26.5}),
    (100, "off", {"temperature": 25.0, "current_temperature": 26.0}),
    (130, "heat", {"temperature": 22.0}),
    (160, "off", {}),
]

#: `get_data_quality` 文案点名、且载荷顶层真的在的键（本轮运行时实测的 12 格里取对外有意义的）
QUALITY_PROMISED = ("checks", "issues", "score", "total_events", "missing_days",
                    "noise_ratio", "summary", "filters", "window", "agent_memory", "ok")
#: 三个 legacy 死名：在新引擎载荷里不存在
DEAD = ("data_quality_issues", "semantic_hints", "agent_memory_hints", "recommended_tool")


def _ev(entity_id, day, minute, state, domain):
    return {"entity_id": entity_id, "ts": "%sT10:%02d:00" % (day, minute), "room": "书房",
            "domain": domain, "new_state": state, "old_state": "x",
            "attrs": {"friendly_name": entity_id.split(".")[-1]}}


def _rows():
    out = []
    for day in (DAY_1, DAY_2):
        out += [_ev(PC, day, i, ("on" if i % 2 else "off"), "binary_sensor") for i in range(6)]
        out += [_ev(LIGHT, day, i, "on", "light") for i in range(3)]
    return out


@pytest.fixture
def svc():
    tmp = tempfile.mkdtemp(prefix="ma_c75_")
    store = Store(os.path.join(tmp, "c75.db"), tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(_rows())
    facade = InsightService(store, Config())
    facade.resolver.refresh(CATALOG)
    yield facade


def _climate_rows():
    """时间戳相对真机 now 生成——气候窗口由 `resolve_range(days)` 从"现在"往前推，
    写死日期会让夹具在某个钟点之后整批落到窗口外（量具量到 0 段会话，看起来像实现坏了）。"""
    from datetime import datetime, timedelta
    base = datetime.now()
    out = []
    for minute, state, extra in CLIMATE_LEG:
        ts = (base - timedelta(minutes=240 - minute)).strftime("%Y-%m-%dT%H:%M:%S")
        attrs = {"friendly_name": "主卧空调", "hvac_action": state}
        attrs.update(extra)
        out.append({"entity_id": AC, "ts": ts, "room": "主卧", "domain": "climate",
                    "new_state": state, "old_state": "x", "attrs": attrs})
    return out


@pytest.fixture
def climate_svc():
    tmp = tempfile.mkdtemp(prefix="ma_c75t_")
    store = Store(os.path.join(tmp, "c75t.db"), tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(_climate_rows())
    facade = InsightService(store, Config())
    facade.resolver.refresh(AC_CATALOG)
    yield facade


def _source(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _handler_doc(func_name):
    """AST 取 `@mcp.tool()` handler 的 docstring——不依赖 mcp SDK 能否导入。"""
    tree = ast.parse(_source(_MCP_SOURCE), filename=_MCP_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            doc = ast.get_docstring(node)
            assert doc, "%s 没有 docstring，工具描述对客户端是空的" % func_name
            return doc
    raise AssertionError("mcp_server.py 里找不到 def %s" % func_name)


def _spec(tool_name):
    """AST 取 `ToolSpec(name=tool_name)` 那条调用的关键字面量。"""
    tree = ast.parse(_source(_SPEC_SOURCE), filename=_SPEC_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ToolSpec":
            kw = {k.arg: k.value for k in node.keywords}
            if isinstance(kw.get("name"), ast.Constant) and kw["name"].value == tool_name:
                out = {}
                for field in ("summary", "description", "pitfall"):
                    v = kw.get(field)
                    out[field] = v.value if isinstance(v, ast.Constant) else ""
                params = []
                for el in getattr(kw.get("params"), "elts", []):
                    if isinstance(el, ast.Call) and el.args:
                        params.append((el.args[0].value,
                                       el.args[2].value if len(el.args) > 2 else ""))
                out["params"] = dict(params)
                return out
    raise AssertionError("tool_schema.py 里没有 ToolSpec(name=%r)" % tool_name)


def _snake_tokens(text):
    return set(re.findall(r"(?<![A-Za-z0-9_])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![A-Za-z0-9_])",
                          text or ""))


def test_get_data_quality_payload_carries_every_key_its_docstring_names(svc):
    """文案点名的键（含 `checks[]` 每格的名字）都得在真载荷里，一个都不能是想象出来的。"""
    doc = _handler_doc("get_data_quality")
    out = svc.get_data_quality(days=7)

    absent = [k for k in QUALITY_PROMISED if k not in out]
    assert not absent, "get_data_quality 载荷缺文案承诺的键：%s" % (absent,)
    for token in ("name", "ok", "value", "ratio", "detail"):
        assert token in doc, "文案没写 `checks[]` 每格含 %r，消费方只能猜" % token
        assert token in out["checks"][0] or token == "ok", (
            "文案写了 %r 但 checks 每格里没有：%r" % (token, sorted(out["checks"][0])))


def test_get_data_quality_copy_does_not_resurrect_legacy_issue_key(svc):
    """`data_quality_issues` 是 legacy 的名字，新引擎叫 `issues`——文案不许再许诺旧名。"""
    doc = _handler_doc("get_data_quality")
    out = svc.get_data_quality(days=7)

    assert "data_quality_issues" not in doc, (
        "get_data_quality 的 docstring 又承诺了 `data_quality_issues`——"
        "该键只活在 insights_legacy.py，新引擎那格叫 issues")
    assert "`issues`" in doc and "issues" in out
    for dead in DEAD:
        assert dead not in out, "死键 %r 回到了载荷里（修法应是改文案，不是加键）" % dead


def test_device_health_copy_keeps_data_quality_issues_because_legacy_owns_it(svc):
    """对偶：device_health 整条交回 legacy（裁5 Q1=A），它的文案写 `data_quality_issues` 是对的。

    没有这条，下一个做"全仓清零死键"的人会连着把**真键**一起改掉。
    """
    doc = _handler_doc("get_device_health")
    out = svc.device_health(days=7)

    assert "data_quality_issues" in doc
    assert "data_quality_issues" in out, (
        "device_health 已不再产出 `data_quality_issues`，文案与载荷又对不上了——"
        "此时应改文案，或重新登记该工具归属")


def test_ask_memory_return_hints_adds_hints_and_nothing_else(svc):
    """`return_hints=True` 只多出顶层 `hints`；legacy 那一对名字在新引擎里根本不存在。"""
    on = svc.ask_memory("书房电脑昨天用了多久", days=7, route="structured", return_hints=True)
    off = svc.ask_memory("书房电脑昨天用了多久", days=7, route="structured", return_hints=False)

    assert "hints" in on, "return_hints=True 却没有 `hints`：nlquery 的开关失效或改名了"
    assert isinstance(on["hints"], list)
    assert "hints" not in off, "return_hints=False 却把 hints 发出去了（开关失效）"
    for dead in ("semantic_hints", "agent_memory_hints"):
        assert dead not in on, "载荷里长出了 legacy 的 %r——与文案的修法方向相反" % dead


def test_ask_memory_copy_names_hints_not_the_legacy_pair():
    """handler docstring 与 ToolSpec 的参数说明都在 prompt 里，两处都得写 `hints`。"""
    doc = _handler_doc("ask_memory")
    spec = _spec("ask_memory")

    assert "`hints`" in doc and "semantic_hints" not in doc and "agent_memory_hints" not in doc
    desc = spec["params"].get("return_hints", "")
    assert desc, "ToolSpec 里 return_hints 的说明丢了"
    assert "hints" in desc and "semantic_hints" not in desc and "agent_memory_hints" not in desc


def test_plan_question_payload_has_no_invented_recommended_tool(svc):
    """`recommended_tool` 从没在新引擎里存在过：本族的修法只有改文案这一条。"""
    out = svc.plan_question("书房电脑昨天用了多久")

    assert "recommended_tool" not in out
    for dead in ("next_tool", "suggested_tool", "tool", "steps"):
        assert dead not in out, "规划面多出了出境键 %r——加键要走载荷键名登记，不许自落" % dead


def test_route_question_copy_only_names_keys_plan_question_returns(svc):
    """summary/pitfall 里点名的 snake_case 键，必须都能在 plan_question 的载荷顶层读到。"""
    spec = _spec("route_question")
    out = svc.plan_question("书房电脑昨天用了多久")
    text = "%s %s %s" % (spec.get("summary", ""), spec.get("description", ""),
                         spec.get("pitfall", ""))

    assert "recommended_tool" not in text, "route_question 的文案又许诺 `recommended_tool`"
    promised = _snake_tokens(text) & {"route", "intent", "entity_ids", "time_range", "hints",
                                      "question", "days", "template_id"}
    missing = [k for k in sorted(promised) if k not in out and k != "template_id"]
    assert not missing, "文案点名但 plan_question 载荷没有的键：%s" % (missing,)
    assert promised, "文案一个键都没点名，等于没告诉模型该读什么"


def test_route_question_cited_intent_values_are_the_real_enum(svc):
    """文案列举的"意图名"必须等于 `Intent` 真值集——修一条假承诺不许换来另一条假承诺。"""
    spec = _spec("route_question")
    text = spec.get("pitfall", "")

    cited = _snake_tokens(text)
    real = {m.value for m in Intent}
    listed = re.search(r"（([^）]*(?:_[^)）]*)+)）", text)
    assert listed, "pitfall 没把意图名清单写在括号里，无法核对"
    cited_in_list = cited & set(re.split(r"[/、，,]", listed.group(1)))
    assert cited_in_list, "括号里的清单没能解析成意图名：%r" % (text,)
    bogus = sorted(cited_in_list - real)
    assert not bogus, "文案列举了不存在的意图名：%s（Intent 真值=%s）" % (
        bogus, sorted(real))


def test_climate_copy_names_attrs_input_not_a_payload_key(climate_svc):
    """`current_temperature` 是**输入侧** attrs 名，不是载荷键——本轮判定，钉住这个区分。

    扫描量具把 `get_climate_sessions` 文案里的 `current_temperature` 记成"键只活在 legacy"，
    实为 `insights_legacy.py:1794` 从事件 attrs 读它、落到会话的 `room_temp_c`。
    所以文案没有凭空许诺输出键；反过来，若哪天真把 attrs 名当载荷键发出去，这条会红。
    """
    doc = _handler_doc("get_climate_sessions")
    out = climate_svc.climate_sessions(days=7)
    sessions = out.get("sessions") or []

    assert "current_temperature" in doc, "文案不再提 attrs 的室温字段，消费方无从知道温度从哪来"
    assert "current_temperature" not in out and "temperature" not in out
    for s in sessions:
        assert "current_temperature" not in s and "temperature" not in s, (
            "会话格子出现了输入侧 attrs 名——载荷形状被改动，需走出境键名登记")


def test_climate_session_temperature_chain_actually_runs(climate_svc):
    """文案那句「直接解析室温/设定温度」必须真跑得通：attrs 有温度 ⇒ 会话给出 `room_temp_c`。

    这一族此前**零锁**（全仓 tests/ 里 `room_temp_c`/`setpoint_c`/`sessions_with_temperature`
    出现 0 次，只有 callsite 形状测试碰过 climate_sessions）⇒ 实现改了、锁没有。
    """
    out = climate_svc.climate_sessions(days=7)
    sessions = out.get("sessions") or []

    assert len(sessions) == 2, "夹具本应拼出 2 段会话（cool 段 + heat 段），实得 %d" % len(sessions)
    warm = [s for s in sessions if s.get("room_temp_c") is not None]
    assert warm, "attrs 里带了 current_temperature，会话却没给出 room_temp_c——文案的解析链断了"
    assert warm[0]["setpoint_c"] is not None, "attrs 里有 temperature，setpoint_c 却是 null"
    assert warm[0]["samples"]["room_temp"] == 3, (
        "状态判不了的那条事件温度也应采信进行中的会话（samples=%s）" % warm[0]["samples"])
    assert out["events_state_unknown"] == 1
    assert out["sessions_with_temperature"] == len(warm)
    assert out["total_sessions"] == len(sessions)
    assert out["sessions_with_temperature"] < out["total_sessions"], (
        "夹具里第二段会话本应无室温，否则下面那句 temperature_note 分支就没被真跑过")
    assert "null" in out["temperature_note"], (
        "存在无室温会话时 temperature_note 仍说『全部会话均已带温度』——文案与数据相反")
