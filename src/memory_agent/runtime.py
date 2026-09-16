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
import json
from datetime import datetime, timedelta
from typing import Any

from .analysis import AnalysisService
from .auth import AuthManager
from .config import Config, get_config
from .face_node_registry import FaceNodeRegistry
from .ha_client import HAClient
from .ha_db import HADBClient
from .agent_memory import AgentMemoryService
from .arena import ArenaService
from .signal_learning import SignalLearningService
from .history import HistoryManager
from .identity import IdentityReconciler, IdentityService
from .insights import InsightService
from .researcher import ResearcherService
from .activity_inference import ActivityInferenceService
from .llm_client import LLMRouter
from .mcp_tokens import MCPTokenStore
from .mqtt_bridge import MqttBridge
from .backup import BackupManager
from .template_validate import validate_all
from .poller import CollectService
from .store import Store, now_local
from .templates import TemplateManager
from .tv_service import TVService
from .vision_service import VisionService


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
        # 竞技场服务（AutoFlow 竞技场对接：快照/灵感/创造力评估/结果记录）
        self.arena = ArenaService(
            self.config, self.store, self.history, self.insights, self.llm
        )
        self.signal_learning = SignalLearningService(self.store, self.agent_memory)
        # 实体身份层（v0.2）：模板/查询经它把「逻辑设备名」解析为当前 entity_id，
        # 从而免疫 HA 集成重登/双集成导致的实体漂移（详见 identity.py 模块文档）。
        self.identity = IdentityService(
            self.store, self.config, self.config.tz_offset_hours
        )
        # 用 getter 而非实例：配置热更新后 HA 客户端与模板管理器会被重建，
        # 对账必须拿到最新的那一个。
        self.identity_reconciler = IdentityReconciler(
            self.identity,
            ha_getter=lambda: self.ha,
            templates_getter=lambda: self.templates,
            tz_offset_hours=self.config.tz_offset_hours,
        )
        # 人脸识别节点池：运行时级共享注册表，vision 与 face_routes 共用同一实例
        self.face = FaceNodeRegistry()
        self.vision = VisionService(self.config, self.store, self.ha)
        self.vision.face = self.face
        # 电视截屏多模态（按需调用）：复用视觉服务的 VLM 通道
        self.tv = TVService(self.config, self.ha, self.vision)
        # MQTT 实时推送（v0.4）：旁路能力，未启用时 publish() 直接空转
        self.mqtt = MqttBridge(self.config)
        self.backup = BackupManager(self.config)
        self._patterns: Any = None
        self._sweep_task: Any = None
        self._identity_task: Any = None
        self._mqtt_task: Any = None
        self._backup_task: Any = None
        self._tpl_validate_task: Any = None
        self._activity_task: Any = None
        # 记忆研究员（v0.8 定向洞察）：依赖上面已装配的 insights/llm/agent_memory/history
        self.researcher = ResearcherService(self)
        # 主动感知·行为推断（v0.9.5）：从 events 产出 canonical 行为状态，供 GET /api/behaviors
        self.activity = ActivityInferenceService(self)
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

        seeded_jobs = await asyncio.to_thread(self.store.ensure_default_insight_jobs)
        if seeded_jobs:
            print(f"[Runtime] 已种子化 {seeded_jobs} 个内置定向洞察任务")

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
        self.vision.start()
        self._started = True
        self._sweep_task = asyncio.create_task(self._periodic_agent_memory_sweep())
        # 身份层：启动即跑一次对账，让模板/查询尽快拿到逻辑映射；失败不影响启动
        try:
            res = await asyncio.to_thread(self.identity_reconciler.reconcile)
            print(
                f"[Identity] 首次对账完成：实体 {res.get('entities', 0)}，"
                f"逻辑设备 {res.get('devices', 0)}，合并 {res.get('merged', 0)}，"
                f"重匹配 {res.get('remapped', 0)}，失效 {res.get('stale', 0)}"
            )
        except Exception as exc:  # noqa: BLE001 - 对账失败不应阻断启动
            print(f"[Identity] 首次对账失败（不影响启动）: {exc}")
        self._publish_health_changes(res if isinstance(res, dict) else {})
        self._identity_task = asyncio.create_task(self._periodic_identity_reconcile())
        if self.mqtt.enabled:
            self._mqtt_task = asyncio.create_task(self._periodic_mqtt_presence())
            print(
                f"[MQTT] 实时推送已启用 → {self.config.tv_mqtt_host}:"
                f"{self.config.tv_mqtt_port} 主题前缀 {self.config.ma_mqtt_topic_prefix}"
            )
        if getattr(self.config, "backup_enabled", False):
            self._backup_task = asyncio.create_task(self._periodic_backup())
            print(f"[Backup] 周期备份已启用 → {self.config.backup_dir}")
        # 模板校验：后台异步、首跑延时，结果落缓存供列表读取（不阻塞启动）
        self._tpl_validate_task = asyncio.create_task(self._periodic_template_validate())
        # 主动感知（v0.9.5）：周期行为推断（低频批处理，非实时流）
        self._activity_task = asyncio.create_task(self._periodic_activity_inference())

        # 记忆研究员（v0.8）：按 researcher_scheduler_time 每日低峰定期洞察
        self.researcher.start()
        print("[Runtime] 启动完成")

    def _publish_health_changes(self, res: dict) -> None:
        """把对账中发生变化的设备健康状态推到 MQTT（A3 告警出口）。"""
        if not self.mqtt.enabled:
            return
        for ch in res.get("health_changes") or []:
            self.mqtt.publish_health_change(
                ch.get("entity_id") or "", ch.get("from") or "", ch.get("to") or ""
            )

    async def _periodic_mqtt_presence(self) -> None:
        """常驻任务：周期推送成员在场快照，**只在内容变化时发**，避免刷屏。"""
        interval = max(
            15, int(getattr(self.config, "ma_mqtt_presence_interval", 60) or 60)
        )
        last_key = ""
        while True:
            try:
                await asyncio.sleep(interval)
                now = now_local(self.config.tz_offset_hours)
                # 回溯窗口取 3 倍间隔，避免边界抖动导致成员被误判为离开
                since = (now - timedelta(seconds=max(120, interval * 3))).isoformat()
                members = await asyncio.to_thread(
                    self.store.recent_presence, since, None, 200
                )
                key = json.dumps(
                    [(m.get("name") or "", m.get("room") or "") for m in members],
                    ensure_ascii=False,
                )
                if key != last_key:
                    # 只有推送真正成功才更新 last_key：否则首次因 broker 未连上而
                    # 失败时，last_key 已被伪更新，之后内容再变也不会重推，
                    # 订阅方将永远拿不到 retain 快照。
                    if self.mqtt.publish_presence(members, now.isoformat()):
                        last_key = key
                    else:
                        print("[MQTT] 在场推送未成功，保留上次快照待下周期重试")
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[MQTT] 在场推送异常: {exc}")

    async def _periodic_backup(self) -> None:
        """常驻任务：每日一次 SQLite VACUUM 快照 + chroma 目录快照，保留 N 份轮转。

        首次启动延迟 1 小时，避免与启动期采集 / 对账争抢 IO；单次失败仅记录，
        不影响主流程（先于 v0.8 存量记忆改写就位，保证可回滚）。
        """
        interval = 86400
        try:
            await asyncio.sleep(3600)
            while True:
                try:
                    res = await asyncio.to_thread(self.backup.run_once)
                    if res.get("ok"):
                        print(
                            f"[Backup] 已完成：{res.get('db')}"
                            f"（保留 {res.get('retained')} 份）"
                        )
                    else:
                        print(f"[Backup] 跳过：{res.get('reason')}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[Backup] 异常: {exc}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    async def _periodic_template_validate(self) -> None:
        """常驻任务：模板校验（启动延时首跑 + 每 6 小时复验）。

        校验需查事件表与身份层，成本不低，因此：
        * 首跑延时，避开启动期采集/对账；
        * 结果落 ``data/template_state.json`` 缓存，列表接口只读缓存；
        * 失败仅记日志，绝不影响主流程。
        """
        interval = 6 * 3600
        try:
            await asyncio.sleep(90)
            while True:
                try:
                    res = await asyncio.to_thread(validate_all, self, True)
                    print(
                        f"[TemplateValidate] 校验完成：{res.get('total')} 个模板，"
                        f"状态 {res.get('summary')}"
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[TemplateValidate] 校验异常: {exc}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    async def _periodic_identity_reconcile(self) -> None:
        """常驻任务：低频对账 HA 实体注册表，维护逻辑设备映射与健康状态。

        频率由 ``identity_reconcile_interval_seconds`` 控制（默认 600s，最小 60s），
        远低于采集频率；单次失败仅记录，不影响主流程。
        """
        interval = max(
            60, int(getattr(self.config, "identity_reconcile_interval_seconds", 600) or 600)
        )
        while True:
            try:
                await asyncio.sleep(interval)
                res = await asyncio.to_thread(self.identity_reconciler.reconcile)
                if not res.get("ok"):
                    # HA 短暂不可达是常态，降级为 debug 级提示即可
                    print(f"[Identity] 周期对账跳过: {res.get('error', '未知')}")
                    continue
                self._publish_health_changes(res)
                print(
                    f"[Identity] 周期对账完成：逻辑设备 {res.get('devices', 0)}，"
                    f"合并 {res.get('merged', 0)}，重匹配 {res.get('remapped', 0)}，"
                    f"失效 {res.get('stale', 0)}"
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[Identity] 周期对账异常: {exc}")

    async def _periodic_activity_inference(self) -> None:
        """常驻任务：周期跑行为推断（v0.9.5），产出 canonical 状态写 ``behavior_states``。

        间隔由 ``activity_interval_seconds`` 控制（默认 300s）；MA 定位为权威记忆
        而非实时触发（实时反应由管家 websocket 承担），故低频批处理即可。
        总开关 ``activity_inference_enabled`` 默认开；单次失败仅记录不影响主流程。
        """
        interval = max(60, int(getattr(self.config, "activity_interval_seconds", 300) or 300))
        last_habit_day = ""
        try:
            await asyncio.sleep(60)  # 首跑延时，避开启动期采集/对账争抢
            while True:
                if getattr(self.config, "activity_inference_enabled", True):
                    try:
                        res = await asyncio.to_thread(self.activity.run)
                        if res.get("ok") and (res.get("persisted") or res.get("candidates")):
                            print(
                                f"[Activity] 行为推断：扫描 {res.get('scanned')} 事件，"
                                f"产出 {res.get('persisted')} 状态 / {res.get('candidates')} 候选"
                            )
                        # 任务 D：每日一次从 behavior_states 沉淀长期习惯
                        day = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d")
                        if day != last_habit_day:
                            hres = await asyncio.to_thread(self.activity.infer_habits)
                            last_habit_day = day
                            if hres.get("saved"):
                                print(f"[Activity] 习惯沉淀：{hres.get('saved')} 条")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[Activity] 行为推断异常: {exc}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

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
        identity_task = getattr(self, "_identity_task", None)
        if identity_task is not None:
            identity_task.cancel()
        mqtt_task = getattr(self, "_mqtt_task", None)
        if mqtt_task is not None:
            mqtt_task.cancel()
        backup_task = getattr(self, "_backup_task", None)
        if backup_task is not None:
            backup_task.cancel()
        tpl_task = getattr(self, "_tpl_validate_task", None)
        if tpl_task is not None:
            tpl_task.cancel()
        activity_task = getattr(self, "_activity_task", None)
        if activity_task is not None:
            activity_task.cancel()
        try:
            self.mqtt.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[Runtime] 关闭 MQTT 客户端异常: {exc}")
        try:
            await self.vision.stop()
        except Exception as exc:
            print(f"[Runtime] 停止视觉巡检异常: {exc}")
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
            or (old.embedding_base_url, old.embedding_model, old.embedding_api_key)
            != (self.config.embedding_base_url, self.config.embedding_model, self.config.embedding_api_key)
        ):
            try:
                self.history.reset_chroma()
                print("[Runtime] chroma / embedding 配置变更，已重置连接与嵌入缓存")
            except Exception as exc:
                print(f"[Runtime] 重置 chroma 连接缓存异常: {exc}")
        self.tokens.config = self.config
        self.llm.reconfigure(self.config)
        self.arena.config = self.config
        self.identity.config = self.config
        self.identity.tz_offset_hours = self.config.tz_offset_hours
        self.identity_reconciler.tz_offset_hours = self.config.tz_offset_hours
        self.mqtt.config = self.config
        self.insights.config = self.config
        self.analysis.config = self.config
        self.agent_memory.config = self.config
        self.collector.reconfigure(self.config, self.ha, self.ha_db)
        self.vision.reconfigure(self.config)
        self.vision.ha = self.ha  # HA 客户端已重建，灯态查询须跟随
        self.tv.reconfigure(self.config, self.ha, self.vision)
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
        embed_task = asyncio.to_thread(self.history.embedding_status)

        ha, llm, stats, chroma, nr, ha_db, embedding = await asyncio.gather(
            ha_task, llm_task, store_task, chroma_task, nr_task, ha_db_task, embed_task,
            return_exceptions=True,
        )

        def _safe(value: Any, fallback: dict) -> dict:
            return value if isinstance(value, dict) else {**fallback, "error": str(value)}

        # 采集滞后：最近一条事件距现在多久（无事件则 None）
        last_ts = (stats if isinstance(stats, dict) else {}).get("last_event_ts") or ""
        collect_lag_seconds = None
        if last_ts:
            try:
                lt = datetime.fromisoformat(last_ts)
                # 关键：last_event_ts 存的是**本地时间**，而容器系统时钟通常为 UTC。
                # 必须用 now_local 换算到同一时区再相减，否则会整整差一个时区偏移
                # （表现为采集滞后为负值）。
                now = now_local(self.config.tz_offset_hours).replace(tzinfo=None)
                collect_lag_seconds = int((now - lt).total_seconds())
            except Exception:
                collect_lag_seconds = None

        return {
            "ha": _safe(ha, {"connected": False}),
            "llm": _safe(llm, {"connected": False}),
            "chroma": _safe(chroma, {"connected": False}),
            "nodered": _safe(nr, {"connected": False}),
            "ha_db": _safe(ha_db, {"enabled": False, "connected": False}),
            "mqtt": self.mqtt.status(),
            "embedding": _safe(embedding, {"configured": False}),
            "store": _safe(stats, {"total_events": 0}),
            "collect_lag_seconds": collect_lag_seconds,
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
