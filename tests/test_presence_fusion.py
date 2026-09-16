"""在场融合 + 名册消除法身份消歧（v0.7.5 轻量版）测试。

纯函数 ``fuse_presence`` 用确定性规则做「未识别占位 → 缺席成员」推断，
不依赖 HA / LLM / 网络。另含 ``store.recent_occupancy`` 的存储层冒烟。
"""

import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.presence_fusion import fuse_presence  # noqa: E402
from memory_agent.store import Store  # noqa: E402


# ── 名册 fixtures ───────────────────────────────────────────────────────────

def _roster():
    return [
        {"name": "lidicn", "rooms": ["客厅"], "appearance_json": {}},
        {"name": "Emily", "rooms": ["客厅"], "appearance_json": {}},
        # Kevin 关联书房 → 消除法归位时先验命中，置信 0.9
        {"name": "Kevin", "rooms": ["书房"], "appearance_json": {}},
    ]


# ── 核心：消除法推断 Kevin 在书房 ──────────────────────────────────────────

def test_elimination_infers_kevin_in_study():
    # 客厅已确认 lidicn+Emily；书房 1 个未识别（count=1, persons=[]）
    occupancy = [
        {"room": "客厅", "persons": ["lidicn", "Emily"], "count": 2},
        {"room": "书房", "persons": [], "count": 1},
    ]
    res = fuse_presence(_roster(), occupancy)
    assert res["method"] == "elimination"
    assert res["home_count"] == 3
    assert res["unresolved_unknown"] == 0
    assert res["occupancy"]["书房"]["unknown"] == 1
    assert len(res["inferred"]) == 1
    inf = res["inferred"][0]
    assert inf["member"] == "Kevin"
    assert inf["room"] == "书房"
    assert inf["confidence"] == 0.9
    assert inf["method"] == "elimination"
    assert inf["reason"]  # 解释文本非空


def test_no_unknown_no_inference():
    occupancy = [
        {"room": "客厅", "persons": ["lidicn", "Emily", "Kevin"], "count": 3},
    ]
    res = fuse_presence(_roster(), occupancy)
    assert res["method"] == "none"
    assert res["home_count"] == 3
    assert res["inferred"] == []
    assert res["unresolved_unknown"] == 0


def test_inconclusive_when_counts_mismatch():
    # 书房未知 2 人，但名册只缺席 Kevin 1 人 → 等式不成立，不编造
    occupancy = [
        {"room": "客厅", "persons": ["lidicn", "Emily"], "count": 2},
        {"room": "书房", "persons": [], "count": 2},
    ]
    res = fuse_presence(_roster(), occupancy)
    assert res["method"] == "inconclusive"
    assert res["inferred"] == []
    assert res["unresolved_unknown"] == 2
    # home_count 只数确定的：客厅 2 人 + 书房未识别 2（未消歧，不计入已知）
    assert res["home_count"] == 2


def test_unknown_identity_not_counted_as_present():
    # 客厅出现「陌生人」占位名 → 不计入 known_present，也不参与消除
    occupancy = [
        {"room": "客厅", "persons": ["未识别成员"], "count": 1},
    ]
    res = fuse_presence(_roster(), occupancy)
    assert "未识别成员" not in res["known_present"]
    assert res["occupancy"]["客厅"]["unknown"] == 1


# ── 存储层：recent_occupancy 取每房间最新一条 ──────────────────────────────

@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="mw_fuse_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s
    try:
        os.remove(db)
    except OSError:
        pass


def test_recent_occupancy_latest_per_room(store):
    # 书房先有一条「无人」(count=0)，后一条「1 人未识别」→ 应取后者
    store.insert_behavior_event({
        "server_ts": "2026-09-13T21:00:00", "room": "书房",
        "persons": [], "count": 0, "trigger": "butler",
    })
    store.insert_behavior_event({
        "server_ts": "2026-09-13T21:04:00", "room": "书房",
        "persons": [], "count": 1, "trigger": "butler",
    })
    store.insert_behavior_event({
        "server_ts": "2026-09-13T21:05:00", "room": "客厅",
        "persons": [{"name": "lidicn"}, {"name": "Emily"}], "count": 2,
        "trigger": "face",
    })
    occ = store.recent_occupancy("2026-09-13T21:00:00")
    by_room = {o["room"]: o for o in occ}
    assert by_room["书房"]["count"] == 1
    assert by_room["书房"]["persons"] == []
    assert by_room["客厅"]["persons"] == ["lidicn", "Emily"]

    # 端到端融合：应推断 Kevin 在书房
    roster = [
        {"name": "lidicn", "rooms": ["客厅"], "appearance_json": {}},
        {"name": "Emily", "rooms": ["客厅"], "appearance_json": {}},
        {"name": "Kevin", "rooms": ["书房"], "appearance_json": {}},
    ]
    fused = fuse_presence(roster, occ)
    assert fused["method"] == "elimination"
    assert fused["inferred"][0]["member"] == "Kevin"
    assert fused["inferred"][0]["room"] == "书房"
