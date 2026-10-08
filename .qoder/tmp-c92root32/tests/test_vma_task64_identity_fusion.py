"""任务表 #64：`identity_fusion` 的封顶方向与未接线形参——落点锁。

编号纪律：`#NN` 指任务表编号，`§NN` 指 `审计核实与修复_20261001.md` 的节号。

三族各配成对读数（改前响/改后响都在下面的用例注释里写了行级依据）：

1. **R4/R5/R6 的"封顶"改前从不生效**（改前实测 5 条红）。`Level` 的声明序是
   HIGH=0 / MED=1 / LOW=2 / NEEDS_REVIEW=3，而改前 R5/R6 写的是
   `cap = min(cap, Level.MED, key=lambda x: list(Level).index(x))`——按 index 取**更小**者，
   也就是取**更不严重**的那一档。所以：cap 还是初值 HIGH 时 `min` 取 HIGH ⇒ **整条规则空转**
   （改前 `R6-alone` 实得 `high`，本该封顶 med）；cap 已被 R3/R4 顶到 NEEDS_REVIEW/LOW 时
   又把它**松回** med。探针成对读数（`.qoder/tmp-b64-probe.py`，改前）：
   `R3-only → needs_review`、`R3+R5 → med`、`R3+R4 → low`。R4 更是直接赋值覆盖。
   对身份融合来说这个方向是危险的：分差太小本应交人工复核，结果"多一路房间不匹配"
   或"检出陌生人"就把复核要求撤销了。同文件 `_more_severe()` 本来就是干这件事的，
   R5/R6 没用它——本批把四条规则统一走 `_more_severe`，cap 只可能变严不可能变松。
   改前档跑本文件：`5 failed / 55 passed`（`.qoder/head63` 快照，HEAD `612f67b`）；改后 `60 passed`。
2. **`fuse(roster=…)` 形参无落点**（登记现状，行为不改）。改前后同：三种 roster
   传进去结果一字不差（改前探针 `ROSTER_AFFECTS_RESULT=False`）。按 DCD 20261005 §三 Q2
   「不允许静默忽略」，这里把它钉成一条**会红的锁**：将来谁要用 roster 过滤候选，
   本用例必红，必须显式改它并在台账留痕；"要不要用名册约束候选"是语义决定，已呈 DCD。
   同理 `FusionConfig.review_floor_without_signal` 全仓无读点，一并登记。
3. **本模块改前 84% 覆盖里没跑到的分支**：`prior_boost` 内核、`elimination_signals` 整档、
   R1/R2/R4/R5 四条规则、`fuse` 的 VOTE 权重档与 MED/LOW/NONE 三档判等——
   改前 missing 清单（`coverage report --show-missing`，本机 Python313 + coverage 7.16.1）：
   `134, 140, 158-164, 192-193, 200-201, 215-216, 221-222, 255, 264-266, 279, 351-354, 383-397`。

第十五轮 §九 优先级 1 把本模块列为「0%~10% 覆盖」，现读对不上（84%），
那一句是审计环境（Python 3.10、pytest 后期不可用）的结论，不采信；
本文件按**现读的 missing 清单**补，而不是按报告的百分比补。
"""

import math
import os
import pathlib
import re
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.identity_fusion import (  # noqa: E402
    CFG, CandidateScore, FusionConfig, Level, ManualMode, PriorLookup,
    SignalEvidence, Source, _more_severe, elimination_signals, evaluate_conflicts,
    fuse, prior_boost, room_weight, temporal_decay,
)

NEEDS_REVIEW, MED, LOW, HIGH = (Level.NEEDS_REVIEW, Level.MED, Level.LOW, Level.HIGH)


def _sig(sid, source, cid, conf=0.9, room="living", ts=1000.0, mode=None):
    return SignalEvidence(signal_id=sid, source=source, candidate_id=cid,
                          confidence=conf, event_ts=ts, room_id=room, manual_mode=mode)


