"""主动感知 v2.0 · P1 算法内核（统计 / 概率 / 学习）。

当前行为推断层（``activity_inference.py``）是**手写规则 + 朴素 n-gram**：可解释、
确定性，但无概率、无学习、无不确定性度量。本模块引入成熟库做**替换式 spike**，
用于验证"算法内核升级"是否值得全面铺开（路线图 P1）。

三项能力（全部依赖守卫，库缺失时优雅降级，不抛崩）：

1. **HMM 活动推断**（``hmmlearn``）：从多路二值传感器（门磁/灯/空调/电脑/在场）
   的分钟级位掩码观测序列，用 CategoricalHMM 无监督学出隐活动状态，再与现有
   手写规则的结果做**对照实验**（一致率 / 每类 P-R-F1 / 混淆矩阵）。
2. **过程挖掘**（``pm4py``）：把（房间·天）作为 case、``tag_state`` 作为 activity
   构造事件日志，归纳出 Petri 网（真实行为过程模型：并行/循环/分支），再用
   token-based replay 做**一致性检验**——偏离模型（fitness<1）的 case 即行为异常。
   这比自己统计 n-gram 频次更有表达力，且"异常"是白送的。
3. **在线异常 + 概念漂移**（``river``）：Half-Space Trees 在线增量学习行为模式并
   打异常分；ADWIN 检测行为分布漂移（"最近作息变了"）。

许可注意：``pm4py`` 为 **AGPLv3**，商用需开源或采购商业许可；本项目为自托管
个人场景，可直接使用，若未来商业化需替换实现或购证。``hmmlearn``(BSD-3)、
``river``(BSD-3) 无此约束。

设计约束：本模块**不写库、不依赖 runtime**，只做纯函数式计算，便于单测与离线
spike；接线（写 ``behavior_states`` / 候选规则）由调用方决定，默认不接入生产
路径（``config.algo_kernel_enabled`` 默认关）。
"""
from __future__ import annotations

import importlib.util
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional, Sequence

logger = logging.getLogger("memory_agent.algo_kernel")

# ── 观测空间：行为传感器标签 → 位掩码 ────────────────────────────────────────
TAG_VOCAB: tuple[str, ...] = (
    "door", "light", "climate", "computer", "presence",
    "media", "cover", "appliance", "other",
)
_TAG_INDEX: dict[str, int] = {t: i for i, t in enumerate(TAG_VOCAB)}
N_FEATURES: int = 1 << len(TAG_VOCAB)  # 512 个观测符号（位掩码全集）

_ON_STATES = {"on", "open", "playing", "heat", "cool", "auto", "detected",
              "home", "unlocked", "1", "true", "yes"}

# 供 spike 对照的活动类别（与 activity_inference 的 infer 名对齐）
DEFAULT_ACTIVITIES: tuple[str, ...] = ("idle", "study_work", "user_asleep", "transit")


def deps_available() -> dict[str, bool]:
    """探测算法库可用性（部署环境可据此决定是否启用内核）。"""
    return {
        m: importlib.util.find_spec(m) is not None
        for m in ("numpy", "hmmlearn", "pm4py", "river")
    }


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def is_on(state: Any) -> bool:
    """与 activity_inference 同口径的 on 判定。"""
    s = str(state or "").strip().lower()
    if s in _ON_STATES:
        return True
    return False


# ── 特征抽取：事件流 → 分钟级观测序列 ────────────────────────────────────────

def encode_tags(tags: Iterable[str]) -> int:
    """把标签集合编码为位掩码整数（未知标签归 ``other``）。"""
    code = 0
    for t in tags:
        i = _TAG_INDEX.get(str(t))
        if i is None:
            i = _TAG_INDEX["other"]
        code |= 1 << i
    return code


def decode_code(code: int) -> list[str]:
    """位掩码 → 标签列表（调试/可解释性用）。"""
    return [t for i, t in enumerate(TAG_VOCAB) if code & (1 << i)]


