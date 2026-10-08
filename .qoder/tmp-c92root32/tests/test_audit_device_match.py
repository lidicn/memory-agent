"""A6 P5 补测：voice_util.match_device 设备匹配回归测试。

覆盖 P2-9：精确匹配应优先于更长的前缀扩展（"电脑"不应匹配"电脑插座"）。
6 条测试：4 条锁缺陷（原实现红）+ 2 条对照（原实现绿）。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.voice_util import match_device


class _MockInsights:
    """模拟 insights 对象，提供 name_map() 和 resolve_room_in_text()。"""

    def __init__(self, name_map):
        self._name_map = name_map

    def name_map(self):
        return self._name_map

    def resolve_room_in_text(self, text):
        return None  # 不锁定房间，让匹配逻辑自由选择


def _make_insights():
    """构造含前缀歧义的设备目录：电脑/电脑插座、客厅灯/客厅灯带、主卧空调/主卧空调插座。"""
    return _MockInsights({
        "switch.study_pc": {
            "friendly_name": "电脑", "domain": "switch", "room": "书房",
        },
        "switch.study_pc_plug": {
            "friendly_name": "电脑插座", "domain": "switch", "room": "书房",
        },
        "light.living_room_lamp": {
            "friendly_name": "客厅灯", "domain": "light", "room": "客厅",
        },
        "light.living_room_strip": {
            "friendly_name": "客厅灯带", "domain": "light", "room": "客厅",
        },
        "climate.master_ac": {
            "friendly_name": "主卧空调", "domain": "climate", "room": "主卧",
        },
        "switch.master_ac_plug": {
            "friendly_name": "主卧空调插座", "domain": "switch", "room": "主卧",
        },
    })


# ── 4 条锁缺陷（P2-9 修复前为红）──────────────────────────────────

def test_exact_name_wins_over_longer_prefix_extension():
    """精确匹配"电脑"应优先于更长的"电脑插座"。

    原 bug：按 friendly_name 长度降序，"电脑插座"(4字) 排在 "电脑"(2字) 前面。
    修复后：按匹配占比排序，精确匹配(fn==query_core)得分最高 2.0。
    """
    ins = _make_insights()
    result = match_device("电脑开了多久", ins)
    assert result is not None, "应匹配到设备"
    assert result["query"] == "电脑", (
        f"精确匹配应优先，期望 query='电脑'，实得 '{result['query']}'"
    )
    assert result["entity_id"] == "switch.study_pc", (
        f"应匹配 switch.study_pc，实得 {result['entity_id']}"
    )


def test_bare_noun_does_not_match_its_own_plug():
    """裸名词"空调"不应匹配到"空调插座"。"""
    ins = _make_insights()
    result = match_device("主卧空调用了多少电", ins)
    assert result is not None
    assert "插座" not in result["query"], (
        f"'空调'不应匹配到插座，实得 '{result['query']}'"
    )
    assert result["entity_id"] == "climate.master_ac", (
        f"应匹配 climate.master_ac，实得 {result['entity_id']}"
    )


def test_prefix_ambiguity_lamp_vs_strip():
    """参数化：客厅灯不应匹配到客厅灯带。"""
    ins = _make_insights()
    result = match_device("客厅灯开了多久", ins)
    assert result is not None
    assert result["query"] == "客厅灯", (
        f"期望 '客厅灯'，实得 '{result['query']}'"
    )
    assert result["entity_id"] == "light.living_room_lamp"


def test_prefix_ambiguity_ac_vs_ac_plug():
    """参数化：主卧空调不应匹配到主卧空调插座。"""
    ins = _make_insights()
    result = match_device("主卧空调运行时长", ins)
    assert result is not None
    assert result["query"] == "主卧空调", (
        f"期望 '主卧空调'，实得 '{result['query']}'"
    )
    assert result["entity_id"] == "climate.master_ac"


# ── 2 条对照（修复前后均应为绿）─────────────────────────────────────

def test_no_match_returns_none():
    """无匹配设备时应返回 None（正确语义，不是错误）。"""
    ins = _make_insights()
    result = match_device("车库门开了多久", ins)
    assert result is None, "车库门不在目录中，应返回 None"


def test_explicit_plug_query_matches_plug():
    """明确查询"插座"时应正确匹配到插座设备。"""
    ins = _make_insights()
    result = match_device("电脑插座用了多少电", ins)
    assert result is not None
    assert result["query"] == "电脑插座", (
        f"明确查插座应匹配 '电脑插座'，实得 '{result['query']}'"
    )
    assert result["entity_id"] == "switch.study_pc_plug"
