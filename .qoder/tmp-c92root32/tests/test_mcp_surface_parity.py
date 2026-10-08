"""MCP 工具面三方对账（wire / ToolSpec / scope 准入表）。

审计登记项的兑现：第一轮 P0-4 之后 MCP 面上有 35 个手写工具**只有实现、没有 ToolSpec**，
于是 `help`/`describe` 查不到、presence 的 `caps.tools`（= `tool_schema.TOOL_NAMES`）
系统性少报 55/90；更狠的是自我日记三件套连 `mcp_scopes` 的准入表都没进——
`list_tools()` 看得见，调用必吃 `NOT_FOUND`（实测 `scope_of('read_self_diary')=='unknown'`）。

两个形状是同一个根因：**「能力清单」和「实现面」由三处各自维护**（@mcp.tool 注册、
ToolSpec 登记、scope 准入表），任何一处漏登记，外部看到的就与真实可调用面不一致。
所以这里不用计数断言（数字会随加工具而变、红久了就没人看），而是**用 AST 从源码读 wire 面**，
逐条比对另两张表——判据跟着实现走，不跟着常量走。

运行口径：AST 部分不需要 `mcp` 包，本机也能跑红；调函数那条需要真服务器，
本机 mcp 为旧 SDK 时（`mcp_server.mcp_server is None`）按既有惯例跳过。
"""

from __future__ import annotations

import ast
import asyncio
import collections
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import tool_schema  # noqa: E402
from memory_agent.mcp_scopes import (  # noqa: E402
    READ,
    REGISTERED_TOOLS,
    WRITE,
    WRITE_TOOLS,
    requires,
    scope_of,
)
from memory_agent.store import Store  # noqa: E402

MCP_SERVER_PY = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "memory_agent", "mcp_server.py"))


