"""change_attribution.py - 行为变化归因（因果推理层阶段1）

检测行为模式变化并自动搜索候选原因。纯统计 + 规则，不引入 LLM 调用。

核心能力：
1. detect_change：基于 Mann-Whitney U 检验和 Cohen's d 检测行为指标显著变化
2. search_candidate_causes：在变化时间窗口内搜索可能导致变化的候选原因事件
3. attribute：完整归因（变化检测 + 原因搜索）

与 behavior_predictor.py 的关系：
- behavior_predictor：预测未来行为（向前看）
- change_attribution：解释过去变化（向后看），复用相同的 persons_json 解析约定
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

# ─── 配置常量 ──────────────────────────────────────────────────

EFFECT_SIZE_THRESHOLD: float = 0.5    # Cohen's d 中等效应阈值
SIGNIFICANCE_LEVEL: float = 0.05      # p 值显著性水平
LARGE_EFFECT_THRESHOLD: float = 1.5   # 大效应阈值（小样本 p 不足时的回退判定）

# 候选原因分类关键词（按 _ 形式切词后精确匹配）
_DEVICE_KW: frozenset[str] = frozenset({
    "light", "lamp", "tv", "television", "aircon", "air_conditioner",
    "ac", "heater", "humidifier", "fan", "speaker", "curtain", "lock",
})
_PERSON_KW: frozenset[str] = frozenset({"face", "person", "visitor", "intruder"})
_ENV_KW: frozenset[str] = frozenset({"door", "window", "motion", "smoke", "gas", "leak"})

# 人类可读事件名称
_DISPLAY: dict[str, str] = {
    "light_on": "开灯", "light_off": "关灯",
    "tv_on": "电视打开", "tv_off": "电视关闭",
    "aircon_on": "空调打开", "aircon_off": "空调关闭",
    "face_known": "已知人脸识别", "face_unknown": "陌生人出现",
    "door_open": "开门", "door_close": "关门",
    "window_open": "开窗", "window_close": "关窗",
    "weekday": "工作日", "weekend": "周末", "holiday": "节假日",
}


# ─── 内部工具函数 ──────────────────────────────────────────────

def _parse_persons(raw: Any) -> list[dict]:
    """解析 persons_json 字段，返回 dict 列表。兼容 str / list / None。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _parse_ts(ts: Any) -> datetime | None:
    """解析 ISO 格式时间戳，失败或过短返回 None。"""
    if not isinstance(ts, str) or len(ts) < 16:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _has_person(ev: dict, person: str) -> bool:
    """检查事件的 persons_json 中是否包含指定人名。"""
    return any(p.get("name") == person for p in _parse_persons(ev.get("persons_json")))


def _classify(action: str) -> str | None:
    """将事件动作名分类到候选原因类型。

    按下划线切词后精确匹配关键词集，避免子串误匹配
    （如 "surface_clean" 不会误判为 person_change）。

    返回 "device_change" / "person_change" / "environment_change" / None。
    """
    if not action:
        return None
    a = action.lower()
    words = set(a.replace("-", "_").split("_"))

    if words & _PERSON_KW:
        return "person_change"
    if words & _ENV_KW:
        return "environment_change"
    if words & _DEVICE_KW:
        return "device_change"
    # 通用设备命名模式回退
    if a.endswith(("_on", "_off", "_open", "_close")):
        return "device_change"
    return None


