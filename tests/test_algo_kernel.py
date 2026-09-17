"""P1 算法内核单测（主动感知 v2.0）。

无第三方库依赖的部分（特征抽取 / 对照指标）必测；依赖 hmmlearn / pm4py / river
的用例在库缺失时自动跳过，保证 NAS/CI 环境可渐进启用。
"""
from datetime import datetime, timedelta

import pytest

from memory_agent import algo_kernel as ak


def _series(codes: list[int], start: str = "2026-09-17T00:00:00") -> list[dict]:
    t0 = datetime.fromisoformat(start)
    return [
        {"bucket": (t0 + timedelta(minutes=i)).isoformat(sep="T"),
         "code": c, "tags": ak.decode_code(c), "n": 1 if c else 0}
        for i, c in enumerate(codes)
    ]


# ── 纯函数：编码 / 特征抽取 ────────────────────────────────────────────────

def test_encode_decode_roundtrip():
    tags = {"door", "light", "computer"}
    code = ak.encode_tags(tags)
    assert set(ak.decode_code(code)) == tags
    # 空集合 → 0（静默态），未知标签归 other
    assert ak.encode_tags(set()) == 0
    assert ak.decode_code(ak.encode_tags({"不存在的标签"})) == ["other"]
    # 多标签位掩码互不干扰
    assert ak.encode_tags({"door"}) != ak.encode_tags({"light"})


def test_extract_observation_series_buckets_and_gaps():
    def tag_of(eid, name):
        return {"light"} if "light" in eid else {"door"}

    t0 = datetime(2026, 9, 17, 10, 0, 0).isoformat(sep="T")
    t2 = datetime(2026, 9, 17, 10, 2, 0).isoformat(sep="T")
    events = [
        {"ts": t0, "entity_id": "light.a", "new_state": "on", "room": "书房"},
        # 10:01 无事件 → 应补出空观测
        {"ts": t2, "entity_id": "binary_sensor.door_b", "new_state": "on", "room": "书房"},
    ]
    series = ak.extract_observation_series(events, tag_of, bucket_sec=60)
    assert [s["bucket"][11:16] for s in series] == ["10:00", "10:01", "10:02"]
    assert series[0]["tags"] == ["light"]
    assert series[1]["code"] == 0            # 空洞补空观测（HMM 的 idle 样本）
    assert series[2]["tags"] == ["door"]


def test_extract_ignores_off_events():
    def tag_of(eid, name):
        return {"light"}

    ts = datetime(2026, 9, 17, 10, 0, 0).isoformat(sep="T")
    series = ak.extract_observation_series(
        [{"ts": ts, "entity_id": "light.a", "new_state": "off"}], tag_of
    )
    # 关灯不构成"活动发生"的观测
    assert series == []


# ── 对照指标（评估 HMM 是否真的优于多数类基线） ────────────────────────────

def test_compare_labelings_perfect_and_baseline():
    ref = ["idle"] * 6 + ["study_work"] * 4
    res = ak.compare_labelings(ref, ref)
    assert res["agreement"] == 1.0
    assert res["per_class"]["study_work"]["f1"] == 1.0
    assert res["majority_baseline"]["label"] == "idle"
    assert res["majority_baseline"]["rate"] == 0.6


def test_compare_labelings_metrics_math():
    pred = ["idle", "study_work", "idle", "study_work"]
    ref = ["idle", "idle", "idle", "study_work"]
    res = ak.compare_labelings(pred, ref)
    assert res["n"] == 4
    assert res["agreement"] == 0.75
    sw = res["per_class"]["study_work"]
    # 预测了 2 次 study_work 中 1 次对（precision 0.5）；真实 1 次全召回（recall 1.0）
    assert sw["precision"] == 0.5 and sw["recall"] == 1.0
    assert res["confusion"]["idle"] == {"idle": 2, "study_work": 1}


# ── 1) HMM 活动推断对照 ────────────────────────────────────────────────────

def test_hmm_learns_repeating_regimes():
    pytest.importorskip("hmmlearn")
    study = ak.encode_tags({"door", "light", "climate", "computer"})
    transit = ak.encode_tags({"door", "light"})
    idle = 0

    codes: list[int] = []
    labels: list[str] = []
    for _ in range(20):  # 20 个重复周期，模式可学
        codes += [study] * 15
        labels += ["study_work"] * 15
        codes += [transit] * 5
        labels += ["transit"] * 5
        codes += [idle] * 10
        labels += ["idle"] * 10

    series = _series(codes)
    res = ak.spike_compare_activity(series, labels, n_states=3, split=0.7)
    assert res["ok"] is True
    cmp = res["comparison"]
    # 关键判据：统计路线必须显著优于"全猜多数类"基线，否则不值得铺开
    assert cmp["agreement"] > cmp["majority_baseline"]["rate"]
    assert cmp["agreement"] > 0.6
    # 隐状态被多数投票映射出语义（可解释性）
    assert set(res["state_labels"].values()) & {"study_work", "idle"}
    assert res["hmm"]["n_states"] == 3


