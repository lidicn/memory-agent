"""v0.9 任务3：MCP 可维护性契约测试（可在容器内 `python -m pytest tests/test_mcp_contract.py` 运行）。

覆盖三块：
1. 响应体上限与超限摘要（`_apply_response_cap`），以及 `list_device_health` 的分页信封与
   默认精简投影（DCD 20261004 MA-裁4，同属「对外看得见的载荷形状」）；
2. 错误结果构造动态适配 mcp 1.x/2.x 字段名（`_build_tool_result`）；
3. 故障注入矩阵（`set_mcp_fault` / `_fault_result` / 分发层短路，不真正执行工具）。

均为纯函数 / 轻量协程，无需运行期 runtime 即可验证（审计落库失败被旁路 try 吞掉）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from memory_agent import mcp_server as ms
from memory_agent.mcp_context import set_caller
from memory_agent.mcp_errors import ALL_CODES, ErrorCode, normalize_tool_result


def _make_result(text: str, is_err: bool = False):
    """构造一个近似 CallToolResult 的对象（避免强依赖 mcp 运行时版本字段名差异）。"""
    from mcp.types import CallToolResult, TextContent

    flds = getattr(CallToolResult, "model_fields", None) or getattr(
        CallToolResult, "__fields__", {}) or {}
    kwargs = {"content": [TextContent(type="text", text=text)]}
    if "is_error" in flds:
        kwargs["is_error"] = is_err
    elif "isError" in flds:
        kwargs["isError"] = is_err
    else:
        kwargs["is_error"] = is_err
    return CallToolResult(**kwargs)


def _is_err(r) -> bool:
    return bool(getattr(r, "is_error", False) or getattr(r, "isError", False))


def _text(r) -> str:
    for c in r.content:
        if getattr(c, "type", "") == "text":
            return str(c.text)
    return ""


# ── 1. 响应体上限 ────────────────────────────────────────────────────────────
def test_apply_response_cap_noop_under_limit():
    r = _make_result("hello world")
    out = ms._apply_response_cap(r, "x", max_bytes=100)
    assert _text(out) == "hello world"


def test_apply_response_cap_noop_when_disabled():
    r = _make_result("x" * 10000)
    out = ms._apply_response_cap(r, "x", max_bytes=0)
    assert _text(out) == "x" * 10000


def test_apply_response_cap_truncates_and_summarizes():
    big = "A" * 5000  # ~5KB
    r = _make_result(big)
    out = ms._apply_response_cap(r, "big_tool", max_bytes=1000)
    out_text = _text(out)
    # 原始正文被截到上限（1000 字节），摘要作为少量固定开销追加其后
    assert out_text.startswith("A" * 1000)
    assert "A" * 1001 not in out_text          # 超出上限的部分已被丢弃
    assert len(out_text.encode("utf-8")) <= 1000 + 300
    assert "响应已截断" in out_text
    assert "big_tool" in out_text


def test_apply_response_cap_preserves_error_flag():
    big = "E" * 5000
    r = _make_result(big, is_err=True)
    out = ms._apply_response_cap(r, "x", max_bytes=1000)
    assert _is_err(out) is True
    assert "响应已截断" in _text(out)


# ── 1b. JSON 载荷超限：按结构降级，不许把 JSON 腰斩 ────────────────────────────
# 生产实测（2026-10-03）：`list_device_health` 原始输出约 650KB > 上限 512KB，
# 字符截断把 JSON 切在半行上（`"entity_id": "sensor.x` 后直接断），
# 调用方 json.loads 报 `Expecting ',' delimiter: line 14056 column 1`。
# 摘要那句「请用…分页参数」对该工具也不成立——它根本没有分页参数。
# （这是投递时的事实；MA-裁4 Q1=A 之后该工具确实有了 limit/offset，摘要措辞也不再点名
#   具体参数，见本文件 §1c 与 test_char_cap_hint_points_at_the_tools_own_parameter_surface。）

def _rows(n: int) -> list:
    return [{"entity_id": f"sensor.entity_{i:04d}", "state": "active",
             "referenced": i % 2, "note": "本轮对账未出现（短暂失联）",
             "updated_at": "2026-10-03T10:00:06"} for i in range(n)]


def test_json_over_cap_stays_parseable_and_reports_what_dropped():
    import json
    payload = json.dumps({"ok": True, "state": "all", "health": _rows(400),
                          "total": 400, "logical_devices": 12}, ensure_ascii=False)
    assert len(payload.encode("utf-8")) > 4000
    out = ms._apply_response_cap(_make_result(payload), "list_device_health", max_bytes=4000)
    text = _text(out)
    data = json.loads(text)                      # 必须仍然是合法 JSON
    assert data["ok"] is True
    assert len(data["health"]) < 400, "超限后行数必须变少"
    assert data["state"] == "all" and data["logical_devices"] == 12, "标量字段不该被切掉"
    for row in data["health"]:
        assert set(row) == {"entity_id", "state", "referenced", "note", "updated_at"}, \
            "留下的行必须整条在，不许半条记录"
    notice = data.get("_truncated")
    assert isinstance(notice, dict), f"缺 _truncated 摘要：{list(data)}"
    assert notice["dropped"] > 0 and notice["original_rows"] == 400
    assert notice["kept_rows"] == len(data["health"])
    assert notice["original_bytes"] > 4000 <= notice["cap_bytes"]
    assert "响应已截断" in notice["hint"]
    assert len(text.encode("utf-8")) <= 4000 + 600


def test_json_shrink_cuts_the_big_list_not_the_small_one():
    import json
    payload = json.dumps({"ok": True, "big": _rows(300), "keep_me": [{"id": 1}]},
                         ensure_ascii=False)
    out = ms._apply_response_cap(_make_result(payload), "t", max_bytes=4000)
    data = json.loads(_text(out))
    assert data["keep_me"] == [{"id": 1}], "小列表不该被顺手裁掉"
    assert len(data["big"]) < 300


def test_json_without_lists_falls_back_to_char_truncation():
    """切结构救不了的场景（巨型标量）必须退回旧的字符截断，而不是抛异常或原样返回。"""
    payload = '{"ok": true, "blob": "' + "x" * 5000 + '"}'
    out = ms._apply_response_cap(_make_result(payload), "blob_tool", max_bytes=1000)
    text = _text(out)
    assert "响应已截断" in text
    assert "blob_tool" in text
    assert len(text.encode("utf-8")) <= 1000 + 400


def test_json_error_result_keeps_its_error_flag_after_structural_shrink():
    import json
    payload = json.dumps({"ok": False, "error": "boom", "items": _rows(300)},
                         ensure_ascii=False)
    out = ms._apply_response_cap(_make_result(payload, is_err=True), "t", max_bytes=4000)
    assert _is_err(out) is True
    data = json.loads(_text(out))
    assert data["ok"] is False and data["error"] == "boom"


def test_non_json_text_still_uses_the_character_cap():
    """纯文本（非 JSON）响应不能被结构降级路径吃掉——旧行为要原样保留。"""
    out = ms._apply_response_cap(_make_result("日" * 3000), "text_tool", max_bytes=1000)
    text = _text(out)
    assert text.startswith("日" * 100)
    assert "响应已截断" in text


def test_char_cap_hint_points_at_the_tools_own_parameter_surface():
    """字符截断的摘要不许点名「分页参数」——它无法知道被点的这个工具有没有那把参数。

    生产现场（2026-10-03）：`list_device_health` 当时根本没有分页参数，摘要却让它
    「请用…分页参数」，那句话对调用方是死路（投递见 inbox 20261003 超限件 §一 事实表）。
    """
    out = ms._apply_response_cap(_make_result("日" * 3000), "text_tool", max_bytes=1000)
    text = _text(out)
    assert "参数面" in text
    assert "请用更窄的时间窗 / 分页参数" not in text
    assert len(text.encode("utf-8")) <= 1000 + 400


# ── 1c. DCD 20261004 MA-裁4：设备健康分页 + 默认精简投影 ────────────────────────
#
# 生产现场（2026-10-03 实测）：`list_device_health` 不带参数约 650KB > 上限 512KB，
# 而 `stable_id` 一个字段就占 51,062 字节——那串中文长名是给人看的，Agent 定位用
# `entity_id`。裁定 Q1=A 分页（默认 500）、Q2=A 默认精简、Q3=维持超限裁行。
# MCP 工具本体在工厂闭包里、测试取不到，所以判据落在两个模块级纯函数 + Store 真库上。

def _health_row(eid: str = "sensor.a", *, stable_id: str = "", note: str = "",
                referenced: int = 0, state: str = "active") -> dict:
    return {"entity_id": eid, "stable_id": stable_id, "state": state,
            "referenced": referenced, "note": note,
            "updated_at": "2026-10-04T01:00:00+08:00"}


_LONG_STABLE = "sensor__米家智能鱼缸异常卡片触发状态按照bit从低到高位1水草灯时间过长2水泵故障"


def test_lean_projection_truncates_only_the_rows_that_need_it():
    rows = [_health_row("sensor.a", stable_id=_LONG_STABLE),
            _health_row("sensor.b", stable_id="sensor.short")]

    out = ms.project_device_health(rows)
    n = ms.DEVICE_HEALTH_STABLE_ID_MAX_CHARS
    assert out[0]["stable_id"] == _LONG_STABLE[:n]
    assert out[0]["stable_id_truncated"] is True
    assert "stable_id_truncated" not in out[1]      # 没被截就不许冒出新键
    assert out[1]["stable_id"] == "sensor.short"
    assert rows[0]["stable_id"] == _LONG_STABLE     # 投影不改原行


def test_note_is_blanked_but_the_key_stays_when_the_entity_is_unreferenced():
    """键在、值为空——不是把键删掉。删键会让消费端 `row["note"]` KeyError。"""
    rows = [_health_row("sensor.a", note="滤网清洗提醒", referenced=0),
            _health_row("sensor.b", note="滤网清洗提醒", referenced=1)]

    out = ms.project_device_health(rows)
    assert "note" in out[0] and out[0]["note"] == ""
    assert out[1]["note"] == "滤网清洗提醒"
    assert rows[0]["note"] == "滤网清洗提醒"


def test_fields_full_leaves_the_rows_exactly_as_stored():
    rows = [_health_row(stable_id=_LONG_STABLE, note="n", referenced=0)]
    for value in ("full", "FULL", " full "):
        assert ms.project_device_health(rows, value) == rows
    assert rows[0]["stable_id"] == _LONG_STABLE


def test_page_walks_every_row_exactly_once_and_total_is_the_pre_page_size():
    total = 7
    rows = [_health_row(f"sensor.{i}") for i in range(total)]

    seen: list[str] = []
    offset, pages = 0, 0
    while True:
        page = ms.device_health_page(rows[offset:offset + 3], total, offset=offset, limit=3)
        assert page["total"] == total               # 全量条数，不随页缩
        assert page["count"] == len(page["health"])
        assert page["offset"] == offset and page["limit"] == 3
        seen.extend(r["entity_id"] for r in page["health"])
        pages += 1
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        offset = page["next_offset"]
        assert pages < 10                           # 翻不完即 next_offset 错了
    assert seen == [f"sensor.{i}" for i in range(total)]
    assert pages == 3


def test_an_empty_page_always_ends_the_walk():
    """空页必须收口，两种空页都要：offset 越过表尾、以及数据被清过后 offset 仍小于 total。

    后一种才咬住判据：按 `offset < total` 判会得到 ``has_more=true`` 且 ``next_offset``
    原地不动，按 ``while has_more: offset = next_offset`` 翻页的消费端就此死循环。
    """
    for off, total in ((99, 5), (3, 5), (0, 0)):
        page = ms.device_health_page([], total, offset=off, limit=3)
        assert page["count"] == 0, f"offset={off} total={total}"
        assert page["has_more"] is False, f"offset={off} total={total}"
        assert page["next_offset"] is None, f"offset={off} total={total}"


def test_page_bounds_are_clamped_into_the_declared_tool_range():
    assert ms.device_health_page([], 0, limit=0)["limit"] == 1
    assert ms.device_health_page([], 0, limit=-5)["limit"] == 1
    assert ms.device_health_page([], 0, limit=10 ** 9)["limit"] == ms.DEVICE_HEALTH_PAGE_LIMIT_MAX
    assert ms.device_health_page([], 0, offset=-3)["offset"] == 0


def test_page_declares_the_field_mode_and_the_default_state_label():
    assert ms.device_health_page([], 0, fields="full")["fields"] == "full"
    assert ms.device_health_page([], 0, fields="lean")["fields"] == "lean"
    assert ms.device_health_page([], 0)["fields"] == "lean"
    assert ms.device_health_page([], 0)["state"] == "all"
    assert ms.device_health_page([], 0, state="stale")["state"] == "stale"
    # 信封不替调用方宣称成功：`ok` 那一位由工具本体在读通之后才写
    assert "ok" not in ms.device_health_page([], 0)


def _store():
    from memory_agent.store import Store

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)              # Store 要自建文件，撞上已存在的空文件会失败
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    return st, path


def test_store_pagination_slices_the_same_order_as_the_unbounded_read():
    st, path = _store()
    try:
        for i in range(5):
            st.upsert_device_health(f"sensor.{i:02d}", state="active" if i % 2 else "stale")
        full = st.list_device_health("")
        assert len(full) == 5
        assert st.list_device_health("", limit=2, offset=0) == full[:2]
        assert st.list_device_health("", limit=2, offset=2) == full[2:4]
        assert st.list_device_health("", limit=2, offset=4) == full[4:]
        # limit=None 仍是无界：身份层重建（identity.py:687）与 HTTP 面板要整张表
        assert st.list_device_health("") == full
        assert st.count_device_health("") == 5
        assert st.count_device_health("stale") == 3
        assert st.count_device_health("unknown") == 0
    finally:
        st.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def test_equal_timestamps_still_page_without_overlap_or_gaps():
    """批量 upsert 打的是同一个 updated_at；排序少一路兜底键就会漏行。

    写入顺序刻意与 entity_id 相反（06→01），这样「没兜底键时按 rowid 返回」与
    「按 entity_id 定序」给出不同读数——变异掉 ORDER BY 的兜底键，这条必红。
    """
    st, path = _store()
    try:
        for i in range(6, 0, -1):
            st.upsert_device_health(f"sensor.{i:02d}", state="active")
        conn = st.connect()
        conn.execute("UPDATE device_health SET updated_at=?", ("2026-10-04T00:00:00+08:00",))
        conn.commit()

        full = [r["entity_id"] for r in st.list_device_health("")]
        assert full == sorted(full)

        walked, offset = [], 0
        while offset < 20:
            page = st.list_device_health("", limit=2, offset=offset)
            if not page:
                break
            walked.extend(r["entity_id"] for r in page)
            offset += 2
        assert walked == full
    finally:
        st.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


# ── 2. 错误结果构造动态字段 ──────────────────────────────────────────────────
def test_build_tool_result_is_error_flag():
    r = ms._build_tool_result("boom", is_error=True)
    # mcp 1.x 用 isError，2.x 用 is_error——统一兼容判定
    assert _is_err(r) is True
    assert _text(r) == "boom"


def test_normalize_contract_ok_false_becomes_is_error():
    payload = '{"ok": false, "error": "成员不存在"}'
    r = _make_result(payload)
    out = normalize_tool_result(r, "get_member")
    assert _is_err(out) is True
    assert "NOT_FOUND" in _text(out)


# ── 3. 故障注入矩阵 ──────────────────────────────────────────────────────────
def _fake_server():
    """会记录是否被真正调用；被调用即抛，确保故障注入短路生效。"""
    calls = {"n": 0}

    class Fake:
        async def call_tool(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("工具不应被真正执行（故障注入应短路）")

    return Fake(), calls


@pytest.mark.parametrize("code", ALL_CODES)
def test_fault_injection_tool_specific_short_circuits(code):
    set_caller("verify", ["read", "write"], "test")
    ms.set_mcp_fault("get_member", code)
    try:
        fake, calls = _fake_server()
        r = asyncio.run(
            ms._tracked_call_tool(fake, "get_member", {})
        )
        assert calls["n"] == 0, "故障注入未短路，工具被真实调用"
        assert _is_err(r) is True
        assert code in _text(r)
    finally:
        ms.clear_mcp_faults()


def test_fault_injection_wildcard_applies_to_any_tool():
    set_caller("verify", ["read", "write"], "test")
    ms.set_mcp_fault("*", ErrorCode.UPSTREAM_UNAVAILABLE)
    try:
        fake, calls = _fake_server()
        r = asyncio.run(
            ms._tracked_call_tool(fake, "some_random_tool", {})
        )
        assert calls["n"] == 0
        assert _is_err(r) is True
        assert ErrorCode.UPSTREAM_UNAVAILABLE in _text(r)
    finally:
        ms.clear_mcp_faults()


def test_fault_injection_cleared_removes_short_circuit():
    set_caller("verify", ["read", "write"], "test")
    ms.set_mcp_fault("get_member", ErrorCode.INTERNAL)
    assert ms.list_mcp_faults().get("get_member") == ErrorCode.INTERNAL
    ms.clear_mcp_faults()
    assert ms.list_mcp_faults() == {}
    # 清除后故障分支不再命中；重新注入可再次短路，证明注册表双向可控、不依赖真实 MCPServer
    ms.set_mcp_fault("get_member", ErrorCode.DENIED)
    try:
        fake, calls = _fake_server()
        r = asyncio.run(ms._tracked_call_tool(fake, "get_member", {}))
        assert calls["n"] == 0
        assert ErrorCode.DENIED in _text(r)
    finally:
        ms.clear_mcp_faults()


def test_set_mcp_fault_rejects_unknown_code():
    with pytest.raises(ValueError):
        ms.set_mcp_fault("x", "NOT_A_REAL_CODE")
    ms.clear_mcp_faults()