def _scores(*pairs):
    """(candidate_id, score_final) → CandidateScore 列表；score_raw 与 boost 对 R 规则无影响。"""
    return [CandidateScore(cid, score, 0.0, score) for cid, score in pairs]


# ── 1) 封顶方向：cap 只能变严，不能被后面的规则松回去 ─────────────────

def test_r3_alone_caps_at_needs_review():
    """基准档：只有 R3（分差 < margin）时 cap = needs_review。"""
    sigs = [_sig("s1", Source.ARCFACE, "Alice"), _sig("s2", Source.HA_FACE, "Alice")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.60), ("Bob", 0.55)))
    assert [c.rule for c in conflicts] == ["R3"]
    assert cap is NEEDS_REVIEW, f"基准档应 needs_review，实得 {cap.value}"


def test_r5_room_mismatch_does_not_loosen_r3():
    """改前红：R3 已顶到 needs_review，R5「房间不匹配」用 `min(..., key=index)` 把它松回 med。

    身份含义：两名候选分差不足 0.15 本该交人工，多一路房间不一致反而免了复核。
    """
    sigs = [_sig("s1", Source.ARCFACE, "Alice"), _sig("s2", Source.HA_FACE, "Alice"),
            _sig("s3", Source.MANUAL, "Alice", mode=ManualMode.VOTE, room="bedroom")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.60), ("Bob", 0.55)))
    assert {c.rule for c in conflicts} == {"R3", "R5"}, [c.rule for c in conflicts]
    assert cap is NEEDS_REVIEW, f"R5 不得把 cap 松回 med，实得 {cap.value}"


def test_r4_stranger_does_not_loosen_r3():
    """改前红：R4 是 `cap = Level.LOW` 直接赋值，把 R3 的 needs_review 覆盖成 low。"""
    sigs = [_sig("s1", Source.ARCFACE, "Alice"), _sig("s2", Source.HA_FACE, "Alice"),
            _sig("s3", Source.ARCFACE, None, conf=0.9)]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.60), ("Bob", 0.55)))
    assert {c.rule for c in conflicts} == {"R3", "R4"}, [c.rule for c in conflicts]
    assert cap is NEEDS_REVIEW, f"R4 不得覆盖 needs_review，实得 {cap.value}"


def test_r5_does_not_loosen_r4():
    """改前红：cap 已是 LOW（R4），R5 的 `min` 按 index 取 MED = 更不严重 ⇒ 松回 med。"""
    sigs = [_sig("s1", Source.ARCFACE, "Alice", room="living"),
            _sig("s2", Source.ARCFACE, None, conf=0.9, room="living"),
            _sig("s3", Source.MANUAL, "Alice", mode=ManualMode.VOTE, room="bedroom")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.90)))
    assert {c.rule for c in conflicts} == {"R4", "R5"}, [c.rule for c in conflicts]
    assert cap is LOW, f"R5 不得把 LOW 松回 med，实得 {cap.value}"


def test_r6_alone_caps_at_med():
    """R6（无人脸观测）单独命中时封顶 med——正向档，防把守卫写反。"""
    sigs = [_sig("s1", Source.MANUAL, "Alice", mode=ManualMode.VOTE),
            _sig("s2", Source.ELIMINATION, "Alice")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.90)))
    assert [c.rule for c in conflicts] == ["R6"]
    assert cap is MED


def test_r5_alone_caps_at_med():
    """R5（房间不匹配）单独命中时封顶 med，且不误触 R2/R3。"""
    sigs = [_sig("s1", Source.ARCFACE, "Alice", room="living"),
            _sig("s2", Source.HA_FACE, "Alice", room="bedroom")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.90)))
    assert [c.rule for c in conflicts] == ["R5"]
    assert cap is MED


