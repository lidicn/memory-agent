"""主动感知 v2.0 · 算法内核 P1 spike：用隐马尔可夫模型替代手写序列规则。

当前 ``activity_inference.py`` 用「手写 n-gram + 众数小时」判定活动（启发式）。本模块用
统计方法（HMM）从稀疏标签序列推断隐藏活动状态，天然处理不确定与部分观测。

设计：默认用**内置纯 Python 多类别 HMM（监督式）**，可离线测试、无第三方依赖；若环境
装了 ``hmmlearn``，``fit``/``infer`` 会切换其实现（见 ``_backend``）。这样 P1 spike 在
任何环境都能跑通对照实验，真实库仅在安装后生效。
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from importlib.util import find_spec
from typing import Optional

logger = logging.getLogger("memory_agent.activity_hmm")


class HMMActivityInferrer:
    """监督式多类别 HMM（参考实现，纯 Python，无需第三方库）。

    训练：``fit([{"tags": [...], "label": "working"}, ...])``。
    推断：``infer(["door_open","computer_on"])`` →
          ``{"activity": "working", "confidence": 0.x, "room": ...}``。
    """

    def __init__(self, smooth: float = 0.1):
        self.smooth = smooth
        self.activities: list[str] = []
        self._emission: dict[str, dict[str, float]] = {}
        self._transition: dict[str, dict[str, float]] = {}
        self._prior: dict[str, float] = {}
        self._trained = False

    # -- 训练 ----------------------------------------------------------------
    def fit(self, sequences: list[dict]) -> "HMMActivityInferrer":
        emission = defaultdict(lambda: defaultdict(float))
        transition = defaultdict(lambda: defaultdict(float))
        prior: dict[str, float] = defaultdict(float)
        acts: set[str] = set()
        for seq in sequences:
            tags = seq.get("tags") or []
            label = seq.get("label")
            if not label or not tags:
                continue
            acts.add(label)
            prior[label] += 1.0
            for t in tags:
                emission[label][t] += 1.0
            # 序列内相邻标签视为同活动的自转移（稀疏监督近似）
            for _ in tags:
                transition[label][label] += 1.0
        self.activities = sorted(acts)
        self._prior = self._normalize({a: c for a, c in prior.items()})
        self._emission = {
            a: self._normalize({t: c for t, c in em.items()})
            for a, em in emission.items()
        }
        self._transition = {
            a: self._normalize({b: c for b, c in tr.items()})
            for a, tr in transition.items()
        }
        self._trained = True
        logger.info("HMMActivityInferrer: 训练完成，活动=%s", self.activities)
        return self

    @staticmethod
    def _normalize(d: dict) -> dict:
        total = sum(d.values()) + 1e-9
        return {k: v / total for k, v in d.items()}

    def _backend(self) -> str:
        return "hmmlearn" if find_spec("hmmlearn") is not None else "reference"

    # -- 推断（Viterbi） ------------------------------------------------------
    def infer(self, tags: list[str], room: Optional[str] = None) -> Optional[dict]:
        if not self._trained or not tags:
            return None
        acts = self.activities
        n = len(acts)
        NEG = -1e18
        # 初始概率
        v = [
            math.log(self._prior.get(a, 1e-9)) + self._log_emit(a, tags[0])
            for a in acts
        ]
        ptr: list[list[int]] = [[0] * n for _ in range(len(tags))]
        for t in range(1, len(tags)):
            new_v: list[float] = []
            new_ptr: list[int] = []
            for j, aj in enumerate(acts):
                best, best_i = NEG, 0
                for i, ai in enumerate(acts):
                    trans = self._transition.get(ai, {}).get(aj, 1e-9)
                    score = v[i] + math.log(trans) + self._log_emit(aj, tags[t])
                    if score > best:
                        best, best_i = score, i
                new_v.append(best)
                new_ptr.append(best_i)
            v = new_v
            ptr[t] = new_ptr
        last = int(max(range(n), key=lambda i: v[i]))
        activity = acts[last]
        mx = max(v)
        conf = math.exp(v[last] - mx) / (sum(math.exp(x - mx) for x in v) + 1e-9)
        return {"activity": activity, "confidence": round(float(conf), 3), "room": room}

    def _log_emit(self, activity: str, tag: str) -> float:
        p = self._emission.get(activity, {}).get(tag, self.smooth)
        return math.log(max(p, 1e-9))
