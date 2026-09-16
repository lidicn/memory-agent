"""OpenSHS 活动推断基准评估（baseline）。

把 OpenSHS 公开标注数据集（宽表 CSV）转换为 MA 事件流，跑 ``infer_activities``，
再与标注真值对比，按「天 × 活动」做二分类，输出 precision / recall / F1。

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
from .openshs_schema import get_activity_map

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "data", "openshs_sample.csv")


def run_benchmark(csv_path: str, map_name: str = "fine") -> dict:
    events = wide_to_events(csv_path)
    mn, mx = start_end(csv_path)

    activity_map = get_activity_map(map_name)
    evaluated = sorted(set(activity_map.values()))

    # 内存库 + 轻量 config（空 name_map 也能打标，因为打标只看 entity_id 子串）。
    store = Store(db_path=":memory:", tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(events)

    cfg = types.SimpleNamespace(tz_offset_hours=0.0, insight_cache_ttl=0, rooms={})
    svc = InsightService(cfg, store)
    res = svc.infer_activities(start=mn, end=mx)

    pred_days: dict[str, set] = defaultdict(set)
    for a in res.get("activities", []):
        pred_days[a.get("day", "")].add(a["activity"])

    gt = ground_truth(csv_path, activity_map=activity_map)
    gt_days = gt["days"]

    # 评估：每个 (activity, day) 视为一个二分类样本。
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    days = set(pred_days) | set(gt_days)
    for act in evaluated:
        for day in days:
            p = act in pred_days.get(day, set())
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
    }


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
    print("预测（按天）:", r["pred_days"])
    print("真值（按天）:", r["gt_days"])
    print("=" * 64)
    if map_name == "coarse":
        print("标签映射说明（coarse）：真实 OpenSHS 公开数据集为粗粒度 7 分类"
              "(sleep/eat/work/leisure/personal/other/anomaly)，")
        print("本基线按 sleep→sleeping、eat→cooking、work→working、"
              "leisure→watching_tv、personal→bathing 映射；")
        print("other/anomaly 无 MA 对应物，未计入评分。leisure/personal 为近似代理"
              "（居家休闲/个人护理），故 watching_tv/bathing 的")
        print("精度/召回应理解为『对休闲/护理时段的覆盖度』，而非严格子活动判别。")
    print("说明：OpenSHS 睡眠段通常只有 presence/light 信号（+mainDoorLock 常亮），")
    print("没有门磁开合的『括号事件』，而 MA 的睡眠检测基于『无家电/门/电脑操作的")
    print("静默间隔』，故 sleep→sleeping 召回偏低属预期基线局限，非适配器缺陷。"
          if map_name != "coarse" else
          "MA 对 working/bathing 在粗粒度集上呈现明显过预测（每日触发），"
          "反映其基于『办公室/卫生间在场』即判定，未区分具体子活动。")


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenSHS activity benchmark")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="OpenSHS 宽表 CSV 路径")
    ap.add_argument("--map", default="fine", choices=["fine", "coarse"],
                   help="标签映射：fine=样本/细粒度数据集，coarse=真实粗粒度 7 分类")
    args = ap.parse_args()
    if not os.path.exists(args.csv):
        print(f"找不到 {args.csv}；先运行: "
              f"PYTHONPATH=src python -m benchmarks.gen_sample {args.csv}", file=sys.stderr)
        return 2
    report = run_benchmark(args.csv, map_name=args.map)
    print_report(report, map_name=args.map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