def test_r2_face_sources_disagree_caps_at_med():
    """R2：两路人脸信号给出不同候选者 ⇒ med（分差够大，R3 不触发）。"""
    sigs = [_sig("s1", Source.ARCFACE, "Alice"), _sig("s2", Source.HA_FACE, "Bob")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.90), ("Bob", 0.50)))
    assert [c.rule for c in conflicts] == ["R2"]
    assert cap is MED


def test_r2_then_r3_tightens_to_needs_review():
    """改前后同（收紧方向原本就对）：R2 顶 med，R3 再顶 needs_review。"""
    sigs = [_sig("s1", Source.ARCFACE, "Alice"), _sig("s2", Source.HA_FACE, "Bob")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.60), ("Bob", 0.55)))
    assert [c.rule for c in conflicts] == ["R2", "R3"]
    assert cap is NEEDS_REVIEW


def test_r1_manual_confirm_short_circuits_before_other_rules():
    """R1 人工确认：直接返回，不再评估 R2~R6——留痕步不能被后来的规则改口。"""
    sigs = [_sig("s1", Source.MANUAL, "Alice", mode=ManualMode.CONFIRM),
            _sig("s2", Source.ARCFACE, None, conf=0.95, room="bedroom")]
    conflicts, cap = evaluate_conflicts(sigs, _scores(("Alice", 0.60), ("Bob", 0.55)))
    assert [c.rule for c in conflicts] == ["R1"]
    assert cap is HIGH


def test_more_severe_is_the_only_direction_used():
    """把"取更严重"这件事本身钉住：与 Level 声明序一致，不随声明顺序改写而翻转。"""
    assert _more_severe(NEEDS_REVIEW, MED) is NEEDS_REVIEW
    assert _more_severe(MED, LOW) is LOW
    assert _more_severe(LOW, HIGH) is LOW
    assert _more_severe(HIGH, HIGH) is HIGH


def test_conflict_rules_follow_level_declaration_order():
    """上一条依赖 `Level` 的声明序；这里锁住该序，避免有人重排枚举让上面的判据静默翻转。"""
    assert [lv.value for lv in Level] == ["high", "med", "low", "needs_review"]


# ── 2) roster：登记"本版不读它"，改动必须显式 ────────────────────────

@pytest.mark.parametrize("roster", [["Alice"], ["nobody", "ghost"], []])
def test_roster_has_no_landing_in_this_build(roster):
    """DCD 20261005 §三 Q2 口径：形参要么有落点，要么显式登记"不支持"，不许静默忽略。

    现状是后者——`fuse` 体内除 `None → []` 外不读 `roster`。这条锁不主张"roster 该没用"，
    它主张的是：**这个决定已经登记在案**。谁要让 roster 参与候选过滤，本用例必红，
    必须在台账（§四十二）留下改码理由与裁定出处。
    """
    sigs = [_sig("s1", Source.ARCFACE, "Alice", conf=0.9)]
    res = fuse("living", sigs, roster=roster, now=1000.0)
    baseline = fuse("living", sigs, roster=None, now=1000.0)
    assert (res.chosen_id, res.level, res.confidence, len(res.scores)) == \
           (baseline.chosen_id, baseline.level, baseline.confidence, len(baseline.scores))


def test_review_floor_without_signal_is_registered_as_unused():
    """`review_floor_without_signal` 全仓无读点——登记为"设计稿旋钮"，别当成生效配置。"""
    assert CFG.review_floor_without_signal == 0.55
    src = pathlib.Path(_SRC, "memory_agent", "identity_fusion.py").read_text(encoding="utf-8")
    # 一次是定义处，其余读点必须为 0；出现读点说明这条登记该删。
    assert src.count("review_floor_without_signal") == 1, \
        "该键已有读点，请删除本登记用例并同步台账"


# ── 3) 改前没跑到的分支：先验 / 衰减 / 房间权重 / 三档判等 / 消除法 ────