def _wire_defs_raw() -> list[tuple[str, list[str], int]]:
    """从源码读 @mcp.tool() 注册：(工具名, 参数名列表, 行号)——**每个 def 一条，不去重**。

    用 AST 而不是 import：`_build_server()` 里的工具函数是闭包、不是模块属性，
    且本机 mcp 版本旧时 `mcp_server.mcp_server` 直接是 None——AST 读数两边同构。
    保留重复是为了能钉住第一轮 P0-4 那个形状（同名工具定义两遍，SDK 丢后一份）。
    """
    with open(MCP_SERVER_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=MCP_SERVER_PY)
    out: list[tuple[str, list[str], int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (getattr(target, "attr", None) or getattr(target, "id", "")) != "tool":
                continue
            args = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
            args += [a.arg for a in node.args.kwonlyargs]
            if node.args.vararg or node.args.kwarg:
                args.append("**kwargs")
            out.append((node.name, args, node.lineno))
    return out


WIRE_DEFS = _wire_defs_raw()
# 后向兼容：WIRE 仍是「名字 → 参数」，但重复定义另有专门的锁在看。
WIRE = {name: params for name, params, _ in WIRE_DEFS}
SPEC_MCP = {s.name for s in tool_schema.TOOL_SPECS if "mcp" in (s.expose or ())}


def test_wire_surface_nonempty_and_ast_reader_finds_the_known_tools():
    # 读数器自己也要有锁：它坏了会让下面三条全部「空集=通过」。
    assert len(WIRE) > 50, f"AST 读到的 @mcp.tool() 只有 {len(WIRE)} 个，读数器或装饰器写法变了"
    # def 条数 == 唯一名条数：当前源码里没有同名重复（重复由下一条锁判红）。
    assert len(WIRE_DEFS) == len(WIRE), (
        f"def 条数 {len(WIRE_DEFS)} ≠ 唯一名 {len(WIRE)}，同名注册确实存在于源码")
    for known in ("help", "ask_memory", "read_self_diary", "write_self_diary",
                  "generate_self_diary", "assign_member_device"):
        assert known in WIRE, f"{known} 不在 wire 面上——注册形态变了，请同步更新本测试的判据"


def test_every_wire_tool_has_a_toolspec():
    """caps/`help`/`describe` 只认 ToolSpec；wire 有、spec 无 = 对外少报能力。"""
    missing = sorted(set(WIRE) - SPEC_MCP)
    assert not missing, f"MCP 面上这些工具没有 ToolSpec 登记（caps 会少报）：{missing}"


GENERATED_MCP = {s.name for s in tool_schema.TOOL_SPECS
                 if s.generated and "mcp" in (s.expose or ())}


def test_wire_surface_is_handwritten_plus_generated_and_no_double_registration():
    """两面之和必须等于实测工具面，且**不许交叉**。

    交叉 = 同一个名字既由 `@mcp.tool()` 手写注册、又被 `register_simple_tools` 动态注册，
    正是第一轮 P0-4「Tool already exists: …」的形状（SDK 丢后一份，谁能跑由导入顺序决定）。
    """
    assert not (set(WIRE) & GENERATED_MCP), (
        f"这些工具同时被手写与 schema 注册（重复注册）：{sorted(set(WIRE) & GENERATED_MCP)}")
    # 交叉锁反过来也要成立：spec 里 generated=False 的工具必须由手写函数提供实现
    handwritten_spec = SPEC_MCP - GENERATED_MCP
    assert handwritten_spec <= set(WIRE), (
        f"这些非 generated 工具在 wire 上找不到实现：{sorted(handwritten_spec - set(WIRE))}")


def test_no_tool_name_is_defined_twice_on_the_wire():
    """第一轮 P0-4 的另一半形状：同一个名字在源码里被 `@mcp.tool()` 定义**两遍**。

    `WIRE` 是名字键的读数器，两份定义会塌成一个——所以这里按 def 计数，不看集合。
    实测（HEAD 复扫）：`_wire_defs_raw()` 读到 85 条 def、85 个唯一名，重复已不在；
    本锁的存在是为了它回来时当场判红，而不是等 SDK 打印 "Tool already exists"。
    """
    counts = collections.Counter(name for name, _, _ in WIRE_DEFS)
    dups = {k: v for k, v in counts.items() if v > 1}
    assert not dups, f"这些工具在 wire 上被定义了多次（后一份会被 SDK 丢弃）：{dups}"


def test_no_phantom_spec_on_the_wire_surface():
    """spec 有、两面都无 = 目录对外宣称一个调不动的工具（第一轮 P0-2 的形状）。

    generated 工具由 `register_simple_tools` 在运行时注册，AST 看不见，单独算一面。
    """
    ghost = sorted(SPEC_MCP - set(WIRE) - GENERATED_MCP)
    assert not ghost, f"ToolSpec 登记了但 MCP 面没有实现：{ghost}"


def test_every_wire_tool_is_scope_registered():
    """未进 REGISTERED_TOOLS/WRITE_TOOLS 的工具，`scope_of` 返回 unknown，
    分发层按 P0-3 一律 NOT_FOUND——工具「看得见却调不动」就是这样造出来的。"""
    admitted = set(REGISTERED_TOOLS) | set(WRITE_TOOLS)
    missing = sorted(n for n in WIRE if n not in admitted)
    assert not missing, f"这些 wire 工具未登记 scope，任何令牌调用都吃 NOT_FOUND：{missing}"
    # 双向锁：准入表里也不许有既不在 wire 也不在 spec 的幽灵名
    ghosts = sorted(admitted - set(WIRE) - SPEC_MCP)
    assert not ghosts, f"scope 准入表里有幽灵工具名：{ghosts}"


def test_wire_tool_scope_resolution_is_never_unknown():
    for name in WIRE:
        assert scope_of(name) != "unknown", f"{name} 在准入表里查不到 scope"
        assert requires(name, [READ, WRITE]), f"{name} 对 read+write 令牌也不可达"


def test_spec_params_align_with_the_wire_signature():
    """目录参数名必须与真实函数签名一一对应（顺序也算），否则 describe 说的和调的要的是两套。

    generated 工具由 schema 反向生成签名、天然对齐；这里真正约束的是手写函数体。
    """
    bad = []
    for name, params in sorted(WIRE.items()):
        spec = tool_schema.SPEC_BY_NAME.get(name)
        if spec is None or spec.generated:
            continue
        declared = [p.name for p in spec.params]
        if declared != params:
            bad.append(f"{name}: spec={declared} wire={params}")
    assert not bad, "ToolSpec 参数与 MCP 函数签名不一致：\n" + "\n".join(bad)


def test_specs_reject_builtin_exposure_without_dispatch_target():
    """补登记的这批全是 MCP 专有：若哪天把它们开放给内置却没有派发目标，validate_specs 必须拦。"""
    for name in ("read_self_diary", "write_self_diary", "generate_self_diary",
                 "query_device_usage", "list_members"):
        spec = tool_schema.SPEC_BY_NAME[name]
        assert "builtin" not in spec.expose, f"{name} 是手写 MCP 专有工具，不该对内置开放"
        assert spec.generated is False, f"{name} 保留手写函数体，不该被 schema 反向注册"


# ── 本轮实测的两个缺陷：自我日记三件套调不动 + assign_member_device 假成功 ──────

DIARY_READ = "read_self_diary"
DIARY_WRITE = ("write_self_diary", "generate_self_diary")


def test_self_diary_read_is_a_registered_read_tool():
    assert DIARY_READ in REGISTERED_TOOLS
    assert scope_of(DIARY_READ) == READ, "未登记时 scope_of 返回 unknown，分发层直接 NOT_FOUND"


def test_self_diary_writes_require_write_token():
    for name in DIARY_WRITE:
        assert name in WRITE_TOOLS, f"{name} 会写 agent_memory，必须登记为写工具"
        assert scope_of(name) == WRITE
        assert not requires(name, [READ]), f"{name} 不该被只读令牌放行"
        assert requires(name, [READ, WRITE]), f"{name} 对 read+write 令牌必须可达"


def _tools_by_name():
    """真服务器里的工具对象（本机 mcp 旧 SDK 时返回 None，测试跳过）。"""
    from memory_agent import mcp_server as ms
    server = getattr(ms, "mcp_server", None)
    if server is None:
        return None, ms
    manager = getattr(server, "_tool_manager", None)
    tools = getattr(manager, "_tools", None) if manager else None
    if not isinstance(tools, dict):
        return None, ms
    return tools, ms


def test_assign_member_device_rejects_unknown_member_instead_of_orphan_rows(monkeypatch):
    """`set_member_devices` 是 DELETE+INSERT 且无外键拦着：成员不存在时过去照样写成员设备表
    并返回 ok=True——调用方以为改成功，实际留下孤儿行。与 assign_member_room 同口径先验存在性。"""
    tools, ms = _tools_by_name()
    if tools is None:
        pytest.skip("MCP SDK 不可用或版本过旧，无法取到已注册工具函数")
    tool = tools.get("assign_member_device")
    assert tool is not None, "assign_member_device 不在已注册工具表里"

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "t.db"), tz_offset_hours=8.0)
        store.init_schema()
        try:
            monkeypatch.setattr(ms, "get_runtime",
                                lambda: type("RT", (), {"store": store})())
            result = asyncio.run(tool.fn(member_id="ghost_member",
                                         entity_ids=["light.study_desk"]))
            assert result.get("ok") is False, f"成员不存在必须拒绝，实得 {result}"
            assert "NOT_FOUND" in str(result.get("error", "")), result
            conn = store.connect()
            with store._lock:
                orphans = conn.execute(
                    "SELECT COUNT(*) FROM member_devices WHERE member_id = ?",
                    ("ghost_member",)).fetchone()[0]
            assert orphans == 0, f"不存在的成员仍被写进 member_devices（{orphans} 行孤儿）"
        finally:
            store.close()


