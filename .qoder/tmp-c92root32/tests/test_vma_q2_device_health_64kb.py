"""DCD 20261004 裁1/裁4 Q2=甲：list_device_health 整页 ≤64KB 自动收窄回归锁。

旧实现：stable_id 截到 40 字符，实测只压掉 12%。
新实现：投影后整页 JSON 字节超过 64KB 时逐行收窄，next_offset 按实际返回行数推进。
"""

import os
import sys
import json

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.mcp_server import (  # noqa: E402
    device_health_page,
    project_device_health,
    DEVICE_HEALTH_PAGE_MAX_BYTES,
    DEVICE_HEALTH_PAGE_LIMIT_DEFAULT,
)


def _make_row(i: int, big_note: bool = False) -> dict:
    return {
        "entity_id": f"light.room_{i:04d}",
        "stable_id": f"客厅主灯_{i:04d}_非常长的稳定标识符用于测试截断",
        "state": "active",
        "room": "客厅",
        "domain": "light",
        "referenced": 1 if i % 3 == 0 else 0,
        "note": "这是一条很长的备注信息，包含设备的详细描述和维护记录，" * 3 if big_note else "",
        "last_seen": "2026-10-04T10:00:00+08:00",
    }


def test_page_under_64kb_unchanged():
    """小页（<64KB）不被收窄，返回全部请求行数。"""
    rows = [_make_row(i) for i in range(50)]
    out = device_health_page(rows, total=100, limit=50)
    assert out["count"] == 50, f"小页不应收窄，实际 {out['count']}"
    assert out["page_bytes"] <= DEVICE_HEALTH_PAGE_MAX_BYTES
    assert out["has_more"] is True
    assert out["next_offset"] == 50


def test_page_over_64kb_auto_narrows():
    """大页（>64KB）自动收窄到 ≤64KB，且 next_offset 按实际行数推进。"""
    # 2000 行带大备注，肯定超过 64KB
    rows = [_make_row(i, big_note=True) for i in range(2000)]
    out = device_health_page(rows, total=5000, limit=2000)
    assert out["page_bytes"] <= DEVICE_HEALTH_PAGE_MAX_BYTES, (
        f"收窄后应 ≤64KB，实际 {out['page_bytes']}"
    )
    assert out["count"] < 2000, f"应被收窄，实际返回 {out['count']}"
    assert out["count"] > 0, "不应收窄到 0 行"
    # next_offset 按实际返回行数推进
    assert out["next_offset"] == out["count"]
    assert out["has_more"] is True


def test_full_fields_also_respects_byte_budget():
    """fields='full' 时同样受 64KB 预算约束。"""
    rows = [_make_row(i, big_note=True) for i in range(2000)]
    out = device_health_page(rows, total=5000, limit=2000, fields="full")
    assert out["page_bytes"] <= DEVICE_HEALTH_PAGE_MAX_BYTES
    assert out["fields"] == "full"


def test_single_huge_row_not_dropped_entirely():
    """单行就超过 64KB 时至少保留 1 行（不能收窄到 0）。"""
    huge_row = {
        "entity_id": "x",
        "stable_id": "x" * 100000,  # 100KB 的 stable_id
        "state": "active",
        "referenced": 0,
        "note": "",
    }
    out = device_health_page([huge_row], total=1, limit=1)
    assert out["count"] == 1, "单行超大时至少保留 1 行"
    # 40 字符截断后 stable_id 只有 40 字，整页应远小于 64KB
    assert out["page_bytes"] <= DEVICE_HEALTH_PAGE_MAX_BYTES + 100  # 容差


def test_page_bytes_field_present():
    """返回体包含 page_bytes 字段（用于改前/改后读数对比）。"""
    rows = [_make_row(i) for i in range(10)]
    out = device_health_page(rows, total=10, limit=10)
    assert "page_bytes" in out
    assert isinstance(out["page_bytes"], int)
    assert out["page_bytes"] > 0


def test_narrowing_does_not_break_pagination():
    """收窄后翻页逻辑仍然正确：has_more 和 next_offset 一致。"""
    rows = [_make_row(i, big_note=True) for i in range(500)]
    out = device_health_page(rows, total=1000, limit=500)
    # 第一页被收窄
    first_count = out["count"]
    assert out["has_more"] is True
    assert out["next_offset"] == first_count
    # 模拟第二页（offset=first_count）
    rows2 = [_make_row(i + 500, big_note=True) for i in range(500)]
    out2 = device_health_page(rows2, total=1000, offset=first_count, limit=500)
    assert out2["offset"] == first_count
    assert out2["page_bytes"] <= DEVICE_HEALTH_PAGE_MAX_BYTES