def _daily_values(
    events: list[dict], person: str, metric: str, room: str | None = None,
) -> dict[str, float]:
    """提取指定人每日指标值，返回 {YYYY-MM-DD: value}。

    metric:
    - "arrival_time":      每天首次出现的小时浮点数（如 19.5 = 19:30）
    - "activity_count":    每天事件数量
    - "room_distribution": 每天在 room 中的事件占比 (0~1)
    - "active_duration":   每天从首次到末次出现的小时数
    """
    out: dict[str, float] = {}

    if metric == "arrival_time":
        for ev in sorted(events, key=lambda e: e.get("server_ts", "")):
            if not _has_person(ev, person):
                continue
            dt = _parse_ts(ev.get("server_ts", ""))
            if dt is None:
                continue
            day = dt.strftime("%Y-%m-%d")
            if day not in out:
                out[day] = dt.hour + dt.minute / 60.0

    elif metric == "activity_count":
        cnt: dict[str, int] = defaultdict(int)
        for ev in events:
            if not _has_person(ev, person):
                continue
            dt = _parse_ts(ev.get("server_ts", ""))
            if dt is not None:
                cnt[dt.strftime("%Y-%m-%d")] += 1
        out = dict(cnt)

    elif metric == "room_distribution":
        if room is None:
            return {}
        rc: dict[str, int] = defaultdict(int)
        tc: dict[str, int] = defaultdict(int)
        for ev in events:
            if not _has_person(ev, person):
                continue
            dt = _parse_ts(ev.get("server_ts", ""))
            if dt is None:
                continue
            day = dt.strftime("%Y-%m-%d")
            tc[day] += 1
            if ev.get("room") == room or ev.get("scene") == room:
                rc[day] += 1
        out = {d: rc.get(d, 0) / t for d, t in tc.items()}

    elif metric == "active_duration":
        span: dict[str, list[datetime]] = {}
        for ev in sorted(events, key=lambda e: e.get("server_ts", "")):
            if not _has_person(ev, person):
                continue
            dt = _parse_ts(ev.get("server_ts", ""))
            if dt is None:
                continue
            day = dt.strftime("%Y-%m-%d")
            if day not in span:
                span[day] = [dt, dt]
            else:
                span[day][1] = dt
        out = {d: (e - s).total_seconds() / 3600.0 for d, (s, e) in span.items()}

    return out


def _stats(vals: list[float], dates: list[str]) -> dict:
    """计算一组值的汇总统计（mean / median / std / count / period）。"""
    n = len(vals)
    if n == 0:
        return {"mean": 0.0, "median": 0.0, "std": 0.0, "count": 0, "period": ""}
    return {
        "mean": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
        "std": round(statistics.stdev(vals), 4) if n >= 2 else 0.0,
        "count": n,
        "period": f"{min(dates)}~{max(dates)}" if dates else "",
    }


