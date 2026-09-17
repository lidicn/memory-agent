"""P2 概率实体解析（Fellegi-Sunter）单测。

关注三件事：
1. 比较向量的等级判定（名称强相似/部分相似、设备号段、room/domain）
2. 概率与三档分档（match / **review**=不确定性 / non）
3. 接入 identity 后：已验证的合并**不回退**，灰区配对进 needs_review 而非被吞掉
"""
import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.entity_resolution import (  # noqa: E402
    AGREE,
    DISAGREE,
    MISSING,
    PARTIAL,
    ProbabilisticMatcher,
    name_level,
    stem_level,
)
from memory_agent.identity import IdentityService  # noqa: E402
from memory_agent.store import Store  # noqa: E402


def _rec(entity_id, name, room="客厅", domain="light"):
    return {"entity_id": entity_id, "name": name, "room": room, "domain": domain}


# ── 比较向量等级 ────────────────────────────────────────────────────────────

def test_name_level_agree_covers_existing_hard_rules():
    """历史硬规则（相等 / ≥0.85 / 公共前缀≥6）统一判为强相似 → 升级不回退。"""
    assert name_level("客厅电视", "客厅电视") == AGREE
    assert name_level("客厅电视", "客厅电视A") == AGREE          # ratio 0.889
    # 「lidicn的电视电视」与「lidicn的电视播放控制」：公共前缀远 ≥6
    assert name_level("lidicn的电视电视", "lidicn的电视播放控制") == AGREE
    assert name_level("书房台灯", "书房灯带") == PARTIAL          # ratio 0.75
    assert name_level("客厅电视", "客厅音响") == DISAGREE
    assert name_level("", "客厅") == MISSING


def test_stem_level_uses_device_number_as_strong_signal():
    """同一硬件被双集成接入时，entity_id 会共享设备号段。"""
    assert stem_level("media_player.chuangmi_cn_1072229835_051a01",
                      "light.chuangmi_cn_1072229835_051a01") == AGREE
    assert stem_level("light.study_desk", "light.study_strip") == PARTIAL
    assert stem_level("light.kitchen_a", "switch.other_x") == DISAGREE


def test_blocked_when_room_or_domain_differs():
    m = ProbabilisticMatcher()
    lv = m.levels(_rec("light.a", "电视", "客厅"),
                  _rec("light.b", "电视", "卧室"))
    assert m.blocked(lv) is True
    assert m.probability(lv) == 0.0      # 沿用"跨 room 不合并"

    diff_domain = m.levels(_rec("light.a", "电视", "客厅", domain="light"),
                           _rec("switch.b", "电视", "客厅", domain="switch"))
    assert m.blocked(diff_domain) is True


# ── 概率与分档 ──────────────────────────────────────────────────────────────

def test_same_device_gets_match_band():
    m = ProbabilisticMatcher()
    res = m.compare(_rec("media_player.live_9", "客厅电视"),
                    _rec("media_player.live_9b", "客厅电视"))
    assert res["band"] == "match"
    assert res["probability"] >= 0.95
    assert res["blocked"] is False


def test_different_devices_get_non_band():
    m = ProbabilisticMatcher()
    res = m.compare(_rec("light.living", "客厅灯", domain="light"),
                    _rec("light.living2", "客厅电视", domain="light"))
    assert res["band"] == "non"
    assert res["probability"] < 0.5


def test_grey_zone_is_review_not_silently_dropped():
    """旧启发式只有"合/不合"两种结果；灰区配对现在会被显式标为待复核。"""
    m = ProbabilisticMatcher()
    res = m.compare(_rec("light.study_desk", "书房台灯", "书房"),
                    _rec("light.study_strip", "书房灯带", "书房"))
    assert res["levels"]["name"] == PARTIAL
    assert res["band"] == "review"
    assert m.review_threshold <= res["probability"] < m.match_threshold


