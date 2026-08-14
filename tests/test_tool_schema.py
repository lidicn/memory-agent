"""tool_schema 一致性测试：保证内置 LLM 与 MCP 共用同一份工具定义、不再漂移。

运行环境：需在能 import memory_worker 的环境执行（容器 / 安装依赖后）。
- 基础断言（无需 mcp）：规格完整性、内置 ⊆ MCP、build_openai_tools 数量正确。
- 进阶断言（import mcp 成功时）：每个 catalogued MCP 工具都有对应的已注册函数，
  且内置与 MCP 共享工具的参数名完全对齐，防止"同名不同参"的割裂。
"""

import inspect
import os
import sys

import pytest

# 让 tests/ 能 import src 下的 memory_worker 包
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_worker.tool_schema import (  # noqa: E402
    TOOL_NAMES,
    TOOL_SPECS,
    SPEC_BY_NAME,
    build_catalog,
    build_openai_tools,
)

# ── 基础断言（不依赖 mcp）────────────────────────────────────────────────────


def test_specs_nonempty_metadata():
    for s in TOOL_SPECS:
        assert s.name, "工具名不能为空"
        assert s.summary and s.description, f"{s.name} 缺 summary/description"
        assert s.example, f"{s.name} 缺 example"
        assert s.pitfall, f"{s.name} 缺 pitfall"
        assert s.expose, f"{s.name} 未设置 expose"


def test_names_unique_and_tool_names_consistent():
    names = [s.name for s in TOOL_SPECS]
    assert len(names) == len(set(names)), f"工具名重复：{names}"
    mcp_spec_names = [n for n in names if "mcp" in SPEC_BY_NAME[n].expose]
    assert set(TOOL_NAMES) == set(mcp_spec_names), "TOOL_NAMES 与 expose='mcp' 的工具集合不一致"


def test_builtin_is_subset_of_mcp():
    builtin = {s.name for s in TOOL_SPECS if "builtin" in s.expose}
    mcp = {s.name for s in TOOL_SPECS if "mcp" in s.expose}
    assert builtin <= mcp, f"内置暴露了 MCP 未暴露的工具：{builtin - mcp}"


def test_openai_tools_for_builtin():
    tools = build_openai_tools(("builtin",))
    names = {t["function"]["name"] for t in tools}
    builtin = {s.name for s in TOOL_SPECS if "builtin" in s.expose}
    assert names == builtin, "build_openai_tools(('builtin',)) 与 builtin 暴露集合不一致"
    for t in tools:
        fn = t["function"]
        assert fn["parameters"]["type"] == "object"
        assert "properties" in fn["parameters"]


def test_catalog_matches_spec():
    cat = build_catalog()
    cat_names = {c["name"] for c in cat}
    mcp = {s.name for s in TOOL_SPECS if "mcp" in s.expose}
    assert cat_names == mcp
    for c in cat:
        s = SPEC_BY_NAME[c["name"]]
        assert c["summary"] == s.summary
        assert [p["name"] for p in c["params"]] == [p.name for p in s.params]


# ── 进阶断言（依赖 mcp 可用）──────────────────────────────────────────────────

mcp_server = None
try:
    import memory_worker.mcp_server as mcp_server  # noqa: E402
except Exception:  # pragma: no cover - 本地无 mcp 时跳过
    mcp_server = None

skip_mcp = pytest.mark.skipif(
    mcp_server is None, reason="mcp 不可用，跳过 MCP 注册一致性检查"
)


@skip_mcp
def test_every_catalogued_mcp_tool_is_registered():
    for name in TOOL_NAMES:
        assert hasattr(mcp_server, name), (
            f"TOOL_NAMES 包含 {name}，但 mcp_server 未定义对应的 @mcp.tool 函数"
        )


@skip_mcp
def test_shared_tool_params_aligned_with_mcp():
    """内置与 MCP 共享工具（builtin+mcp）的参数名必须与 MCP 已注册函数签名对齐。"""
    for s in TOOL_SPECS:
        if "builtin" not in s.expose or "mcp" not in s.expose:
            continue
        assert hasattr(mcp_server, s.name), f"共享工具 {s.name} 未注册到 MCP"
        fn = getattr(mcp_server, s.name)
        sig_params = list(inspect.signature(fn).parameters.keys())
        spec_params = [p.name for p in s.params]
        assert sig_params == spec_params, (
            f"共享工具 {s.name} 的 MCP 函数参数 {sig_params} 与 spec 参数 {spec_params} 不一致"
        )


@skip_mcp
def test_catalog_from_spec_used_by_help():
    # help 工具依赖的 TOOL_CATALOG 应来自 build_catalog（与 spec 同源）
    assert mcp_server.TOOL_CATALOG is build_catalog() or mcp_server.TOOL_CATALOG == build_catalog()
