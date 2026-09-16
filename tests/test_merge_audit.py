"""合并审计 + 拆分回滚（v0.6 #1）单元测试。"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.store import Store  # noqa: E402


@pytest.fixture
def store():
    s = Store(":memory:", tz_offset_hours=8)
    s.init_schema()
    return s


MERGED = {
    "stable_id": "merged_living_tv",
    "display_name": "客厅电视",
    "device_class": "media_player",
    "primary_entity": "media_player.living_tv_a",
    "candidates": [
        {"entity_id": "media_player.living_tv_a", "name": "客厅电视A", "state": "active"},
        {"entity_id": "media_player.living_tv_b", "name": "客厅电视B", "state": "active"},
    ],
    "provenance": "auto-merged",
}


def test_list_and_split_merged(store):
    store.upsert_logical_device(MERGED)
    rows = store.list_merged_logical_devices()
    assert len(rows) == 1
    assert rows[0]["stable_id"] == "merged_living_tv"

    res = store.split_logical_device("merged_living_tv")
    assert res["ok"]
    assert set(res["split_into"]) == {"media_player.living_tv_a", "media_player.living_tv_b"}

    # 原合并设备消失，拆出的子设备为 user-pinned（不再被 A2 自动重合并）
    assert store.get_logical_device("merged_living_tv") is None
    a = store.get_logical_device("media_player.living_tv_a")
    assert a["provenance"] == "user-pinned"
    assert len(a["candidates"]) == 1
    # 审计列表清空
    assert store.list_merged_logical_devices() == []


def test_split_rejects_non_merged(store):
    store.upsert_logical_device(
        {
            "stable_id": "x",
            "display_name": "x",
            "provenance": "discovered",
            "candidates": [{"entity_id": "e1"}],
        }
    )
    res = store.split_logical_device("x")
    assert not res["ok"]


def test_split_unknown_returns_404(store):
    res = store.split_logical_device("nope")
    assert not res["ok"]
    assert res.get("code") == 404
