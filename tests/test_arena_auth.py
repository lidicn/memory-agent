"""竞技场令牌（kind=arena）的鉴权与路径白名单测试。

竞技场是外部服务（AutoFlow），用独立 ``arena_`` 令牌访问本服务的窄接口。
该令牌**不应该**拿到整个 WebUI 的权限，因此锁死两件事：
1. 令牌有效时只放行 ``ARENA_ENDPOINTS`` 白名单（快照接口）；
2. 令牌无效 / 越权时，白名单路径同样拒访。

鉴权由 app.py AuthMiddleware 把关；ACP 工具面由 acp_auth.py 把关
（见 test_arena_acp_auth 的思路，运行时集成验证）。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import app as app_module  # noqa: E402
from memory_agent.app import AuthMiddleware  # noqa: E402
from memory_agent.app import ARENA_ENDPOINTS  # noqa: E402
from memory_agent.config import Config  # noqa: E402

ARENA_TOKEN = "arena_test_token_xyz"


class _StubTokens:
    def __init__(self):
        self._by_token = {ARENA_TOKEN: ("arena-cli", "arena")}

    def verify(self, token):
        return self._by_token.get(token, (None, None))[0]

    def kind(self, name):
        for t, (n, k) in self._by_token.items():
            if n == name:
                return k
        return None


class _StubRuntime:
    def __init__(self):
        self.tokens = _StubTokens()


@pytest.fixture
def arena_runtime(monkeypatch):
    rt = _StubRuntime()
    monkeypatch.setattr(app_module, "get_runtime", lambda: rt)
    monkeypatch.setattr(app_module, "get_config", lambda: Config())
    return rt


async def _probe(path: str, headers: dict) -> tuple[bool, int]:
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


def _bearer(token: str = ARENA_TOKEN) -> dict:
    return {"authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_arena_token_allowed_on_whitelist(arena_runtime):
    for path in ("/api/arena/snapshot", "/api/arena/snapshots",
                 "/api/arena/snapshots/study_room"):
        reached, status = await _probe(path, _bearer())
        assert reached, f"{path} 应放行"
        assert status == 200


@pytest.mark.asyncio
async def test_arena_token_rejected_outside_whitelist(arena_runtime):
    for path in ("/api/config", "/api/behaviors", "/api/members"):
        reached, status = await _probe(path, _bearer())
        assert not reached, f"{path} 不应放行"
        assert status == 403


@pytest.mark.asyncio
async def test_wrong_token_rejected(arena_runtime):
    reached, status = await _probe("/api/arena/snapshot", _bearer("arena_wrong"))
    assert not reached
    assert status == 401


@pytest.mark.asyncio
async def test_anonymous_rejected(arena_runtime):
    reached, status = await _probe("/api/arena/snapshot", {})
    assert not reached
    assert status == 401


def test_arena_endpoints_constant():
    assert "/api/arena/snapshot" in ARENA_ENDPOINTS
    assert "/api/arena/snapshots" in ARENA_ENDPOINTS
