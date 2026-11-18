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

import bisect
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from .day_bounds import clamp_minutes


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


def _event_has_person(ev: dict, person: str) -> bool:
    """该事件是否归属 `person`（`persons` 现行形状与 `persons_json` 旧形状都认）。"""
    raw = ev.get("persons_json")
    if raw is None:
        raw = ev.get("persons") or "[]"
    try:
        import json
        persons = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    return any(
        isinstance(p, dict) and (p.get("name") == person or p.get("member_id") == person)
        for p in persons
    )


def _latest_dt(events: list[dict]) -> datetime | None:
    """库内 `server_ts` 的最大值；解析不出来的行只废它自己。"""
    stamps = [dt for dt in (_event_dt(ev.get("server_ts")) for ev in (events or []))
              if dt is not None]
    return max(stamps) if stamps else None


def _filter_window(events: list[dict], window_min: int,
                   person: str | None) -> tuple[list[dict], datetime | None]:
    """按「最新事件时刻」回溯 `window_min` 分钟取窗口内事件（可限定成员）。

    基准取库内 max(server_ts) 而不是墙钟 now：行为事件库以离散快照为主，
    用墙钟会在"此刻无事件"时把整窗判空（2026-11-17 AF 六档全零即是这种读法）。
    """
    latest_ts = _latest_dt(events)
    if latest_ts is None:
        return [], None
    window_start = latest_ts - timedelta(minutes=window_min)
    recent: list[dict] = []
    for ev in events or []:
        ev_dt = _event_dt(ev.get("server_ts"))
        if ev_dt is None or ev_dt < window_start:
            continue
        if person and not _event_has_person(ev, person):
            continue
        recent.append(ev)
    return recent, latest_ts


def diagnose_intent_windows(
    events: list[dict],
    person: str | None = None,
    windows: tuple[int, ...] | list[int] = (5, 15, 30),
) -> dict:
    """意图空转归因：把「没出结果」拆成可行动的四类（C3）。

    2026-11-17 AF 联调：六档窗口全零，与"规则面全坏"长得一模一样，调用方
    无法判断该换时段再测还是该修规则。本函数给出：
    ``no_events``（库空/时间戳全解析不出）｜``person_empty``（该成员无事件）｜
    ``window_empty``（窗口内事件为 0）｜``insufficient_events``（窗口内 <2 条）｜
    ``no_rule_match``（有事件、无规则命中）｜``matched``（有命中）。

    返回 ``{as_of, events_total, person, windows[], reason, matched_any}``，
    每窗带 ``events_in_window``／``matched``／``confidence``。纯函数，⛔ 依赖 MCP SDK。
    """
    who = (person or "").strip() or None
    data = list(events or [])
    latest_ts = _latest_dt(data)
    detail: dict = {
        "as_of": latest_ts.isoformat() if latest_ts else "",
        "events_total": len(data),
        "person": who or "",
        "windows": [],
        "reason": "",
        "matched_any": False,
    }
    if latest_ts is None:
        detail["reason"] = "no_events"
        return detail

    any_events = False
    any_match = False
    max_count = 0
    for raw_window in windows:
        window = clamp_minutes(raw_window)
        recent, _ = _filter_window(data, window, who)
        count = len(recent)
        max_count = max(max_count, count)
        any_events = any_events or count > 0
        # recent 已按人过滤，这里 person=None 防二次过滤把结果再削一遍
        intent = infer_intent(recent, window_min=window, person=None) if recent else None
        if intent:
            any_match = True
        detail["windows"].append({
            "window_min": window,
            "events_in_window": count,
            "matched": intent.get("intent") if intent else None,
            "confidence": intent.get("confidence") if intent else None,
            "path": intent.get("path") if intent else None,
        })

    if any_match:
        detail["reason"] = "matched"
    elif not any_events:
        detail["reason"] = "person_empty" if who else "window_empty"
    elif max_count < 2:
        detail["reason"] = "insufficient_events"
    else:
        detail["reason"] = "no_rule_match"
    detail["matched_any"] = any_match
    return detail


