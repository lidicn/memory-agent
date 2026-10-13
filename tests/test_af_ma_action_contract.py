"""AF 消费面 ↔ MA 提供面 的四枚动作契约锁（路线图 G-MA-03/G-MA-05 的盘上侧）。

真源是 AF 仓 `src/autoforge/af_adapters/ma.py` 的四份常量（`MA_ACTIONS`／`_REQUIRED_PARAMS`／
`_REQUIRED_KEYS`／`AF_ONLY_PARAMS`；取法＝`git -C <AF 仓> show HEAD:src/autoforge/af_adapters/ma.py`，
本档 2026-10-13 转抄时那文件最后由 `16f35f9` 改过）。本档把它们**转抄**成判据，方向只有一个：
**AF 会发出去的参数，MA 必须收得到；AF 会读出来的键，MA 必须给得出**。

⚠️ **本锁的射程**与 `tests/contract/test_ma_db_contract.py` 同形：对端名单是**转抄**的，所以
**AF 那侧改名本锁⛔ 看得见**（它锁的是 MA 这一侧：MA 改名／丢键／加必填参数都会当场咬）。
AF 侧的分叉由巡检轮对撞，⛔ 把本档当第二份真源维护。

读数器仍走 AST：`@mcp.tool()` 那批函数是 `_build_server()` 里的闭包，本机 mcp 版本旧时 import 不到。
"""

from __future__ import annotations

import ast
import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent import tool_schema  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MCP_SERVER_PY = os.path.join(REPO, "src", "memory_agent", "mcp_server.py")
TEMPLATES_PY = os.path.join(REPO, "src", "memory_agent", "templates.py")

#: AF `MA_ACTIONS`：动作名 → AF 允许出现在 IR params 里的键名。
AF_ALLOWED_PARAMS = {
    "route_question": ("question", "days"),
    "list_analysis_templates": ("category",),
    "export_insight": ("template_id",),
    "run_analysis_template": ("template_id", "days", "start", "end", "include_timeline"),
}
#: AF `_REQUIRED_PARAMS`：AF 认为必给的键。
AF_REQUIRED_PARAMS = {
    "route_question": ("question",),
    "export_insight": ("template_id",),
    "run_analysis_template": ("template_id",),
}
#: AF `_REQUIRED_KEYS`：成功载荷必须含的键（`export_insight` 的三块在 `insight` 那一层之下）。
AF_REQUIRED_KEYS = {
    "list_analysis_templates": ("templates",),
    "export_insight": ("insight", "queries", "implementation"),
    "run_analysis_template": ("template", "window", "entities", "summary_text"),
    "route_question": (),
}
#: AF `AF_ONLY_PARAMS`：AF 侧自用、⛔ 转给 MA 的键。
AF_ONLY_PARAMS = frozenset({"entity_id"})


def _parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), filename=path)


def _wire_defs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    out: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (getattr(target, "attr", None) or getattr(target, "id", "")) == "tool":
                out[node.name] = node
    return out


WIRE = _wire_defs(_parse(MCP_SERVER_PY))


def _ma_signature(name: str) -> tuple[set[str], set[str]]:
    """返回 (MA 收得到的参数名, 其中必填的参数名)。手写工具读 wire，generated 工具读 ToolSpec。"""
    node = WIRE.get(name)
    if node is not None:
        positional = node.args.args + node.args.kwonlyargs
        defaults = list(node.args.defaults)
        kw_defaults = list(node.args.kw_defaults)
        n_default = len(defaults)
        accepted, required = set(), set()
        for idx, arg in enumerate(node.args.args):
            if arg.arg in ("self", "cls"):
                continue
            accepted.add(arg.arg)
            has_default = idx >= len(node.args.args) - n_default
            if not has_default:
                required.add(arg.arg)
        for arg, dflt in zip(node.args.kwonlyargs, kw_defaults, strict=False):
            accepted.add(arg.arg)
            if dflt is None:
                required.add(arg.arg)
        return accepted, required
    spec = tool_schema.SPEC_BY_NAME.get(name)
    if spec is None:
        raise AssertionError(f"{name} 既⛔ 在 wire 面也⛔ 在 ToolSpec 面")
    params = [p for p in (spec.params or []) if p.name != "self"]
    return {p.name for p in params}, {p.name for p in params if p.required}