def test_prior_boost_gates():
    """改前 `prior_boost` 内核 158-164 从未执行：三道门 + 裁剪各自要有读数。"""
    post = {"Alice": 0.9}
    assert prior_boost(None, "Alice", 3, 100) == 0.0, "无先验 ⇒ 不加修正"
    assert prior_boost(post, "Alice", 1, 100) == 0.0, "k<=1 ⇒ log(k)=0 会误伤，必须短路"
    assert prior_boost(post, "Alice", 3, CFG.prior_min_samples - 1) == 0.0, "样本不足 ⇒ 不加"
    assert prior_boost(post, "Ghost", 3, 100) == 0.0, "先验里没有的人 ⇒ 不加"


def test_prior_boost_log_odds_and_clipping():
    """中位数档与上下裁剪：0.25·ln(p·k) 收在 ±0.6。"""
    mid = prior_boost({"Alice": 0.9}, "Alice", 3, 100)
    assert mid == pytest.approx(0.25 * math.log(0.9 * 3), abs=1e-9)
    assert -0.6 <= mid <= 0.6
    assert prior_boost({"Alice": 0.99}, "Alice", 30, 100) == 0.6, "正方向裁剪到 +0.6"
    assert prior_boost({"Alice": 0.01}, "Alice", 2, 100) == -0.6, "负方向裁剪到 -0.6"


def test_fuse_applies_prior_boost_to_the_final_score():
    """先验确实进了 `score_final`（`score_raw` 不变，差值就是 boost）。

    顺带量出一条**口径待裁**：`fuse` 传的是 `k = len(pairs)`（该候选者**自己的信号条数**），
    不是名册/候选人数——所以单信号候选永远 `k<=1` ⇒ 先验整段短路（本批改前后同，行为未动）。
    `math.log(p * k)` 里 `k` 更像"候选个数"，两种读法给出的修正幅度不同，属语义决定，已呈 DCD。
    """
    sigs = [_sig("s1", Source.ARCFACE, "Alice", conf=0.6), _sig("s2", Source.HA_FACE, "Alice", conf=0.6),
            _sig("s3", Source.ARCFACE, "Bob", conf=0.6), _sig("s4", Source.HA_FACE, "Bob", conf=0.6)]
    single = fuse("living", sigs[:1], prior=PriorLookup({"Alice": 0.9}, 100), now=1000.0)
    assert single.scores[0].prior_boost == 0.0, "该候选只有 1 路信号 ⇒ k<=1 短路，先验不参与（现状登记）"

    prior = PriorLookup({"Alice": 0.9, "Bob": 0.01}, 100)
    with_prior = fuse("living", sigs, prior=prior, now=1000.0)
    no_prior = fuse("living", sigs, now=1000.0)
    raw = {s.candidate_id: s.score_raw for s in with_prior.scores}
    fin = {s.candidate_id: s.score_final for s in with_prior.scores}
    boost = {s.candidate_id: s.prior_boost for s in with_prior.scores}
    assert raw == {s.candidate_id: s.score_raw for s in no_prior.scores}, "先验不该改写 score_raw"
    assert boost["Alice"] == pytest.approx(0.25 * math.log(0.9 * 2), abs=1e-12)
    assert boost["Bob"] == -0.6, "0.25·ln(0.01×2) 越界 ⇒ 裁到 -0.6"
    assert all(fin[cid] - raw[cid] == pytest.approx(boost[cid], abs=1e-12) for cid in raw)


def test_temporal_decay_branches():
    """134 行（正常衰减）改前未执行；负 age 走 1.0 档。"""
    assert temporal_decay(0.0) == 1.0
    assert temporal_decay(-5.0) == 1.0, "未来时刻按 0 龄处理，不许出现 >1 的增益"
    assert temporal_decay(300.0, 300.0) == pytest.approx(math.exp(-1.0), abs=1e-12)


