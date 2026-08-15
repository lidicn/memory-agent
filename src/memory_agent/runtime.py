"""应用运行时容器

为什么需要它
------------
重构前 ``poller`` 是 ``app.py`` 的模块级全局变量，靠用户访问首页时的
``lazy_startup()`` 惰性创建，而 API 层（原 ``webui.py``）根本拿不到这个实例，
于是「立即采集」只能返回一句假消息。

``AppRuntime`` 把 config / store / ha / history / templates / collector /
llm / analysis / tokens 收拢为一个进程级单例，在 ASGI lifespan 启动阶段构建，
API 路由与 MCP 工具通过 ``get_runtime()`` 共享同一实例。
配置变更后调用 ``reload_config()`` 重建下游客户端，彻底解决
「改了配置不生效」的问题。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .analysis import AnalysisService
from .auth import AuthManager
from .config import Config, get_config
from .ha_client import HAClient
from .ha_db import HADBClient
from .agent_memory import AgentMemoryService
from .history import HistoryManager
from .insights import InsightService
from .llm_client import LLMRouter
from .mcp_tokens import MCPTokenStore
from .poller import CollectService
from .store import Store
from .templates import TemplateManager


class AppRuntime:
    """进程级依赖容器。"""

    def __init__(self) -> None:
        self.config: Config = get_config()
        self.store = Store(self.config.db_path, self.config.tz_offset_hours)
        self.auth = AuthManager(self.config)
        self.ha = HAClient(self.config)
        self.ha_db = self._build_ha_db(self.config)
        self.history = HistoryManager(self.config, self.store)
        self.templates = TemplateManager(self.config.data_dir)
        self.tokens = MCPTokenStore(self.config)
        self.llm = LLMRouter(self.config)
        self.insights = InsightService(self.config, self.store)
        self.analysis = AnalysisService(self.config, self.store, self.llm)
        self.collector = CollectService(
            self.config, self.ha, self.history, self.store, self.ha_db
        )
        self.agent_memory = AgentMemoryService(
            self.config, self.store, self.history
        )
        self._patterns: Any = None
        self._sweep_task: Any = None
        self._started = False

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def startup(self) -> None:
        if self._started:
            return
        print("[Runtime] 启动中…")
        await asyncio.to_thread(self.store.init_schema)

        stale = await asyncio.to_thread(self.store.mark_stale_jobs)
        if stale:
            print(f"[Runtime] 标记 {stale} 个中断的采集任务")

        migrated = await asyncio.to_thread(self.tokens.migrate_legacy)
        if migrated:
            print(f"[Runtime] 迁移 {migrated} 个历史 MCP Token 为哈希存储")

        seeded = await asyncio.to_thread(self._seed_builtin_skills)
        if seeded:
            print(f"[Runtime] 种子化 {seeded} 个内置技能到 {self.config.skills_dir}")

        if self.config.data_retention_days > 0:
            await asyncio.to_thread(
                self.store.purge_old, self.config.data_retention_days
            )

        await self.collector.start()
        self._started = True
        self._sweep_task = asyncio.create_task(self._periodic_agent_memory_sweep())
        print("[Runtime] 启动完成")

    def _build_ha_db(self, config: Config) -> Any:
        """按配置构建直连 HA MariaDB 的只读客户端；未启用或缺少密码时返回 None。"""
        if not getattr(config, "ha_db_enabled", False):
            return None
        if not getattr(config, "ha_db_password", ""):
            print("[Runtime] HA MariaDB 未配置密码，跳过直读客户端（采集回退 REST）")
            return None
        try:
            return HADBClient(
                host=config.ha_db_host,
                port=config.ha_db_port,
                user=config.ha_db_user,
                password=config.ha_db_password,
                db=config.ha_db_name,
                query_batch=config.ha_db_query_batch,
                timeout=config.ha_db_query_timeout,
                enabled=True,
                tz_offset_hours=config.tz_offset_hours,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Runtime] 创建 HA MariaDB 客户端失败（采集将回退 REST）: {exc}")
            return None

    def _seed_builtin_skills(self) -> int:
        """首次启动时把包内内置技能（skills_bundle）写入 skills_dir 供 Agent 通过 MCP 拉取。

        仅当技能不存在时写入，不覆盖 Agent 已迭代的更高版本。"""
        from .mcp_server import seed_builtin_skills

        return seed_builtin_skills(self)

    async def shutdown(self) -> None:
        print("[Runtime] 关闭中…")
        task = getattr(self, "_sweep_task", None)
        if task is not None:
            task.cancel()
        try:
            await self.collector.stop()
        except Exception as exc:
            print(f"[Runtime] 停止采集服务异常: {exc}")
        try:
            await self.llm.close()
        except Exception as exc:
            print(f"[Runtime] 关闭 LLM 客户端异常: {exc}")
        ha_db = getattr(self, "ha_db", None)
        if ha_db is not None:
            try:
                ha_db.close()
            except Exception as exc:
                print(f"[Runtime] 关闭 HA MariaDB 客户端异常: {exc}")
        await asyncio.to_thread(self.store.close)
        self._started = False
        print("[Runtime] 已关闭")

    async def _periodic_agent_memory_sweep(self) -> None:
        """常驻任务：按 agent_sweep_interval_seconds 周期做自动晋升 + 镜像 reconcile。

        无 chroma / 无待晋升项时静默跳过，不影响主流程（v2 #4 / #9）。
        """
        interval = max(
            60, int(getattr(self.config, "agent_sweep_interval_seconds", 86400))
        )
        while True:
            try:
                await asyncio.sleep(interval)
                res = await asyncio.to_thread(self.agent_memory.sweep_and_reconcile)
                sweep = res.get("sweep", {})
                print(
                    f"[AgentMemory] 周期 sweep 完成：扫描 {sweep.get('scanned', 0)}，"
                    f"晋升 {sweep.get('promoted', 0)}，"
                    f"修复镜像 {res.get('reconcile', {}).get('fixed', 0)}"
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[AgentMemory] 周期 sweep 异常: {exc}")

    # ── 配置热更新 ───────────────────────────────────────────────────────

    def reload_config(self) -> Config:
        """重新读取配置并重建依赖它的客户端。"""
        old = self.config
        self.config = get_config()
        self.auth.config = self.config
        self.auth.users_file = self.config.users_file
        self.ha = HAClient(self.config)
        self.ha_db = self._build_ha_db(self.config)
        self.history.config = self.config
        # chroma 地址（host/port）变化时，重置连接缓存，让下次访问用新地址重连，
        # 否则运行时会一直卡在「首次连接失败」的状态，健康页/写入都报错。
        if self.history is not None and (
            (old.chroma_host, old.chroma_port)
            != (self.config.chroma_host, self.config.chroma_port)
        ):
            try:
                self.history.reset_chroma()
                print("[Runtime] chroma 地址变更，已重置连接缓存")
            except Exception as exc:
                print(f"[Runtime] 重置 chroma 连接缓存异常: {exc}")
        self.tokens.config = self.config
        self.llm.reconfigure(self.config)
        self.insights.config = self.config
        self.analysis.config = self.config
        self.agent_memory.config = self.config
        self.collector.reconfigure(self.config, self.ha, self.ha_db)
        self.store.tz_offset_hours = self.config.tz_offset_hours
        return self.config

    # ── 可选依赖 ─────────────────────────────────────────────────────────

    @property
    def patterns(self):
        """行为模式库（Chroma）。Node-RED 兼容层使用，向量库不可用时返回 None。"""
        if self._patterns is None:
            try:
                from .patterns import PatternManager

                self._patterns = PatternManager(self.config)
            except Exception as exc:
                print(f"[Runtime] 行为模式库不可用: {exc}")
                self._patterns = False
        return self._patterns or None

    # ── 健康检查 ─────────────────────────────────────────────────────────

    async def health(self) -> dict:
        ha_task = asyncio.to_thread(self.ha.get_status)
        llm_task = self.llm.ping()
        store_task = asyncio.to_thread(self.store.stats)
        chroma_task = asyncio.to_thread(self.history.chroma_status)
        nr_task = asyncio.to_thread(self._nr_status)
        ha_db_task = asyncio.to_thread(self._ha_db_status)

        ha, llm, stats, chroma, nr, ha_db = await asyncio.gather(
            ha_task, llm_task, store_task, chroma_task, nr_task, ha_db_task,
            return_exceptions=True,
        )

        def _safe(value: Any, fallback: dict) -> dict:
            return value if isinstance(value, dict) else {**fallback, "error": str(value)}

        return {
            "ha": _safe(ha, {"connected": False}),
            "llm": _safe(llm, {"connected": False}),
            "chroma": _safe(chroma, {"connected": False}),
            "nodered": _safe(nr, {"connected": False}),
            "ha_db": _safe(ha_db, {"enabled": False, "connected": False}),
            "store": _safe(stats, {"total_events": 0}),
            "tokens": len(self.tokens.list_tokens()),
            "collecting": self.collector.is_running,
        }

    def _ha_db_status(self) -> dict:
        ha_db = getattr(self, "ha_db", None)
        if ha_db is None:
            return {"enabled": False, "connected": False, "reason": "未启用或缺少凭据"}
        try:
            res = ha_db.ping()
            return {"enabled": True, "connected": bool(res.get("ok")), **res}
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "connected": False, "error": str(exc)}

    def _nr_status(self) -> dict:
        import httpx

        url = (self.config.nr_url or "").rstrip("/")
        if not url:
            return {"connected": False, "url": "", "error": "未配置"}
        try:
            auth = None
            if self.config.nr_user:
                auth = (self.config.nr_user, self.config.nr_pass)
            resp = httpx.get(f"{url}/settings", auth=auth, timeout=5)
            return {"connected": resp.status_code < 500, "url": url,
                    "status_code": resp.status_code}
        except Exception as exc:
            return {"connected": False, "url": url, "error": str(exc)}


# ── 单例访问 ───────────────────────────────────────────────────────────────

_runtime: AppRuntime | None = None
_lock = asyncio.Lock()


def get_runtime() -> AppRuntime:
    """获取（必要时创建）运行时单例。

    正常路径下由 lifespan 提前创建；MCP 工具在极端情况下也能安全触达。
    """
    global _runtime
    if _runtime is None:
        _runtime = AppRuntime()
    return _runtime


async def start_runtime() -> AppRuntime:
    async with _lock:
        runtime = get_runtime()
        await runtime.startup()
        return runtime


async def stop_runtime() -> None:
    global _runtime
    async with _lock:
        if _runtime is not None:
            await _runtime.shutdown()
