"""WO-MA-003 §十 O-4: ACP 会话跨属主拒绝测试。

验证 cancel 的 owner check 先于 run_id 查询：
异主体 + 确有 run_id 时，返回 -32602（属主不匹配），
而不是 -32001（无进行中的任务）。

进程内桩：自建 SessionStore、假 run_id（不在 _RUNS 中）、
stdin 送入不落盘。不需要真 run、不需要真 LLM。
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.acp_server import _STORE, acp_handle
from memory_agent.acp_protocol import ERR_INVALID_PARAMS, ERR_SESSION_NOT_FOUND


def test_cancel_cross_owner_denied_before_run_id_check():
    """异主体 + 确有 run_id → cancel 返回 -32602（属主不匹配），非 -32001。"""
    sid = "test_cross_owner_sid"
    owner_a = "owner_A_test"
    owner_b = "owner_B_test"
    fake_run_id = "fake_run_never_in_RUNS_12345"

    # 清理：确保测试前会话不存在
    _STORE.delete(sid)

    try:
        # A 创建会话（属主 owner_A）
        _STORE.new(sid, owner_token=owner_a)

        # 手动设置 run_id（模拟 in-flight run，假 run_id 不在 _RUNS 中）
        _STORE.bind(sid, fake_run_id, owner_token=owner_a)

        # 确认会话确有 run_id
        meta = _STORE.get(sid)
        assert meta is not None, "会话应存在"
        assert meta.get("run_id") == fake_run_id, "会话应有 run_id"

        # B（异主体）发 cancel
        scope = {"state": {"acp_token_name": owner_b}}
        payload = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "cancel",
            "params": {"sessionId": sid},
        }
        result, _ = asyncio.run(acp_handle(None, payload, scope))

        # 断言：返回 error，code 应为 -32602（属主不匹配）
        assert result is not None, "应返回响应"
        error = result.get("error")
        assert error is not None, "应返回错误"
        code = error.get("code")
        assert code == ERR_INVALID_PARAMS, (
            f"异主体 cancel 应返回 {ERR_INVALID_PARAMS}（属主不匹配），"
            f"实际 {code}（{error.get('message')}）。"
            f"若为 {ERR_SESSION_NOT_FOUND} 说明 owner check 在 run_id 之后，回归了。"
        )
        assert "属主" in error.get("message", "") or "无权" in error.get("message", ""), (
            f"错误消息应含属主/无权，实际: {error.get('message')}"
        )

        print(f"PASS: cancel 异主体返回 {code}（{error.get('message')}）")

    finally:
        # 清理
        _STORE.delete(sid)


def test_cancel_same_owner_with_run_id_passes_owner_check():
    """同主体 + 确有 run_id → cancel 通过 owner check，走到 run.abort（不返回属主拒绝）。"""
    sid = "test_same_owner_sid"
    owner_a = "owner_A_same"
    fake_run_id = "fake_run_same_owner_67890"

    _STORE.delete(sid)

    try:
        _STORE.new(sid, owner_token=owner_a)
        _STORE.bind(sid, fake_run_id, owner_token=owner_a)

        # A（同主体）发 cancel
        scope = {"state": {"acp_token_name": owner_a}}
        payload = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "cancel",
            "params": {"sessionId": sid},
        }
        result, _ = asyncio.run(acp_handle(None, payload, scope))

        # 同主体应通过 owner check。假 run_id 不在 _RUNS 中，
        # run.abort() 不会被调用（_RUNS.get 返回 None），应返回 cancelling
        assert result is not None
        # 不应是属主拒绝
        error = result.get("error")
        if error:
            assert error.get("code") != ERR_INVALID_PARAMS, "同主体不应返回属主拒绝"
        print(f"PASS: cancel 同主体返回 result（非属主拒绝）")

    finally:
        _STORE.delete(sid)


def test_check_owner_fail_close():
    """check_owner 空主体返回 False（fail-close）。"""
    sid = "test_fail_close_sid"
    _STORE.delete(sid)
    try:
        _STORE.new(sid, owner_token="owner_X")
        assert _STORE.check_owner(sid, "") == False, "空主体应 fail-close 返回 False"
        assert _STORE.check_owner(sid, "wrong") == False, "错误主体应返回 False"
        assert _STORE.check_owner(sid, "owner_X") == True, "正确主体应返回 True"
        assert _STORE.check_owner("nonexistent_sid", "owner_X") == False, "不存在会话应返回 False"
        print("PASS: check_owner fail-close")
    finally:
        _STORE.delete(sid)


if __name__ == "__main__":
    test_cancel_cross_owner_denied_before_run_id_check()
    test_cancel_same_owner_with_run_id_passes_owner_check()
    test_check_owner_fail_close()
    print("\n3 passed")
