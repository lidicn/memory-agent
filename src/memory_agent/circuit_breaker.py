"""通用断路器（v0.9 离线降级）。

依赖（LLM / embedding / HA …）不可达时，避免每次调用都付出超时代价：
连续失败达阈值 → **打开**（短路，快速失败 / 降级到纯规则）；冷却后**半开**
（放行一次探测），成功则复位。状态经 ``all_states()`` 暴露给健康/指标端点。

纯标准库、线程安全、进程级单例（重启清零，与 MCP 统计口径一致）。
"""

from __future__ import annotations

import threading
import time


class CircuitBreaker:
    """三态断路器：closed（正常）/ open（短路）/ half_open（探测）。"""

    def __init__(self, name: str, fail_threshold: int = 3,
                 cooldown_seconds: float = 60.0) -> None:
        self.name = name
        self.fail_threshold = max(1, int(fail_threshold))
        self.cooldown_seconds = max(0.1, float(cooldown_seconds))
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = 0.0
        self._half_open = False
        self._last_error = ""

    def allow(self) -> bool:
        """是否放行本次调用：closed 放行；open 冷却期内拒绝；冷却到则半开放行一次。"""
        with self._lock:
            if self._failures < self.fail_threshold:
                return True
            if (time.monotonic() - self._opened_at) >= self.cooldown_seconds:
                self._half_open = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = 0.0
            self._half_open = False
            self._last_error = ""

    def record_failure(self, error: str = "") -> None:
        with self._lock:
            self._failures += 1
            if error:
                self._last_error = error[:200]
            if self._failures >= self.fail_threshold:
                self._opened_at = time.monotonic()
                self._half_open = False

    def state(self) -> dict:
        with self._lock:
            if self._failures < self.fail_threshold:
                st = "closed"
            elif self._half_open:
                st = "half_open"
            else:
                st = "open"
            return {
                "name": self.name,
                "state": st,
                "failures": self._failures,
                "threshold": self.fail_threshold,
                "cooldown_seconds": self.cooldown_seconds,
                "opened_age_seconds": (
                    round(time.monotonic() - self._opened_at, 1) if self._opened_at else None
                ),
                "last_error": self._last_error,
            }


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_breaker(name: str, fail_threshold: int = 3,
                cooldown_seconds: float = 60.0) -> CircuitBreaker:
    """取（或创建）进程级命名断路器。"""
    with _BREAKERS_LOCK:
        b = _BREAKERS.get(name)
        if b is None:
            b = CircuitBreaker(name, fail_threshold, cooldown_seconds)
            _BREAKERS[name] = b
        return b


def all_states() -> list[dict]:
    """所有断路器状态快照（供健康/指标端点）。"""
    with _BREAKERS_LOCK:
        return [b.state() for b in _BREAKERS.values()]
