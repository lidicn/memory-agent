"""启动状态机——非阻塞健康检查（DCD 20261010 Q3 批准）。

启动序列：
    starting → db_check → ready

- starting：Uvicorn 刚起，尚未开始 DB 检查
- db_check：正在跑 check_and_recover（quick_check / integrity_check）
- ready：DB 检查通过，服务就绪

health() 返回严格三键：{ok, starting, stage}。
失败细节放在 get_health_status() 供 /api/health 合并。
"""
from __future__ import annotations

import threading
from typing import Any

_state = "starting"
_detail: dict[str, Any] = {}
_lock = threading.Lock()


def set_state(state: str, **detail: Any) -> None:
    """切换状态，附带可选详情。"""
    global _state
    with _lock:
        _state = state
        if detail:
            _detail.update(detail)


def get_state() -> str:
    with _lock:
        return _state


def health_dict() -> dict[str, Any]:
    """liveness 端点用：{ok, starting, stage} 三键。"""
    with _lock:
        return {
            "ok": _state == "ready",
            "starting": _state != "ready",
            "stage": _state,
            "service": "memory-agent",
        }


def get_health_status() -> dict[str, Any]:
    """/api/health 合并用：状态 + 详情。"""
    with _lock:
        d = health_dict()
        d["detail"] = dict(_detail)
        return d