def extract_observation_series(
    events: Sequence[dict],
    tag_of: Callable[[str, str], set],
    bucket_sec: int = 60,
    fill_gaps: bool = True,
    max_buckets: int = 20000,
) -> list[dict]:
    """把事件流压成 ``bucket_sec`` 粒度的观测序列（HMM 的输入）。

    ``tag_of(entity_id, friendly_name) -> set[str]`` 由调用方注入（生产传
    ``InsightService._tags_of``），本模块不耦合标签体系。

    返回按时间升序的 ``[{"bucket": iso, "code": int, "tags": [...], "n": 事件数}]``。
    """
    per_bucket: dict[str, set] = {}
    counts: Counter = Counter()
    for e in events:
        ts = _parse_ts(e.get("ts") or e.get("server_ts"))
        if ts is None:
            continue
        if not is_on(e.get("new_state") if "new_state" in e else e.get("state")):
            continue  # 只看"开启"边沿：关灯/关门不构成活动观测
        epoch = int(ts.timestamp())
        b_epoch = epoch - (epoch % max(1, bucket_sec))
        key = datetime.fromtimestamp(b_epoch, ts.tzinfo).isoformat(sep="T")
        try:
            tags = tag_of(e.get("entity_id") or "", e.get("friendly_name") or "")
        except Exception:  # noqa: BLE001
            tags = set()
        if not tags:
            continue
        per_bucket.setdefault(key, set()).update(tags)
        counts[key] += 1

    if not per_bucket:
        return []

    keys = sorted(per_bucket)
    out: list[dict] = []
    if fill_gaps:
        # 补齐空洞为空观测（全 0）——HMM 需要"什么都不开"的静默态才能学出 idle
        cur = _parse_ts(keys[0])
        end = _parse_ts(keys[-1])
        step = timedelta(seconds=max(1, bucket_sec))
        while cur is not None and end is not None and cur <= end:
            k = cur.isoformat(sep="T")
            tags = per_bucket.get(k, set())
            out.append({"bucket": k, "code": encode_tags(tags),
                        "tags": sorted(tags), "n": counts.get(k, 0)})
            cur = cur + step
            if len(out) >= max_buckets:
                break
    else:
        for k in keys:
            tags = per_bucket[k]
            out.append({"bucket": k, "code": encode_tags(tags),
                        "tags": sorted(tags), "n": counts.get(k, 0)})
    return out


# ── 1) HMM 活动推断（hmmlearn） ──────────────────────────────────────────────

