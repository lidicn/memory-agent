"""豆包管家令牌的鉴权与路径白名单测试。

管家是外部服务（doubao-butler），用独立 ``butler_token`` 访问本服务的窄接口。
该令牌**不应该**拿到整个 WebUI 的权限，因此这里锁死两件事：
1. 令牌有效时只放行 ``BUTLER_ENDPOINTS`` 白名单；
2. 令牌无效 / 未配置时，白名单路径同样拒访。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import app as app_module  # noqa: E402
from memory_agent.app import AuthMiddleware  # noqa: E402
from memory_agent.config import Config  # noqa: E402

TOKEN = "btl_test_token_abcdef"


@pytest.fixture
def butler_config(monkeypatch):
    cfg = Config(butler_token=TOKEN)
    monkeypatch.setattr(app_module, "get_config", lambda: cfg)
    return cfg


async def _probe(path: str, headers: dict) -> tuple[bool, int]:
    """返回 (是否放行到下游, 响应状态码)。"""
    reached: list = []
    statuses: list = []

    async def downstream(scope, receive, send):
        reached.append(scope.get("state", {}).get("user"))

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        if message.get("type") == "http.response.start":
            statuses.append(message.get("status"))

    scope = {
        "type": "http",
        "path": path,
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }
    await AuthMiddleware(downstream)(scope, receive, send)
    return bool(reached), (statuses[0] if statuses else 200)


def _bearer(token: str = TOKEN) -> dict:
    return {"authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_butler_token_allowed_on_whitelist(butler_config):
    for path in ("/api/members", "/api/members/abc123",
                 "/api/vision/presence", "/api/insights/member-schedule",
                 "/api/tv/state", "/api/tv/screenshot", "/api/tv/analyze"):
        reached, status = await _probe(path, _bearer())
        assert reached, f"{path} 应放行"
        assert status == 200


@pytest.mark.asyncio
async def test_butler_token_rejected_outside_whitelist(butler_config):
    for path in ("/api/config", "/api/behaviors", "/api/vision/status"):
        reached, status = await _probe(path, _bearer())
        assert not reached, f"{path} 不应放行"
        assert status == 403


@pytest.mark.asyncio
async def test_wrong_token_rejected(butler_config):
    reached, status = await _probe("/api/vision/presence", _bearer("btl_wrong"))
    assert not reached
    assert status == 401


@pytest.mark.asyncio
async def test_channel_closed_when_unconfigured(monkeypatch):
    """未配置 butler_token 时通道关闭 —— 避免默认空串被当成有效令牌。"""
    monkeypatch.setattr(app_module, "get_config", lambda: Config(butler_token=""))
    reached, status = await _probe("/api/vision/presence", _bearer(""))
    assert not reached
    assert status == 401


@pytest.mark.asyncio
async def test_anonymous_rejected(butler_config):
    reached, status = await _probe("/api/members", {})
    assert not reached
    assert status == 401