def test_missing_field_does_not_count_as_disagreement():
    """缺失字段不应被当成"不一致"（否则无 room 信息的实体会被系统性低估）。"""
    with_room = m = ProbabilisticMatcher()
    a = {"entity_id": "light.x", "name": "客厅灯", "room": "客厅", "domain": "light"}
    b = {"entity_id": "light.y", "name": "客厅灯", "room": "", "domain": "light"}
    lv = m.levels(a, b)
    assert lv["room"] == MISSING
    assert m.blocked(lv) is False
    assert m.probability(lv) > 0.5


def test_em_fit_runs_and_serialization_roundtrip():
    m = ProbabilisticMatcher()
    pairs = [(_rec(f"light.a{i}", "客厅灯"), _rec(f"light.b{i}", "客厅灯"))
             for i in range(30)]
    fit = m.fit(pairs, iterations=5)
    assert fit["ok"] is True and fit["pairs"] == 30

    blob = m.to_dict()
    m2 = ProbabilisticMatcher.from_dict(blob)
    assert m2.priors.keys() == m.priors.keys()
    assert abs(m2.lamb - m.lamb) < 1e-9
    res1 = m.compare(_rec("light.a", "客厅电视"), _rec("light.b", "客厅电视"))
    res2 = m2.compare(_rec("light.a", "客厅电视"), _rec("light.b", "客厅电视"))
    assert res1["probability"] == res2["probability"]


def test_fit_rejects_too_few_pairs():
    m = ProbabilisticMatcher()
    fit = m.fit([(_rec("light.a", "x"), _rec("light.b", "x"))], min_pairs=20)
    assert fit["ok"] is False


# ── 接入 identity ───────────────────────────────────────────────────────────

class _FakeHA:
    def __init__(self, catalog):
        self._catalog = catalog

    def discover_entities(self):
        return self._catalog


def _rooms(*entities):
    rooms: dict = {}
    for entity_id, name, room, state in entities:
        rooms.setdefault(room, {"entities": {}})
        rooms[room]["entities"][entity_id] = {
            "name": name, "domain": entity_id.split(".")[0], "state": state,
        }
    return {"ok": True, "rooms": rooms, "total_entities": len(entities)}


@pytest.fixture
def env():
    tmp = tempfile.mkdtemp(prefix="ma_res_")
    store = Store(os.path.join(tmp, "test.db"), tz_offset_hours=0.0)
    store.init_schema()
    return store, IdentityService(store, None, 0.0)


def _reconciler(service, ha, **kw):
    from memory_agent.identity import IdentityReconciler

    return IdentityReconciler(service, ha_getter=lambda: ha, tz_offset_hours=0.0, **kw)


def test_reconcile_still_merges_verified_pair(env):
    """已验证的合并（同 room 同 domain 同名称族）不因概率化而回退。"""
    store, service = env
    ha = _FakeHA(_rooms(
        ("media_player.live_9", "lidicn的电视电视", "客厅", "on"),
        ("media_player.live_9_pc", "lidicn的电视播放控制", "客厅", "on"),
    ))
    res = _reconciler(service, ha).reconcile()
    assert res["ok"] is True
    assert res["merged"] >= 1
    assert res["devices"] == 1


def test_reconcile_reports_grey_zone_as_needs_review(env):
    """灰区配对不合并，但暴露在 needs_review 里（旧行为：静默丢弃）。"""
    store, service = env
    ha = _FakeHA(_rooms(
        ("light.study_desk", "书房台灯", "书房", "on"),
        ("light.study_strip", "书房灯带", "书房", "on"),
    ))
    rec = _reconciler(service, ha)
    res = rec.reconcile()
    assert res["ok"] is True
    assert res["merged"] == 0
    assert res["devices"] == 2
    assert res["needs_review_count"] >= 1
    item = res["needs_review"][0]
    assert item["name"] in ("书房台灯", "书房灯带")
    assert item["probability"] >= rec.matcher.review_threshold