def test_assign_member_device_still_writes_for_a_real_member(monkeypatch):
    """反例锁：存在性守卫不能把正常路径也一起拦掉。"""
    tools, ms = _tools_by_name()
    if tools is None:
        pytest.skip("MCP SDK 不可用或版本过旧，无法取到已注册工具函数")
    tool = tools["assign_member_device"]

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "t.db"), tz_offset_hours=8.0)
        store.init_schema()
        try:
            member = store.create_member("锁人", "🦉")
            monkeypatch.setattr(ms, "get_runtime",
                                lambda: type("RT", (), {"store": store})())
            result = asyncio.run(tool.fn(member_id=member["id"],
                                         entity_ids=["media_player.tv_livingroom"]))
            assert result.get("ok") is True, result
            fresh = store.get_member(member["id"])
            assert "media_player.tv_livingroom" in (fresh.get("devices") or []), fresh
        finally:
            store.close()


# ── DCD 20261007 §一 裁甲：目录唯一真源 = tool_schema.build_catalog() ──────────

# 这里曾有**第二份真源**：`mcp_server.py:169` 一份 434 行手抄的 `TOOL_CATALOG` 字面量
# （48 条），在文件末尾被 `TOOL_CATALOG = build_catalog()` 覆盖，而四个读者
# （help / describe / selftest / WebUI 的 MCP 接入页）全在函数体内、取到的都是覆盖后的值。
# 那份镜像无人消费、条目却比真源少 46 条，逐条补齐是白付的双向维护费——本批删除。
# 下面三把门钉的是"它别回来"：绑定点唯一、值必须是 build_catalog()、且不许有 import 期读者。

