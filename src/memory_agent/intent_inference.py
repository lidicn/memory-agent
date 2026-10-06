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

import logging

from datetime import datetime, timedelta


# ── 内置意图规则（无 LLM 快路径）──────────────────────────────────────────
# 每条规则：触发条件（行为标签列表）+ 意图 + 置信度 + 建议动作
# vMA-1.2.1：scene_graph_triggers 是第二路（场景图 objects/relations）的触发词，
# 与原路径（时间 + 设备状态）并行打分，两路各算各的分，最终取置信度高的一路。
INTENT_RULES: list[dict] = [
    {
        "intent": "watch_tv",
        "label": "想看电视",
        "triggers": ["tv_on", "tv_power_on", "电视打开", "客厅电视打开"],
        "secondary_triggers": ["light_on", "客厅灯打开", "couch", "沙发"],
        "scene_graph_triggers": ["电视", "沙发", "投影", "遥控器", "tv"],
        "confidence": 0.8,
        "suggestion": "可询问是否需要切换到常用频道或调节音量",
        "room": "客厅",
    },
    {
        "intent": "study_work",
        "label": "想工作/学习",
        "triggers": ["computer_on", "电脑打开", "书房电脑打开", "desk_on"],
        "secondary_triggers": ["light_on", "书房灯打开", "climate_on", "书房空调打开"],
        "scene_graph_triggers": ["电脑", "笔记本", "书桌", "键盘", "显示器"],
        "confidence": 0.85,
        "suggestion": "可保持书房环境安静，空调设为常用温度",
        "room": "书房",
    },
    {
        "intent": "sleep",
        "label": "想睡觉",
        "triggers": ["bedroom_light_off", "卧室灯关闭", "主卧室灯关闭"],
        "secondary_triggers": ["climate_on", "卧室空调打开", "bed", "上床"],
        "scene_graph_triggers": ["床", "枕头", "被子", "睡衣"],
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
        "scene_graph_triggers": ["钥匙", "背包", "外套", "鞋"],
        "confidence": 0.7,
        "suggestion": "可检查是否有关灯/关空调需求，启动离家安防模式",
        "room": "玄关",
    },
    {
        "intent": "arrive_home",
        "label": "刚回家",
        "triggers": ["face_known", "人脸识别", "door_open"],
        "secondary_triggers": ["light_on", "灯打开", "shoes_off", "脱鞋"],
        "scene_graph_triggers": ["背包", "购物袋", "伞", "拖鞋"],
        "confidence": 0.8,
        "suggestion": "可开启欢迎模式（灯光/空调/音乐），询问今天过得怎么样",
        "room": "玄关",
    },
    {
        "intent": "eat",
        "label": "想吃饭",
        "triggers": ["dining_light_on", "餐厅灯打开", "kitchen_on", "厨房灯打开"],
        "secondary_triggers": ["fridge_open", "冰箱打开", "cook", "做饭"],
        "scene_graph_triggers": ["餐桌", "碗", "锅", "冰箱", "餐具"],
        "confidence": 0.65,
        "suggestion": "可询问是否需要播放背景音乐或推荐菜谱",
        "room": "餐厅/厨房",
    },
    {
        "intent": "exercise",
        "label": "想运动",
        "triggers": ["livingroom_clear", "客厅清空", "mat", "瑜伽垫"],
        "secondary_triggers": ["music_on", "音乐打开", "tv_on", "电视打开"],
        "scene_graph_triggers": ["瑜伽垫", "哑铃", "跳绳", "运动服"],
        "confidence": 0.5,
        "suggestion": "可播放运动音乐或健身视频",
        "room": "客厅",
    },
]


# 场景图是「画面里有什么」的间接证据，弱于设备状态直证，基准分打折
_SG_PATH_FACTOR = 0.85
_SG_HIT_BONUS = 0.05
_SG_MAX_HITS = 3
_CONFIDENCE_CAP = 0.95
# 两路（设备/行为 + 场景图）命中**同一条规则**时，互为佐证加分
_SG_CORROBORATION_BONUS = 0.04