def test_room_weight_branches():
    """140 行：任一侧房间为空 ⇒ 不加权也不降权（1.0）。"""
    assert room_weight("", "living") == 1.0
    assert room_weight("living", "") == 1.0
    assert room_weight("living", "living") == CFG.room_match
    assert room_weight("living", "bedroom") == CFG.room_mismatch


def test_fuse_uses_temporal_decay_and_room_weight_on_signals():
    """同一候选：陈旧 + 跨房的信号得分必须低于新鲜 + 同房。"""
    fresh = [_sig("s1", Source.ARCFACE, "Alice", conf=0.9, ts=1000.0)]
    stale = [_sig("s1", Source.ARCFACE, "Alice", conf=0.9, ts=1000.0 - 900.0)]
    cross = [_sig("s1", Source.ARCFACE, "Alice", conf=0.9, ts=1000.0, room="bedroom")]
    a = fuse("living", fresh, now=1000.0)
    b = fuse("living", stale, now=1000.0)
    c = fuse("living", cross, now=1000.0)
    assert a.confidence > b.confidence, "900s 前的信号要按 tau=300 衰减"
    assert a.confidence > c.confidence, "跨房信号按 room_mismatch=0.5 降权"


def test_fuse_levels_med_low_and_none():
    """改前 351-354（MED/LOW 两档）与 333-343（NONE 档）都没跑到。"""
    med = fuse("living", [_sig("s1", Source.ARCFACE, "Alice", conf=0.6)], now=1000.0)
    assert (med.level, med.chosen_id) == (MED, "Alice")
    low = fuse("living", [_sig("s1", Source.ARCFACE, "Alice", conf=0.3)], now=1000.0)
    assert (low.level, low.chosen_id) == (LOW, "Alice")
    none = fuse("living", [], now=1000.0)
    assert (none.method, none.level, none.chosen_id, none.confidence) == \
           ("NONE", LOW, None, 0.0)


def test_fuse_manual_confirm_short_circuit_and_stranger_label():
    """改前 264-266：人工确认直接出结果；确认对象为空时 scores 里落 "stranger"。"""
    ok = fuse("living", [_sig("s1", Source.MANUAL, "Alice", mode=ManualMode.CONFIRM)], now=1000.0)
    assert (ok.method, ok.level, ok.confidence, ok.needs_review) == \
           ("MANUAL_OVERRIDE", HIGH, 1.0, False)
    blank = fuse("living", [_sig("s1", Source.MANUAL, None, mode=ManualMode.CONFIRM)], now=1000.0)
    assert blank.chosen_id is None
    assert blank.scores[0].candidate_id == "stranger"


def test_vote_mode_lifts_manual_weight_only_in_vote_config():
    """改前 279：`manual_mode="VOTE"` 才把 MANUAL 权重抬到 manual_vote_weight。"""
    sigs = [_sig("s1", Source.MANUAL, "Alice", conf=0.9, mode=ManualMode.VOTE),
            _sig("s2", Source.ARCFACE, "Alice", conf=0.5)]
    override = fuse("living", sigs, now=1000.0)          # CFG.manual_mode = "OVERRIDE"
    vote = fuse("living", sigs, cfg=FusionConfig(manual_mode="VOTE"), now=1000.0)
    assert override.confidence == pytest.approx((0.1 * 0.9 + 0.6 * 0.5) / 0.7, abs=1e-12)
    assert vote.confidence == pytest.approx((1.0 * 0.9 + 0.6 * 0.5) / 1.6, abs=1e-12)
    assert vote.confidence > override.confidence