def _module_binding_nodes(tree):
    """模块级（深度 0）赋值语句：[(绑定名, lineno, 值节点), ...]，多目标/元组解包不算。"""
    out = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        else:
            continue
        if isinstance(target, ast.Name) and node.value is not None:
            out.append((target.id, node.lineno, node.value))
    return out


def _import_time_names(node):
    """节点里**import 期**会求值的名字：进装饰器/默认值/基类/复合语句体，不进函数与方法的体。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        out = [n for dec in node.decorator_list for n in _import_time_names(dec)]
        out += [n for d in node.args.defaults for n in _import_time_names(d)]
        out += [n for d in node.args.kw_defaults if d for n in _import_time_names(d)]
        return out
    if isinstance(node, ast.ClassDef):
        out = [n for dec in node.decorator_list for n in _import_time_names(dec)]
        out += [n for b in node.bases for n in _import_time_names(b)]
        out += [n for kw in node.keywords for n in _import_time_names(kw)]
        for stmt in node.body:
            out += _import_time_names(stmt)
        return out
    if isinstance(node, ast.Name):
        # 只数"读"（Load）：绑定语句自己的目标名是 Store，否则 `TOOL_CATALOG = build_catalog()`
        # 这一行会把自己判成 import 期读者。
        return [node.id] if isinstance(node.ctx, ast.Load) else []
    out = []
    for child in ast.iter_child_nodes(node):
        out += _import_time_names(child)
    return out


def _catalog_source_readings(path):
    """从源码读目录面的形状量（不 import mcp，本机与容器同构）。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    bindings = _module_binding_nodes(tree)
    r = {"catalog": [b for b in bindings if b[0] == "TOOL_CATALOG"],
         "names": [b for b in bindings if b[0] == "TOOL_NAMES"],
         "big_dict_tables": [],
         "import_time_reads": []}
    for name, lineno, value in bindings:
        if (isinstance(value, ast.List) and len(value.elts) >= 10
                and sum(1 for e in value.elts if isinstance(e, ast.Dict)) >= 10):
            r["big_dict_tables"].append((name, lineno, len(value.elts)))
    # 全局口径：目录的两个名字在 import 期**一处都不许被读**（真源绑定自身那两行的值里
    # 只含 build_catalog / TOOL_NAMES_FROM_SPEC，不会误报）。不按"最早绑定之前"划界——
    # 那会让"字面量 + 派生行"一起回来时派生行落在界后而漏检（对照档 M1 实测抓到这一点）。
    for node in tree.body:
        hits = [n for n in _import_time_names(node) if n in ("TOOL_CATALOG", "TOOL_NAMES")]
        if hits:
            r["import_time_reads"].append((type(node).__name__, node.lineno,
                                           sorted(set(hits))))
    return r