def _cohens_d(before: list[float], after: list[float]) -> float:
    """Cohen's d 效应量 = (after_mean − before_mean) / pooled_std。

    零方差时封顶 ±10.0，均值相同返回 0.0。
    """
    n1, n2 = len(before), len(after)
    if n1 == 0 or n2 == 0:
        return 0.0
    m1, m2 = statistics.mean(before), statistics.mean(after)
    s1 = statistics.stdev(before) if n1 >= 2 else 0.0
    s2 = statistics.stdev(after) if n2 >= 2 else 0.0

    if n1 >= 2 and n2 >= 2:
        pv = ((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / (n1 + n2 - 2)
        pooled = math.sqrt(pv) if pv > 0 else 0.0
    else:
        pooled = max(s1, s2)

    diff = m2 - m1
    if pooled < 1e-10:
        return 0.0 if abs(diff) < 1e-10 else (10.0 if diff > 0 else -10.0)
    return round(diff / pooled, 4)


def _norm_cdf(x: float) -> float:
    """标准正态分布 CDF（基于 math.erf，纯 stdlib）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _mw_exact_p(n1: int, n2: int, u_obs: int) -> float:
    """Mann-Whitney U 精确双尾 p 值（小样本无并列值）。

    DP 递推：f(m, n, u) = f(m−1, n, u−n) + f(m, n−1, u)
    复杂度 O(n1·n2·n1·n2)，n1·n2 ≤ 400 时 < 1s。
    """
    mx = n1 * n2
    dp: list[list[list[int]]] = [
        [[0] * (mx + 1) for _ in range(n2 + 1)] for _ in range(n1 + 1)
    ]
    dp[0][0][0] = 1

    for i in range(n1 + 1):
        for j in range(n2 + 1):
            if i == 0 and j == 0:
                continue
            for u in range(mx + 1):
                v = 0
                if i > 0:
                    pu = u - j
                    if 0 <= pu <= mx:
                        v += dp[i - 1][j][pu]
                if j > 0:
                    v += dp[i][j - 1][u]
                dp[i][j][u] = v

    total = math.comb(n1 + n2, n1)
    p_le = sum(dp[n1][n2][u] for u in range(min(u_obs, mx) + 1)) / total
    return round(min(1.0, 2.0 * p_le), 6)


def _mw_p(before: list[float], after: list[float]) -> float:
    """Mann-Whitney U 检验双尾 p 值。

    小样本 (n1·n2 ≤ 400, N ≤ 30) 且无并列值时用精确检验；
    否则用正态近似（含并列校正 + 连续性校正）。
    """
    n1, n2 = len(before), len(after)
    if n1 == 0 or n2 == 0 or (n1 == 1 and n2 == 1):
        return 1.0

    comb = sorted(
        [(v, 0) for v in before] + [(v, 1) for v in after], key=lambda x: x[0]
    )
    N = n1 + n2

    ranks = [0.0] * N
    ties: list[int] = []
    i = 0
    while i < N:
        j = i + 1
        while j < N and comb[j][0] == comb[i][0]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[k] = avg
        ties.append(j - i)
        i = j

    r1 = sum(ranks[k] for k in range(N) if comb[k][1] == 0)
    u1 = n1 * n2 + n1 * (n1 + 1) / 2.0 - r1
    u2 = n1 * n2 - u1
    u_min = min(u1, u2)

    has_ties = any(t > 1 for t in ties)

    # 小样本无并列 → 精确检验
    if not has_ties and n1 * n2 <= 400 and N <= 30:
        return _mw_exact_p(n1, n2, int(round(u_min)))

    # 正态近似（并列校正 + 连续性校正）
    mu = n1 * n2 / 2.0
    tie_term = sum(t ** 3 - t for t in ties)
    var = (n1 * n2 / 12.0) * ((N + 1) - tie_term / (N * (N - 1)))
    sigma = math.sqrt(max(var, 1e-10))
    z = max(0.0, (abs(u_min - mu) - 0.5) / sigma)
    return round(min(1.0, max(0.0, 2.0 * (1.0 - _norm_cdf(z)))), 6)


def _empty_result(metric: str, person: str) -> dict:
    """数据不足 (< 2 天) 时返回的空结果。"""
    z = {"mean": 0.0, "median": 0.0, "std": 0.0, "count": 0, "period": ""}
    return {
        "changed": False, "metric": metric, "person": person,
        "before": dict(z), "after": dict(z),
        "effect_size": 0.0, "p_value": 1.0, "direction": "none",
    }


def _correlation(change_ct: int, baseline_ct: int) -> float:
    """时间相关性 (0~1)：两窗口频次差异占总频次的比例。"""
    total = change_ct + baseline_ct
    return abs(change_ct - baseline_ct) / total if total > 0 else 0.0


def _confidence(change_ct: int, baseline_ct: int, corr: float) -> float:
    """综合置信度 (0~1) = 0.5·相关性 + 0.5·频率变化幅度。

    频率变化幅度通过 Laplace 平滑的折叠变化率映射：
    fold=1 → 0, fold=3 (或 1/3) → 封顶 1.0。
    """
    fold = (change_ct + 1) / (baseline_ct + 1)
    mag = (
        min(1.0, (fold - 1.0) / 2.0)
        if fold >= 1.0
        else min(1.0, (1.0 / fold - 1.0) / 2.0)
    )
    return 0.5 * corr + 0.5 * mag


def _description(event_type: str, change_ct: int, baseline_ct: int, lookback: int) -> str:
    """生成人类可读的候选原因描述。"""
    name = _DISPLAY.get(event_type, event_type)
    if baseline_ct == 0 and change_ct > 0:
        return f"变化前{lookback}天内{name}出现了{change_ct}次（基线期无此事件）"
    if change_ct == 0 and baseline_ct > 0:
        return f"变化前{lookback}天内{name}消失（基线期有{baseline_ct}次）"
    if change_ct > baseline_ct:
        pct = round((change_ct - baseline_ct) / baseline_ct * 100)
        return f"变化前{lookback}天内{name}次数增加了{pct}%"
    pct = round((baseline_ct - change_ct) / baseline_ct * 100)
    return f"变化前{lookback}天内{name}次数减少了{pct}%"


def _is_weekday(date_str: str) -> bool:
    """判断 YYYY-MM-DD 是否为工作日（周一~周五）。"""
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


# ─── 公开 API ──────────────────────────────────────────────────

def detect_change(
    events: list[dict],
    person: str,
    metric: str,
    split_ratio: float = 0.5,
    room: str | None = None,
) -> dict:
    """检测某人的行为指标是否发生显著变化。

    将事件按时间排序后，按 split_ratio 分成前半段（基线）和后半段（观察期）。
    metric 支持: "arrival_time"（到家时间，小时浮点数）、
    "activity_count"（单位时间事件数）、
    "room_distribution"（某房间占比，需配合 room 参数）、
    "active_duration"（活跃时长）。

    changed 判定: |Cohen's d| > 0.5 且 (p < 0.05 或 |d| ≥ 1.5)。

    返回::

        {
            "changed": bool,
            "metric": str,
            "person": str,
            "before": {"mean": float, "median": float, "std": float,
                       "count": int, "period": "YYYY-MM-DD~YYYY-MM-DD"},
            "after":  {"mean": float, "median": float, "std": float,
                       "count": int, "period": "YYYY-MM-DD~YYYY-MM-DD"},
            "effect_size": float,
            "p_value": float,
            "direction": "increase" | "decrease" | "none",
        }

    Note
    ----
    ``room`` 为额外可选参数（metric="room_distribution" 时必须提供），
    不传时不影响原有调用方式。
    """
    daily = _daily_values(events, person, metric, room)
    dates = sorted(daily)
    n = len(dates)
    if n < 2:
        return _empty_result(metric, person)

    si = max(1, min(n - 1, int(n * split_ratio)))
    before_dates, after_dates = dates[:si], dates[si:]
    bv = [daily[d] for d in before_dates]
    av = [daily[d] for d in after_dates]

    bs = _stats(bv, before_dates)
    as_ = _stats(av, after_dates)
    ed = _cohens_d(bv, av)
    pv = _mw_p(bv, av)

    changed = (
        abs(ed) > EFFECT_SIZE_THRESHOLD
        and (pv < SIGNIFICANCE_LEVEL or abs(ed) >= LARGE_EFFECT_THRESHOLD)
    )

    if not changed:
        direction = "none"
    elif as_["mean"] > bs["mean"]:
        direction = "increase"
    elif as_["mean"] < bs["mean"]:
        direction = "decrease"
    else:
        direction = "none"

    return {
        "changed": changed, "metric": metric, "person": person,
        "before": bs, "after": as_,
        "effect_size": ed, "p_value": pv, "direction": direction,
    }


def search_candidate_causes(
    events: list[dict],
    person: str,
    change_start_ts: str,
    lookback_days: int = 7,
) -> list[dict]:
    """在行为变化发生前后的时间窗口内，搜索可能导致变化的候选原因事件。

    窗口定义::

        基线窗口 = [change_start_ts − 2·lookback, change_start_ts − lookback)
        变化窗口 = [change_start_ts − lookback,    change_start_ts]

    候选原因类型:
        device_change     设备状态变化（light_on / tv_on / aircon_on / ...）
        person_change     人员变化（face_known / face_unknown / ...）
        environment_change 环境变化（door_open / window_open / ...）
        schedule_change   日程相关（weekday / weekend / holiday）

    返回按 confidence 降序的候选原因列表::

        [
            {
                "cause_type": str,
                "event_type": str,
                "count": int,
                "baseline_count": int,
                "correlation": float,
                "confidence": float,
                "description": str,
            },
            ...
        ]
    """
    cdt = _parse_ts(change_start_ts)
    if cdt is None:
        return []

    delta = timedelta(days=lookback_days)
    ws, we = cdt - delta, cdt                      # 变化窗口
    bs_lo, bs_hi = cdt - 2 * delta, cdt - delta    # 基线窗口

    # ── 事件频次统计 + 日期收集 ──
    action_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"c": 0, "b": 0})
    person_days_c: set[str] = set()
    person_days_b: set[str] = set()
    holiday_dates: set[str] = set()

    for ev in events:
        dt = _parse_ts(ev.get("server_ts", ""))
        if dt is None:
            continue
        day = dt.strftime("%Y-%m-%d")

        # 节假日检测：action 含 "holiday" 或 extra_json 标记
        act = (ev.get("action") or "").lower()
        is_hol = "holiday" in act
        if not is_hol:
            ex_raw = ev.get("extra_json") or ""
            try:
                ex = json.loads(ex_raw) if isinstance(ex_raw, str) else ex_raw
                if isinstance(ex, dict) and ex.get("holiday"):
                    is_hol = True
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if is_hol:
            holiday_dates.add(day)

        in_c = ws <= dt <= we
        in_b = bs_lo <= dt < bs_hi

        # 日程分析：记录 person 活跃日
        if in_c or in_b:
            if _has_person(ev, person):
                (person_days_c if in_c else person_days_b).add(day)

        # 事件频次分析：所有事件（含非 person 相关的环境事件）
        ct = _classify(act)
        if ct is not None:
            if in_c:
                action_counts[act]["c"] += 1
            elif in_b:
                action_counts[act]["b"] += 1

    # ── 设备 / 人员 / 环境候选 ──
    candidates: list[dict] = []
    for action, cnt in action_counts.items():
        c, b = cnt["c"], cnt["b"]
        if c == b:
            continue
        ctype = _classify(action)
        if ctype is None:
            continue
        corr = _correlation(c, b)
        conf = _confidence(c, b, corr)
        candidates.append({
            "cause_type": ctype,
            "event_type": action,
            "count": c,
            "baseline_count": b,
            "correlation": round(corr, 2),
            "confidence": round(conf, 2),
            "description": _description(action, c, b, lookback_days),
        })

    # ── 日程候选（基于 person 活跃日期的星期分布） ──
    for sched in ("weekday", "weekend", "holiday"):
        if sched == "weekday":
            c = sum(1 for d in person_days_c if _is_weekday(d) and d not in holiday_dates)
            b = sum(1 for d in person_days_b if _is_weekday(d) and d not in holiday_dates)
        elif sched == "weekend":
            c = sum(1 for d in person_days_c if not _is_weekday(d) and d not in holiday_dates)
            b = sum(1 for d in person_days_b if not _is_weekday(d) and d not in holiday_dates)
        else:
            c = sum(1 for d in person_days_c if d in holiday_dates)
            b = sum(1 for d in person_days_b if d in holiday_dates)
        if c == b:
            continue
        corr = _correlation(c, b)
        conf = _confidence(c, b, corr)
        candidates.append({
            "cause_type": "schedule_change",
            "event_type": sched,
            "count": c,
            "baseline_count": b,
            "correlation": round(corr, 2),
            "confidence": round(conf, 2),
            "description": _description(sched, c, b, lookback_days),
        })

    candidates.sort(key=lambda x: -x["confidence"])
    return candidates


def attribute(
    events: list[dict],
    person: str,
    metric: str,
    split_ratio: float = 0.5,
    lookback_days: int = 7,
    room: str | None = None,
) -> dict:
    """完整归因：先检测变化，再搜索候选原因。

    - changed=False → candidate_causes 为空列表
    - changed=True  → candidate_causes 至少含 1 个候选（找不到时插入降级说明）

    返回 detect_change() 全部字段 + candidate_causes 字段。
    """
    result = detect_change(events, person, metric, split_ratio, room)

    if not result["changed"]:
        result["candidate_causes"] = []
        return result

    # 变化起始时间 = after 期首日
    after_period = result["after"]["period"]
    if after_period and "~" in after_period:
        change_start = after_period.split("~")[0]
        causes = search_candidate_causes(
            events, person, f"{change_start}T00:00:00", lookback_days
        )
    else:
        causes = []

    # fail-safe：保证 changed=True 时至少 1 个候选
    if not causes:
        causes = [{
            "cause_type": "schedule_change",
            "event_type": "weekday",
            "count": result["after"]["count"],
            "baseline_count": result["before"]["count"],
            "correlation": 0.1,
            "confidence": 0.1,
            "description": "行为模式发生了变化，但未找到明确的外部原因",
        }]

    result["candidate_causes"] = causes
    return result