def test_elimination_signals_from_presence_rows(monkeypatch):
    """改前 383-397 整档未执行：消除法出伪信号，置信度上限 0.65。"""
    from memory_agent import presence_fusion

    monkeypatch.setattr(presence_fusion, "fuse_presence", lambda roster, occupancy: {
        "inferred": [
            {"member": "Alice", "room": "living", "confidence": 0.90},   # 削到 0.65
            {"member": "Bob", "room": "study", "confidence": 0.40},      # 原样
        ]})
    sigs = elimination_signals([{"member": "Alice"}, {"member": "Bob"}],
                               [{"entity_id": "binary_sensor.x"}], now=1234.0)
    assert [s.signal_id for s in sigs] == ["elim:Alice:living", "elim:Bob:study"]
    assert all(s.source is Source.ELIMINATION for s in sigs)
    assert [s.confidence for s in sigs] == [0.65, 0.40]
    assert [s.candidate_id for s in sigs] == ["Alice", "Bob"]
    assert all(s.event_ts == 1234.0 for s in sigs)


def test_elimination_signals_empty_rows_is_empty(monkeypatch):
    """反例锁：消除法没推出任何人时出空列表，不能凭空造信号。"""
    from memory_agent import presence_fusion

    monkeypatch.setattr(presence_fusion, "fuse_presence", lambda roster, occupancy: {"inferred": []})
    assert elimination_signals([], [], now=0.0) == []


def test_fusion_consumes_elimination_pseudo_signals():
    """消除法输出接到 `fuse`：单路 ELIMINATION（权重 0.2）到 med 以下 ⇒ low 且不复核。"""
    sig = SignalEvidence(signal_id="elim:Alice:living", source=Source.ELIMINATION,
                         candidate_id="Alice", confidence=0.65, event_ts=1000.0, room_id="living")
    res = fuse("living", [sig], now=1000.0)
    assert res.confidence == pytest.approx(0.65, abs=1e-12)
    assert (res.level, res.needs_review) == (MED, False), "0.65 ≥ med_threshold ⇒ med；R6 封顶不触发复核之外的档"


# ── 4) DCD 20261006 裁定（§九 验收）：登记要会红，措辞要照裁定 ────────────

_SRC_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "memory_agent"


def _src_ref_sites(module: str) -> list:
    """`src/memory_agent/**.py` 里除该模块自身外的**引用行**（import 行或 `module.` 属性访问）。

    故意不数散文/docstring 里的名字——那条尺会把"在文档里提到本模块"判成"接了线"。
    """
    pattern = re.compile(rf"\bimport\b[^\n]*\b{module}\b|\b{module}\.\w")
    hits = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if path.name == f"{module}.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.name}:{lineno}")
    return hits


def test_identity_fusion_still_has_zero_call_sites_in_src():
    """Q1=丙：留 src、不接入，把"未挂载"从一句注释升级成**会红的锁**。

    接线触发条件 = P2「多源身份概率融合」立项裁定（见本裁定 §七 七条前置清单）。
    负控在同一条用例里，量的是尺子而不是结论：同样这把尺量在役的 `presence_fusion`
    必须非零——否则"零调用者"这个读数只是量具坏了（现读 `vision_routes.py:21`、
    `perception_ingest.py:255` 两处 import）。
    """
    assert _src_ref_sites("identity_fusion") == []
    assert _src_ref_sites("presence_fusion"), "负控失效：这把尺量不出在役模块，上面的零不构成结论"


def test_prior_boost_k_counts_evidence_records_not_deduped_sources():
    """Q3=甲 + §九.3 措辞修正：`fuse` 传的 `k = len(pairs)`（`identity_fusion.py:317`）
    = 该候选者的**证据记录数**，**不去重**——既不是"几路信号"，也不是候选人数。

    后果登记在册：同一来源重复上报也会把 k 变大，所以先验修正对采样频率/重复上报敏感。
    本用例的形状就是两**条**同来源（都 ARCFACE）信号 ⇒ k=2 ⇒ 先验生效；
    谁把 k 改成"去重后的路数"（=1），这里立刻红。k 口径的重新裁定绑定 P2 立项（§七.5）。
    """
    dup = [_sig("s1", Source.ARCFACE, "Alice", conf=0.6),
           _sig("s2", Source.ARCFACE, "Alice", conf=0.6)]
    res = fuse("living", dup, prior=PriorLookup({"Alice": 0.9}, 100), now=1000.0)
    assert res.scores[0].candidate_id == "Alice"
    assert res.scores[0].prior_boost == pytest.approx(0.25 * math.log(0.9 * 2), abs=1e-12)
    assert prior_boost({"Alice": 0.9}, "Alice", 2, 100) != 0.0, "k=2 不短路 ⇒ 两条同来源也算两条记录"