class HmmActivityModel:
    """基于 CategoricalHMM 的活动状态推断（无监督）。

    观测 = 标签位掩码（离散符号），隐状态 = 活动类别。用 Baum-Welch 学参数、
    Viterbi 解码路径，``posteriors`` 给出每步置信度——这是手写规则完全没有的
    「不确定性度量」。

    **观测词表稠密化（重要）**：位掩码理论取值 2^9=512，若直接作为观测符号，
    ``K`` 个隐状态会产生 ``K×512`` 个发射参数（K=4 即 2059 个自由参数），远超声学
    样本量，Baum-Welch 必然退化解（hmmlearn 会显式警告 degenerate）。因此按**训练集
    频次**取 top ``max_symbols`` 个观测 → 稠密下标，其余归入 ``other`` 兜底符号，
    参数量降到 ``K×max_symbols`` 量级，与家居数据规模匹配。
    """

    def __init__(self, n_states: int = 4, random_state: int = 42, n_iter: int = 100,
                 max_symbols: int = 32):
        self.n_states = max(2, int(n_states))
        self.random_state = int(random_state)
        self.n_iter = max(1, int(n_iter))
        self.max_symbols = max(4, int(max_symbols))
        self._model: Any = None
        self._symbol_map: dict[int, int] = {}   # 位掩码 → 稠密下标
        self._other_index: int = 0              # 未登录观测的兜底下标
        self.state_labels_: dict[int, str] = {}
        self.available: bool = importlib.util.find_spec("hmmlearn") is not None

    # -- 训练/推理 --------------------------------------------------------
    def _build_vocab(self, series: Sequence[dict]) -> None:
        """按频次取 top 观测构建稠密词表（其余归 other）。"""
        freq = Counter(int(s.get("code") or 0) for s in series)
        top = [code for code, _ in freq.most_common(self.max_symbols - 1)]
        self._symbol_map = {code: i for i, code in enumerate(top)}
        self._other_index = len(self._symbol_map)  # other 占最后一格

    @property
    def n_symbols(self) -> int:
        return max(2, len(self._symbol_map) + 1)

    def _codes(self, series: Sequence[dict]):
        import numpy as np
        return np.array(
            [[self._symbol_map.get(int(s.get("code") or 0), self._other_index)]
             for s in series],
            dtype=int,
        )

    def fit(self, series: Sequence[dict]) -> "HmmActivityModel":
        """在观测序列上训练（Baum-Welch）。数据不足时保持未训练状态。"""
        if not self.available or len(series) < self.n_states * 5:
            return self
        self._build_vocab(series)
        from hmmlearn.hmm import CategoricalHMM
        x = self._codes(series)
        model = CategoricalHMM(
            n_components=self.n_states,
            n_features=self.n_symbols,
            n_iter=self.n_iter,
            tol=1e-4,
            random_state=self.random_state,
            init_params="ste",
        )
        try:
            model.fit(x, lengths=[len(x)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("algo_kernel: HMM 训练失败: %s", exc)
            return self
        self._model = model
        return self

    @property
    def trained(self) -> bool:
        return self._model is not None

    def predict(self, series: Sequence[dict]) -> list[int]:
        if not self.trained or not series:
            return []
        x = self._codes(series)
        try:
            return [int(v) for v in self._model.predict(x)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("algo_kernel: HMM 解码失败: %s", exc)
            return []

    def posteriors(self, series: Sequence[dict]) -> list[float]:
        """每步最大后验概率（置信度 0-1）。"""
        if not self.trained or not series:
            return []
        x = self._codes(series)
        try:
            proba = self._model.predict_proba(x)
        except Exception:  # noqa: BLE001
            return []
        return [float(max(row)) for row in proba]

    # -- 状态→活动语义映射 ------------------------------------------------
    def map_states(self, hidden: Sequence[int], ref_labels: Sequence[str]) -> dict[int, str]:
        """用参照标签（手写规则结果）给每个隐状态投票出活动名。

        隐状态本身无名字（无监督），借参照标签做多数投票即可对齐语义，
        这是把 HMM 结果和既有规则**可比**化的关键一步。
        """
        votes: dict[int, Counter] = {}
        for h, lab in zip(hidden, ref_labels):
            votes.setdefault(int(h), Counter())[str(lab)] += 1
        self.state_labels_ = {
            h: (c.most_common(1)[0][0] if c else "unknown")
            for h, c in votes.items()
        }
        return self.state_labels_

    def predict_labels(self, hidden: Sequence[int], default: str = "unknown") -> list[str]:
        return [self.state_labels_.get(int(h), default) for h in hidden]

    def to_dict(self) -> dict:
        """可解释摘要：各状态主导观测 + 转移矩阵。"""
        if not self.trained:
            return {"trained": False, "n_states": self.n_states}
        try:
            # 稠密下标 → 位掩码 → 可读标签（other 兜底符号显式标出）
            inv = {v: k for k, v in self._symbol_map.items()}
            top: dict[int, list[str]] = {}
            for s in range(self.n_states):
                row = list(self._model.emissionprob_[s])
                best = sorted(range(len(row)), key=lambda i: -row[i])[:3]
                top[s] = [
                    ("(other)" if i == self._other_index else
                     ("+".join(decode_code(inv.get(i, 0))) or "idle"))
                    for i in best
                ]
            trans = [[round(float(v), 3) for v in row] for row in self._model.transmat_]
            return {"trained": True, "n_states": self.n_states,
                    "n_symbols": self.n_symbols,
                    "state_top_observations": top,
                    "state_labels": {str(k): v for k, v in self.state_labels_.items()},
                    "transmat": trans}
        except Exception:  # noqa: BLE001
            return {"trained": True, "n_states": self.n_states}


# ── 评价：两个标注序列的对照 ─────────────────────────────────────────────────

def compare_labelings(pred: Sequence[str], ref: Sequence[str]) -> dict:
    """预测 vs 参照（手写规则）的多分类对照：一致率 + 每类 P/R/F1 + 混淆。

    同时给出 **majority baseline**（全猜参照里最多的类）——只有显著超过基线，
    统计路线才算有价值。
    """
    pairs = [(str(p), str(r)) for p, r in zip(pred, ref)]
    n = len(pairs)
    if n == 0:
        return {"n": 0, "agreement": 0.0, "per_class": {}, "confusion": {}}
    agree = sum(1 for p, r in pairs if p == r)
    labels = sorted({r for _, r in pairs} | {p for p, _ in pairs})
    per_class: dict[str, dict] = {}
    for lab in labels:
        tp = sum(1 for p, r in pairs if p == lab and r == lab)
        fp = sum(1 for p, r in pairs if p == lab and r != lab)
        fn = sum(1 for p, r in pairs if p != lab and r == lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_class[lab] = {"precision": round(prec, 3), "recall": round(rec, 3),
                          "f1": round(f1, 3), "support": tp + fn}
    confusion: dict[str, dict[str, int]] = {}
    for p, r in pairs:
        confusion.setdefault(r, Counter())[p] += 1
    ref_counts = Counter(r for _, r in pairs)
    majority = ref_counts.most_common(1)[0]
    return {
        "n": n,
        "agreement": round(agree / n, 3),
        "majority_baseline": {"label": majority[0],
                              "rate": round(majority[1] / n, 3)},
        "per_class": per_class,
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }


def spike_compare_activity(
    series: Sequence[dict],
    rule_labels: Sequence[str],
    n_states: int = 4,
    split: float = 0.7,
    map_labels: Sequence[str] | None = None,
) -> dict:
    """HMM vs 手写规则的对照实验（train/test 切分，避免自评虚高）。

    :param series: ``extract_observation_series`` 的输出（含 code）
    :param rule_labels: 与 series 等长的**手写规则标签**（参照）
    :param split: 前 70% 训练/映射状态语义，后 30% 评估
    :param map_labels: 给隐状态投票语义时用的标签（缺省用 rule_labels）。
        传入真值标签可测出 **HMM 隐结构表达力上界**——若上界仍不优于基线，
        说明问题不在"状态语义映射"而在"隐结构本身没学到东西"。
    """
    if not series or len(series) != len(rule_labels):
        return {"ok": False, "error": "series 与 rule_labels 长度不一致或为空"}
    cut = max(1, min(len(series) - 1, int(len(series) * split)))
    train_s, test_s = list(series[:cut]), list(series[cut:])
    train_l, test_l = list(rule_labels[:cut]), list(rule_labels[cut:])
    map_l = list(map_labels[:cut]) if map_labels is not None and len(map_labels) == len(series) else train_l
    if len(map_l) != len(train_s):
        map_l = train_l

    model = HmmActivityModel(n_states=n_states).fit(train_s)
    if not model.trained:
        return {"ok": False, "error": "HMM 未训练（库缺失或样本不足）",
                "deps": deps_available()}
    hidden_train = model.predict(train_s)
    model.map_states(hidden_train, map_l)
    pred = model.predict_labels(model.predict(test_s))
    cmp = compare_labelings(pred, test_l)
    return {
        "ok": True,
        "n_states": n_states,
        "train_n": len(train_s),
        "test_n": len(test_s),
        "mapping_source": "truth" if map_labels is not None else "rules",
        "state_labels": {str(k): v for k, v in model.state_labels_.items()},
        "comparison": cmp,
        # 测试段的逐桶预测与参照，供调用方与**第三方真值**对照（benchmark 用）
        "predictions": pred,
        "reference": test_l,
        "hmm": model.to_dict(),
    }


# ── 2) 过程挖掘 + 一致性检验（pm4py） ────────────────────────────────────────

def build_process_cases(
    events: Sequence[dict],
    tag_of: Callable[[str, str], set],
    primary_tag: Optional[Callable[[set], str]] = None,
    min_case_events: int = 3,
    max_cases: int = 3000,
    bucket_sec: int = 600,
    skip_rooms: Sequence[str] = ("", "未知", "未分区"),
) -> list[dict]:
    """事件流 → 过程挖掘 case 列表（``case = 房间|日期``，活动 = ``tag_on``）。

    **时间桶聚合（关键）**：家庭事件是秒级的（电视/在场传感器来回交替），若一条
    事件一步，一天的轨迹会有上百步且每天都不重样 —— 变体支持度恒为 1、DFG 稀边
    遍地都是，异常判定被噪声主导。因此先把事件压进 ``bucket_sec``（默认 10min）
    时间桶：桶内出现过的标签各记一步（按首次出现顺序），再压缩跨桶的相邻重复，
    得到"这一天大致怎么走"的粗粒度过程轨迹。

    :param bucket_sec: 时间桶粒度（秒）；``0`` 表示不聚合（逐事件，仅调试用）
    :param skip_rooms: 丢弃的伪房间名（HA 未分区/未知），避免产生无意义 case
    """
    def _primary(tags: set) -> str:
        if primary_tag is not None:
            try:
                return str(primary_tag(tags))
            except Exception:  # noqa: BLE001
                pass
        for t in TAG_VOCAB:  # 按词表优先级取主标签，确定性与 tag_of 实现无关
            if t in tags:
                return t
        return "other"

    grouped: dict[str, list] = {}
    skip = set(skip_rooms or ())
    for e in events:
        ts = _parse_ts(e.get("ts") or e.get("server_ts"))
        if ts is None or not is_on(e.get("new_state") if "new_state" in e else e.get("state")):
            continue
        room = (e.get("room") or "").strip()
        if room in skip:
            continue
        try:
            tags = tag_of(e.get("entity_id") or "", e.get("friendly_name") or "")
        except Exception:  # noqa: BLE001
            tags = set()
        if not tags:
            continue
        case = f"{room or '未知'}|{ts.date().isoformat()}"
        grouped.setdefault(case, []).append((ts, _primary(tags)))

    order = {t: i for i, t in enumerate(TAG_VOCAB)}
    out: list[dict] = []
    for case, items in list(grouped.items())[:max_cases]:
        items.sort(key=lambda x: x[0])
        acts: list[str] = []
        if bucket_sec and int(bucket_sec) > 0:
            # 先按时间桶收集标签集合（保持桶顺序）
            buckets: list[tuple[int, set]] = []
            step = int(bucket_sec)
            for ts, tag in items:
                key = int(ts.timestamp()) // step
                if not buckets or buckets[-1][0] != key:
                    buckets.append((key, set()))
                buckets[-1][1].add(tag)
            # 只在标签**相对上一桶新出现**时记一步（"这一步是这桶新起的活动"）。
            # 这样长时间挂着的在场/媒体不会每个桶都记一遍，轨迹长度收敛到
            # "一天里大致依次发生了哪些事"，与过程挖掘的语义一致。
            prev: set = set()
            for _key, tags in buckets:
                for t in sorted(tags - prev, key=lambda x: order.get(x, 99)):
                    if not acts or acts[-1] != t:
                        acts.append(t)
                prev = tags
        else:
            for _, tag in items:
                if not acts or acts[-1] != tag:
                    acts.append(tag)
        if len(acts) < min_case_events:
            continue
        room, _, day = case.partition("|")
        out.append({"case": case, "room": room, "day": day,
                    "activities": [f"{a}_on" for a in acts]})
    return out


def mine_process_model_pure(
    cases: Sequence[dict],
    min_edge_support: float = 0.1,
    min_activity_support: float = 0.1,
    min_cases_per_room: int = 1,
) -> dict:
    """纯 Python 过程模型 + 一致性检验（**无第三方依赖**）。

    做法等价于 pm4py 的 DFG 挖掘 + 一致性检验，但只保留对"发现异常"真正有用的
    部分：直接跟随图（DFG）+ 变体统计 + 稀有边/稀有活动判据。

    为何把它作为**主路径**而非 pm4py 的退路：P1 spike 实测中 17 个异常 case 的
    判据**全部**来自 ``rare_edges``，Petri 网 + token 回放的 ``replay_not_fit``
    没有额外贡献（归纳出的网对小样本日志过于宽松）。而纯 Python 版无 AGPL 约束、
    无重型依赖、导入零成本，更适合常驻在 J3455 NAS 上。
    pm4py 仍作为可选增强（补充 Petri 网库所/变迁与更严格的一致性判据）。
    """
    cases_all = len(cases)
    n = cases_all
    if n == 0:
        # 返回结构保持完整（空结果与正常结果同构，调用方无需分支处理）
        return {"ok": True, "engine": "pure", "cases": 0, "note": "无可用 case",
                "fitness_rate": 1.0, "dfg_edges": 0, "start_activities": 0,
                "end_activities": 0, "activities": 0, "top_variants": [],
                "variants_by_room": {}, "anomaly_count": 0, "anomalies": [],
                "cases_all": 0, "skipped_rooms": []}

    # 「观测充分度」门槛：某房间只有 1-2 天历史时根本不存在"常态"可偏离，
    # 按比例判稀有边必然把这两天全判成异常（线上实测：14 天 / 13 房间 →
    # 37% 房间-日被判异常，完全不可用）。故先剔除样本不足的房间，
    # 只在"有足够历史可比"的房间内做一致性检验。
    room_counts = Counter(c["room"] for c in cases)
    if min_cases_per_room and int(min_cases_per_room) > 1:
        thr = int(min_cases_per_room)
        kept = [c for c in cases if room_counts[c["room"]] >= thr]
        skipped_rooms = sorted(r for r, cnt in room_counts.items() if cnt < thr)
        cases = kept
        n = len(cases)
        if n == 0:
            return {"ok": True, "engine": "pure", "cases": 0,
                    "note": f"各房间样本均不足 {thr} 天，暂不做一致性检验",
                    "fitness_rate": 1.0, "dfg_edges": 0, "start_activities": 0,
                    "end_activities": 0, "activities": 0, "top_variants": [],
                    "variants_by_room": {}, "anomaly_count": 0, "anomalies": [],
                    "cases_all": cases_all,
                    "skipped_rooms": skipped_rooms, "min_cases_per_room": thr}

    variants: Counter = Counter(tuple(c["activities"]) for c in cases)
    edge_count: Counter = Counter()
    act_count: Counter = Counter()
    start_count: Counter = Counter()
    end_count: Counter = Counter()

    # **一致性检验必须按房间做**：每个房间有各自的行为过程（厨房 vs 卧室完全不同），
    # 用全局 DFG 判"稀有边"会把"厨房的日常"当成"相对客厅的稀有事"——线上实测
    # 全局口径下 50% 的房间-日被判异常，噪声完全淹没信号。改为房间内比对后，
    # "异常"才真的表示"这个房间今天不像它平时的样子"。
    by_room_cases: dict[str, list[dict]] = {}
    for c in cases:
        by_room_cases.setdefault(c["room"], []).append(c)

    anomalies: list[dict] = []
    for rm, room_cases in by_room_cases.items():
        rn = len(room_cases)
        r_edge: Counter = Counter()
        r_act: Counter = Counter()
        for c in room_cases:
            acts = c["activities"]
            for a in set(acts):
                r_act[a] += 1
            for a, b in zip(acts, acts[1:]):
                r_edge[(a, b)] += 1
            start_count[acts[0]] += 1
            end_count[acts[-1]] += 1
        edge_count.update(r_edge)   # 全局汇总仅用于报告（dfg_edges 等）
        act_count.update(r_act)

        for c in room_cases:
            acts = c["activities"]
            rare_edges = [f"{a}->{b}" for a, b in zip(acts, acts[1:])
                          if r_edge[(a, b)] / rn < min_edge_support]
            rare_acts = [a for a in sorted(set(acts))
                         if r_act[a] / rn < min_activity_support]
            reasons: list[str] = []
            if rare_edges:
                reasons.append("rare_edges")
            if rare_acts:
                reasons.append("rare_activities")
            if not reasons:
                continue
            # 严重度 0-1：越稀有的边/活动越多越严重（供排序与前端展示）
            severity = round(
                min(1.0, (len(rare_edges) + len(rare_acts)) / max(1, len(acts))), 3)
            anomalies.append({
                "case": c["case"], "day": c["day"], "room": c["room"],
                "reasons": reasons, "activities": acts,
                "rare_edges": rare_edges, "rare_activities": rare_acts,
                "severity": severity,
            })
    anomalies.sort(key=lambda x: (-x["severity"], x["case"]))

    # 按房间的高频变体：房间是行为语义的关键维度，供服务层产出带房间的候选规则
    variants_by_room = {
        rm: [{"activities": list(k), "count": v} for k, v in
             Counter(tuple(c["activities"]) for c in room_cases).most_common(5)]
        for rm, room_cases in by_room_cases.items()
    }

    return {
        "ok": True,
        "engine": "pure",
        "cases": n,
        "cases_all": cases_all,
        "reviewed_rooms": sorted({c["room"] for c in cases}),
        "skipped_rooms": sorted(
            r for r, cnt in room_counts.items() if cnt < int(min_cases_per_room or 1)
        ) if int(min_cases_per_room or 1) > 1 else [],
        "min_cases_per_room": int(min_cases_per_room or 1),
        "fitness_rate": round((n - len(anomalies)) / n, 3),
        "dfg_edges": len(edge_count),
        "start_activities": len(start_count), "end_activities": len(end_count),
        "activities": len(act_count),
        "top_variants": [{"activities": list(k), "count": v}
                         for k, v in variants.most_common(8)],
        "variants_by_room": variants_by_room,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }


def mine_process_model(
    events: Sequence[dict],
    tag_of: Callable[[str, str], set],
    primary_tag: Optional[Callable[[set], str]] = None,
    min_case_events: int = 3,
    max_cases: int = 3000,
    min_edge_support: float = 0.1,
    min_activity_support: float = 0.1,
    with_pm4py: bool = True,
    bucket_sec: int = 600,
    skip_rooms: Sequence[str] = ("", "未知", "未分区"),
    min_cases_per_room: int = 4,
) -> dict:
    """从事件流挖掘行为**过程模型**并做一致性检验（异常 = 偏离模型）。

    case = ``房间|日期``（一天一房间一条轨迹），activity = ``tag_on``。
    异常判据 = 使用了稀有直接跟随边 / 稀有活动（可选叠加 pm4py token 回放）。

    :param with_pm4py: 装了 pm4py 时是否额外做 Petri 网 + token 回放增强
        （默认 True；纯 Python 核心已足够产出异常，pm4py 仅补充严格一致性）。
    :param bucket_sec: 轨迹时间桶粒度（秒），见 ``build_process_cases``
    :returns: 变体统计 / DFG / 一致性 / 异常 case（``engine`` 标明实际引擎）
    """
    cases = build_process_cases(events, tag_of, primary_tag=primary_tag,
                               min_case_events=min_case_events, max_cases=max_cases,
                               bucket_sec=bucket_sec, skip_rooms=skip_rooms)
    res = mine_process_model_pure(cases, min_edge_support, min_activity_support,
                                  min_cases_per_room=min_cases_per_room)
    res["bucket_sec"] = int(bucket_sec or 0)
    if not cases or not with_pm4py:
        return res
    if importlib.util.find_spec("pm4py") is None:
        res["pm4py"] = "未安装（纯 Python 路径，异常判定不受影响）"
        return res
    try:
        return _enrich_with_pm4py(res, cases)
    except Exception as exc:  # noqa: BLE001 —— 增强失败不影响纯 Python 结论
        res["pm4py"] = f"增强失败（已忽略）: {exc}"
        return res


def _enrich_with_pm4py(res: dict, cases: Sequence[dict]) -> dict:
    """用 pm4py 补 Petri 网/库所变迁数 + token 回放，追加 ``replay_not_fit`` 判据。"""
    import pm4py  # noqa: F401
    from pm4py.objects.log.obj import EventLog, Trace, Event
    from pm4py.algo.conformance import tokenreplay

    log = EventLog()
    for c in cases:
        tr = Trace()
        tr.attributes["concept:name"] = c["case"]
        for i, a in enumerate(c["activities"]):
            ev = Event()
            ev["concept:name"] = a
            tr.append(ev)
        log.append(tr)

    dfg, start_acts, end_acts = pm4py.discover_dfg(log)
    net, im, fm = pm4py.discover_petri_net_inductive(log)
    diag = tokenreplay.algorithm.apply(log, net, im, fm)

    by_case = {a["case"]: a for a in res.get("anomalies") or []}
    for c, d in zip(cases, diag):
        if bool(d.get("trace_is_fit", True)):
            continue
        entry = by_case.get(c["case"])
        if entry is None:
            entry = {"case": c["case"], "day": c["day"], "room": c["room"],
                     "reasons": [], "activities": c["activities"],
                     "rare_edges": [], "rare_activities": [], "severity": 0.0}
            res.setdefault("anomalies", []).append(entry)
        entry["reasons"].append("replay_not_fit")
        entry["fitness"] = round(float(d.get("trace_fitness") or 0.0), 3)
        entry["missing_tokens"] = d.get("missing_tokens")
        entry["remaining_tokens"] = d.get("remaining_tokens")

    n = len(cases)
    res["engine"] = "pure+pm4py"
    res["places"] = len(net.places)
    res["transitions"] = len(net.transitions)
    res["dfg_edges"] = max(res.get("dfg_edges") or 0, len(dfg))
    res["start_activities"] = len(start_acts)
    res["end_activities"] = len(end_acts)
    res["anomaly_count"] = len(res.get("anomalies") or [])
    res["fitness_rate"] = round((n - res["anomaly_count"]) / n, 3) if n else 0.0
    res["anomalies"] = sorted(res.get("anomalies") or [],
                              key=lambda x: (-x.get("severity") or 0.0, x["case"]))
    return res


# ── 3) 在线异常 + 概念漂移（river） ──────────────────────────────────────────

class OnlineAnomalyDetector:
    """在线行为异常分（Half-Space Trees）+ 概念漂移（ADWIN）。

    与"批处理统计"不同，river 的模型是**增量**的：每条观测来即 ``learn_one``
    更新，天然适配"行为随时间演变"。Half-Space Trees 给的是无监督异常分
    （越大越异常），不需要标注；ADWIN 在事件速率/异常分序列上检测分布突变。
    """

    def __init__(self, n_trees: int = 25, height: int = 8,
                 window_size: int = 250, seed: int = 42, drift_delta: float = 0.002):
        self.available = importlib.util.find_spec("river") is not None
        self._hst: Any = None
        self._adwin: Any = None
        if self.available:
            from river import anomaly, drift
            self._hst = anomaly.HalfSpaceTrees(
                n_trees=n_trees, height=height, window_size=window_size, seed=seed
            )
            self._adwin = drift.ADWIN(delta=drift_delta)

    @staticmethod
    def _features(item: dict) -> dict:
        """观测 → river 特征字典（各标签是否出现 + 事件密度）。"""
        tags = set(item.get("tags") or decode_code(int(item.get("code") or 0)))
        feats = {t: (1 if t in tags else 0) for t in TAG_VOCAB}
        feats["density"] = float(item.get("n") or 0)
        return feats

    def score_stream(self, series: Sequence[dict]) -> dict:
        """顺序喂入观测，返回异常分序列与漂移信息。"""
        if not self.available:
            return {"ok": False, "error": "river 未安装"}
        if not series:
            return {"ok": True, "n": 0, "anomalies": [], "drift": {"detected": False}}
        scores: list[float] = []
        anomalies: list[dict] = []
        drifts: list[dict] = []
        for i, item in enumerate(series):
            feats = self._features(item)
            s = float(self._hst.score_one(feats))
            self._hst.learn_one(feats)
            scores.append(s)
            # ADWIN 监测**行为活跃度**（每桶事件数）的分布突变：
            # 用事件密度比用异常分更直观也更灵敏——"最近活动突然变多/变少"。
            # 注意 river 的 update() 返回 None，漂移须读 drift_detected 属性。
            density = float(item.get("n") or 0)
            self._adwin.update(density)
            if getattr(self._adwin, "drift_detected", False):
                drifts.append({"index": i, "bucket": item.get("bucket"),
                               "density": density, "score": round(s, 4)})
            if i >= 30:  # 前 30 步作热身，避免冷启动误报
                threshold = _quantile(scores[:-1], 0.95)
                if s > max(threshold, 1e-9):
                    anomalies.append({"bucket": item.get("bucket"),
                                      "tags": item.get("tags"),
                                      "score": round(s, 4)})
        return {
            "ok": True,
            "n": len(series),
            "score_mean": round(sum(scores) / len(scores), 4),
            "score_max": round(max(scores), 4),
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[:20],
            "drift": {
                "detected": bool(drifts),
                "count": len(drifts),
                "points": drifts[:10],
            },
        }


def _quantile(values: Sequence[float], q: float) -> float:
    """纯 Python 分位数（避免为一个小函数引入 numpy 依赖）。"""
    xs = sorted(float(v) for v in values)
    if not xs:
        return 0.0
    k = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[k]


def run_algorithm_kernel(
    events: Sequence[dict],
    tag_of: Callable[[str, str], set],
    rule_labels: Sequence[str] | None = None,
    primary_tag: Optional[Callable[[set], str]] = None,
    n_states: int = 4,
    bucket_sec: int = 60,
) -> dict:
    """P1 一站式 spike 入口：HMM 对照 + 过程挖掘 + 在线异常。

    ``rule_labels`` 为空时跳过 HMM 对照（HMM 需参照标签做语义映射），
    仍返回过程挖掘与在线异常结果。
    """
    series = extract_observation_series(events, tag_of, bucket_sec=bucket_sec)
    out: dict = {
        "deps": deps_available(),
        "observations": len(series),
        "bucket_sec": bucket_sec,
    }
    if rule_labels and len(rule_labels) == len(series):
        out["hmm_vs_rules"] = spike_compare_activity(series, rule_labels, n_states=n_states)
    else:
        out["hmm_vs_rules"] = {"ok": False, "error": "缺少对齐的参照标签，跳过对照"}
    out["process_model"] = mine_process_model(events, tag_of, primary_tag=primary_tag)
    out["online_anomaly"] = OnlineAnomalyDetector().score_stream(series)
    return out
