"""第六轮审计（长跑稳定性）修复的运行时回归锁。

覆盖三项实测复现过的缺陷：
* CRITICAL-1 —— 幂等键「查缓存 → 执行 → 写缓存」三步跨线程竞态（同 key 并发会各自执行）；
* M-1 —— 幂等 TTL 以小时配置、却按 `%Y-%m-%d` 存，实际存活 24.5~48h 且小时级 TTL 全塌成同一天；
* M-2 / M-3 —— LLM 与取帧的重试没有退避/抖动，故障时并发请求齐步重试。

静态半边（锁纪律）在 tests/test_vma_r6_db_lock_discipline.py。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import llm_client  # noqa: E402
from memory_agent import vision_service as vs  # noqa: E402
from memory_agent.store import (  # noqa: E402
    IDEMPOTENCY_TS_FMT,
    Store,
    now_local,
)


def _store() -> Store:
    db = os.path.join(tempfile.mkdtemp(prefix="ma_r6_"), "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    return s


def _set_expires(store: Store, idem_key: str, ts: str) -> None:
    with store._db() as conn:
        conn.execute("UPDATE idempotency_keys SET expires_at=? WHERE idem_key=?",
                     (ts, idem_key))
        conn.commit()


def _get_expires(store: Store, idem_key: str) -> str:
    with store._db() as conn:
        row = conn.execute("SELECT expires_at FROM idempotency_keys WHERE idem_key=?",
                           (idem_key,)).fetchone()
    return row["expires_at"] if row else ""


# ── CRITICAL-1：并发同 key 只能有一次执行 ────────────────────────────────

def test_c1_concurrent_same_key_executes_once():
    store = _store()
    n = 12
    barrier = threading.Barrier(n)
    states: list[str] = []
    executed: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker():
        try:
            barrier.wait(15)
            claim = store.reserve_idempotency("dup", "some_tool", ttl_hours=1)
            with guard:
                states.append(claim["state"])
                if claim["state"] == "reserved":
                    executed.append("dup")
                    store.finalize_idempotency("dup", '{"ok": true}', ttl_hours=1)
        except BaseException as exc:  # noqa: BLE001
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert not errors, errors
    assert states.count("reserved") == 1
    assert len(executed) == 1, f"同 key 并发执行了 {len(executed)} 次"
    # 其余调用方要么看到在跑的占位，要么拿到已完成的结果回放——但都不再执行工具
    assert states.count("in_flight") + states.count("done") == n - 1
    cached = store.get_idempotency("dup")
    assert cached and cached["result_text"] == '{"ok": true}'
    assert store.pending_idempotency_count() == 0


def test_c1_failed_execution_releases_the_placeholder():
    """失败不缓存：撤占位后同 key 可以重试（原 save_idempotency 语义）。"""
    store = _store()
    assert store.reserve_idempotency("k", "t")["state"] == "reserved"
    assert store.reserve_idempotency("k", "t")["state"] == "in_flight"
    assert store.release_idempotency("k") == 1
    assert store.reserve_idempotency("k", "t")["state"] == "reserved"
    # 已完成的行不受 release 影响
    store.finalize_idempotency("k", '{"ok": 1}')
    assert store.release_idempotency("k") == 0
    assert store.get_idempotency("k")["result_text"] == '{"ok": 1}'


def test_c1_pending_row_is_not_a_cache_hit():
    store = _store()
    store.reserve_idempotency("p", "t")
    assert store.get_idempotency("p") is None


# ── M-1：TTL 的存储粒度必须是秒，不是天 ─────────────────────────────────

def test_m1_hour_ttl_does_not_collapse_to_one_date():
    store = _store()
    store.save_idempotency("h1", "t", "{}", ttl_hours=1)
    store.save_idempotency("h12", "t", "{}", ttl_hours=12)
    store.save_idempotency("h24", "t", "{}", ttl_hours=24)
    e1, e12, e24 = (_get_expires(store, k) for k in ("h1", "h12", "h24"))
    for e in (e1, e12, e24):
        assert len(e) == len("2026-10-02 23:59:59"), e
    # 旧实现三条都写成同一个日期字符串，小时级 TTL 完全失效
    assert len({e1, e12, e24}) == 3


def test_m1_expiry_is_compared_in_seconds():
    store = _store()
    store.save_idempotency("soon", "t", '{"ok": 1}', ttl_hours=24)
    assert store.get_idempotency("soon") is not None
    # 30 秒后到期：按日期比较会说"今天还没过完"，按时间戳比较必须判过期
    _set_expires(store, "soon",
                 (now_local(0.0) + timedelta(seconds=30)).strftime(IDEMPOTENCY_TS_FMT))
    assert store.get_idempotency("soon") is not None
    _set_expires(store, "soon",
                 (now_local(0.0) - timedelta(seconds=30)).strftime(IDEMPOTENCY_TS_FMT))
    assert store.get_idempotency("soon") is None


def test_m1_legacy_date_only_rows_are_normalized_on_restart():
    tmp = tempfile.mkdtemp(prefix="ma_r6_legacy_")
    db = os.path.join(tmp, "test.db")
    s = Store(db, tz_offset_hours=0.0)
    s.init_schema()
    with s._db() as conn:
        conn.execute(
            """INSERT INTO idempotency_keys
               (idem_key, tool, result_text, is_error, created_at, expires_at, state)
               VALUES ('old', 't', '{}', 0, '2026-10-01T00:00:00', ?, 'done')""",
            (now_local(0.0).strftime("%Y-%m-%d"),),
        )
        conn.commit()
    # 重启自检：同一颗 DB 再走一次建表入口
    Store(db, tz_offset_hours=0.0).init_schema()
    assert _get_expires(s, "old").endswith(" 23:59:59")


def test_m1_purge_sweeps_stale_pending_rows():
    store = _store()
    store.reserve_idempotency("stale", "t")
    assert store.pending_idempotency_count() == 1
    _set_expires(store, "stale",
                 (now_local(0.0) - timedelta(minutes=5)).strftime(IDEMPOTENCY_TS_FMT))
    assert store.purge_idempotency() >= 1
    assert store.pending_idempotency_count() == 0


# ── M-2：LLM 重试要有指数退避 + 抖动，且 4xx 不重试 ──────────────────────

def test_m2_retry_delay_is_jittered_exponential_backoff():
    draws = [llm_client._retry_delay(0) for _ in range(40)]
    assert all(0.0 <= d <= llm_client._RETRY_BASE_SECONDS for d in draws)
    assert len(set(draws)) > 1, "重试间隔没有抖动，并发请求会齐步重试"
    for attempt in range(4):
        cap = min(llm_client._RETRY_MAX_SECONDS,
                  llm_client._RETRY_BASE_SECONDS * (2 ** attempt))
        sample = [llm_client._retry_delay(attempt) for _ in range(20)]
        assert all(0.0 <= d <= cap for d in sample)
    assert llm_client._retry_delay(0, "3") == 3.0
    assert llm_client._retry_delay(0, "0") == 0.0
    # 超出上限 / 非数字的 Retry-After 不采纳，回落到抖动退避
    assert llm_client._retry_delay(0, "9999") <= llm_client._RETRY_BASE_SECONDS
    assert llm_client._retry_delay(0, "gmt-date") <= llm_client._RETRY_BASE_SECONDS


class _Resp:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _FakeClient:
    """替身 httpx.AsyncClient：按脚本回答状态码，记录每次请求。"""

    seen: list[str] = []
    script: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        item = _FakeClient.script.pop(0)
        _FakeClient.seen.append(url)
        if isinstance(item, Exception):
            raise item
        status, text, hdrs = item
        return _Resp(status, text, hdrs)


def _provider() -> "llm_client.LLMProvider":
    return llm_client.LLMProvider(
        {"name": "fake", "model": "m", "api_key": "k", "api_url": "http://x/v1"}
    )


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.seen = []
    _FakeClient.script = []
    monkeypatch.setattr(llm_client.httpx, "AsyncClient", _FakeClient)
    calls: list = []

    def spy(attempt: int, retry_after=None) -> float:
        calls.append((attempt, retry_after))
        return 0.0

    monkeypatch.setattr(llm_client, "_retry_delay", spy)
    yield calls


def test_m2_5xx_is_retried_with_backoff_then_recovers(fake_http):
    _FakeClient.script = [
        (503, "upstream busy", {"Retry-After": "7"}),
        (200, '{"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}], '
              '"usage": {}}', {}),
    ]
    out = asyncio.run(_provider().chat([{"role": "user", "content": "hi"}]))
    assert out["content"] == "hello"
    assert len(_FakeClient.seen) == 2
    assert fake_http == [(0, "7")]


def test_m2_5xx_exhausts_and_reports_status(fake_http):
    _FakeClient.script = [(503, "busy", {}), (503, "busy", {})]
    with pytest.raises(llm_client.LLMError) as exc:
        asyncio.run(_provider().chat([{"role": "user", "content": "hi"}]))
    assert "503" in str(exc.value)
    assert len(_FakeClient.seen) == llm_client._LLM_MAX_ATTEMPTS


def test_m2_4xx_fails_fast_without_retry(fake_http):
    _FakeClient.script = [(401, "bad key", {})]
    with pytest.raises(llm_client.LLMError) as exc:
        asyncio.run(_provider().chat([{"role": "user", "content": "hi"}]))
    assert "HTTP 401" in str(exc.value)
    assert len(_FakeClient.seen) == 1
    assert fake_http == [], "4xx 是请求本身不对，不该退避重试"


def test_m2_transport_error_is_retried(fake_http):
    _FakeClient.script = [
        httpx.ReadTimeout("gone quiet", request=httpx.Request("POST", "http://x/v1/chat/completions")),
        (200, '{"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}', {}),
    ]
    out = asyncio.run(_provider().chat([{"role": "user", "content": "hi"}]))
    assert out["content"] == "ok"
    assert fake_http == [(0, None)]


# ── M-3：取帧退避加抖动 ─────────────────────────────────────────────────

def _vision_service() -> "vs.VisionService":
    cfg = SimpleNamespace(go2rtc_base_url="http://go2rtc:1984",
                          go2rtc_user="", go2rtc_pass="")
    return vs.VisionService(cfg, None, None)


def test_m3_frame_backoff_is_jittered(monkeypatch):
    svc = _vision_service()
    req = httpx.Request("GET", "http://go2rtc:1984/api/frame.jpeg")
    attempts: list[int] = []

    def fake_get(url, auth=None, timeout=None):
        attempts.append(1)
        raise httpx.ConnectError("connection refused", request=req)

    sleeps: list[float] = []
    monkeypatch.setattr(vs.httpx, "get", fake_get)
    monkeypatch.setattr(vs.time, "sleep", lambda s: sleeps.append(s))
    # 抖动系数固定为下界，验证 sleep 确实乘了随机因子而非线性定值
    monkeypatch.setattr(vs.random, "uniform", lambda a, b: a)
    with pytest.raises(httpx.ConnectError):
        svc.fetch_frame("cam", timeout=1.0, retries=2)
    assert len(attempts) == 3
    assert sleeps == [0.5 * 1 * 0.5, 0.5 * 2 * 0.5], sleeps


def test_m3_frame_backoff_varies_between_calls(monkeypatch):
    svc = _vision_service()
    req = httpx.Request("GET", "http://go2rtc:1984/api/frame.jpeg")

    def fake_get(url, auth=None, timeout=None):
        raise httpx.ConnectError("nope", request=req)

    runs: list[list[float]] = []
    monkeypatch.setattr(vs.httpx, "get", fake_get)
    for _ in range(8):
        sleeps: list[float] = []
        monkeypatch.setattr(vs.time, "sleep", lambda s, bag=sleeps: bag.append(s))
        with pytest.raises(httpx.ConnectError):
            svc.fetch_frame("cam", timeout=1.0, retries=2)
        assert 0.25 <= sleeps[0] <= 0.75
        assert 0.5 <= sleeps[1] <= 1.5
        runs.append(sleeps)
    assert len({tuple(r) for r in runs}) > 1, "退避没有抖动"