def _dict_return_keys(tree: ast.Module, func_name: str, min_keys: int = 2) -> list[list[str]]:
    """收集某个函数里所有「键数 ≥ min_keys 的 dict 字面量 return」的键名。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            out = []
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                    keys = [k.value for k in ret.value.keys if isinstance(k, ast.Constant)]
                    if len(keys) >= min_keys:
                        out.append(keys)
            return out
    raise AssertionError(f"{func_name} 在源码里找不到——读数器或函数名变了")


def test_af_allowlist_reader_selflock():
    """判据表⛔ 空：四枚动作、参数元组非空、键名单非空——空集会让下面所有腿假绿。"""
    assert len(AF_ALLOWED_PARAMS) == 4, f"AF 动作名单变了（现读 {len(AF_ALLOWED_PARAMS)} 枚）"
    assert all(v for v in AF_ALLOWED_PARAMS.values()), "AF 允许参数里有空元组"
    assert set(AF_ALLOWED_PARAMS) == set(AF_REQUIRED_KEYS), "两份 AF 名单的动作集不一致"
    for action in AF_ALLOWED_PARAMS:
        accepted, _ = _ma_signature(action)
        assert accepted, f"MA 侧 {action} 一个参数都读不到——读数器坏了"


def test_every_param_af_may_send_is_accepted_by_ma():
    """AF 会透传的每一个键名，MA 的签名里必须有——否则那一次调用必吃 TypeError 或被静默丢参。"""
    bad = {}
    for action, allowed in AF_ALLOWED_PARAMS.items():
        accepted, _ = _ma_signature(action)
        missing = sorted(set(allowed) - accepted)
        if missing:
            bad[action] = missing
    assert not bad, f"AF 允许透传但 MA 签名里⛔ 有的键：{bad}"


def test_params_af_thinks_required_are_required_on_ma():
    """AF 标必给的键，MA 侧⛔ 能带默认值——带默认值＝AF 漏传时 MA 静默按默认跑。"""
    bad = {}
    for action, required in AF_REQUIRED_PARAMS.items():
        _, ma_required = _ma_signature(action)
        missing = sorted(set(required) - ma_required)
        if missing:
            bad[action] = missing
    assert not bad, f"AF 当必给、MA 却给了默认值的键：{bad}"


def test_af_only_params_are_not_part_of_ma_signature():
    """AF 侧专用键（`entity_id`）⛔ 该出现在 MA 这四枚动作的签名里，否则透传语义就分了叉。"""
    leaked = {}
    for action in AF_ALLOWED_PARAMS:
        accepted, _ = _ma_signature(action)
        hit = sorted(AF_ONLY_PARAMS & accepted)
        if hit:
            leaked[action] = hit
    assert not leaked, f"AF 自用键出现在 MA 签名里：{leaked}"


def test_export_insight_payload_carries_the_three_blocks():
    """AF `_REQUIRED_KEYS` 在 `insight` 那一层下读三块；`_ALL_EMPTY_REFUSED` 也点名它。"""
    tree = _parse(TEMPLATES_PY)
    rows = _dict_return_keys(tree, "export_insight", min_keys=3)
    assert rows, "export_insight 里没有 ≥3 键的 dict 字面量 return"
    ok = [keys for keys in rows if set(AF_REQUIRED_KEYS["export_insight"]) <= set(keys)]
    assert ok, f"export_insight 的返回里没有同时含 insight/queries/implementation 的那一份：{rows}"


def test_run_template_payload_carries_the_four_blocks():
    """`run_analysis_template` 的成功体由 `templates.run_template` 产出，AF 按四键判形状。"""
    tree = _parse(TEMPLATES_PY)
    rows = _dict_return_keys(tree, "run_template", min_keys=3)
    ok = [keys for keys in rows if set(AF_REQUIRED_KEYS["run_analysis_template"]) <= set(keys)]
    assert ok, f"run_template 的成功体⛔ 含 template/window/entities/summary_text：{rows}"


def test_list_analysis_templates_payload_key_is_templates():
    """AF 只在 wire 面上读这一枚键名，MCP 包装层丢键＝该腿当场形状塌。"""
    rows = _dict_return_keys(_parse(MCP_SERVER_PY), "list_analysis_templates", min_keys=2)
    ok = [keys for keys in rows if "templates" in keys]
    assert ok, f"list_analysis_templates 的返回体里没有 `templates` 键：{rows}"


def test_export_insight_wrapper_keeps_the_nesting_key():
    """AF 的 `_NESTED_BLOCK` 先落到 `insight` 那一层再读三块——包装层丢这枚键，三块一起读空。"""
    rows = _dict_return_keys(_parse(MCP_SERVER_PY), "export_insight", min_keys=1)
    ok = [keys for keys in rows if {"ok", "insight"} <= set(keys)]
    assert ok, f"mcp_server 的 export_insight 成功体⛔ 含 insight 那一层：{rows}"


def test_ma_adds_no_required_param_af_never_sends():
    """反方向那条腿：MA 把参数改成必填或新增必填，而 AF 的透传名单里没有它 ⇒ 每次调用必缺参。"""
    bad = {}
    for action, allowed in AF_ALLOWED_PARAMS.items():
        _, ma_required = _ma_signature(action)
        extra = sorted(ma_required - set(allowed))
        if extra:
            bad[action] = extra
    assert not bad, f"MA 侧必填、AF 透传名单⛔ 收的键：{bad}"