def _event_dt(raw) -> datetime | None:
    """解析单条事件的 `server_ts`；非字符串 / 畸形一律只废它自己，绝不抛。

    第十三轮 P4-3 的 `now` 兜底不给回来：一条坏行就能让整句 `max()` 抛异常把基准
    换成容器墙钟，历史事件全落窗外 ⇒ `/api/behaviors/intents` 静默返回空。
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _match_time_window(rule: dict, ref: datetime) -> bool:
    """规则声明了 time_window 时，判断 ref 时刻是否落在窗口内（支持跨午夜）。

    空窗口 = 不限（显式放行）；**读不通的窗口 = 不成立**。改前 except 里 `return True`，
    一条写坏的 time_window（缺 `-`、非数字、只有半边、干脆不是字符串）会让该规则全天匹配，
    约束静默消失且无痕迹 —— 第二期审计 MA-04。判据取自同仓 `patterns._json_object`：
    坏条件不能退化成「不限」。
    """
    tw = rule.get("time_window")
    if not tw:
        return True
    try:
        start_h, end_h = tw.split("-")
        start_h, end_h = int(start_h.split(":")[0]), int(end_h.split(":")[0])
    except (ValueError, AttributeError, TypeError):
        logging.getLogger(__name__).warning(
            "[Intent] 规则 %s 的 time_window=%r 读不通，按不成立处理",
            rule.get("id") or rule.get("intent") or "?", tw)
        return False
    current_h = ref.hour
    if start_h <= end_h:
        return start_h <= current_h < end_h
    return current_h >= start_h or current_h < end_h


def _score_device_path(rule: dict, behavior_labels: set) -> tuple[float, list] | None:
    """原路径：时间（由调用方保证）+ 设备状态/行为文本。主触发必须命中。"""
    primary_hits = [t for t in rule["triggers"] if t.lower() in behavior_labels]
    if not primary_hits:
        return None
    secondary_hits = [
        t for t in rule.get("secondary_triggers", []) if t.lower() in behavior_labels
    ]
    score = float(rule.get("confidence") or 0.0)
    score += min(0.15, len(secondary_hits) * 0.05)
    return round(min(_CONFIDENCE_CAP, score), 2), primary_hits + secondary_hits


def _score_scene_graph_path(rule: dict, sg_labels: set) -> tuple[float, list] | None:
    """第二路：只吃场景图 objects/relations 特征，命中场景图触发词才计分。

    场景图缺失/为空时 sg_labels 为空集 → 返回 None，调用方自动降级原路径。
    """
    sg_triggers = rule.get("scene_graph_triggers") or []
    if not sg_triggers:
        return None
    hits = [t for t in sg_triggers if t.lower() in sg_labels]
    if not hits:
        return None
    score = float(rule.get("confidence") or 0.0) * _SG_PATH_FACTOR
    score += min(0.1, (min(len(hits), _SG_MAX_HITS) - 1) * _SG_HIT_BONUS)
    return round(min(_CONFIDENCE_CAP, score), 2), hits


def _extract_event_labels(events: list[dict]) -> tuple[set, set]:
    """从事件抽取两路特征：behavior_labels（action/scene/room）与 scene_graph_labels。

    场景图 objects 取 name、relations 取三元组，兼容历史的纯字符串写法；
    任何一条事件的场景图解析失败都只影响该条，不抛错（自动降级）。
    """
    behavior_labels: set = set()
    sg_labels: set = set()
    for ev in events:
        for key in ("action", "scene", "room"):
            val = (ev.get(key) or "").lower()
            if val:
                behavior_labels.add(val)
        sg_raw = ev.get("scene_graph_json")
        if not sg_raw:
            continue
        try:
            import json
            sg = json.loads(sg_raw) if isinstance(sg_raw, str) else sg_raw
        except Exception:
            continue
        if not isinstance(sg, dict):
            continue
        for obj in sg.get("objects") or []:
            if isinstance(obj, dict):
                name = str(obj.get("name") or "").strip().lower()
                if name:
                    sg_labels.add(name)
            elif isinstance(obj, str) and obj.strip():
                sg_labels.add(obj.strip().lower())
        for rel in sg.get("relations") or []:
            if isinstance(rel, dict):
                for part in ("subject", "predicate", "object"):
                    val = str(rel.get(part) or "").strip().lower()
                    if val:
                        sg_labels.add(val)
            elif isinstance(rel, str) and rel.strip():
                sg_labels.add(rel.strip().lower())
        # 场景图里的 scene_text 也归入设备/行为路（它是同一帧的场景描述文本）
        text = str(sg.get("scene_text") or "").strip().lower()
        if text:
            behavior_labels.add(text)
    return behavior_labels, sg_labels


def infer_intent(
    events: list[dict],
    window_min: int = 10,
    person: str | None = None,
) -> dict | None:
    """从最近的行为事件推断用户意图（vMA-1.2.1 双路径）。

    events: 行为事件列表，每项含 server_ts、action、scene、room、persons_json、
            以及可选的 scene_graph_json（场景图第二路特征）。
    window_min: 时间窗口（分钟），只看最近 N 分钟的事件。
    person: 限定某个人（可选）。

    两路并行打分：
    - 原路径：时间窗口 + 设备状态/行为文本（triggers / secondary_triggers）
    - 场景图路径：scene_graph_json 的 objects / relations（scene_graph_triggers）
    取置信度更高的一路；两路都命中时在 evidence 里标 "both"。
    场景图缺失或为空自动降级原路径，不报错。

    返回 {"intent": str, "label": str, "confidence": float, "evidence": list, "suggestion": str}
    或 None（无足够证据）。
    """
    if not events:
        return None

    # 基准取库内 max(server_ts)：解析不出来的行只废它自己，不牵动整批基准
    stamped = [(_event_dt(ev.get("server_ts")), ev) for ev in events]
    stamps = [dt for dt, _ in stamped if dt is not None]
    if not stamps:
        return None
    latest_ts = max(stamps)

    window_start = latest_ts - timedelta(minutes=window_min)

    # 过滤时间窗口内的事件
    recent_events = []
    for ev_dt, ev in stamped:
        if ev_dt is None or ev_dt < window_start:
            continue
        # 如果限定了 person，只看该人的事件
        if person:
            persons_raw = ev.get("persons_json")
            if persons_raw is None:
                # list_behavior_events 已把 persons_json 反序列化成 persons
                persons_raw = ev.get("persons") or "[]"
            try:
                import json
                persons = json.loads(persons_raw) if isinstance(persons_raw, str) else persons_raw
            except (ValueError, TypeError):
                persons = []
            if not any(
                p.get("name") == person or p.get("member_id") == person
                for p in persons if isinstance(p, dict)
            ):
                continue
        recent_events.append(ev)

    if not recent_events:
        return None

    behavior_labels, scene_graph_labels = _extract_event_labels(recent_events)

    best_intent = None
    best_score = 0.0

    for rule in INTENT_RULES:
        # 检查时间窗口限制
        if not _match_time_window(rule, latest_ts):
            continue

        device = _score_device_path(rule, behavior_labels)
        scene = _score_scene_graph_path(rule, scene_graph_labels)

        if device and scene:
            path = "both"
            fused = max(device[0], scene[0]) + _SG_CORROBORATION_BONUS
            chosen = (round(min(_CONFIDENCE_CAP, fused), 2), device[1] + scene[1])
            evidence = list(dict.fromkeys(device[1] + scene[1]))
        elif device:
            path = "device_state"
            chosen = device
            evidence = list(device[1])
        elif scene:
            path = "scene_graph"
            chosen = scene
            evidence = list(scene[1])
        else:
            continue

        score = chosen[0]
        if score > best_score:
            best_score = score
            best_intent = {
                "intent": rule["intent"],
                "label": rule["label"],
                "confidence": round(score, 2),
                "evidence": evidence,
                "suggestion": rule["suggestion"],
                "room": rule.get("room", ""),
                "path": path,
                "scene_graph_used": bool(scene),
                "events_in_window": len(recent_events),
            }

    return best_intent


def infer_intent_sequence(
    events: list[dict],
    person: str | None = None,
    max_intents: int = 3,
    window_min: int | None = None,
) -> list[dict]:
    """从行为事件序列推断多个可能的意图（按置信度排序）。

    返回最多 max_intents 个意图，每个含 intent/label/confidence/evidence/suggestion。

    window_min：调用方指定的时间窗口（分钟）。给了就**只用这一个窗口**——单个窗口
    最多产出一个意图，这是"只看最近 N 分钟"的字面语义，不做静默补窗口。
    不给则按 [5, 15, 30] 三窗口各试一遍再按置信度排序。
    """
    if not events:
        return []

    # 用不同时间窗口推断
    results = []
    seen_intents = set()

    for window in [5, 15, 30] if window_min is None else [max(1, int(window_min))]:
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