def test_hmm_posteriors_give_uncertainty():
    pytest.importorskip("hmmlearn")
    study = ak.encode_tags({"door", "light", "climate", "computer"})
    series = _series([study] * 40 + [0] * 40)
    model = ak.HmmActivityModel(n_states=2).fit(series)
    assert model.trained
    post = model.posteriors(series)
    assert len(post) == len(series)
    assert all(0.0 <= p <= 1.0 for p in post)
    assert max(post) > 0.5  # 模式清晰时应给出高置信


# ── 2) 过程挖掘 + 一致性检验 ───────────────────────────────────────────────

def _proc_events(extra: bool) -> list[dict]:
    """构造 20 天正常轨迹（door→light→computer）；extra=True 时插入异常日。"""
    tag_of = lambda eid, name: {eid.split(".")[0]}  # noqa: E731 - 实体域即标签

    def day(d: int, acts: list[str]) -> list[dict]:
        base = datetime(2026, 9, d, 9, 0, 0)
        return [
            {"ts": (base + timedelta(minutes=i)).isoformat(sep="T"),
             "entity_id": f"{a}.x", "new_state": "on", "room": "书房"}
            for i, a in enumerate(acts)
        ]

    events: list[dict] = []
    for i in range(20):
        events += day(i + 1, ["door", "light", "computer"])
    if extra:
        # 异常日：多了一次 appliance（模型里不存在的行为）
        events += day(25, ["door", "light", "appliance", "computer"])
    return events, tag_of


def test_mine_process_model_fits_normal_log():
    pytest.importorskip("pm4py")
    events, tag_of = _proc_events(extra=False)
    res = ak.mine_process_model(events, tag_of)
    assert res["ok"] is True
    assert res["cases"] == 20
    assert res["fitness_rate"] == 1.0
    assert res["anomaly_count"] == 0
    assert res["top_variants"][0]["activities"] == ["door_on", "light_on", "computer_on"]


def test_mine_process_model_flags_deviation():
    pytest.importorskip("pm4py")
    events, tag_of = _proc_events(extra=True)
    res = ak.mine_process_model(events, tag_of)
    assert res["ok"] is True
    assert res["cases"] == 21
    # 偏离已学过程模型的 case 被判为行为异常（一致性检验白送异常）
    assert res["anomaly_count"] >= 1
    assert res["fitness_rate"] < 1.0
    assert res["anomalies"][0]["case"].endswith("2026-09-25")


# ── 3) 在线异常 + 概念漂移 ────────────────────────────────────────────────

def test_online_anomaly_detects_spike_and_drift():
    pytest.importorskip("river")
    series = _series([0] * 250 + [ak.encode_tags({"door", "light", "climate", "computer"})] * 60)
    res = ak.OnlineAnomalyDetector().score_stream(series)
    assert res["ok"] is True
    assert res["n"] == 310
    # 稳态静默后突现高密度活动 → 应被标记（无监督，不需标注）
    assert res["anomaly_count"] >= 1
    # 分布突变 → ADWIN 应报漂移
    assert res["drift"]["detected"] is True


def test_online_anomaly_handles_empty():
    res = ak.OnlineAnomalyDetector().score_stream([])
    assert res["ok"] is True and res["n"] == 0


# ── 依赖探测 + 一站式入口 ─────────────────────────────────────────────────

def test_deps_available_shape():
    d = ak.deps_available()
    assert set(d) == {"numpy", "hmmlearn", "pm4py", "river"}
    assert all(isinstance(v, bool) for v in d.values())


def test_run_algorithm_kernel_end_to_end():
    events, tag_of = _proc_events(extra=True)
    res = ak.run_algorithm_kernel(events, tag_of, rule_labels=None)
    assert res["observations"] > 0
    # 无参照标签时跳过 HMM 对照但不报错，其余两项仍产出
    assert res["hmm_vs_rules"]["ok"] is False
    assert "process_model" in res and "online_anomaly" in res