READINGS = _catalog_source_readings(MCP_SERVER_PY)


def test_tool_catalog_is_bound_exactly_once_from_the_spec_builder():
    """目录面在 mcp_server 里**恰好一处**模块级绑定，且值必须是 `build_catalog()` 调用。

    钉的是"覆盖"这个动作本身：谁再手写一份字面量、或把真源换成别的东西（本地拼装、
    缓存常量），这里当场判红，而不是等 help/describe 与 caps.tools 悄悄分叉。
    """
    cat = READINGS["catalog"]
    assert len(cat) == 1, f"模块级 TOOL_CATALOG 绑定应恰好一处，实为 {[(l, ast.dump(v)[:60]) for _, l, v in cat]}"
    lineno, value = cat[0][1], cat[0][2]
    assert isinstance(value, ast.Call), f":{lineno} 的 TOOL_CATALOG 不再是函数调用，而是 {type(value).__name__}"
    func = value.func
    assert isinstance(func, ast.Name) and func.id == "build_catalog", (
        f":{lineno} 的目录真源不是 build_catalog()，而是 {ast.dump(func)[:80]}")
    assert not value.args and not value.keywords, f"build_catalog() 传了参数，同源口径要重核：{ast.dump(value)[:120]}"
    names = READINGS["names"]
    assert len(names) == 1, f"模块级 TOOL_NAMES 绑定应恰好一处，实为 {[l for _, l, _ in names]}"
    value = names[0][2]
    assert isinstance(value, ast.Name) and value.id == "TOOL_NAMES_FROM_SPEC", (
        f":{names[0][1]} 的 TOOL_NAMES 不再取自 tool_schema（实为 {ast.dump(value)[:80]}）")


def test_no_hand_written_tool_dict_table_is_bound_at_module_level():
    """镜像回归的通用形状：模块级不许再有 ≥10 条的 dict 列表字面量。

    逐条盯 `TOOL_CATALOG` 只能防同名回来，防不住换个名字再来一份"待接线的真源"。
    """
    assert not READINGS["big_dict_tables"], (
        f"mcp_server.py 里出现手写大表（≥10 条 dict）：{READINGS['big_dict_tables']}")


def test_catalog_is_never_read_at_import_time():
    """目录名字在 import 期**一处都不许被读**：读者只能在函数体运行期取真源绑定后的值。

    这正是被删那份字面量当初"能活着"的原因——`:604` 的派生行在 import 期消费它。
    谁把 TOOL_CATALOG/TOOL_NAMES 放进装饰器、默认值或模块级语句，这里判红。
    """
    assert not READINGS["import_time_reads"], (
        f"import 期就读目录的地方：{READINGS['import_time_reads']}")


def test_describe_page_serves_the_whole_spec_catalog():
    """四个读者里唯一能不调 SDK 就跑到的一条：WebUI 的 MCP 接入页必须拿到**整份**目录。

    `describe()` 读的是模块级 `TOOL_CATALOG`/`TOOL_NAMES`，而那份手写字面量只有 48 条、
    真源有 90 条——镜像若还在（或被换成截断版），这一页就会少报能力，且一声不响。
    """
    from memory_agent import mcp_server as ms

    info = ms.describe()
    catalog = info["catalog"]
    names = info["tools"]
    assert len(catalog) == len(tool_schema.build_catalog()), (
        f"describe() 给前端的目录 {len(catalog)} 条，真源 {len(tool_schema.build_catalog())} 条")
    assert names == ms.TOOL_NAMES and len(names) == len(catalog), (
        f"tools 与 catalog 不同源：{len(names)} vs {len(catalog)}")
    assert [t["name"] for t in catalog] == list(names), "目录与工具名清单顺序/内容不一致"
    missing = [t["name"] for t in catalog if not t.get("summary")]
    assert not missing, f"这些工具进目录却没有 summary，前端只能显示兜底文案：{missing}"
