"""家庭成员 / 生活习惯档案 存储层测试。

用临时 sqlite 库验证成员 CRUD、房间/设备关联、标签写回与覆盖逻辑。
不依赖真实 Home Assistant 数据或 LLM。
"""

import os
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_worker.store import Store  # noqa: E402


@pytest.fixture
def store():
    tmp = tempfile.mkdtemp(prefix="mw_members_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    yield s
    try:
        os.remove(db)
    except OSError:
        pass


def test_create_and_list_member(store):
    m = store.create_member(name="爸爸", avatar_emoji="🦉", avatar_bg="#0EA5E9", note="家里老大")
    assert m["id"]
    assert m["name"] == "爸爸"
    assert m["avatar_emoji"] == "🦉"
    assert m["rooms"] == [] and m["devices"] == [] and m["tags"] == []

    members = store.list_members()
    assert len(members) == 1
    assert members[0]["id"] == m["id"]


def test_update_member(store):
    m = store.create_member(name="妈妈")
    updated = store.update_member(m["id"], name="妈妈2", avatar_emoji="🍳")
    assert updated["name"] == "妈妈2"
    assert updated["avatar_emoji"] == "🍳"
    # 未传入的字段保持不变
    assert updated["rooms"] == []


def test_delete_member_cascades(store):
    m = store.create_member(name="小明")
    store.set_member_rooms(m["id"], ["次卧"])
    store.set_member_devices(m["id"], ["device_tracker.xiaoming"])
    store.add_member_tag(m["id"], "学生党", "activity", "🎒", 0.8)
    assert store.list_members()[0]["tags"]

    store.delete_member(m["id"])
    assert store.list_members() == []
    # 级联清理：标签、房间、设备应一并清除
    assert store.list_member_tags(m["id"]) == []
    assert store._member_rooms(store.connect(), m["id"]) == []


def test_rooms_and_devices_assignment(store):
    m = store.create_member(name="爷爷")
    store.set_member_rooms(m["id"], ["客卧", "书房"])
    store.set_member_devices(m["id"], ["device_tracker.yeye", "switch.massage"])
    got = store.get_member(m["id"])
    assert sorted(got["rooms"]) == sorted(["客卧", "书房"])
    assert sorted(got["devices"]) == sorted(["device_tracker.yeye", "switch.massage"])

    # 覆盖式更新
    store.set_member_rooms(m["id"], ["客卧"])
    assert store.get_member(m["id"])["rooms"] == ["客卧"]


def test_member_tag_overwrite_and_delete(store):
    m = store.create_member(name="爸爸")
    t1 = store.add_member_tag(m["id"], "夜猫子", "sleep", "🦉", 0.7, ["主卧凌晨活跃"], source="agent")
    assert t1["confidence"] == 0.7
    assert t1["source"] == "agent"
    assert t1["evidence"] == ["主卧凌晨活跃"]

    # 同名标签覆盖刷新，不新增行
    t2 = store.add_member_tag(m["id"], "夜猫子", "sleep", "🦉", 0.95, ["连续7天凌晨后熄灯"], source="agent")
    assert t2["confidence"] == 0.95
    assert len(store.list_member_tags(m["id"])) == 1

    store.delete_member_tag(m["id"], "夜猫子")
    assert store.list_member_tags(m["id"]) == []
