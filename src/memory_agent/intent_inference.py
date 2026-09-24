"""P4b 意图推断：从观察到的行为序列推断用户意图。

与 activity_inference.py 的区别：
- activity_inference：从传感器序列重建"发生了什么"（事后归因）
- intent_inference：从当前行为推断"用户想要什么"（向前看，用于主动服务）

核心能力：
1. 规则意图推断：基于已知行为模式（开灯+开电视=想看电视）
2. 序列意图推断：基于时间窗口内的行为序列推断意图
3. 意图置信度：给出推断的置信度和支持证据
4. 意图建议：基于推断的意图给出主动服务建议

设计原则：
- 无 LLM 快路径：常见意图用规则匹配，毫秒级响应
- 语义交 Agent：复杂/模糊意图交 LLM 做语义推断
- 可解释：每个意图推断都带支持证据（哪些行为触发了推断）
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any


# ── 内置意图规则（无 LLM 快路径）──────────────────────────────────────────
# 每条规则：触发条件（行为标签列表）+ 意图 + 置信度 + 建议动作
INTENT_RULES: list[dict] = [
    {
        "intent": "watch_tv",
        "label": "想看电视",
        "triggers": ["tv_on", "tv_power_on", "电视打开", "客厅电视打开"],
        "secondary_triggers": ["light_on", "客厅灯打开", "couch", "沙发"],
        "confidence": 0.8,
        "suggestion": "可询问是否需要切换到常用频道或调节音量",
        "room": "客厅",
    },
    {
        "intent": "study_work",
        "label": "想工作/学习",
        "triggers": ["computer_on", "电脑打开", "书房电脑打开", "desk_on"],
        "secondary_triggers": ["light_on", "书房灯打开", "climate_on", "书房空调打开"],
        "confidence": 0.85,
        "suggestion": "可保持书房环境安静，空调设为常用温度",
        "room": "书房",
    },
    {
        "intent": "sleep",
        "label": "想睡觉",
        "triggers": ["bedroom_light_off", "卧室灯关闭", "主卧室灯关闭"],
        "secondary_triggers": ["climate_on", "卧室空调打开", "bed", "上床"],
        "confidence": 0.75,
        "suggestion": "可关闭其他房间灯光，空调设为睡眠模式",
        "room": "主卧室",
        "time_window": "21:00-06:00",
    },
    {
        "intent": "leave_home",
        "label": "要出门",
        "triggers": ["door_open", "门打开", "大门打开", "front_door_open"],
        "secondary_triggers": ["shoes", "换鞋", "key", "拿钥匙"],
        "confidence": 0.7,
        "suggestion": "可检查是否有关灯/关空调需求，启动离家安防模式",
        "room": "玄关",
    },
    {
        "intent": "arrive_home",
        "label": "刚回家",
        "triggers": ["face_known", "人脸识别", "door_open"],
        "secondary_triggers": ["light_on", "灯打开", "shoes_off", "脱鞋"],
        "confidence": 0.8,
        "suggestion": "可开启欢迎模式（灯光/空调/音乐），询问今天过得怎么样",
        "room": "玄关",
    },
    {
        "intent": "eat",
        "label": "想吃饭",
        "triggers": ["dining_light_on", "餐厅灯打开", "kitchen_on", "厨房灯打开"],
        "secondary_triggers": ["fridge_open", "冰箱打开", "cook", "做饭"],
        "confidence": 0.65,
        "suggestion": "可询问是否需要播放背景音乐或推荐菜谱",
        "room": "餐厅/厨房",
    },
    {
        "intent": "exercise",
        "label": "想运动",
        "triggers": ["livingroom_clear", "客厅清空", "mat", "瑜伽垫"],
        "secondary_triggers": ["music_on", "音乐打开", "tv_on", "电视打开"],
        "confidence": 0.5,
        "suggestion": "可播放运动音乐或健身视频",
        "room": "客厅",
    },
]


def infer_intent(
    events: list[dict],
    window_min: int = 10,
    person: str | None = None,
) -> dict | None:
    """从最近的行为事件推断用户意图。

    events: 行为事件列表，每项含 server_ts、action、scene、room、persons_json。
    window_min: 时间窗口（分钟），只看最近 N 分钟的事件。
    person: 限定某个人（可选）。

    返回 {"intent": str, "label": str, "confidence": float, "evidence": list, "suggestion": str}
    或 None（无足够证据）。
    """
    if not events:
        return None

    # 取最近的事件时间作为基准
    now = datetime.now()
    try:
        latest_ts = max(
            datetime.fromisoformat(ev.get("server_ts", ""))
            for ev in events
            if ev.get("server_ts")
        )
    except (ValueError, TypeError):
        latest_ts = now

    window_start = latest_ts - timedelta(minutes=window_min)

    # 过滤时间窗口内的事件
    recent_events = []
    for ev in events:
        ts_str = ev.get("server_ts", "")
        if not ts_str:
            continue
        try:
            ev_dt = datetime.fromisoformat(ts_str)
        except ValueError:
            continue
        if ev_dt >= window_start:
            # 如果限定了 person，只看该人的事件
            if person:
                persons_raw = ev.get("persons_json") or "[]"
                try:
                    import json
                    persons = json.loads(persons_raw) if isinstance(persons_raw, str) else persons_raw
                except (json.JSONDecodeError, TypeError):
                    persons = []
                if not any(p.get("name") == person for p in persons):
                    continue
            recent_events.append(ev)

    if not recent_events:
        return None

    # 提取行为标签（action + scene + room）
    behavior_labels = set()
    for ev in recent_events:
        action = (ev.get("action") or "").lower()
        scene = (ev.get("scene") or "").lower()
        room = (ev.get("room") or "").lower()
        if action:
            behavior_labels.add(action)
        if scene:
            behavior_labels.add(scene)
        if room:
            behavior_labels.add(room)

    # 匹配意图规则
    best_intent = None
    best_score = 0.0

    for rule in INTENT_RULES:
        # 检查时间窗口限制
        if "time_window" in rule:
            tw = rule["time_window"]
            start_h, end_h = tw.split("-")
            start_h = int(start_h.split(":")[0])
            end_h = int(end_h.split(":")[0])
            current_h = latest_ts.hour
            if start_h <= end_h:
                if not (start_h <= current_h < end_h):
                    continue
            else:  # 跨午夜
                if not (current_h >= start_h or current_h < end_h):
                    continue

        # 主触发条件
        primary_hits = [t for t in rule["triggers"] if t.lower() in behavior_labels]
        if not primary_hits:
            continue

        # 辅助触发条件（加分）
        secondary_hits = [t for t in rule.get("secondary_triggers", []) if t.lower() in behavior_labels]

        # 计算分数：基础置信度 + 辅助触发加分
        score = rule["confidence"]
        score += min(0.15, len(secondary_hits) * 0.05)
        score = min(0.95, score)

        if score > best_score:
            best_score = score
            best_intent = {
                "intent": rule["intent"],
                "label": rule["label"],
                "confidence": round(score, 2),
                "evidence": primary_hits + secondary_hits,
                "suggestion": rule["suggestion"],
                "room": rule.get("room", ""),
                "events_in_window": len(recent_events),
            }

    return best_intent


def infer_intent_sequence(
    events: list[dict],
    person: str | None = None,
    max_intents: int = 3,
) -> list[dict]:
    """从行为事件序列推断多个可能的意图（按置信度排序）。

    返回最多 max_intents 个意图，每个含 intent/label/confidence/evidence/suggestion。
    """
    if not events:
        return []

    # 用不同时间窗口推断
    results = []
    seen_intents = set()

    for window in [5, 15, 30]:
        intent = infer_intent(events, window_min=window, person=person)
        if intent and intent["intent"] not in seen_intents:
            intent["window_min"] = window
            results.append(intent)
            seen_intents.add(intent["intent"])

    # 按置信度排序
    results.sort(key=lambda x: -x["confidence"])
    return results[:max_intents]


def get_intent_suggestions(intent: dict) -> list[str]:
    """基于推断的意图给出具体的主动服务建议列表。"""
    if not intent:
        return []

    suggestions = [intent.get("suggestion", "")]

    # 根据意图类型补充建议
    intent_type = intent.get("intent", "")
    if intent_type == "watch_tv":
        suggestions.append("可询问是否需要打开常用 App（如 Netflix/YouTube）")
    elif intent_type == "study_work":
        suggestions.append("可开启专注模式（通知静音、灯光调亮）")
    elif intent_type == "sleep":
        suggestions.append("可设置闹钟和明天的日程提醒")
    elif intent_type == "leave_home":
        suggestions.append("可检查门窗是否关好，启动安防监控")
    elif intent_type == "arrive_home":
        suggestions.append("可播报今天的重要消息和待办事项")

    return [s for s in suggestions if s]
