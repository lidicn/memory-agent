"""v0.9 契约完善单测：MCP 错误模型统一（ok:false → isError + 错误码）。"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.mcp_errors import (  # noqa: E402
    ALL_CODES,
    ErrorCode,
    infer_error_code,
    normalize_tool_result,
)


def _text_result(payload):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
        is_error=False,
    )


@pytest.mark.parametrize("msg,code", [
    ("成员不存在", ErrorCode.NOT_FOUND),
    ("template_id 不能为空", ErrorCode.INVALID_PARAM),
    ("权限不足", ErrorCode.DENIED),
    ("chroma 不可用", ErrorCode.UPSTREAM_UNAVAILABLE),
    ("请求过于频繁", ErrorCode.RATE_LIMITED),
    ("内部错误", ErrorCode.INTERNAL),
])
def test_infer_error_code(msg, code):
    assert infer_error_code(msg) == code


def test_normalize_ok_false_to_iserror():
    res = normalize_tool_result(_text_result({"ok": False, "error": "成员不存在"}))
    assert getattr(res, "isError", False) or getattr(res, "is_error", False)
    body = json.loads(res.content[0].text)
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.NOT_FOUND
    assert body["error"]["message"] == "成员不存在"


def test_normalize_keeps_extra_as_detail():
    res = normalize_tool_result(
        _text_result({"ok": False, "error": "没有名为 x 的工具", "available": ["a", "b"]}))
    body = json.loads(res.content[0].text)
    assert body["error"]["code"] == ErrorCode.NOT_FOUND
    assert body["detail"]["available"] == ["a", "b"]


def test_normalize_ok_true_untouched():
    res = normalize_tool_result(_text_result({"ok": True, "data": 1}))
    assert not (getattr(res, "isError", False) or getattr(res, "is_error", False))


def test_normalize_already_error_untouched():
    orig = SimpleNamespace(content=[], is_error=True)
    assert normalize_tool_result(orig) is orig


def test_all_codes_enumerated():
    assert set(ALL_CODES) == {
        "NOT_FOUND", "INVALID_PARAM", "UPSTREAM_UNAVAILABLE",
        "DENIED", "RATE_LIMITED", "INTERNAL",
    }
