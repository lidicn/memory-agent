"""应用令牌（TVPilot / DeskPilot）管理 —— v0.6 多 app_token 分发与吊销。

安全模型沿用 ``mcp_tokens``：
* 明文 Token 只在生成的那一次返回给前端，落盘只存 ``sha256`` 与前 12 位前缀；
* 校验使用 ``secrets.compare_digest`` 常量时间比对，避免时序侧信道；
* 同时兼容 v0.3 遗留的单 ``app_token``（环境变量 ``APP_TOKEN``），保证既有部署不断连。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from typing import Any

from .config import get_config
from .store import now_local

TOKEN_PREFIX = "app_"
PREFIX_LEN = 12
LAST_USED_THROTTLE_SECONDS = 15


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AppTokenStore:
    def __init__(self, config: Any = None):
        self.config = config
        self._lock = threading.RLock()
        self._last_persist: dict[str, float] = {}

    def _cfg(self):
        return self.config or get_config()

    def _now(self) -> str:
        tz = getattr(self._cfg(), "tz_offset_hours", 8)
        return now_local(tz).isoformat(timespec="seconds")

    # ── 生成 / 吊销 / 列表 ───────────────────────────────────────────────────

    def generate(self, name: str, source: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "缺少令牌名称"}
        if len(name) > 64:
            return {"ok": False, "error": "名称过长（上限 64 字符）"}
        source = (source or "").strip()
        with self._lock:
            tokens = dict(self._cfg().app_tokens or {})
            if name in tokens:
                return {"ok": False, "error": f"名称已存在: {name}"}
            plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
            record = {
                "hash": _hash(plain),
                "prefix": plain[:PREFIX_LEN],
                "created_at": self._now(),
                "last_used_at": "",
                "use_count": 0,
                "source": source,
            }
            tokens[name] = record
            cfg = self._cfg()
            cfg.app_tokens = tokens
            cfg.save()
        print(f"[AppToken] 已生成应用令牌: {name} ({record['prefix']}…)")
        return {"ok": True, "name": name, "token": plain, "prefix": record["prefix"], "source": source}

    def revoke(self, name: str) -> bool:
        with self._lock:
            tokens = dict(self._cfg().app_tokens or {})
            if name not in tokens:
                return False
            del tokens[name]
            cfg = self._cfg()
            cfg.app_tokens = tokens
            cfg.save()
        print(f"[AppToken] 已吊销应用令牌: {name}")
        return True

    def list_tokens(self) -> list[dict]:
        tokens = self._cfg().app_tokens or {}
        items = []
        for name, value in tokens.items():
            if isinstance(value, dict):
                items.append(
                    {
                        "name": name,
                        "prefix": value.get("prefix", ""),
                        "created_at": value.get("created_at", ""),
                        "last_used_at": value.get("last_used_at", ""),
                        "use_count": int(value.get("use_count") or 0),
                        "source": value.get("source", ""),
                    }
                )
        return sorted(items, key=lambda x: x["created_at"], reverse=True)

    def count(self) -> int:
        return len(self._cfg().app_tokens or {})

    # ── 校验 ───────────────────────────────────────────────────────────────

    def verify(self, token: str) -> dict | None:
        """校验令牌；命中返回 ``{name, source}``，否则 ``None``。

        多令牌优先；全部未命中时回退到 v0.3 遗留单 ``app_token``。
        """
        if not token:
            return None
        token = token.strip()
        digest = _hash(token)
        with self._lock:
            tokens = self._cfg().app_tokens or {}
            for name, value in tokens.items():
                if isinstance(value, dict) and value.get("hash"):
                    if secrets.compare_digest(value["hash"], digest):
                        self._touch(name)
                        return {"name": name, "source": value.get("source", "")}
        # 兼容遗留单令牌
        legacy = (self._cfg().app_token or "").strip()
        if legacy and secrets.compare_digest(_hash(legacy), digest):
            return {"name": "legacy", "source": ""}
        return None

    def _touch(self, name: str) -> None:
        """更新最后使用时间与调用次数（写盘节流，避免每次调用都重写 config.json）。"""
        import time

        with self._lock:
            tokens = dict(self._cfg().app_tokens or {})
            record = tokens.get(name)
            if not isinstance(record, dict):
                return
            record = {
                **record,
                "last_used_at": self._now(),
                "use_count": int(record.get("use_count") or 0) + 1,
            }
            tokens[name] = record
            cfg = self._cfg()
            cfg.app_tokens = tokens

            now = time.time()
            if now - self._last_persist.get(name, 0.0) < LAST_USED_THROTTLE_SECONDS:
                return
            self._last_persist[name] = now
            try:
                cfg.save()
            except Exception as exc:  # noqa: BLE001
                print(f"[AppToken] 更新最后使用时间失败: {exc}")


def get_app_token_store() -> AppTokenStore:
    return AppTokenStore(get_config())
