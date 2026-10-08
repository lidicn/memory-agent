"""v0.9 离线降级单测：通用断路器三态行为。"""

import os
import sys
import time

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    all_states,
    get_breaker,
)


def test_breaker_opens_and_half_opens():
    b = CircuitBreaker("t1", fail_threshold=2, cooldown_seconds=0.2)
    assert b.allow() is True
    b.record_failure("e1")
    assert b.allow() is True  # 未达阈值仍 closed
    b.record_failure("e2")
    assert b.allow() is False  # 达阈值 → open
    assert b.state()["state"] == "open"
    time.sleep(0.25)
    assert b.allow() is True  # 冷却到 → half_open 放行一次
    assert b.state()["state"] == "half_open"


def test_success_resets():
    b = CircuitBreaker("t2", fail_threshold=1, cooldown_seconds=60)
    b.record_failure("boom")
    assert b.allow() is False
    b.record_success()
    assert b.state()["state"] == "closed"
    assert b.allow() is True


def test_registry_singleton():
    b1 = get_breaker("shared")
    b2 = get_breaker("shared")
    assert b1 is b2
    names = {s["name"] for s in all_states()}
    assert "shared" in names