def replay_intent_match(
    events: list[dict],
    windows: tuple[int, ...] | list[int] = (5, 15, 30),
    person: str | None = None,
    top_n: int = 10,
) -> dict:
    """离线回放：把**每个事件时刻当作窗口末端**跑一遍意图规则，量规则面匹配率（B 档）。

    为什么不是实时调用：`infer_behavior_intent` 的窗口基准是"库内最新事件"，实时
    调用只能看到"此刻"一个切面；2026-11-17 AF 六档全零实测就是这种读法（那一刻
    没有落在窗口里的行为序列）——**正确空转，但量不出匹配率**。按事件时间戳回放
    才是"规则面到底薄不薄"的可比读数。

    口径（与链主 2026-11-17 定的 B 档一致）：
    * 分母 = 可解析时间戳的行为事件数（可限定成员）；
    * 分子 = 以该事件为窗口末端时，至少命中一条意图规则的**事件数**（逐窗口各算）；
    * 分路径 = 命中时走的是 device_state / scene_graph / both；
    * TOP 未命中 = 所有窗口都没命中的窗口末端事件，按 `action|scene` 签名计数（⛔ 人名）。

    `server_ts` 的 aware/naive 混排按"aware 折 UTC 再去 tzinfo"归一后排序；
    坏行只废它自己。纯函数，⛔ 依赖 MCP SDK / store。
    """
    entries: list[tuple[datetime, dict]] = []
    for ev in events or []:
        dt = _event_dt(ev.get("server_ts"))
        if dt is None:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        if person and not _event_has_person(ev, person):
            continue
        entries.append((dt, ev))
    entries.sort(key=lambda item: item[0])

    total = len(entries)
    detail: dict = {
        "as_of": entries[-1][0].isoformat() if entries else "",
        "events_total": total,
        "person": (person or "").strip() or "",
        "windows": [],
        "paths": {},
        "top_unmatched": [],
    }
    if not total:
        detail["windows"] = [
            {"window_min": clamp_minutes(w), "matched_end_events": 0, "match_rate": 0.0}
            for w in windows
        ]
        return detail

    times = [dt for dt, _ in entries]
    paths: Counter = Counter()
    matched_any: set[int] = set()

    for raw_window in windows:
        window = clamp_minutes(raw_window)
        matched = 0
        for idx, (dt, _ev) in enumerate(entries):
            start = dt - timedelta(minutes=window)
            lo = bisect.bisect_left(times, start)
            slice_events = [e for _, e in entries[lo:idx + 1]]
            intent = infer_intent(slice_events, window_min=window, person=None)
            if intent:
                matched += 1
                matched_any.add(idx)
                paths[intent.get("path") or "unknown"] += 1
        detail["windows"].append({
            "window_min": window,
            "matched_end_events": matched,
            "match_rate": round(matched / total, 3),
        })

    unmatched = Counter()
    for idx, (_dt, ev) in enumerate(entries):
        if idx in matched_any:
            continue
        sig = str(ev.get("action") or ev.get("scene") or "unknown").strip() or "unknown"
        if ev.get("scene_graph_json"):
            sig += "+scene_graph"
        unmatched[sig] += 1
    detail["paths"] = dict(paths)
    detail["top_unmatched"] = [
        {"pattern": sig, "count": count} for sig, count in unmatched.most_common(top_n)
    ]
    return detail


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
    recent_events, latest_ts = _filter_window(events, window_min, person)
    if not recent_events or latest_ts is None:
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

    # window_min 只有下界时极值会走到 `timedelta(minutes=)`：`2000000000` 直接
    # OverflowError（500），`1000000000` 不崩但窗口回溯到公元 124 年 = 全量扫历史
    # （第二期审计 MA-13，lesson 87「有下界 ≠ 有上界」）。
    for window in [5, 15, 30] if window_min is None else [clamp_minutes(window_min)]:
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
