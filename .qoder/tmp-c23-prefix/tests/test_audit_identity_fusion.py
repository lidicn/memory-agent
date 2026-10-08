"""A6 P5 补测：identity_fusion 身份融合回归测试。

覆盖 P1-5：未匹配信号（candidate_id=None）不应稀释已匹配候选者的置信度。
6 条测试：3 条锁缺陷（原实现红）+ 3 条对照（原实现绿）。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.identity_fusion import (
    fuse, SignalEvidence, Source, Level, FusionConfig,
)


def _sig(sid, source, cid, conf=0.9, room="living_room", ts=1000.0):
    return SignalEvidence(
        signal_id=sid, source=source, candidate_id=cid,
        confidence=conf, event_ts=ts, room_id=room,
    )


# ── 3 条锁缺陷（P1-5 修复前为红）──────────────────────────────────

def test_unmatched_signal_must_not_dilute_matched_one():
    """未匹配信号不应稀释已匹配候选者的置信度。

    原 bug：total_weight 包含 candidate_id=None 的信号源权重，
    1 路 ARCFACE 匹配 A(0.9) + 1 路 HA_FACE 未匹配 → 置信度从 0.9 被稀释到 0.6。
    修复后：total_weight 只计有匹配的信号源，置信度保持 0.9。
    """
    signals = [
        _sig("s1", Source.ARCFACE, "Alice", conf=0.9),
        _sig("s2", Source.HA_FACE, None, conf=0.8),  # 未匹配/陌生人
    ]
    result = fuse("living_room", signals, roster=["Alice", "Bob"])
    assert result.chosen_id == "Alice", f"应选中 Alice，实得 {result.chosen_id}"
    assert result.confidence >= 0.85, (
        f"未匹配信号不应稀释，期望 >=0.85，实得 {result.confidence}"
    )


def test_unmatched_signal_must_not_invert_winner():
    """未匹配信号不应导致高置信匹配者翻盘为陌生人。

    原 bug：多路未匹配信号累积权重超过匹配信号，chosen_id 变 None。
    修复后：已匹配候选者不受未匹配信号影响。
    """
    signals = [
        _sig("s1", Source.ARCFACE, "Alice", conf=0.9),
        _sig("s2", Source.HA_FACE, None, conf=0.7),
        _sig("s3", Source.ELIMINATION, None, conf=0.6),
    ]
    result = fuse("living_room", signals, roster=["Alice", "Bob"])
    assert result.chosen_id == "Alice", (
        f"未匹配信号不应翻盘，期望 Alice，实得 {result.chosen_id}"
    )


def test_weak_unmatched_signal_must_not_downgrade_level():
    """弱未匹配信号不应把 HIGH 降级为 NEEDS_REVIEW。

    原 bug：稀释后置信度 0.6 < high_threshold(0.8)，等级降为 NEEDS_REVIEW。
    修复后：置信度保持 0.9，等级 HIGH。
    """
    signals = [
        _sig("s1", Source.ARCFACE, "Alice", conf=0.95),
        _sig("s2", Source.HA_FACE, None, conf=0.5),
    ]
    result = fuse("living_room", signals, roster=["Alice", "Bob"])
    assert result.level == Level.HIGH, (
        f"期望 HIGH，实得 {result.level}（confidence={result.confidence}）"
    )


# ── 3 条对照（修复前后均应为绿，证明断言有判别力）──────────────────

def test_single_signal_preserves_confidence():
    """单路匹配信号应保留其置信度。"""
    signals = [_sig("s1", Source.ARCFACE, "Alice", conf=0.9)]
    result = fuse("living_room", signals, roster=["Alice"])
    assert result.chosen_id == "Alice"
    assert abs(result.confidence - 0.9) < 0.01, (
        f"单路信号置信度应≈0.9，实得 {result.confidence}"
    )


def test_two_consistent_signals_stay_high():
    """两路一致信号应保持高置信度。"""
    signals = [
        _sig("s1", Source.ARCFACE, "Alice", conf=0.9),
        _sig("s2", Source.HA_FACE, "Alice", conf=0.85),
    ]
    result = fuse("living_room", signals, roster=["Alice", "Bob"])
    assert result.chosen_id == "Alice"
    assert result.confidence >= 0.8, f"两路一致应高置信，实得 {result.confidence}"


def test_no_signal_returns_none():
    """无信号时应返回陌生人（chosen_id=None）。"""
    result = fuse("living_room", [], roster=["Alice", "Bob"])
    assert result.chosen_id is None, "无信号应返回陌生人"
