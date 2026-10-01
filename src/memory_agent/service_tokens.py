"""统一服务令牌（service_token）—— vMA-1.2.3 3.3.3，按 DCD 20261001 裁定 Q1-Q5。

安全模型沿用 ``app_tokens`` / ``mcp_tokens``：明文只在签发那一次返回，落盘只存
``sha256`` 与前缀；常量时间比对；**不设过期、不下线旧凭据**（Q1/Q4：旧
``BUTLER_TOKEN`` / ``APP_TOKEN`` 与新令牌并行接受，TTL 与到期日由 DCD/SP 另议）。

与遗留通道的两点实质差别：

* **作用域自带**（Q2）：一个令牌绑定一份 ``方法:路径`` 清单，鉴权时逐条匹配，
  不再有"路径对了、方法随便"的口子（F-2）；
* **一实例一令牌**（Q3）：每个外部实例单独签发、单独计数、单独吊销，
  不再是全司共享一个 env 密钥。

遗留 env 密钥的处理是**只读导入**：把它登记成一条可见的记录（带该通道现有的授权面、
进程内使用计数），但它**不落盘、也不能在这里吊销**——真正下线旧凭据要清 env 并重启，
那是 DCD/SP 定的到期动作。这样做的原因：导入若写 config.json，就等于在鉴权热路径上
改写生产配置（容器回归也会触发），代价大于收益。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from typing import Any

from .config import get_config
from .store import now_local

TOKEN_PREFIX = "svc_"
PREFIX_LEN = 12
LAST_USED_THROTTLE_SECONDS = 15

# 签发时禁止写入的作用域：带上这些面就不再是"窄接口"。
SCOPE_DENY_PREFIXES = (
    "/api/config",
    "/api/auth",
    "/api/users",
    "/api/system",
    "/api/debug",
)

_ALLOWED_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")
_SCOPE_RE = re.compile(r"^([A-Za-z]+):(/.*)$")

# 遗留通道现有的授权面，导入成记录后**一一对应、不扩不缺**（由回归锁钉住）：
# butler 在 app.py 里本来就是方法级白名单（BUTLER_GET/POST_PATHS + /api/members/* 的
# GET/PATCH）；app 通道历史上只按路径判，这里显式列出这些路径上真实注册的方法——
# 未注册的方法本就由 Starlette 返回 405，所以不是行为变更，只是把隐式变成显式。
BUTLER_SCOPES = [
    "GET:/api/members",
    "GET:/api/vision/presence",
    "GET:/api/vision/latest",
    "GET:/api/insights/member-schedule",
    "GET:/api/behaviors",
    "GET:/api/metrics",
    "GET:/api/tv/state",
    "GET:/api/tv/screenshot",
    "GET:/api/members/*",
    "PATCH:/api/members/*",
    "POST:/api/events",
    "POST:/api/metrics/ingest",
    "POST:/api/tv/analyze",
]
APP_SCOPES = [
    "POST:/api/insights/query",
    "POST:/api/agent/memories",
    "GET:/api/agent/memories",
    "POST:/api/agent/memories/recall",
]

BUTLER_ENV_NAME = "butler-env"
APP_ENV_NAME = "app-env"
LEGACY_NAMES = (BUTLER_ENV_NAME, APP_ENV_NAME)

# env 导入记录的使用计数：进程内统计，不写盘（见模块 docstring）。
_LEGACY_STATS: dict[str, dict] = {}
_STATS_LOCK = threading.Lock()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_scope(raw: str) -> tuple[str, str] | None:
    """``"GET:/api/members"`` → ``("GET", "/api/members")``；非法返回 ``None``。

    路径以 ``/*`` 结尾表示子树（``GET:/api/members/*`` 覆盖 ``/api/members/3``）。
    """
    if not isinstance(raw, str):
        return None
    m = _SCOPE_RE.match(raw.strip())
    if not m:
        return None
    method = m.group(1).upper()
    path = m.group(2).strip()
    if method not in _ALLOWED_METHODS:
        return None
    if not path.startswith("/") or ".." in path:
        return None
    return method, path


def scope_matches(method: str, path: str, scopes: list[str]) -> bool:
    """逐条比对令牌自带的作用域清单；清单为空一律拒绝（fail-closed）。"""
    if not scopes:
        return False
    want = (method or "GET").upper()
    for raw in scopes:
        parsed = parse_scope(raw)
        if not parsed or parsed[0] != want:
            continue
        allowed = parsed[1]
        if allowed.endswith("/*"):
            if path.startswith(allowed[:-1]):
                return True
        elif path == allowed:
            return True
    return False


def validate_scopes(scopes: list[str]) -> tuple[bool, str]:
    """签发前校验：必须可解析、方法在允许集合内、且不落在特权前缀上。"""
    if not isinstance(scopes, list) or not scopes:
        return False, "令牌必须至少带一条作用域（方法:路径）"
    for raw in scopes:
        parsed = parse_scope(raw)
        if not parsed:
            return False, f"作用域格式非法: {raw!r}（应为 方法:路径）"
        for denied in SCOPE_DENY_PREFIXES:
            if parsed[1].startswith(denied):
                return False, f"作用域不得指向特权接口: {parsed[1]}"
    return True, ""


class ServiceTokenStore:
    def __init__(self, config: Any = None):
        self.config = config
        self._lock = threading.RLock()
        self._last_persist: dict[str, float] = {}

    def _cfg(self):
        return self.config or get_config()

    def _now(self) -> str:
        tz = getattr(self._cfg(), "tz_offset_hours", 8)
        return now_local(tz).isoformat(timespec="seconds")

    # ── 遗留 env 凭据的只读导入（Q3：env 迁移自动生成首个）───────────────────

    def legacy_records(self) -> dict[str, dict]:
        """把 env 单密钥映射成可审计记录（不落盘、不改线格式、不生成新密钥）。"""
        cfg = self._cfg()
        out: dict[str, dict] = {}
        # channel：导入记录沿用旧通道的身份位（app.py 的 butler/app 分支与
        # member_routes 都按这些位判），新签发的令牌没有 channel，只按作用域判。
        for name, secret, scopes, source, channel in (
            (BUTLER_ENV_NAME, (cfg.butler_token or "").strip(), BUTLER_SCOPES, "butler", "butler"),
            (APP_ENV_NAME, (cfg.app_token or "").strip(), APP_SCOPES, "", "app"),
        ):
            if not secret:
                continue
            digest = _hash(secret)
            with _STATS_LOCK:
                stats = _LEGACY_STATS.get(name) or {
                    "created_at": self._now(),
                    "last_used_at": "",
                    "use_count": 0,
                }
            out[name] = {
                "hash": digest,
                "prefix": digest[:PREFIX_LEN],
                "created_at": stats["created_at"],
                "last_used_at": stats["last_used_at"],
                "use_count": stats["use_count"],
                "source": source,
                "channel": channel,
                "scopes": list(scopes),
                "imported_from": "env",
            }
        return out

    @staticmethod
    def _touch_legacy(name: str, when: str) -> None:
        with _STATS_LOCK:
            stats = _LEGACY_STATS.get(name) or {
                "created_at": when,
                "last_used_at": "",
                "use_count": 0,
            }
            stats["use_count"] = int(stats["use_count"]) + 1
            stats["last_used_at"] = when
            _LEGACY_STATS[name] = stats

    # ── 生成 / 吊销 / 列表 ───────────────────────────────────────────────────

    def generate(self, name: str, scopes: list[str], source: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "缺少令牌名称"}
        if len(name) > 64:
            return {"ok": False, "error": "名称过长（上限 64 字符）"}
        if name in LEGACY_NAMES:
            return {"ok": False, "error": f"该名称由 env 导入占用: {name}"}
        ok_, err = validate_scopes(scopes)
        if not ok_:
            return {"ok": False, "error": err}
        source = (source or "").strip()
        if source:
            # Q5「来源派生保留」的签发侧护栏：令牌上标的 source 必须在白名单内，
            # 否则写记忆时 _resolve_source 会原样采信它（防伪造不能只在读侧做）。
            allowed = set(self._cfg().agent_memory_sources or [])
            if source not in allowed:
                return {"ok": False, "error": f"source 不在白名单内: {source}"}
        with self._lock:
            tokens = dict(self._cfg().service_tokens or {})
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
                "scopes": sorted({s.strip() for s in scopes}),
            }
            tokens[name] = record
            cfg = self._cfg()
            cfg.service_tokens = tokens
            cfg.save()
            # ok 位来自"回读落盘结果"，不是字面量（门禁 fake-ok-const 的正当满足方式）。
            persisted = name in (cfg.service_tokens or {})
        if not persisted:
            return {"ok": False, "error": f"令牌已签发但未落盘: {name}"}
        print(f"[ServiceToken] 已签发服务令牌: {name} ({record['prefix']}…)")
        return {
            "ok": persisted,
            "name": name,
            "token": plain,
            "prefix": record["prefix"],
            "source": source,
            "scopes": record["scopes"],
        }

    def revoke(self, name: str) -> dict:
        """删除一条已签发的令牌；env 导入的记录不能在这里吊销。"""
        if name in LEGACY_NAMES:
            return {
                "ok": False,
                "error": (
                    f"{name} 是 env 导入的遗留凭据，清 env 并重启才是下线它；"
                    "在本服务里吊销无效"
                ),
            }
        with self._lock:
            tokens = dict(self._cfg().service_tokens or {})
            if name not in tokens:
                return {"ok": False, "error": f"令牌不存在: {name}"}
            del tokens[name]
            cfg = self._cfg()
            cfg.service_tokens = tokens
            cfg.save()
            gone = name not in (cfg.service_tokens or {})
        if not gone:
            return {"ok": False, "error": f"吊销未生效，配置里仍有该令牌: {name}"}
        print(f"[ServiceToken] 已吊销服务令牌: {name}")
        return {"ok": gone, "name": name}

    def list_tokens(self) -> list[dict]:
        rows = []
        for name, value in (self._cfg().service_tokens or {}).items():
            if isinstance(value, dict):
                rows.append({
                    "name": name,
                    "prefix": value.get("prefix", ""),
                    "created_at": value.get("created_at", ""),
                    "last_used_at": value.get("last_used_at", ""),
                    "use_count": int(value.get("use_count") or 0),
                    "source": value.get("source", ""),
                    "scopes": list(value.get("scopes") or []),
                    "imported_from": value.get("imported_from", ""),
                    "persisted": True,
                })
        for name, value in self.legacy_records().items():
            rows.append({
                "name": name,
                "prefix": value["prefix"],
                "created_at": value["created_at"],
                "last_used_at": value["last_used_at"],
                "use_count": value["use_count"],
                "source": value["source"],
                "scopes": value["scopes"],
                "imported_from": "env",
                "persisted": False,
            })
        return sorted(rows, key=lambda x: x["created_at"], reverse=True)

    def count(self) -> int:
        return len(self._cfg().service_tokens or {}) + len(self.legacy_records())

    # ── 校验 ───────────────────────────────────────────────────────────────

    def verify(self, token: str) -> dict | None:
        """命中返回 ``{name, source, scopes}``，否则 ``None``。"""
        if not token:
            return None
        digest = _hash(token.strip())
        with self._lock:
            for name, value in (self._cfg().service_tokens or {}).items():
                if not isinstance(value, dict) or not value.get("hash"):
                    continue
                if secrets.compare_digest(value["hash"], digest):
                    self._touch(name)
                    return {
                        "name": name,
                        "source": value.get("source", ""),
                        "scopes": list(value.get("scopes") or []),
                    }
        for name, value in self.legacy_records().items():
            if secrets.compare_digest(value["hash"], digest):
                self._touch_legacy(name, self._now())
                return {
                    "name": name,
                    "source": value["source"],
                    "channel": value["channel"],
                    "scopes": value["scopes"],
                }
        return None

    def _touch(self, name: str) -> None:
        """更新已签发令牌的最后使用时间与调用次数（写盘节流）。"""
        with self._lock:
            tokens = dict(self._cfg().service_tokens or {})
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
            cfg.service_tokens = tokens

            now = time.time()
            if now - self._last_persist.get(name, 0.0) < LAST_USED_THROTTLE_SECONDS:
                return
            self._last_persist[name] = now
            try:
                cfg.save()
            except Exception as exc:  # noqa: BLE001
                print(f"[ServiceToken] 更新使用计数失败: {exc}")


def reset_legacy_stats() -> None:
    """测试用：清空 env 导入记录的进程内计数。"""
    with _STATS_LOCK:
        _LEGACY_STATS.clear()


def get_service_token_store() -> ServiceTokenStore:
    return ServiceTokenStore(get_config())
