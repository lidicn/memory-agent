"""P4c 意图→主动服务联动：推断意图后自动建议/执行相关动作。

与 rule_engine.py 的关系：
- rule_engine：事件→规则匹配→动作执行（用户预定义规则）
- intent_action：意图→动作建议映射（内置常识，无需用户配置）

设计原则：
- 默认 dry_run（只建议不执行），避免误操作
- 动作类型复用 rule_engine 已支持的类型（light/tts/alert/log/webhook/camera）
- 可解释：每个建议带理由（为什么这个意图对应这个动作）
"""

from __future__ import annotations

from typing import Any


# ── 意图→动作建议映射（内置常识）──────────────────────────────────────────
# 每个意图对应一组建议动作，按优先级排序
INTENT_ACTIONS: dict[str, list[dict]] = {
    "watch_tv": [
        {
            "action": "light",
            "target": "客厅主灯",
            "operation": "dim",
            "level": 30,
            "reason": "看电视时调暗客厅灯光，减少屏幕反光",
            "auto": False,  # 需要用户确认
        },
        {
            "action": "tts",
            "message": "已为你打开电视，需要切换到常用频道吗？",
            "reason": "询问是否需要进一步服务",
            "auto": True,
        },
    ],
    "study_work": [
        {
            "action": "light",
            "target": "书房灯",
            "operation": "on",
            "reason": "工作/学习时保持书房明亮",
            "auto": False,
        },
        {
            "action": "tts",
            "message": "书房环境已准备好，需要开启专注模式吗？",
            "reason": "询问是否需要专注模式（通知静音）",
            "auto": True,
        },
    ],
    "sleep": [
        {
            "action": "light",
            "target": "客厅主灯",
            "operation": "off",
            "reason": "睡觉时关闭客厅灯光",
            "auto": False,
        },
        {
            "action": "light",
            "target": "房间床头灯",
            "operation": "dim",
            "level": 10,
            "reason": "卧室床头灯调暗，营造睡眠氛围",
            "auto": False,
        },
        {
            "action": "tts",
            "message": "晚安，需要设置闹钟吗？",
            "reason": "睡前问候",
            "auto": True,
        },
    ],
    "leave_home": [
        {
            "action": "alert",
            "message": "检测到出门意图，建议检查：灯光是否关闭、空调是否关闭、门窗是否关好",
            "reason": "出门前安全检查提醒",
            "auto": True,
        },
        {
            "action": "light",
            "target": "全部灯光",
            "operation": "off",
            "reason": "出门时关闭所有灯光",
            "auto": False,
        },
    ],
    "arrive_home": [
        {
            "action": "light",
            "target": "客厅主灯",
            "operation": "on",
            "reason": "回家时自动打开客厅灯光",
            "auto": False,
        },
        {
            "action": "tts",
            "message": "欢迎回家，今天过得怎么样？",
            "reason": "回家问候",
            "auto": True,
        },
    ],
    "eat": [
        {
            "action": "tts",
            "message": "需要播放背景音乐吗？",
            "reason": "用餐时询问是否需要音乐",
            "auto": True,
        },
    ],
    "exercise": [
        {
            "action": "tts",
            "message": "需要播放运动音乐或健身视频吗？",
            "reason": "运动时询问是否需要音乐/视频",
            "auto": True,
        },
    ],
}


def get_intent_actions(intent: str) -> list[dict]:
    """获取某个意图的建议动作列表。

    返回：[
        {"action": "light", "target": "客厅主灯", "operation": "dim", "level": 30, "reason": "...", "auto": false},
        ...
    ]
    """
    return INTENT_ACTIONS.get(intent, [])


def get_all_intent_actions() -> dict[str, list[dict]]:
    """获取所有意图→动作映射（用于文档/调试）。"""
    return INTENT_ACTIONS


async def execute_intent_actions(
    intent: str,
    rt: Any,
    dry_run: bool = True,
    person: str | None = None,
) -> dict:
    """执行（或预览）某个意图的建议动作。

    Args:
        intent: 意图名称（如 "watch_tv"）
        rt: runtime 对象
        dry_run: True=只预览不执行，False=实际执行
        person: 触发意图的人（可选，用于日志）

    Returns:
        {
            "intent": str,
            "dry_run": bool,
            "actions": [
                {"action": ..., "target": ..., "reason": ..., "auto": bool, "executed": bool, "result": str},
                ...
            ],
            "summary": {"total": int, "executed": int, "skipped": int, "need_confirm": int},
        }
    """
    actions = get_intent_actions(intent)
    if not actions:
        return {
            "intent": intent,
            "dry_run": dry_run,
            "actions": [],
            "summary": {"total": 0, "executed": 0, "skipped": 0, "need_confirm": 0},
        }

    results = []
    executed = 0
    skipped = 0
    need_confirm = 0

    for action_def in actions:
        action_type = action_def.get("action", "log")
        auto = action_def.get("auto", False)

        result_entry = {
            **action_def,
            "executed": False,
            "result": "",
        }

        if dry_run:
            # 预览模式：只记录，不执行
            result_entry["result"] = "预览模式，未执行"
            if not auto:
                need_confirm += 1
                result_entry["result"] = "预览模式，需要用户确认后执行"
            skipped += 1
        elif not auto:
            # 非自动动作：需要用户确认，跳过
            need_confirm += 1
            result_entry["result"] = "需要用户确认，已跳过"
            skipped += 1
        else:
            # 自动动作：执行
            try:
                if action_type == "tts":
                    # TTS 播报
                    message = action_def.get("message", "")
                    if rt.alert_dispatcher:
                        await rt.alert_dispatcher.dispatch(
                            kind="tts",
                            message=message,
                            source=f"intent:{intent}",
                        )
                    result_entry["result"] = f"TTS 已播报: {message[:30]}..."
                    executed += 1
                elif action_type == "alert":
                    # 告警
                    message = action_def.get("message", "")
                    if rt.alert_dispatcher:
                        await rt.alert_dispatcher.dispatch(
                            kind="alert",
                            message=message,
                            source=f"intent:{intent}",
                        )
                    result_entry["result"] = f"告警已发送: {message[:30]}..."
                    executed += 1
                elif action_type == "log":
                    # 日志
                    result_entry["result"] = "已记录日志"
                    executed += 1
                else:
                    # light/camera/webhook 等需要外部系统配合的动作
                    # 目前只记录，实际执行需要 butler/HA 配合
                    result_entry["result"] = f"动作类型 {action_type} 需要外部系统配合，已记录建议"
                    skipped += 1
            except Exception as e:
                result_entry["result"] = f"执行失败: {str(e)}"
                skipped += 1

        results.append(result_entry)

    return {
        "intent": intent,
        "dry_run": dry_run,
        "person": person,
        "actions": results,
        "summary": {
            "total": len(actions),
            "executed": executed,
            "skipped": skipped,
            "need_confirm": need_confirm,
        },
    }
