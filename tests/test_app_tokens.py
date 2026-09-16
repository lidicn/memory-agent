"""多应用令牌（v0.6）单元测试。

重点验证：哈希校验、来源透传、吊销、列表，以及兼容 v0.3 遗留单 app_token。
不落盘：monkeypatch 掉 Config.save，避免污染真实配置。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.app_tokens import AppTokenStore, TOKEN_PREFIX, PREFIX_LEN  # noqa: E402


@pytest.fixture
def store():
    cfg = Config()
    cfg.app_token = ""  # 无遗留令牌
    cfg.app_tokens = {}
    cfg.save = lambda: None  # 不落盘
    return AppTokenStore(cfg)


def test_generate_returns_plain_token(store):
    res = store.generate("tvpilot")
    assert res["ok"]
    assert res["token"].startswith(TOKEN_PREFIX)
    assert res["prefix"] == res["token"][:PREFIX_LEN]
    assert res["name"] == "tvpilot"


def test_verify_matches_generated_and_carries_source(store):
    res = store.generate("deskpilot", source="vision")
    rec = store.verify(res["token"])
    assert rec is not None
    assert rec["name"] == "deskpilot"
    assert rec["source"] == "vision"


def test_verify_rejects_wrong_or_empty(store):
    store.generate("tvpilot")
    assert store.verify("app_wrong") is None
    assert store.verify("") is None


def test_revoke_removes_token(store):
    res = store.generate("tvpilot")
    assert store.revoke("tvpilot") is True
    assert store.verify(res["token"]) is None
    assert store.revoke("tvpilot") is False  # 已不存在


def test_revoke_unknown_returns_false(store):
    assert store.revoke("nope") is False


def test_list_tokens_reports_records(store):
    store.generate("a")
    store.generate("b", source="x")
    names = {i["name"] for i in store.list_tokens()}
    assert names == {"a", "b"}
    # source 透传
    b = next(i for i in store.list_tokens() if i["name"] == "b")
    assert b["source"] == "x"


def test_duplicate_name_rejected(store):
    store.generate("dup")
    res = store.generate("dup")
    assert res["ok"] is False
    assert "已存在" in res["error"]


def test_missing_name_rejected(store):
    assert store.generate("").get("ok") is False


def test_legacy_single_token_still_works(store):
    store.config.app_token = "legacy-secret-123"
    rec = store.verify("legacy-secret-123")
    assert rec is not None
    assert rec["name"] == "legacy"


def test_legacy_fallback_only_when_multi_misses(store):
    # 多令牌存在但令牌不匹配时，不应误命中遗留单令牌
    store.generate("tvpilot")
    store.config.app_token = "legacy-secret-123"
    assert store.verify("legacy-secret-123") is not None  # 遗留兜底生效
    assert store.verify("app_definitely-wrong") is None
