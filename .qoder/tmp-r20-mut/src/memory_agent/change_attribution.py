"""change_attribution.py - 行为变化归因（因果推理层）

P5a（阶段1）：检测行为模式变化并自动搜索候选原因。纯统计 + 规则。
P5b（阶段2）：条件概率建模——分组比较法，把归因从"相关性"升级到"因果性"。
P5c（阶段3）：反事实查询——回答"如果没有这个事件，行为会怎样？"
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from .day_bounds import clamp_days

EFFECT_SIZE_THRESHOLD: float = 0.5
SIGNIFICANCE_LEVEL: float = 0.05
LARGE_EFFECT_THRESHOLD: float = 1.5

CONDITIONAL_MIN_DAYS: int = 14
CONDITIONAL_MIN_EVENT_DAYS: int = 3
CONDITIONAL_LIFT_SATURATION: float = 3.0

_DEVICE_KW: frozenset[str] = frozenset({
    "light", "lamp", "tv", "television", "aircon", "air_conditioner",
    "ac", "heater", "humidifier", "fan", "speaker", "curtain", "lock",
})
_PERSON_KW: frozenset[str] = frozenset({"face", "person", "visitor", "intruder"})
_ENV_KW: frozenset[str] = frozenset({"door", "window", "motion", "smoke", "gas", "leak"})

_DISPLAY: dict[str, str] = {
    "light_on": "开灯", "light_off": "关灯",
    "tv_on": "电视打开", "tv_off": "电视关闭",
    "aircon_on": "空调打开", "aircon_off": "空调关闭",
    "face_known": "已知人脸识别", "face_unknown": "陌生人出现",
    "door_open": "开门", "door_close": "关门",
    "window_open": "开窗", "window_close": "关窗",
    "weekday": "工作日", "weekend": "周末", "holiday": "节假日",
}


def _parse_persons(raw: Any) -> list[dict]:
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
    if not isinstance(ts, str) or len(ts) < 16:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _has_person(ev: dict, person: str) -> bool:
    for p in _parse_persons(ev.get("persons_json")):
        if isinstance(p, dict):
            if p.get("name") == person:
                return True
        elif isinstance(p, str):
            if p == person:
                return True
    return False


def _classify(action: str) -> str | None:
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
    if a.endswith(("_on", "_off", "_open", "_close")):
        return "device_change"
    return None


def _daily_values(events, person, metric, room=None):
    out = {}
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
        cnt = defaultdict(int)
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
        rc = defaultdict(int)
        tc = defaultdict(int)
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
        span = {}
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


def _stats(vals, dates):
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


def _cohens_d(before, after):
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


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _mw_exact_p(n1, n2, u_obs):
    mx = n1 * n2
    dp = [[[0] * (mx + 1) for _ in range(n2 + 1)] for _ in range(n1 + 1)]
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


def _mw_p(before, after):
    n1, n2 = len(before), len(after)
    if n1 == 0 or n2 == 0 or (n1 == 1 and n2 == 1):
        return 1.0
    comb = sorted([(v, 0) for v in before] + [(v, 1) for v in after], key=lambda x: x[0])
    N = n1 + n2
    ranks = [0.0] * N
    ties = []
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
    if not has_ties and n1 * n2 <= 400 and N <= 30:
        return _mw_exact_p(n1, n2, int(round(u_min)))
    mu = n1 * n2 / 2.0
    tie_term = sum(t ** 3 - t for t in ties)
    var = (n1 * n2 / 12.0) * ((N + 1) - tie_term / (N * (N - 1)))
    sigma = math.sqrt(max(var, 1e-10))
    z = max(0.0, (abs(u_min - mu) - 0.5) / sigma)
    return round(min(1.0, max(0.0, 2.0 * (1.0 - _norm_cdf(z)))), 6)


def _empty_result(metric, person):
    z = {"mean": 0.0, "median": 0.0, "std": 0.0, "count": 0, "period": ""}
    return {"changed": False, "metric": metric, "person": person,
            "before": dict(z), "after": dict(z),
            "effect_size": 0.0, "p_value": 1.0, "direction": "none"}


def _correlation(change_ct, baseline_ct):
    total = change_ct + baseline_ct
    return abs(change_ct - baseline_ct) / total if total > 0 else 0.0


def _confidence(change_ct, baseline_ct, proximity=0.0):
    lift = (change_ct + 1.0) / (baseline_ct + 1.0)
    lift_score = max(0.0, min(1.0, (lift - 1.0) / 2.0))
    count_score = min(1.0, change_ct / 5.0)
    return round(0.4 * lift_score + 0.3 * count_score + 0.3 * proximity, 4)


def _temporal_proximity(dts, change_dt, half_life_days):
    if not dts or half_life_days <= 0:
        return 0.0
    decay = math.log(2) / half_life_days
    total = 0.0
    for dt in dts:
        delta_days = abs((dt - change_dt).total_seconds()) / 86400.0
        total += math.exp(-decay * delta_days)
    return round(total / len(dts), 4)


def _description(event_type, change_ct, baseline_ct, lookback):
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


def _is_weekday(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


# ─── P5a 公开 API ──────────────────────────────────────────────

def detect_change(events, person, metric, split_ratio=0.5, room=None):
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
    changed = abs(ed) > EFFECT_SIZE_THRESHOLD and (pv < SIGNIFICANCE_LEVEL or abs(ed) >= LARGE_EFFECT_THRESHOLD)
    if not changed:
        direction = "none"
    elif as_["mean"] > bs["mean"]:
        direction = "increase"
    elif as_["mean"] < bs["mean"]:
        direction = "decrease"
    else:
        direction = "none"
    return {"changed": changed, "metric": metric, "person": person,
            "before": bs, "after": as_, "effect_size": ed, "p_value": pv, "direction": direction}


def search_candidate_causes(events, person, change_start_ts, lookback_days=7):
    cdt = _parse_ts(change_start_ts)
    if cdt is None:
        return []
    window_days = clamp_days(lookback_days)
    # `lookback_days` 在这条函数里有三个出口：窗口跨度、衰减半衰期、给读者看的描述文案。
    # 改前只有第一个走 `clamp_days`，另两个用**原值**——传 10**9 时窗口被夹成 3650 天、
    # 半衰期却是 5e8 天 ⇒ `_temporal_proximity` 对所有事件年龄一律给 ≈1.0，
    # 末尾按 confidence 的排序静默失效（不崩，只是算错；第八轮 P3-2 只收了会崩的那一半），
    # 而 description 还写着"变化前 1000000000 天内"。三个出口同一个口径。
    delta = timedelta(days=window_days)
    ws, we = cdt - delta, cdt
    bs_lo, bs_hi = cdt - 2 * delta, cdt - delta
    action_counts = defaultdict(lambda: {"c": 0, "b": 0})
    action_dts = defaultdict(list)
    person_days_c = set()
    person_days_b = set()
    holiday_dates = set()
    half_life = window_days / 2.0
    for ev in events:
        dt = _parse_ts(ev.get("server_ts", ""))
        if dt is None:
            continue
        day = dt.strftime("%Y-%m-%d")
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
        if in_c or in_b:
            if _has_person(ev, person):
                (person_days_c if in_c else person_days_b).add(day)
        ct = _classify(act)
        if ct is not None:
            if in_c:
                action_counts[act]["c"] += 1
                action_dts[act].append(dt)
            elif in_b:
                action_counts[act]["b"] += 1
    candidates = []
    for action, cnt in action_counts.items():
        c, b = cnt["c"], cnt["b"]
        if c == b:
            continue
        ctype = _classify(action)
        if ctype is None:
            continue
        corr = _correlation(c, b)
        prox = _temporal_proximity(action_dts.get(action, []), cdt, half_life)
        conf = _confidence(c, b, prox)
        candidates.append({"cause_type": ctype, "event_type": action, "count": c,
                           "baseline_count": b, "correlation": round(corr, 2),
                           "temporal_proximity": round(prox, 2), "confidence": round(conf, 2),
                           "description": _description(action, c, b, window_days)})
    for sched in ("weekday", "weekend", "holiday"):
        if sched == "weekday":
            c_dates = [d for d in person_days_c if _is_weekday(d) and d not in holiday_dates]
            b = sum(1 for d in person_days_b if _is_weekday(d) and d not in holiday_dates)
        elif sched == "weekend":
            c_dates = [d for d in person_days_c if not _is_weekday(d) and d not in holiday_dates]
            b = sum(1 for d in person_days_b if not _is_weekday(d) and d not in holiday_dates)
        else:
            c_dates = [d for d in person_days_c if d in holiday_dates]
            b = sum(1 for d in person_days_b if d in holiday_dates)
        c = len(c_dates)
        if c == b:
            continue
        corr = _correlation(c, b)
        sched_dts = [datetime.strptime(d, "%Y-%m-%d") for d in c_dates]
        prox = _temporal_proximity(sched_dts, cdt, half_life)
        conf = _confidence(c, b, prox)
        candidates.append({"cause_type": "schedule_change", "event_type": sched, "count": c,
                           "baseline_count": b, "correlation": round(corr, 2),
                           "temporal_proximity": round(prox, 2), "confidence": round(conf, 2),
                           "description": _description(sched, c, b, window_days)})
    candidates.sort(key=lambda x: -x["confidence"])
    return candidates


def attribute(events, person, metric, split_ratio=0.5, lookback_days=7, room=None):
    result = detect_change(events, person, metric, split_ratio, room)
    if not result["changed"]:
        result["candidate_causes"] = []
        return result
    after_period = result["after"]["period"]
    if after_period and "~" in after_period:
        change_start = after_period.split("~")[0]
        causes = search_candidate_causes(events, person, f"{change_start}T00:00:00", lookback_days)
    else:
        causes = []
    if not causes:
        causes = [{"cause_type": "schedule_change", "event_type": "weekday",
                   "count": result["after"]["count"], "baseline_count": result["before"]["count"],
                   "correlation": 0.1, "confidence": 0.1,
                   "description": "行为模式发生了变化，但未找到明确的外部原因"}]
    result["candidate_causes"] = causes
    return result


# ═══════════════════════════════════════════════════════════════
# P5b 条件概率建模（分组比较法）
# ═══════════════════════════════════════════════════════════════


def _fisher_exact_2x2(a, b, c, d):
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d
    a_min = max(0, col1 - row2)
    a_max = min(row1, col1)
    if a_min >= a_max:
        return 1.0
    from math import lgamma
    def _hgp(x):
        if x < 0 or row1 - x < 0 or col1 - x < 0 or row2 - (col1 - x) < 0:
            return 0.0
        lp = (lgamma(row1 + 1) + lgamma(row2 + 1) + lgamma(col1 + 1) + lgamma(col2 + 1)
              - lgamma(n + 1) - lgamma(x + 1) - lgamma(row1 - x + 1)
              - lgamma(col1 - x + 1) - lgamma(row2 - (col1 - x) + 1))
        return math.exp(lp)
    p_obs = _hgp(a)
    p_tail = sum(_hgp(x) for x in range(a_min, a_max + 1) if _hgp(x) <= p_obs + 1e-15)
    return round(min(1.0, max(0.0, p_tail)), 6)


def _event_days(events, event_type):
    target = event_type.lower()
    days = set()
    for ev in events:
        if (ev.get("action") or "").lower() != target:
            continue
        dt = _parse_ts(ev.get("server_ts", ""))
        if dt is not None:
            days.add(dt.strftime("%Y-%m-%d"))
    return days


def analyze_conditional_causes(events, person, metric, change_start_ts,
                                lookback_days=30, room=None, candidate_event_types=None,
                                min_days=CONDITIONAL_MIN_DAYS, min_event_days=CONDITIONAL_MIN_EVENT_DAYS):
    """P5b 分组比较法：有事件天 vs 无事件天的行为指标差异。

    对每个候选事件类型，把变化点之前 lookback_days 内的天分成两组，
    直接比较两组的行为指标分布（Mann-Whitney U + Cohen's d），
    再计算 P(变化|事件) vs P(变化|无事件) 的条件概率比和 Fisher 精确检验。
    """
    cdt = _parse_ts(change_start_ts)
    if cdt is None:
        return {"enabled": False, "reason": "change_start_ts 解析失败", "causes": []}

    daily = _daily_values(events, person, metric, room)
    start_date = (cdt - timedelta(days=clamp_days(lookback_days))).strftime("%Y-%m-%d")
    end_date = cdt.strftime("%Y-%m-%d")
    relevant = {d: v for d, v in daily.items() if start_date <= d < end_date}

    if len(relevant) < min_days:
        return {"enabled": False, "reason": f"有数据天数不足（{len(relevant)} < {min_days}）",
                "total_data_days": len(relevant), "causes": []}

    all_vals = list(relevant.values())
    global_mean = statistics.mean(all_vals)
    global_std = statistics.stdev(all_vals) if len(all_vals) >= 2 else 0.0

    if candidate_event_types is None:
        p5a = search_candidate_causes(events, person, change_start_ts, min(7, lookback_days))
        candidate_event_types = [c["event_type"] for c in p5a]
        for common in ("tv_on", "light_on", "aircon_on", "door_open", "face_known", "face_unknown"):
            if common not in candidate_event_types:
                candidate_event_types.append(common)

    if not candidate_event_types:
        return {"enabled": True, "method": "group_comparison", "lookback_days": lookback_days,
                "total_data_days": len(relevant), "causes": []}

    causes = []
    for et in candidate_event_types:
        ev_d = _event_days(events, et)
        ev_in = {d for d in ev_d if d in relevant}
        no_ev_in = {d for d in relevant if d not in ev_d}
        if len(ev_in) < min_event_days or len(no_ev_in) < min_event_days:
            continue

        ev_vals = [relevant[d] for d in ev_in]
        no_ev_vals = [relevant[d] for d in no_ev_in]
        m_ev = statistics.mean(ev_vals)
        m_no = statistics.mean(no_ev_vals)
        es = _cohens_d(ev_vals, no_ev_vals)
        mwp = _mw_p(ev_vals, no_ev_vals)

        if global_std > 1e-10:
            ch_ev = sum(1 for v in ev_vals if abs(v - global_mean) > global_std)
            ch_no = sum(1 for v in no_ev_vals if abs(v - global_mean) > global_std)
        else:
            ch_ev, ch_no = 0, 0

        n_ev, n_no = len(ev_vals), len(no_ev_vals)
        p_ch_ev = (ch_ev + 1.0) / (n_ev + 2.0)
        p_ch_no = (ch_no + 1.0) / (n_no + 2.0)
        cl = p_ch_ev / p_ch_no if p_ch_no > 0 else (float("inf") if p_ch_ev > 0 else 1.0)
        cs = 1.0 if cl == float("inf") else max(0.0, min(1.0, (cl - 1.0) / (CONDITIONAL_LIFT_SATURATION - 1.0)))

        a, b = ch_ev, n_ev - ch_ev
        c, d = ch_no, n_no - ch_no
        fp = _fisher_exact_2x2(a, b, c, d)
        sig = fp < 0.05 or mwp < 0.05

        ctype = _classify(et) or "schedule_change"
        name = _DISPLAY.get(et, et)
        direction = "升高" if m_ev > m_no else "降低"
        if sig and abs(es) >= 0.5:
            desc = (f"{name}发生的{n_ev}天均值{m_ev:.2f}，无事件的{n_no}天均值{m_no:.2f}，"
                    f"{direction}{abs(m_ev - m_no):.2f}（d={es:.2f}，MW p={mwp:.3f}，lift={cl:.2f}x）")
        else:
            desc = f"{name}与行为指标无显著关联（MW p={mwp:.3f}，d={es:.2f}）"

        causes.append({
            "event_type": et, "cause_type": ctype,
            "event_days": n_ev, "no_event_days": n_no,
            "mean_event": round(m_ev, 4), "mean_no_event": round(m_no, 4),
            "effect_size": es, "mann_whitney_p": mwp,
            "p_change_given_event": round(p_ch_ev, 4), "p_change_given_no_event": round(p_ch_no, 4),
            "conditional_lift": round(cl, 4) if cl != float("inf") else 999.0,
            "causal_strength": round(cs, 4), "fisher_p": fp, "significant": sig,
            "description": desc,
        })

    causes.sort(key=lambda x: (-int(x["significant"]), -x["causal_strength"]))
    return {"enabled": True, "method": "group_comparison", "lookback_days": lookback_days,
            "total_data_days": len(relevant), "causes": causes}


def attribute_with_conditional(events, person, metric, split_ratio=0.5, lookback_days=7,
                                conditional_lookback_days=30, room=None):
    """P5a + P5b 完整归因。"""
    result = attribute(events, person, metric, split_ratio, lookback_days, room)
    if not result["changed"]:
        result["conditional_analysis"] = {"enabled": False, "reason": "未检测到行为变化", "causes": []}
        return result
    ap = result["after"]["period"]
    if not ap or "~" not in ap:
        result["conditional_analysis"] = {"enabled": False, "reason": "无法确定变化起始时间", "causes": []}
        return result
    cs_ts = f"{ap.split('~')[0]}T00:00:00"
    ctypes = [c["event_type"] for c in result.get("candidate_causes", [])]
    cond = analyze_conditional_causes(events, person, metric, cs_ts,
                                       lookback_days=conditional_lookback_days, room=room,
                                       candidate_event_types=ctypes or None)
    if cond["enabled"]:
        cmap = {c["event_type"]: c for c in cond["causes"]}
        for cause in result.get("candidate_causes", []):
            et = cause.get("event_type")
            if et in cmap:
                cause["conditional"] = {
                    "event_days": cmap[et]["event_days"], "no_event_days": cmap[et]["no_event_days"],
                    "mean_event": cmap[et]["mean_event"], "mean_no_event": cmap[et]["mean_no_event"],
                    "effect_size": cmap[et]["effect_size"], "mann_whitney_p": cmap[et]["mann_whitney_p"],
                    "conditional_lift": cmap[et]["conditional_lift"], "causal_strength": cmap[et]["causal_strength"],
                    "fisher_p": cmap[et]["fisher_p"], "significant": cmap[et]["significant"],
                }
                if cmap[et]["significant"]:
                    cause["confidence_with_causal"] = round(
                        0.4 * cause.get("confidence", 0.0) + 0.6 * cmap[et]["causal_strength"], 4)
    result["conditional_analysis"] = cond
    return result


# ═══════════════════════════════════════════════════════════════
# P5c 反事实查询
# ═══════════════════════════════════════════════════════════════


def _ci_95(vals):
    """均值的 95% 置信区间（t 分布近似，n<30 用 t，否则用 z）。"""
    n = len(vals)
    if n == 0:
        return (0.0, 0.0)
    m = statistics.mean(vals)
    if n < 2:
        return (round(m, 4), round(m, 4))
    se = statistics.stdev(vals) / math.sqrt(n)
    # 常用 t 临界值近似（自由度 n-1）
    t_table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
               6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
               11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
               16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
               21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
               26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045}
    tcrit = t_table.get(min(n - 1, 29), 1.96) if n < 30 else 1.96
    margin = tcrit * se
    return (round(m - margin, 4), round(m + margin, 4))


def counterfactual_query(events, person, metric, event_type, change_start_ts,
                          lookback_days=30, room=None):
    """P5c 反事实查询：如果没有这个事件，行为指标会怎样？

    基于 P5b 分组比较法，用无事件天的分布作为反事实估计。
    返回：实际值（有事件天均值）、反事实预测值（无事件天均值）、
    差异、95% 置信区间、因果效应量、显著性。
    """
    cdt = _parse_ts(change_start_ts)
    if cdt is None:
        return {"enabled": False, "reason": "change_start_ts 解析失败"}

    daily = _daily_values(events, person, metric, room)
    start_date = (cdt - timedelta(days=clamp_days(lookback_days))).strftime("%Y-%m-%d")
    end_date = cdt.strftime("%Y-%m-%d")
    relevant = {d: v for d, v in daily.items() if start_date <= d < end_date}

    if len(relevant) < CONDITIONAL_MIN_DAYS:
        return {"enabled": False, "reason": f"有数据天数不足（{len(relevant)} < {CONDITIONAL_MIN_DAYS}）",
                "total_data_days": len(relevant)}

    ev_d = _event_days(events, event_type)
    ev_in = {d for d in ev_d if d in relevant}
    no_ev_in = {d for d in relevant if d not in ev_d}

    if len(ev_in) < CONDITIONAL_MIN_EVENT_DAYS or len(no_ev_in) < CONDITIONAL_MIN_EVENT_DAYS:
        return {"enabled": False, "reason": f"事件天数不足（事件天={len(ev_in)}, 无事件天={len(no_ev_in)}）",
                "event_days": len(ev_in), "no_event_days": len(no_ev_in)}

    ev_vals = [relevant[d] for d in ev_in]
    no_ev_vals = [relevant[d] for d in no_ev_in]

    actual = statistics.mean(ev_vals)
    counterfactual = statistics.mean(no_ev_vals)
    diff = actual - counterfactual

    es = _cohens_d(ev_vals, no_ev_vals)
    mwp = _mw_p(ev_vals, no_ev_vals)
    ci_actual = _ci_95(ev_vals)
    ci_counterfactual = _ci_95(no_ev_vals)

    # 反事实预测的置信区间：用无事件天的 CI
    # 差异的标准误（独立样本）
    n1, n2 = len(ev_vals), len(no_ev_vals)
    s1 = statistics.stdev(ev_vals) if n1 >= 2 else 0.0
    s2 = statistics.stdev(no_ev_vals) if n2 >= 2 else 0.0
    se_diff = math.sqrt(s1 ** 2 / n1 + s2 ** 2 / n2) if (n1 >= 2 and n2 >= 2) else 0.0
    tcrit = 1.96 if (n1 + n2) >= 30 else 2.0
    ci_diff = (round(diff - tcrit * se_diff, 4), round(diff + tcrit * se_diff, 4))

    name = _DISPLAY.get(event_type, event_type)
    direction = "升高" if diff > 0 else "降低"
    significant = mwp < 0.05 and abs(es) >= 0.5

    if significant:
        desc = (f"如果没有{name}，{metric}预计为{counterfactual:.2f}（实际{actual:.2f}），"
                f"差异{direction}{abs(diff):.2f}（95% CI [{ci_diff[0]:.2f}, {ci_diff[1]:.2f}]，"
                f"d={es:.2f}，MW p={mwp:.3f}）")
    else:
        desc = f"{name}对{metric}的因果效应不显著（MW p={mwp:.3f}，d={es:.2f}），无法给出可靠反事实预测"

    return {
        "enabled": True,
        "event_type": event_type,
        "person": person,
        "metric": metric,
        "lookback_days": lookback_days,
        "event_days": n1,
        "no_event_days": n2,
        "actual_value": round(actual, 4),
        "counterfactual_value": round(counterfactual, 4),
        "difference": round(diff, 4),
        "ci_actual": ci_actual,
        "ci_counterfactual": ci_counterfactual,
        "ci_difference": ci_diff,
        "effect_size": es,
        "mann_whitney_p": mwp,
        "significant": significant,
        "description": desc,
    }
