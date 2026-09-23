"""MCP 接入 Token 管理

安全模型
--------
* 明文 Token 只在生成的那一次返回给前端，落盘只存 ``sha256`` 与前 12 位前缀。
* 校验使用 ``secrets.compare_digest`` 常量时间比对，避免时序侧信道。
* 存量明文（``config.agent_tokens`` 里的 ``{name: "mcp_xxx"}`` 与
  ``config.mcp_auth_token``）在启动时一次性迁移为哈希结构，
  **保证用户现有 Agent 不断连**。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from typing import Any

from .mcp_scopes import ALL_SCOPES, DEFAULT_SCOPES, normalize
from .store import now_local

TOKEN_PREFIX = "mcp_"
PREFIX_LEN = 12
LAST_USED_THROTTLE_SECONDS = 15
# P0 安全修复：无 scopes 字段令牌的告警去重（每令牌只报一次）
_LEGACY_SCOPE_WARNED: set[str] = set()
# P0 安全修复：存量/遗留令牌的默认权限改为只读（fail-safe）。
# 原设计为「保证现有 Agent 不断连」而视作全权（read+write+admin），
# 但这导致无 scopes 字段的令牌可执行写操作，是 fail-open 漏洞。
# 需要写权限的令牌必须显式设置 scopes（WebUI「MCP 接入」可调整）。
LEGACY_SCOPES = list(DEFAULT_SCOPES)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class MCPTokenStore:
    def __init__(self, config):
        self.config = config
        self._lock = threading.RLock()
        self._last_persist: dict[str, float] = {}

    def _now(self) -> str:
        """按用户配置的时区打时间戳。

        容器内 ``datetime.now()`` 通常是 UTC，直接落盘会让「最近使用」
        比真实调用时间少 8 小时 —— 这正是 MCP 页面时间显示不对的原因。
        """
        tz = getattr(self.config, "tz_offset_hours", 8)
        return now_local(tz).isoformat(timespec="seconds")

    # ── 迁移 ─────────────────────────────────────────────────────────────

    def migrate_legacy(self) -> int:
        """把明文 Token 转为哈希结构。幂等，可重复执行。"""
        with self._lock:
            tokens: dict[str, Any] = dict(self.config.agent_tokens or {})
            migrated = 0

            for name, value in list(tokens.items()):
                if isinstance(value, str) and value:
                    tokens[name] = {
                        "hash": _hash(value),
                        "prefix": value[:PREFIX_LEN],
                        "created_at": self._now(),
                        "last_used_at": "",
                        "migrated": True,
                    }
                    migrated += 1

            legacy = (self.config.mcp_auth_token or "").strip()
            if legacy:
                legacy_hash = _hash(legacy)
                already = any(
                    isinstance(v, dict) and v.get("hash") == legacy_hash
                    for v in tokens.values()
                )
                if not already:
                    tokens["legacy-default"] = {
                        "hash": legacy_hash,
                        "prefix": legacy[:PREFIX_LEN],
                        "created_at": self._now(),
                        "last_used_at": "",
                        "migrated": True,
                    }
                    migrated += 1

            if migrated:
                self.config.agent_tokens = tokens
                try:
                    self.config.save()
                except Exception as exc:
                    print(f"[MCPToken] 迁移结果保存失败: {exc}")
            return migrated

    # ── 生成 / 撤销 / 列表 ───────────────────────────────────────────────

    def generate(
        self, name: str, prefix: str | None = None, kind: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        """生成令牌。

        v0.7.5：新增 ``scopes``，未指定时**默认只读**（``read``）。
        写类工具（见 ``mcp_scopes.WRITE_TOOLS``）需要显式带 ``write``。
        """
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "缺少 Token 名称"}
        if len(name) > 64:
            return {"ok": False, "error": "名称过长（上限 64 字符）"}
        # P1-20: kind 白名单校验 —— 只允许 mcp / acp；
        # debug kind 只能通过 /api/debug/tokens 专门接口生成（require_admin + 环境限制），
        # 防止低权限入口通过 create_token 传 kind="debug" 绕过 admin 校验。
        allowed_kinds = {"mcp", "acp"}
        kind_val = (kind or "mcp").strip().lower()
        if kind_val not in allowed_kinds:
            return {"ok": False, "error": f"非法 kind: {kind_val}（允许: mcp / acp；debug 请用调试令牌专用接口）"}
        prefix = prefix or TOKEN_PREFIX
        granted = normalize(scopes) if scopes is not None else list(DEFAULT_SCOPES)
        with self._lock:
            tokens = dict(self.config.agent_tokens or {})
            if name in tokens:
                return {"ok": False, "error": f"名称已存在: {name}"}
            plain = prefix + secrets.token_urlsafe(32)
            record = {
                "hash": _hash(plain),
                "prefix": plain[:PREFIX_LEN],
                "created_at": self._now(),
                "last_used_at": "",
                "use_count": 0,
                "kind": kind_val,
                "scopes": granted,
            }
            tokens[name] = record
            self.config.agent_tokens = tokens
            self.config.save()
        print(f"[MCPToken] 已生成 Token: {name} ({record['prefix']}…)")
        return {"ok": True, "name": name, "token": plain, **record}

    def kind(self, name: str) -> str:
        """返回 Token 用途：``mcp``（默认）或 ``debug``。"""
        rec = (self.config.agent_tokens or {}).get(name)
        if isinstance(rec, dict):
            return rec.get("kind", "mcp")
        return "mcp"

    def scopes(self, name: str) -> list[str]:
        """返回令牌权限。

        P0 安全修复：存量/迁移令牌没有 ``scopes`` 字段 → 视为只读（fail-safe），
        不再默认全权。需要写权限的令牌必须显式设置 scopes。
        """
        rec = (self.config.agent_tokens or {}).get(name)
        if isinstance(rec, dict) and rec.get("scopes"):
            return normalize(rec.get("scopes"))
        # P0 安全修复：无 scopes 字段的令牌按只读处理，并告警（节流：每令牌只报一次）
        if name not in _LEGACY_SCOPE_WARNED:
            _LEGACY_SCOPE_WARNED.add(name)
            _log.warning(
                "【安全】令牌 '%s' 无 scopes 字段，按只读处理（原设计为全权，已修复为 fail-safe）。"
                "如需写权限，请在 WebUI「MCP 接入」显式设置 scopes。",
                name,
            )
        return list(LEGACY_SCOPES)

    def update_scopes(self, name: str, scopes) -> bool:
        """调整令牌权限（WebUI 收紧/放开用）。"""
        with self._lock:
            tokens = dict(self.config.agent_tokens or {})
            rec = tokens.get(name)
            if not isinstance(rec, dict):
                return False
            rec = {**rec, "scopes": normalize(scopes)}
            tokens[name] = rec
            self.config.agent_tokens = tokens
            self.config.save()
        return True

    def revoke(self, name: str) -> bool:
        with self._lock:
            tokens = dict(self.config.agent_tokens or {})
            if name not in tokens:
                return False
            del tokens[name]
            self.config.agent_tokens = tokens
            self.config.save()
        print(f"[MCPToken] 已撤销 Token: {name}")
        return True

    def list_tokens(self) -> list[dict]:
        tokens = self.config.agent_tokens or {}
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
                        "kind": value.get("kind", "mcp"),
                        "scopes": normalize(value.get("scopes")) if value.get("scopes")
                                  else list(LEGACY_SCOPES),
                        "scopes_inherited": not bool(value.get("scopes")),
                        "migrated": bool(value.get("migrated")),
                    }
                )
            else:
                # 尚未迁移的明文条目，只暴露前缀
                items.append(
                    {
                        "name": name,
                        "prefix": str(value)[:PREFIX_LEN],
                        "created_at": "",
                        "last_used_at": "",
                        "use_count": 0,
                        "kind": "mcp",
                        "scopes": list(LEGACY_SCOPES),
                        "scopes_inherited": True,
                        "migrated": False,
                    }
                )
        return sorted(items, key=lambda x: x["created_at"], reverse=True)

    def count(self) -> int:
        return len(self.config.agent_tokens or {})

    # ── 校验 ─────────────────────────────────────────────────────────────

    def verify(self, token: str) -> str | None:
        """校验 Token，命中返回名称，否则返回 None。"""
        if not token:
            return None
        token = token.strip()
        digest = _hash(token)
        matched: str | None = None
        for name, value in (self.config.agent_tokens or {}).items():
            if isinstance(value, dict):
                stored = value.get("hash", "")
            else:
                stored = _hash(str(value))
            if stored and secrets.compare_digest(stored, digest):
                matched = name
                break
        if matched is None:
            legacy = (self.config.mcp_auth_token or "").strip()
            if legacy and secrets.compare_digest(_hash(legacy), digest):
                matched = "legacy-default"
        if matched:
            self._touch(matched)
        return matched

    def _touch(self, name: str) -> None:
        """更新最后使用时间与调用次数。

        内存里**每次都更新**（保证页面刷新即可看到最新调用时间），
        只对写盘做节流，避免每次 MCP 调用都重写 config.json。
        """
        import time

        with self._lock:
            tokens = dict(self.config.agent_tokens or {})
            record = tokens.get(name)
            if not isinstance(record, dict):
                return
            record = {
                **record,
                "last_used_at": self._now(),
                "use_count": int(record.get("use_count") or 0) + 1,
            }
            tokens[name] = record
            self.config.agent_tokens = tokens

            now = time.time()
            if now - self._last_persist.get(name, 0.0) < LAST_USED_THROTTLE_SECONDS:
                return
            self._last_persist[name] = now
            try:
                self.config.save()
            except Exception as exc:
                print(f"[MCPToken] 更新最后使用时间失败: {exc}")
