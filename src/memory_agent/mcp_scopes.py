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
ADMIN = "admin"  # WO-MA-005: 审计通道，可列全量记忆（含 revoked），需出证
ALL_SCOPES = (READ, WRITE, ADMIN)
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
    # P1.1 过程挖掘：刷新行为异常（落库）+ 人工复核
    "refresh_behavior_anomalies",
    "review_behavior_anomaly",
    # P1.2 在线异常/漂移：刷新（落库）
    "refresh_behavior_drift",
    # P1.4 规则召回：产出放宽建议（落库）
    "refresh_rule_recall_gaps",
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


def assert_write_tools_complete() -> list[str]:
    """启动期完整性断言：检查疑似写工具是否都已登记到 WRITE_TOOLS。

    安全加固（审计 P0-6）：分类表用「例外清单」而非「显式声明」，新增写工具若
    忘了登记 WRITE_TOOLS，即被当作读工具对所有 scope 开放。本函数在启动时用
    工具名关键词匹配识别疑似写工具（create/update/delete/trigger/refresh/save/
    promote/revoke/feedback/teach/define/assign/confirm/review/sweep/rollback 等），
    检查是否已登记 WRITE_TOOLS，缺失则记 error。

    注意：这是启发式检查，可能漏检（非典型命名的写工具）或误报（命名像写但实际只读）。
    新增工具时应主动评估是否需要加入 WRITE_TOOLS。

    返回疑似但未登记的写工具名列表（空列表表示通过）。
    """
    try:
        from .tool_schema import TOOL_SPECS
    except Exception:  # noqa: BLE001
        _log.warning("无法导入 tool_schema，跳过写工具完整性断言")
        return []

    # 疑似写工具的关键词前缀（工具名以这些开头）
    _WRITE_PREFIXES = (
        "create_", "update_", "delete_", "remove_", "add_", "set_",
        "trigger_", "refresh_", "save_", "promote_", "revoke_", "rollback_",
        "feedback_", "teach_", "define_", "assign_", "confirm_", "review_",
        "sweep_", "merge_", "ingest_", "collect_",
    )

    missing: list[str] = []
    for spec in TOOL_SPECS:
        if "mcp" not in spec.expose:
            continue
        name = spec.name
        if name in WRITE_TOOLS:
            continue
        # 启发式：工具名以写关键词开头
        if any(name.startswith(p) for p in _WRITE_PREFIXES):
            missing.append(name)

    if missing:
        _log.error(
            "【安全告警】以下疑似写工具未登记到 WRITE_TOOLS，将被默认为只读对所有 scope 开放: %s",
            ", ".join(sorted(missing)),
        )
    else:
        _log.info("MCP 写工具完整性断言通过：未发现疑似写工具漏登记")

    return missing
