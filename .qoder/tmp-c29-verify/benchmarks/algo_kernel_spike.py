"""P1 算法内核 spike：HMM / 过程挖掘 / 在线异常 与现有手写规则的对照实验。

回答路线图 P1 的核心问题：**把行为推断从手写规则换成统计/概率库，值不值得？**

三方对照（有标注数据时）：
    手写规则(activity_inference)  vs  HMM(hmmlearn)  vs  **数据真值**
只有 HMM 显著优于「手写规则」和「多数类基线」，铺开才有价值。

用法
----
    # OpenSHS 公开数据集（自带样本）
    PYTHONPATH=src python -m benchmarks.algo_kernel_spike

    # 真实 OpenSHS 数据集（粗粒度 7 分类）
    PYTHONPATH=src python -m benchmarks.algo_kernel_spike --csv benchmarks/data/openshs_real.csv --map coarse

    # 真实 MA 数据库（无真值，只跑 HMM-vs-规则 + 过程挖掘 + 在线异常）
    PYTHONPATH=src python -m benchmarks.algo_kernel_spike --db /data/memory.db --days 7

注：算法库（hmmlearn / pm4py / river）缺失时对应段落自动跳过并提示安装。
"""
from __future__ import annotations

import argparse
import json
import os
import types
from collections import defaultdict

from memory_agent import algo_kernel as ak
from memory_agent.insights import InsightService
from memory_agent.store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "data", "openshs_sample.csv")


# ── 数据装载 ──────────────────────────────────────────────────────────────

def _load_csv(csv_path: str, map_name: str) -> tuple[list[dict], list[tuple], str, str]:
    """返回 (events, 真值片段, 窗口起, 窗口止)。"""
    from .openshs_convert import ground_truth, start_end, wide_to_events
    from .openshs_schema import get_activity_map

    events = wide_to_events(csv_path)
    mn, mx = start_end(csv_path)
    gt = ground_truth(csv_path, activity_map=get_activity_map(map_name))
    spans = [(s["start"], s["end"], s["ma"]) for s in gt["segments"]]
    return events, spans, mn, mx


def _load_db(db_path: str, days: int) -> tuple[list[dict], list[tuple], str, str]:
    """从真实 MA SQLite 库取事件（无真值片段）。"""
    from datetime import timedelta

    from memory_agent.store import now_local

    store = Store(db_path)
    store.init_schema()
    now = now_local(store.tz_offset_hours)
    end = now.isoformat(sep="T")
    start = (now - timedelta(days=max(1, days))).isoformat(sep="T")
    events = store.query_events(start=start, end=end, order="asc", limit=200000)
    norm = [{"entity_id": e.get("entity_id"), "room": e.get("room"),
             "new_state": e.get("new_state"), "ts": e.get("ts")} for e in events]
    return norm, [], start, end


# ── 逐桶标注（规则 / 真值） ────────────────────────────────────────────────

def _bucket_labels(
    buckets: list[str], spans: list[tuple], default: str = "idle"
) -> tuple[list[str], list[bool]]:
    """把「区间片段」展开成逐桶标签，并给出可评估掩码。

    * 桶落在 **已映射** 片段内 → 用该活动标注，可评估
    * 桶落在 **未映射** 片段内（真值缺活动名，如 relax）→ 不可评估（真值未知）
    * 桶不在任何片段内 → ``idle``（静默即空闲），可评估
    """
    by_day: dict[str, list[tuple]] = defaultdict(list)
    for s, e, lab in spans:
        if lab:
            by_day[str(s)[:10]].append((str(s), str(e), str(lab)))
    labels: list[str] = []
    masks: list[bool] = []
    for b in buckets:
        lab = default
        evaluable = True
        for s, e, l in by_day.get(b[:10], ()):
            if s <= b <= e:
                lab, evaluable = l, True
                break
        labels.append(lab)
        masks.append(evaluable)
    return labels, masks


def _rule_spans(mn: str, mx: str, events: list[dict], store: Store):
    """跑现有手写规则（activity_inference 同源的 infer_activities），取活动片段。"""
    cfg = types.SimpleNamespace(tz_offset_hours=store.tz_offset_hours,
                                insight_cache_ttl=0, rooms={})
    svc = InsightService(cfg, store)
    res = svc.infer_activities(start=mn, end=mx)
    spans = [(a.get("start_ts"), a.get("end_ts"), a.get("activity"))
             for a in res.get("activities", []) or []]
    return [s for s in spans if s[0] and s[1] and s[2]], svc


