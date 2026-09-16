"""来源白名单 + 按 token 派生来源（v0.6 #3）单元测试。

核心安全点：应用令牌带 source 时强制采用（忽略调用方自报），其余来源须落在白名单。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import get_config  # noqa: E402
from memory_agent.api.agent_memory_routes import _resolve_source  # noqa: E402


@pytest.fixture(autouse=True)
def _cfg():
    cfg = get_config()
    cfg.agent_memory_sources = ["ma", "butler", "vision", "manual"]
    yield


def test_token_source_wins_over_body():
    user = {"app": True, "app_source": "vision"}
    assert _resolve_source("butler", user) == "vision"


def test_token_source_ignores_spoofed_body():
    user = {"app": True, "app_source": "butler"}
    assert _resolve_source("ma", user) == "butler"


def test_jwt_valid_source_passthrough():
    user = {"username": "u"}
    assert _resolve_source("butler", user) == "butler"


def test_jwt_invalid_source_falls_back_to_ma():
    user = {"username": "u"}
    assert _resolve_source("hacked-source", user) == "ma"


def test_empty_source_defaults_ma():
    user = {"username": "u"}
    assert _resolve_source("", user) == "ma"


def test_non_app_user_uses_body_source():
    user = {"app": False}
    assert _resolve_source("vision", user) == "vision"


def test_unknown_source_with_token_still_token():
    # 即便 body 写白名单外的值，token 派生优先级最高
    user = {"app": True, "app_source": "manual"}
    assert _resolve_source("zzz", user) == "manual"
