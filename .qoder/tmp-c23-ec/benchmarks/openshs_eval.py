"""OpenSHS 评估协议（Plan B：让分数可解释）。

背景
----
原先只报「天 × 活动」的裸 macro-F1。但真实家庭里 sleep/watchTV 几乎天天发生
（基础率 93%），「无脑每天预测全部活动」这种退化基线就能拿到 0.649，
比 MA 的 0.638 还高 —— 裸 F1 在这个口径下**没有鉴别力**。

因此本模块提供三层评估：

Level 1 · 天级（保留原口径，但补对照）
    - 退化基线：always_positive / majority / random(按基础率)
    - 主指标改为 **lift**（MA_F1 − 最强基线_F1）与 **MCC**（对基础率不敏感）
    - 目的：如实回答「比傻瓜强吗」

Level 2 · 时段级 + 段级（主判据，衡量时间定位）
    - 时段级：把时间轴切成固定槽（默认 5 min），逐槽判二分类。
      基础率骤降到「活动时长占比」量级，退化基线直接崩 ≈0，才有真实鉴别力。
    - 段级  ：把 GT 的每个连续片段当成「待检事件」，MA 只要有同名活动区间
      与之时间重叠即算检出。这是标准 HAR 的事件检测口径，对 MA「定位粗」更公平。

纯度说明：仅依赖标准库，无需 sklearn / numpy。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

__all__ = [
    "day_level",
    "slot_level",
    "segment_level",
]

BASELINE_KEYS = ("always_positive", "majority", "random")


def _scores(tp: float, fp: float, fn: float, tn: float) -> dict:
    """由混淆矩阵算出一套指标（TP/FP 可能为浮点，用于 random 基线的期望值）。"""
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    den = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = (tp * tn - fp * fn) / den if den else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    return {
        "tp": int(round(tp)), "fp": int(round(fp)),
        "fn": int(round(fn)), "tn": int(round(tn)),
        "precision": round(prec, 3), "recall": round(rec, 3),
        "f1": round(f1, 3), "mcc": round(mcc, 3),
        "balanced_acc": round((rec + spec) / 2, 3),
    }


def _baselines(pos: int, total: int) -> dict:
    """给定正例数与总样本数，算三个退化基线。

    always_positive: 永远预测正例
    majority       : 基础率 >=0.5 则永远预测正例，否则永远预测负例
    random         : 以基础率为概率随机预测（用期望计数，消除随机波动）
    """
    neg = total - pos
    b = pos / total if total else 0.0
    ap = _scores(pos, neg, 0, 0)
    if b >= 0.5:
        mj = _scores(pos, neg, 0, 0)
    else:
        mj = _scores(0, 0, pos, neg)
    rd = _scores(total * b * b, total * (1 - b) * b,
                 total * b * (1 - b), total * (1 - b) * (1 - b))
    return {"always_positive": ap, "majority": mj, "random": rd}


def _macro(per_act: dict) -> float:
    return round(sum(v["f1"] for v in per_act.values()) / max(1, len(per_act)), 3)


def _lift_block(ma_per: dict, base_per: dict) -> dict:
    ma_macro = _macro(ma_per)
    bl_macro = {k: _macro({a: base_per[a][k] for a in base_per}) for k in BASELINE_KEYS}
    best = max(bl_macro.values()) if bl_macro else 0.0
    return {
        "ma": ma_macro,
        "baselines": bl_macro,
        "best_baseline": round(best, 3),
        "lift": round(ma_macro - best, 3),
    }


# ---------------------------------------------------------------- Level 1 ----

def day_level(gt_days: dict, pred_days: dict, days: list, evaluated: list) -> dict:
    """天级口径（原口径）+ 退化基线对照 + lift/MCC。"""
    total = len(days)
    ma_per: dict = {}
    for act in evaluated:
        tp = fp = fn = tn = 0
        for d in days:
            p = act in pred_days.get(d, set())
            g = act in gt_days.get(d, set())
            if p and g:
                tp += 1
            elif p and not g:
                fp += 1
            elif g and not p:
                fn += 1
            else:
                tn += 1
        ma_per[act] = _scores(tp, fp, fn, tn)

    base_per = {}
    for act in evaluated:
        pos = sum(1 for d in days if act in gt_days.get(d, set()))
        base_per[act] = {"gt_days": pos, "base_rate": round(pos / total, 3),
                         **_baselines(pos, total)}
    return {"level": "day", "n_samples_per_activity": total,
            "ma": ma_per, "baselines": base_per, "macro": _lift_block(ma_per, base_per)}


# ---------------------------------------------------------------- Level 2 ----

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _collect_intervals(gt_segments: list, ma_activities: list, evaluated: list):
    """把 GT 片段与 MA 活动分别收敛成 {activity: [(start, end), ...]}。"""
    gt_iv: dict = {}
    for s in gt_segments:
        act = s.get("ma")
        if not act or act not in evaluated:
            continue
        try:
            gt_iv.setdefault(act, []).append((_parse(s["start"]), _parse(s["end"])))
        except (KeyError, TypeError, ValueError):
            continue

    ma_iv: dict = {}
    missing = 0
    for a in ma_activities:
        act = a.get("activity", "")
        if act not in evaluated:
            continue
        s, e = a.get("start_ts"), a.get("end_ts")
        if not (s and e):
            # 无时间区间的活动（如 bathing 的占位推断 / 遥测兜底）无法用于时间定位评估。
            missing += 1
            continue
        try:
            ma_iv.setdefault(act, []).append((_parse(s), _parse(e)))
        except (TypeError, ValueError):
            missing += 1
    return gt_iv, ma_iv, missing


def slot_level(gt_segments: list, ma_activities: list, evaluated: list,
               start_iso: str, end_iso: str, slot_seconds: int = 300) -> dict:
    """时段级：把时间轴切成固定槽（默认 5 min），逐槽做二分类。

    预测/真值均以「槽中点是否落在区间内」判定。这是衡量**时间定位精度**的严格口径。
    """
    gt_iv, ma_iv, missing = _collect_intervals(gt_segments, ma_activities, evaluated)
    try:
        cur = _parse(start_iso)
        end = _parse(end_iso)
    except (TypeError, ValueError):
        return {"level": "slot", "error": "无法解析时间窗口"}

    half = timedelta(seconds=slot_seconds / 2)
    slots = []
    while cur < end:
        slots.append(cur)
        cur += timedelta(seconds=slot_seconds)
    total = len(slots)

    ma_per: dict = {}
    base_per: dict = {}
    for act in evaluated:
        g_ints = gt_iv.get(act, [])
        m_ints = ma_iv.get(act, [])
        tp = fp = fn = tn = 0
        for t0 in slots:
            mid = t0 + half
            g = any(s <= mid <= e for s, e in g_ints)
            p = any(s <= mid <= e for s, e in m_ints)
            if p and g:
                tp += 1
            elif p:
                fp += 1
            elif g:
                fn += 1
            else:
                tn += 1
        ma_per[act] = _scores(tp, fp, fn, tn)
        pos = tp + fn  # GT 正例
        base_per[act] = {"gt_slots": pos,
                         "base_rate": round(pos / total, 4) if total else 0.0,
                         **_baselines(pos, total)}

    return {"level": "slot", "slot_seconds": slot_seconds,
            "n_samples_per_activity": total,
            "n_MA_records_without_interval": missing,
            "ma": ma_per, "baselines": base_per,
            "macro": _lift_block(ma_per, base_per)}


def segment_level(gt_segments: list, ma_activities: list, evaluated: list) -> dict:
    """段级事件检测：GT 连续片段 = 待检事件，MA 同名活动区间与之重叠即算检出。

    这是标准 HAR 的事件口径，对 MA「定位粗（首末证据包络）」更公平：
    只要 MA 的活动窗口盖到了真实片段，就算命中，不惩罚它窗口过宽。
    """
    gt_iv, ma_iv, missing = _collect_intervals(gt_segments, ma_activities, evaluated)

    ma_per: dict = {}
    for act in evaluated:
        g = gt_iv.get(act, [])
        m = ma_iv.get(act, [])
        hit_g = sum(1 for gs, ge in g
                    if any(not (me < gs or ms > ge) for ms, me in m))
        hit_m = sum(1 for ms, me in m
                    if any(not (ge < ms or gs > me) for gs, ge in g))
        tp, fn, fp = hit_g, len(g) - hit_g, len(m) - hit_m
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        ma_per[act] = {"tp": tp, "fp": fp, "fn": fn, "gt_segments": len(g),
                       "ma_records": len(m),
                       "precision": round(prec, 3), "recall": round(rec, 3),
                       "f1": round(f1, 3)}

    return {"level": "segment", "n_MA_records_without_interval": missing,
            "ma": ma_per, "macro_f1": _macro(ma_per)}