def _apply_mask(pred: list[str], ref: list[str], mask: list[bool],
                offset: int = 0) -> tuple[list[str], list[str]]:
    p: list[str] = []
    r: list[str] = []
    for i, (a, b) in enumerate(zip(pred, ref)):
        j = i + offset
        if j < len(mask) and mask[j]:
            p.append(a)
            r.append(b)
    return p, r


def _macro_nonidle(cmp: dict, exclude: str = "idle") -> float:
    """非 idle 类的 macro-F1 —— 桶级一致率会被 idle 主导，必须看这个。

    MA 对用户的回答是「今天做饭了吗/工作了吗」，真正有价值的是**非空闲活动**
    的识别质量，而不是"大部分时间都是 idle"这种平凡正确。
    """
    vals = [v["f1"] for k, v in (cmp.get("per_class") or {}).items()
            if k != exclude and v.get("support", 0) > 0]
    return round(sum(vals) / len(vals), 3) if vals else 0.0


# 房间 ↔ 该房间的"主活动"（用于按房间的二元活动检测实验）
_ROOM_ACTIVITY = {
    "卧室": "sleeping", "厨房": "cooking", "书房": "working",
    "客厅": "watching_tv", "卫生间": "bathing",
}


def _labels_for_activity(buckets: list[str], spans: list[tuple], target: str) -> list[str]:
    """把该桶是否落在 ``target`` 活动片段内 → ``target`` / ``idle`` 二元标签。

    用于「按房间」实验：全局混在一起的位掩码观测区分不出活动（全屋灯/门都混在
    一起），按房间切开后每个房间只需回答"本房间是否在做它的主活动"。
    """
    by_day: dict[str, list[tuple]] = defaultdict(list)
    for s, e, lab in spans:
        if lab == target:
            by_day[str(s)[:10]].append((str(s), str(e)))
    out: list[str] = []
    for b in buckets:
        hit = any(s <= b <= e for s, e in by_day.get(b[:10], ()))
        out.append(target if hit else "idle")
    return out


def per_room_eval(events: list[dict], gt_spans: list[tuple], rule_spans: list[tuple],
                  bucket_sec: int, n_states: int) -> dict:
    """按房间的二元活动检测对照（HMM vs 手写规则，均对真值）。"""
    out: dict = {}
    tag_of = InsightService._tags_of
    for room, act in _ROOM_ACTIVITY.items():
        rev = [e for e in events if (e.get("room") or "") == room]
        if len(rev) < 20:
            continue
        ser = ak.extract_observation_series(rev, tag_of, bucket_sec=bucket_sec)
        if len(ser) < 200:
            continue
        buckets = [s["bucket"] for s in ser]
        truth = _labels_for_activity(buckets, gt_spans, act)
        if act not in truth:  # 真值里该房间主活动未出现（如 coarse 无 watching_tv）
            continue
        rule_l = _labels_for_activity(buckets, rule_spans, act)
        hmm = ak.spike_compare_activity(ser, rule_l, n_states=n_states, split=0.7)
        if not hmm.get("ok"):
            continue
        offset = hmm["train_n"]
        c_hmm = ak.compare_labelings(hmm["predictions"], truth[offset:])
        c_rule = ak.compare_labelings(rule_l[offset:], truth[offset:])
        pos = sum(1 for t in truth if t == act)
        out[room] = {
            "activity": act, "buckets": len(ser), "positive_buckets": pos,
            "hmm": {"f1": c_hmm["per_class"].get(act, {}).get("f1", 0.0),
                    "precision": c_hmm["per_class"].get(act, {}).get("precision", 0.0),
                    "recall": c_hmm["per_class"].get(act, {}).get("recall", 0.0)},
            "rules": {"f1": c_rule["per_class"].get(act, {}).get("f1", 0.0),
                      "precision": c_rule["per_class"].get(act, {}).get("precision", 0.0),
                      "recall": c_rule["per_class"].get(act, {}).get("recall", 0.0)},
            "majority_rate": c_hmm["majority_baseline"]["rate"],
        }
    return out


# ── 主流程 ────────────────────────────────────────────────────────────────

