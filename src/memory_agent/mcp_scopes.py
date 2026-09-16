"""MCP 工具级权限（v0.7.5-1 scope 授权）

设计原则
--------
* **默认只读**：新令牌只带 ``read``；写工具必须显式授权 ``write``。
* **分类按「是否改变状态」**：会写入库 / 改配置 / 触发外部动作的工具归 ``write``，
  其余（含会花钱但不改数据的 ``ask_memory`` / ``analyze_camera``）仍是 ``read``。
* **未知工具保守放行 read**：新增工具若忘了登记，按 read 处理并在日志提示，
  不因为漏登记就拒绝调用（避免升级即断链）。
"""

from __future__ import annotations

import logging

_log = logging.getLogger("mcp.scopes")

READ = "read"
WRITE = "write"
ALL_SCOPES = (READ, WRITE)
DEFAULT_SCOPES = [READ]

# 会改变系统状态的工具（写入库 / 改配置 / 触发采集 / 变更记忆状态）
WRITE_TOOLS = frozenset({
    # 成员与档案
    "create_member",
    "assign_member_room",
    "assign_member_device",
    "confirm_member_tag",
    # 活动规则 / 信号学习
    "define_activity",
    "teach_signal",
    # Agent 记忆生命周期
    "add_semantic_memory",
    "promote_memory",
    "revoke_memory",
    "rollback_agent_memory",
    "feedback_memory",
    "sweep_promote_candidates",
    # 模板 / 技能沉淀
    "save_analysis_template",
    "delete_analysis_template",
    "save_skill",
    # 运维动作
    "trigger_collection",
    "trigger_incremental_collection",
})

_REPORTED_UNKNOWN: set[str] = set()


def scope_of(tool: str) -> str:
    """返回工具所需 scope：write 或 read（默认）。"""
    if not tool:
        return READ
    if tool in WRITE_TOOLS:
        return WRITE
    return READ


def requires(tool: str, granted: list[str] | tuple[str, ...] | None) -> bool:
    """判断令牌的 scopes 是否足以调用该工具。"""
    need = scope_of(tool)
    if need == READ:
        return True
    return WRITE in (granted or [])


def normalize(scopes) -> list[str]:
    """规范化 scope 列表：去重、过滤非法值、保持顺序。空值 → 默认只读。"""
    if not scopes:
        return list(DEFAULT_SCOPES)
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",")]
    out: list[str] = []
    for s in scopes or []:
        s = str(s).strip().lower()
        if s in ALL_SCOPES and s not in out:
            out.append(s)
    return out or list(DEFAULT_SCOPES)


def note_unknown(tool: str) -> None:
    """记录未登记的工具名（只提示一次），便于后续补登记。"""
    if tool and tool not in WRITE_TOOLS and tool not in _REPORTED_UNKNOWN:
        _REPORTED_UNKNOWN.add(tool)
        _log.debug("MCP 工具未在权限表中登记，按 read 处理: %s", tool)
