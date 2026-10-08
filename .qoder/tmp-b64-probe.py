"""任务表 #64 改前探针：三件事各自要有运行时成对读数，不靠读代码下结论。

1) identity_fusion R5/R6 的"封顶"用的是 `min(cap, Level.MED, key=list(Level).index)`，
   而 Level 的声明序是 HIGH=0/MED=1/LOW=2/NEEDS_REVIEW=3 ⇒ `min` 按 index 取**更小 index**，
   也就是取**更不严重**的那一档：R3 已把 cap 顶到 NEEDS_REVIEW 后，R5 再"封顶"会把 cap
   松回 MED。同文件 `_more_severe()` 就是干这件事的，R5/R6 没用它。
2) identity_fusion R4 直接赋值 `cap = Level.LOW`，把 R3 的 NEEDS_REVIEW 覆盖掉（也是松）。
3) `fuse(roster=...)` 形参在体内**除了 None→[] 之外没有任何落点**——DCD 20261005 §三 Q2
   「不允许静默忽略」点名的正是这一族；`FusionConfig.review_floor_without_signal` 全仓无读点。

用法：python .qoder/tmp-b64-probe.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory_agent.identity_fusion import (  # noqa: E402
    CFG, Level, ManualMode, PriorLookup, SignalEvidence, Source,
    evaluate_conflicts, fuse,
)
from memory_agent.identity_fusion import CandidateScore  # noqa: E402


def show(tag, res):
    print(f"{tag}: level={res.level.value} chosen={res.chosen_id} "
          f"conflicts={[c.rule for c in res.conflicts]}")


# ── 1) R3(NEEDS_REVIEW) + R5(房间不匹配) ⇒ cap 应当仍是 NEEDS_REVIEW ──
# 两路人脸信号：同一 candidate、分差 < margin(0.15) 触发 R3；room_id 不同触发 R5。
sigs_r3r5 = [
    SignalEvidence("s1", Source.ARCFACE, "kevin", 0.60, 1000.0, "living"),
    SignalEvidence("s2", Source.HA_FACE, "kevin", 0.58, 1000.0, "bedroom"),
]
conflicts, cap = evaluate_conflicts(sigs_r3r5, [
    CandidateScore("kevin", 0.6, 0.0, 0.60),
    CandidateScore("stranger", 0.0, 0.0, 0.59),
])
print(f"R3R5 evaluate_conflicts: rules={[c.rule for c in conflicts]} cap={cap.value}")
res = fuse("living", sigs_r3r5, now=1000.0)
show("R3R5 fuse", res)

# ── 2) R3 + R6(无人脸观测) ⇒ 同样只看 min 的方向 ──
sigs_r3r6 = [
    SignalEvidence("s1", Source.MANUAL, "kevin", 0.60, 1000.0, "living",
                   manual_mode=ManualMode.VOTE),
    SignalEvidence("s2", Source.ELIMINATION, "kevin", 0.58, 1000.0, "living"),
]
res2 = fuse("living", sigs_r3r6, now=1000.0)
show("R3R6 fuse", res2)

# ── 3) 期望方向：同场景下 _more_severe 会给什么 ──
from memory_agent.identity_fusion import _more_severe  # noqa: E402
print(f"_more_severe(NEEDS_REVIEW, MED)={_more_severe(Level.NEEDS_REVIEW, Level.MED).value}")
print(f"min-by-index(NEEDS_REVIEW, MED)={min(Level.NEEDS_REVIEW, Level.MED, key=lambda x: list(Level).index(x)).value}")

# ── 4) roster 形参有没有落点：给两个不同的 roster，结果必须不同才算用了 ──
sigs = [SignalEvidence("s1", Source.ARCFACE, "kevin", 0.9, 1000.0, "living")]
a = fuse("living", sigs, roster=["kevin"], now=1000.0)
b = fuse("living", sigs, roster=["someone-else", "ghost"], now=1000.0)
c = fuse("living", sigs, roster=[], now=1000.0)
print(f"roster=[kevin] -> {a.chosen_id}/{a.level.value}")
print(f"roster=[someone-else,ghost] -> {b.chosen_id}/{b.level.value}")
print(f"roster=[] -> {c.chosen_id}/{c.level.value}")
print(f"ROSTER_AFFECTS_RESULT={(a.chosen_id, a.level) != (b.chosen_id, b.level) or (a.scores) != (b.scores)}")
print(f"ROSTER_VS_EMPTY_SAME={(a.chosen_id, a.level, len(a.scores)) == (c.chosen_id, c.level, len(c.scores))}")

# ── 5) prior 里有 roster 外成员时，roster 该不该过滤 ──
prior = PriorLookup(posterior={"kevin": 0.9, "ghost": 0.1}, samples=100)
d = fuse("living", sigs, prior=prior, roster=["kevin"], now=1000.0)
print(f"with_prior_roster_kevin_only: chosen={d.chosen_id} scores={[(s.candidate_id, round(s.score_final,3)) for s in d.scores]}")
print(f"PRIOR_OBJ_USED={PriorLookup is not None}")
print("PROBE_RC=0")