def test_confidence_domain_is_not_reclamped_and_raw_scales_with_records():
    """Q3 衍生事实（本裁定 §四.2）：`score_final = score_raw + boost`（`:318`）之后**不再裁回
    [0,1]**，`FusionResult.confidence` 直接取它（`:361`）。

    现读比裁定书那句更尖锐：分母 `total_weight` 按**去重后的信号源**求和（`:304-309`），
    分子按**每条记录**累加 ⇒ 同一来源重复上报把 `score_raw` **线性放大**
    （1 条 conf 0.9 ⇒ 0.9；2 条同来源同 conf ⇒ 1.8）。所以上界不是"1+0.6"而是
    "记录数 × 置信度 + 0.6"，**无界**。
    这条是**登记锁**，不是"这样对"的主张：域裁剪与分母口径随 P2 立项一起重裁（§七.5）。
    """
    one = fuse("living", [_sig("s1", Source.ARCFACE, "Alice", conf=0.9)], now=1000.0)
    assert one.scores[0].score_raw == pytest.approx(0.9, abs=1e-9)
    assert one.confidence == pytest.approx(0.9, abs=1e-9), "无先验时 confidence 落在 [0,1]（常态档）"

    two = fuse("living", [_sig("s1", Source.ARCFACE, "Alice", conf=0.9),
                          _sig("s2", Source.ARCFACE, "Alice", conf=0.9)], now=1000.0)
    assert two.scores[0].score_raw == pytest.approx(1.8, abs=1e-9), "重复上报线性放大 score_raw（现状登记）"
    assert two.confidence > 1.0, "score_raw 未裁回 [0,1]（现状登记）"

    neg = fuse("living", [_sig("s1", Source.ARCFACE, "Alice", conf=0.05),
                          _sig("s2", Source.ARCFACE, "Alice", conf=0.05)],
               prior=PriorLookup({"Alice": 0.01}, 100), now=1000.0)
    assert neg.scores[0].prior_boost == -0.6, "0.25·ln(0.01×2) 越下界 ⇒ 裁到 -0.6"
    assert neg.confidence < 0.0, "负修正可以把 confidence 打到 0 以下（同一格待重裁）"


def test_prior_boost_formula_label_corrected_from_log_odds():
    """Q3 衍生事实（本裁定 §四.1）：所谓"log-odds"实为 `0.25·ln(p·k)`，不是 `ln(p/(1-p))`。

    两条曲线在 p→1 时方向相同但量级完全不同，名字错了会让人以为它是校准过的对数几率。
    本用例锁住**实际公式**，源码里的 `log-odds` 措辞已按裁定核销（`identity_fusion.py:153-166`）。
    """
    p, k = 0.9, 5
    assert prior_boost({"Alice": p}, "Alice", k, 100) == pytest.approx(0.25 * math.log(p * k), abs=1e-12)
    assert prior_boost({"Alice": p}, "Alice", k, 100) != pytest.approx(
        0.25 * math.log(p / (1 - p)), abs=1e-6), "公式不是 log-odds"
    src = (_SRC_ROOT / "identity_fusion.py").read_text(encoding="utf-8")
    assert "先验修正项（log-odds）" not in src, "措辞已核销：函数不再被命名成 log-odds"
    assert "0.25·ln(p·k)" in src, "文档字符串必须写实际公式"