def run_spike(csv_path: str | None, db_path: str | None, map_name: str,
              days: int, bucket_sec: int, n_states: int) -> dict:
    deps = ak.deps_available()
    print("=" * 72)
    print("P1 算法内核 spike  |  依赖:", deps)

    if db_path:
        events, gt_spans, mn, mx = _load_db(db_path, days)
        source = f"db:{db_path} (近 {days} 天)"
    else:
        events, gt_spans, mn, mx = _load_csv(csv_path or DEFAULT_CSV, map_name)
        source = f"csv:{csv_path or DEFAULT_CSV} (map={map_name})"
    print(f"数据源: {source}")
    print(f"事件 {len(events)} 条，窗口 {mn} → {mx}")

    store = Store(":memory:", tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events([{**e, "day": str(e.get("ts"))[:10], "domain": (e.get("entity_id") or "").split(".")[0],
                          "old_state": e.get("old_state") or ""} for e in events])

    tag_of = InsightService._tags_of
    series = ak.extract_observation_series(events, tag_of, bucket_sec=bucket_sec)
    buckets = [s["bucket"] for s in series]
    print(f"观测序列: {len(series)} 桶（{bucket_sec}s/桶，含空洞补零）")

    out: dict = {"source": source, "deps": deps, "events": len(events),
                 "observations": len(series), "bucket_sec": bucket_sec,
                 "window": [mn, mx]}

    # 规则标签（作为 HMM 的语义参照 + 与真值对比的一方）
    rule_spans, _ = _rule_spans(mn, mx, events, store)
    rule_labels, _ = _bucket_labels(buckets, rule_spans)
    print(f"手写规则活动片段: {len(rule_spans)} 段")

    # 真值标签（有标注数据时）
    truth_labels: list[str] = []
    truth_mask: list[bool] = []
    if gt_spans:
        truth_labels, truth_mask = _bucket_labels(buckets, gt_spans)
        print(f"真值活动片段: {len(gt_spans)} 段（可评估桶 {sum(truth_mask)}/{len(buckets)}）")
    out["rule_segments"] = len(rule_spans)
    out["gt_segments"] = len(gt_spans)

    # ── 1) HMM vs 手写规则 vs 真值 ─────────────────────────────────────
    hmm = ak.spike_compare_activity(series, rule_labels, n_states=n_states, split=0.7)
    out["hmm_vs_rules"] = {k: v for k, v in hmm.items() if k != "hmm"}
    if hmm.get("ok"):
        pred = hmm["predictions"]
        ref = hmm["reference"]
        cmp = hmm["comparison"]
        print("-" * 72)
        print(f"[HMM vs 手写规则] 一致率={cmp['agreement']}  "
              f"多数类基线={cmp['majority_baseline']['rate']} "
              f"({cmp['majority_baseline']['label']})")
        print(f"  隐状态→活动映射: {hmm['state_labels']}")
        if truth_labels:
            offset = hmm["train_n"]
            arg_mask = truth_mask[offset:]
            # 三方各自与真值对比（同一切分区间，公平可比）
            p_hmm, t_hmm = _apply_mask(pred, truth_labels[offset:], arg_mask)
            p_rule, t_rule = _apply_mask(ref, truth_labels[offset:], arg_mask)
            c_hmm = ak.compare_labelings(p_hmm, t_hmm)
            c_rule = ak.compare_labelings(p_rule, t_rule)
            # 上界变体：用真值给隐状态投票语义（测 HMM 隐结构的表达力上限）
            upper = ak.spike_compare_activity(series, rule_labels, n_states=n_states,
                                              split=0.7, map_labels=truth_labels)
            c_up = None
            if upper.get("ok"):
                p_up, t_up = _apply_mask(upper["predictions"], truth_labels[offset:], arg_mask)
                c_up = ak.compare_labelings(p_up, t_up)
            out["vs_truth"] = {"hmm": c_hmm, "rules": c_rule,
                               "hmm_upper_bound": c_up, "n": len(t_hmm)}
            m_hmm = _macro_nonidle(c_hmm)
            m_rule = _macro_nonidle(c_rule)
            m_up = _macro_nonidle(c_up) if c_up else None
            print("-" * 72)
            print(f"[对真值·同一测试区间 n={len(t_hmm)}]")
            print(f"  桶级一致率   HMM={c_hmm['agreement']}  "
                  f"规则={c_rule['agreement']}  多数类基线={c_hmm['majority_baseline']['rate']}")
            print(f"  非idle macro-F1   HMM={m_hmm}  规则={m_rule}"
                  + (f"  HMM上界(真值映射)={m_up}" if m_up is not None else ""))
            acts = sorted({k for c in (c_hmm, c_rule) for k in c["per_class"]
                           if k != "idle"})
            if acts:
                print("  逐活动 F1: " + "  ".join(
                    f"{a}(HMM={c_hmm['per_class'].get(a, {}).get('f1', 0)}/"
                    f"规则={c_rule['per_class'].get(a, {}).get('f1', 0)})" for a in acts))
            verdict = ("HMM 占优" if m_hmm > m_rule else "规则占优/持平")
            print(f"  => 判定: {verdict}（非idle macro-F1 Δ={m_hmm - m_rule:+.3f}）")
            if m_up is not None and m_up <= c_hmm["majority_baseline"]["rate"]:
                print("     注: HMM 上界未超过多数类基线 → 隐结构本身区分力有限，"
                      "需换特征/换模型而非仅调状态数")
            out["macro_nonidle"] = {"hmm": m_hmm, "rules": m_rule, "hmm_upper": m_up}
        else:
            print("  （无真值，仅能给出 HMM-规则一致率）")
        # ── 1b) 按房间的二元活动检测（验证"全局聚合粒度"是否为主因）──────
        if truth_labels:
            print("-" * 72)
            print("[按房间·本房间主活动检测（HMM vs 规则，均对真值）]")
            pr = per_room_eval(events, gt_spans, rule_spans, bucket_sec, n_states)
            out["per_room"] = pr
            for room, r in pr.items():
                print(f"  {room}/{r['activity']}: 正样本={r['positive_buckets']}/{r['buckets']}  "
                      f"HMM F1={r['hmm']['f1']}(P={r['hmm']['precision']}/R={r['hmm']['recall']})  "
                      f"规则 F1={r['rules']['f1']}(P={r['rules']['precision']}/R={r['rules']['recall']})")
            if not pr:
                print("  （无满足条件的房间）")
        out["state_labels"] = hmm["state_labels"]
    else:
        print(f"[HMM] 跳过: {hmm.get('error')}")

    # ── 2) 过程挖掘 ───────────────────────────────────────────────────
    print("-" * 72)
    pm = ak.mine_process_model(events, tag_of)
    out["process_model"] = pm
    if pm.get("ok"):
        print(f"[过程挖掘] case={pm['cases']}  一致性={pm['fitness_rate']}  "
              f"异常={pm['anomaly_count']}  库所/变迁={pm['places']}/{pm['transitions']}")
        for v in pm["top_variants"][:3]:
            print(f"  变体 x{v['count']}: {' → '.join(v['activities'])}")
        for a in pm["anomalies"][:3]:
            print(f"  异常 {a['case']}: {a['reasons']} | {' → '.join(a['activities'])}")
    else:
        print(f"[过程挖掘] 跳过: {pm.get('error')}")

    # ── 3) 在线异常 + 漂移 ────────────────────────────────────────────
    print("-" * 72)
    an = ak.OnlineAnomalyDetector().score_stream(series)
    out["online_anomaly"] = an
    if an.get("ok"):
        print(f"[在线异常] n={an['n']}  异常={an['anomaly_count']}  "
              f"分均值={an['score_mean']}  峰值={an['score_max']}  "
              f"漂移={an['drift']['detected']}({an['drift']['count']})")
        for p in an["drift"]["points"][:3]:
            print(f"  漂移@{p['bucket']} density={p['density']}")
    else:
        print(f"[在线异常] 跳过: {an.get('error')}")
    print("=" * 72)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="P1 算法内核 spike")
    ap.add_argument("--csv", default="", help="OpenSHS 宽表 CSV 路径")
    ap.add_argument("--db", default="", help="真实 MA SQLite 库路径（与 --csv 二选一）")
    ap.add_argument("--map", default="fine", choices=["fine", "coarse"], dest="map_name")
    ap.add_argument("--days", type=int, default=7, help="--db 模式的回溯天数")
    ap.add_argument("--bucket-sec", type=int, default=60, help="观测桶粒度（秒）")
    ap.add_argument("--states", type=int, default=4, help="HMM 隐状态数")
    ap.add_argument("--out", default=os.path.join(HERE, "data", "algo_kernel_spike.json"),
                    help="结果 JSON 输出路径")
    args = ap.parse_args()

    res = run_spike(args.csv or None, args.db or None, args.map_name,
                    args.days, args.bucket_sec, args.states)
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.out}")
    except Exception as exc:  # noqa: BLE001
        print(f"报告写入失败: {exc}")


if __name__ == "__main__":
    main()
