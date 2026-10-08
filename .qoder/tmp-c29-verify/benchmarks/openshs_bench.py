"""OpenSHS 活动推断基准评估（baseline）。

把 OpenSHS 公开标注数据集（宽表 CSV）转换为 MA 事件流，跑 ``infer_activities``，
再与标注真值对比，按「天 × 活动」做二分类，输出 precision / recall / F1。

口径说明（方案A 适配）
---------------------
``infer_activities`` 已重构为「时段启发式」：不再输出 sleep/cook 等具体活动名，
而是 夜间活动/晨间活动/日间活动/晚间*/可能离家 等时段标签。本基准在
**benchmark 侧**经 ``openshs_schema.LEGACY_TO_PERIOD`` 把旧活动名映射到时段标签
集合后统一判定（预测标签 ∩ 活动映射集合非空 → 视为命中），不改生产代码。
新口径含义是「活动发生时段与预测时段兼容」的覆盖度，时段粒度较粗、标签间存在
多对一坍缩（如日间活动同时兼容 cooking/working），F1 数值不可与旧口径直接比较。

用法
----
    # 默认用自带样本（benchmarks/data/openshs_sample.csv）
    PYTHONPATH=src python -m benchmarks.openshs_bench

    # 换成完整 OpenSHS 数据集
    PYTHONPATH=src python -m benchmarks.openshs_bench --csv /path/to/dataset.csv

    # 真实粗粒度 7 分类数据集（sleep/eat/work/leisure/personal/other/anomaly）
    PYTHONPATH=src python -m benchmarks.openshs_bench --csv /path/to/dataset.csv --map coarse

    # 重新生成样本
    PYTHONPATH=src python -m benchmarks.gen_sample benchmarks/data/openshs_sample.csv

注：``infer_activities`` 纯事件逻辑、不依赖 chroma/LLM，可在本机直接跑；
若本机 sqlite 缺 FTS5 模块（init_schema 建虚拟表失败），请在 NAS 容器内运行。
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from collections import defaultdict

from memory_agent.insights import InsightService
from memory_agent.store import Store

from .openshs_convert import ground_truth, start_end, wide_to_events
from .openshs_eval import BASELINE_KEYS, day_level, segment_level, slot_level
from .openshs_schema import (
    LEGACY_TO_PERIOD,
    get_activity_map,
    legacy_activity_hit,
    period_to_legacy,
)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "data", "openshs_sample.csv")


def _ma_records_with_intervals(records: list[dict]) -> list[dict]:
    """把时段启发式记录（day + start_hour/end_hour）还原为带绝对时间区间的
    legacy 口径记录，供 slot/segment 级评估使用。

    一条时段记录按 ``period_to_legacy`` 展开为多个可能的旧活动（多对一坍缩的
    逆向代价，见 openshs_schema 注释）；无法展开的记录保留原标签名。
    """
    out: list[dict] = []
    for a in records:
        day = a.get("day") or ""
        sh, eh = a.get("start_hour"), a.get("end_hour")
        acts = period_to_legacy([a.get("name", "")])
        if not day or sh is None or eh is None:
            continue
        for act in (acts or {a.get("name", "")}):
            out.append({**a, "activity": act,
                        "start_ts": f"{day}T{int(sh):02d}:00:00",
                        "end_ts": f"{day}T{int(eh):02d}:59:59"})
    return out


def run_benchmark(csv_path: str, map_name: str = "fine", slot_seconds: int = 300) -> dict:
    events = wide_to_events(csv_path)
    mn, mx = start_end(csv_path)

    activity_map = get_activity_map(map_name)
    evaluated = sorted(set(activity_map.values()))

    # 内存库 + 轻量 config（空 name_map 也能打标，因为打标只看 entity_id 子串）。
    store = Store(db_path=":memory:", tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(events)

    cfg = types.SimpleNamespace(tz_offset_hours=0.0, insight_cache_ttl=0, rooms={})
    svc = InsightService(store, cfg)
    res = svc.infer_activities(start=mn, end=mx)

    # 预测侧：新 infer_activities 输出「时段标签」，按天聚合标签集合。
    pred_days: dict[str, set] = defaultdict(set)
    for a in res.get("activities", []):
        pred_days[a.get("day", "")].add(a.get("name", ""))

    gt = ground_truth(csv_path, activity_map=activity_map)
    gt_days = gt["days"]

    # 评估：每个 (activity, day) 视为一个二分类样本。
    # 判定经 LEGACY_TO_PERIOD 映射层统一两侧口径（见模块 docstring 方案A 说明）。
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    days = set(pred_days) | set(gt_days)
    for act in evaluated:
        for day in days:
            p = legacy_activity_hit(act, pred_days.get(day, set()))
            g = act in gt_days.get(day, set())
            if p and g:
                tp[act] += 1
            elif p and not g:
                fp[act] += 1
            elif g and not p:
                fn[act] += 1

    per_act = {}
    for act in evaluated:
        p = tp[act]
        f = fp[act]
        n = fn[act]
        prec = p / (p + f) if (p + f) else 0.0
        rec = p / (p + n) if (p + n) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_act[act] = {"tp": p, "fp": f, "fn": n,
                        "precision": round(prec, 3),
                        "recall": round(rec, 3),
                        "f1": round(f1, 3)}

    micro_p = sum(tp.values())
    micro_f = sum(fp.values())
    micro_n = sum(fn.values())
    micro_prec = micro_p / (micro_p + micro_f) if (micro_p + micro_f) else 0.0
    micro_rec = micro_p / (micro_p + micro_n) if (micro_p + micro_n) else 0.0
    micro_f1 = (2 * micro_prec * micro_rec / (micro_prec + micro_rec)
                if (micro_prec + micro_rec) else 0.0)
    macro_f1 = sum(v["f1"] for v in per_act.values()) / max(1, len(per_act))

    ma_records = res.get("activities", [])
    days_sorted = sorted(days)
    # Level 1（day）：预测标签集合先经 period_to_legacy 逆展开回旧活动名口径，
    # 与 gt_days（旧活动名）保持同口径；Level 2（slot/segment）：用 day+小时
    # 还原绝对时间区间后参与时间定位评估。
    pred_days_legacy = {d: period_to_legacy(labels)
                        for d, labels in pred_days.items()}
    ma_records_typed = _ma_records_with_intervals(ma_records)
    levels = {
        "day": day_level(gt_days, pred_days_legacy, days_sorted, evaluated),
        "slot": slot_level(gt["segments"], ma_records_typed, evaluated, mn, mx,
                           slot_seconds=slot_seconds),
        "segment": segment_level(gt["segments"], ma_records_typed, evaluated),
    }

    return {
        "csv": csv_path,
        "n_events": len(events),
        "window": (mn, mx),
        "pred_days": {k: sorted(v) for k, v in pred_days.items()},
        "gt_days": gt_days,
        "per_activity": per_act,
        "micro": {"precision": round(micro_prec, 3),
                  "recall": round(micro_rec, 3),
                  "f1": round(micro_f1, 3)},
        "macro_f1": round(macro_f1, 3),
        "segments": gt["segments"],
        "levels": levels,
    }


def _print_macro(m: dict, tag: str) -> None:
    print("-" * 64)
    print(f"{tag} macro-F1: MA={m['ma']}  |  基线: "
          + "  ".join(f"{k}={v}" for k, v in m["baselines"].items()))
    verdict = "有增益" if m["lift"] > 0 else "无增益 / 不如傻瓜基线"
    print(f"{tag} 最强基线={m['best_baseline']}  =>  LIFT={m['lift']:+.3f}   [{verdict}]")
    print("=" * 64)


def _print_levels(levels: dict) -> None:
    """打印 Level 1（天级 + 退化基线）与 Level 2（时段级 / 段级）的对照结果。"""
    d = levels.get("day")
    if d:
        print("=" * 64)
        print("【Level 1】天级 · 对照退化基线   样本数/活动 =", d["n_samples_per_activity"])
        print("-" * 64)
        print(f"{'活动':<14}{'基础率':>7}{'MA_F1':>8}{'最强基线':>10}{'MA_MCC':>9}")
        for act, v in d["ma"].items():
            b = d["baselines"][act]
            best = max(b[k]["f1"] for k in BASELINE_KEYS)
            print(f"{act:<14}{b['base_rate']:>7.2f}{v['f1']:>8}{best:>10}{v['mcc']:>9.3f}")
        _print_macro(d["macro"], "Level 1")

    s = levels.get("slot")
    if s and "error" not in s:
        print("【Level 2a】时段级 · 严格时间定位   槽=%ss  "
              "样本数/活动=%s  无区间MA记录=%s"
              % (s["slot_seconds"], s["n_samples_per_activity"],
                 s["n_MA_records_without_interval"]))
        print("-" * 64)
        print(f"{'活动':<14}{'基础率':>8}{'MA_F1':>8}{'最强基线':>10}{'MA_MCC':>9}")
        for act, v in s["ma"].items():
            b = s["baselines"][act]
            best = max(b[k]["f1"] for k in BASELINE_KEYS)
            print(f"{act:<14}{b['base_rate']:>8.4f}{v['f1']:>8}{best:>10}{v['mcc']:>9.3f}")
        _print_macro(s["macro"], "Level 2a")

    g = levels.get("segment")
    if g:
        print("【Level 2b】段级事件检测 · 对 MA 粗定位更公平   "
              "无区间MA记录=%s" % g["n_MA_records_without_interval"])
        print("-" * 64)
        print(f"{'活动':<14}{'GT段':>6}{'MA条':>6}{'TP':>5}{'FP':>5}{'FN':>5}"
              f"{'P':>8}{'R':>8}{'F1':>8}")
        for act, v in g["ma"].items():
            print(f"{act:<14}{v['gt_segments']:>6}{v['ma_records']:>6}{v['tp']:>5}"
                  f"{v['fp']:>5}{v['fn']:>5}{v['precision']:>8}{v['recall']:>8}{v['f1']:>8}")
        print("-" * 64)
        print(f"MACRO-F1 (segment-level): {g['macro_f1']}")
        print("=" * 64)


def print_report(r: dict, map_name: str = "fine") -> None:
    print("=" * 64)
    print("OpenSHS 活动推断基准 · 基线报告")
    print("=" * 64)
    print(f"数据集      : {r['csv']}")
    print(f"标签映射    : {map_name}（"
          f"{'细粒度/样本' if map_name == 'fine' else '真实粗粒度 7 分类'}）")
    print(f"时间窗口    : {r['window'][0]}  →  {r['window'][1]}")
    print(f"转换事件数  : {r['n_events']}")
    print("-" * 64)
    print(f"{'活动':<14}{'TP':>4}{'FP':>4}{'FN':>4}{'P':>8}{'R':>8}{'F1':>8}")
    for act, v in r["per_activity"].items():
        print(f"{act:<14}{v['tp']:>4}{v['fp']:>4}{v['fn']:>4}"
              f"{v['precision']:>8}{v['recall']:>8}{v['f1']:>8}")
    print("-" * 64)
    print(f"{'MICRO':<14}            "
          f"{r['micro']['precision']:>8}{r['micro']['recall']:>8}{r['micro']['f1']:>8}")
    print(f"MACRO-F1 (avg over activities): {r['macro_f1']}")
    print("-" * 64)
    print("预测（按天·时段标签）:", r["pred_days"])
    print("真值（按天·旧活动名）:", r["gt_days"])
    print("=" * 64)
    print("口径说明（方案A）：infer_activities 现为时段启发式（夜间/晨间/日间/晚间*/可能离家），")
    print("真值旧活动名经 LEGACY_TO_PERIOD 时段集合映射后与预测统一判定；评分含义为")
    print("「活动与预测时段的兼容性/覆盖度」，时段多对一坍缩会使 F1 相对旧口径偏乐观，不可直接对比。")
    if map_name == "coarse":
        print("标签映射说明（coarse）：真实 OpenSHS 公开数据集为粗粒度 7 分类"
              "(sleep/eat/work/leisure/personal/other/anomaly)，")
        print("本基线按 sleep→sleeping、eat→cooking、work→working、"
              "leisure→watching_tv、personal→bathing 映射；")
        print("other/anomaly 无 MA 对应物，未计入评分。leisure/personal 为近似代理"
              "（居家休闲/个人护理），故 watching_tv/bathing 的")
        print("精度/召回应理解为『对休闲/护理时段的覆盖度』，而非严格子活动判别。")
    print("说明（方案A 时段启发式口径）：现 infer_activities 输出的是「时段标签」")
    print("（夜间/晨间/日间/晚间*/可能离家，见 openshs_schema.PERIOD_LABELS），")
    print("真值经 LEGACY_TO_PERIOD 映射后判定，含义是『活动可能发生的时段是否被")
    print("预测覆盖』，粒度粗于旧活动名判别，时段标签间存在多对一坍缩（如日间活动")
    print("同时兼容 cooking/working），F1 不可与旧『规则匹配』口径直接比较；")
    print("跨午夜的睡眠段真值记在起始日、起床锚点预测记在次日，天级口径下会产生")
    print("结构性 FP/FN（如样本末 04-02 的『可能离家』实为窗口收尾后的静默）。")

    levels = r.get("levels") or {}
    if levels:
        _print_levels(levels)


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenSHS activity benchmark")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="OpenSHS 宽表 CSV 路径")
    ap.add_argument("--map", default="fine", choices=["fine", "coarse"],
                   help="标签映射：fine=样本/细粒度数据集，coarse=真实粗粒度 7 分类")
    ap.add_argument("--slot-seconds", type=int, default=300,
                   help="时段级评估的槽长（秒，默认 300 = 5 分钟）")
    args = ap.parse_args()
    if not os.path.exists(args.csv):
        print(f"找不到 {args.csv}；先运行: "
              f"PYTHONPATH=src python -m benchmarks.gen_sample {args.csv}", file=sys.stderr)
        return 2
    report = run_benchmark(args.csv, map_name=args.map,
                           slot_seconds=args.slot_seconds)
    print_report(report, map_name=args.map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
