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
from . import adm_linkage, house_time
from .face_node_registry import FaceNodeRegistry
from .ha_client import HAClient
from .ha_db import HADBClient
from .agent_memory import AgentMemoryService
from .ha_assist import HaAssist
from .arena import ArenaService
from .signal_learning import SignalLearningService
from .history import HistoryManager
from .identity import IdentityReconciler, IdentityService
from .insights import InsightService
from .researcher import ResearcherService
from .activity_inference import ActivityInferenceService
from .llm_client import LLMRouter
from .mcp_tokens import MCPTokenStore
from .mqtt_bridge import MqttBridge, reset_presence_probe
from .backup import BackupManager
from .template_validate import validate_all
from .poller import CollectService
from .store import Store, now_local
from .task_registry import task_registry
from .templates import TemplateManager
from .tv_service import TVService
from .vision_service import VisionService
from .alert_dispatcher import AlertDispatcher
from .semantic_dedup import SemanticDeduplicator
from .livingroom_ai import LivingRoomAIIngest
from .candidate_promotion import get_promoter
from .causal_scanner import CausalScanner
from .announcer import Announcer


class AppRuntime:
    """进程级依赖容器。"""

    def __init__(self) -> None:
        self.config: Config = get_config()
        self.store = Store(self.config.db_path, self.config.tz_offset_hours,
                           backup_dir=getattr(self.config, "backup_dir", "") or "")
        self.auth = AuthManager(self.config)
        self.ha = HAClient(self.config)
        self.ha_db = self._build_ha_db(self.config)
        self.history = HistoryManager(self.config, self.store)
        self.templates = TemplateManager(self.config.data_dir)
        self.tokens = MCPTokenStore(self.config)
        self.llm = LLMRouter(self.config)
        self.insights = InsightService(self.store, self.config)
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
        # HA Assist 集成（v1.0-2）：家庭记忆增强的会话回答后端（OpenAI 兼容 /v1/*）
        self.ha_assist = HaAssist(self.config, self.agent_memory, self.llm, self.insights)
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
        # Phase 4.1 统一告警分发单飞：全局单例，注入 vision 与 away_mode
        self.alert_dispatcher = AlertDispatcher(default_cooldown_seconds=300)
        # Phase 4.2 建议语义去重：复用 embedding 端点，embedding 不可用时回退精确匹配
        self.semantic_dedup = SemanticDeduplicator(self.config)
        self.vision = VisionService(self.config, self.store, self.ha, agent_memory=self.agent_memory, alert_dispatcher=self.alert_dispatcher)
        self.vision.face = self.face
        # 电视截屏多模态（按需调用）：复用视觉服务的 VLM 通道
        self.tv = TVService(self.config, self.ha, self.vision)
        # MQTT 实时推送（v0.4）：旁路能力，未启用时 publish() 直接空转
        self.mqtt = MqttBridge(self.config)
        # 视觉异常告警出口（Phase 2，默认关）：把 MQTT 桥接注入视觉服务
        self.vision.mqtt = self.mqtt
        self.backup = BackupManager(self.config)
        self._patterns: Any = None
        self._sweep_task: Any = None
        self._identity_task: Any = None
        self._mqtt_task: Any = None
        self._backup_task: Any = None
        self._tpl_validate_task: Any = None
        self._activity_task: Any = None
        self._device_feed: Any = None
        self._device_feed_task: Any = None
        self._livingroom_ai_task: Any = None
        # 记忆研究员（v0.8 定向洞察）：依赖上面已装配的 insights/llm/agent_memory/history
        self.researcher = ResearcherService(self)
        # 主动感知·行为推断（v0.9.5）：从 events 产出 canonical 行为状态，供 GET /api/behaviors
        self.activity = ActivityInferenceService(self)
        # Phase 3 主动规则引擎：STATIC 规则 + 滑窗去抖
        from .perception_rules import RuleEngine
        self.rule_engine = RuleEngine()
        self._started = False

    # ── 生命周期 ─────────────────────────────────────────────────────────


    async def _run_retention_cleanup(self) -> None:
        """后台执行数据保留清理，不阻塞启动。"""
        try:
            await asyncio.to_thread(
                self.store.purge_old, self.config.data_retention_days
            )
            # B-MA-05: 定期清理幂等键和 MCP 审计表，防止无限增长
            await asyncio.to_thread(self.store.purge_idempotency)
            await asyncio.to_thread(self.store.purge_mcp_audit, 30)
            print("[Runtime] 数据清理完成：events/idempotency/mcp_audit")
        except Exception as exc:  # noqa: BLE001
            print(f"[Runtime] 数据保留清理失败（不影响运行）: {exc}")

    async def startup(self) -> None:
        if self._started:
            return
        print("[Runtime] 启动中…")
        rec = await asyncio.to_thread(self.store.check_and_recover)
        if rec.get('recovered'):
            print(f"[Runtime] DB recovered: {rec['backup_used']}")
        elif rec.get('error'):
            print(f"[Runtime] DB warning: {rec['error']}")
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

        # 数据清理移到后台异步执行，不阻塞启动
        # events 表 100 万行时 DELETE 可能需数分钟，同步执行会卡死 startup
        if self.config.data_retention_days > 0:
            self._retention_task = task_registry.create(
                self._run_retention_cleanup(), name="runtime.retention_cleanup"
            )

        await self.collector.start()
        self.vision.start()
        self._started = True
        self._sweep_task = task_registry.create(
            self._periodic_agent_memory_sweep(), name="runtime.agent_memory_sweep"
        )
        # 身份层：首次对账挪到后台（审计 P1-12）。reconcile 要等 HA 回答：
        # HA 地址不通时实测这一步卡 5139 ms（走连接失败/超时路径），而它后面还排着
        # MQTT/备份/模板校验/活动推断的任务注册——启动阻塞会连带放大成"整站卡十几秒"。
        # 原注释自己就写着「失败不影响启动」，那它同样不该阻塞启动。
        self._identity_first_pass_task = task_registry.create(
            self._first_identity_reconcile(), name="runtime.identity_first_pass"
        )
        self._identity_task = task_registry.create(
            self._periodic_identity_reconcile(), name="runtime.identity_reconcile"
        )
        if self.mqtt.enabled:
            self._mqtt_task = task_registry.create(
                self._periodic_mqtt_presence(), name="runtime.mqtt_presence"
            )
            # 启动即尝试建连并登记 LWT：connect() 是异步的，此刻多半还没连上，
            # 这次返回 False 不要紧——周期任务里的 ensure_advertised 会补发，
            # 但 will_set 必须在 CONNECT 之前完成，所以客户端要在这里就造出来。
            self.mqtt.ensure_advertised(self.adm_caps())
            print(
                f"[MQTT] 实时推送已启用 → {self.config.tv_mqtt_host}:"
                f"{self.config.tv_mqtt_port} 主题前缀 {self.config.ma_mqtt_topic_prefix}"
            )
        if getattr(self.config, "backup_enabled", False):
            self._backup_task = task_registry.create(
                self._periodic_backup(), name="runtime.backup"
            )
            print(f"[Backup] 周期备份已启用 → {self.config.backup_dir}")
        # 模板校验：后台异步、首跑延时，结果落缓存供列表读取（不阻塞启动）
        self._tpl_validate_task = task_registry.create(
            self._periodic_template_validate(), name="runtime.template_validate"
        )
        # 主动感知（v0.9.5）：周期行为推断（低频批处理，非实时流）
        self._activity_task = task_registry.create(
            self._periodic_activity_inference(), name="runtime.activity_inference"
        )
        # 设备事件 feed（DCD 裁定 20261001 §二 Q1=B）：独立批量扫描器，小时级轮询。
        # 任务常驻、开关每轮重读——SP 把总开关打开不该再等一次重启。
        from .device_feed import get_device_feed
        from .rule_engine import get_rule_engine
        self._device_feed = get_device_feed(
            self.store, get_rule_engine(self.store, self.alert_dispatcher), self.config)
        self._device_feed_task = task_registry.create(
            self._periodic_device_feed(), name="runtime.device_feed"
        )
        print(f"[DeviceFeed] 通道已装配：间隔 {self._device_feed.interval_seconds()}s，"
              f"总开关 device_feed_enabled={self._device_feed.enabled()}，"
              f"dry_run={self._device_feed.dry_run()}")
        # 主动感知 v2.0 · Phase 0.1 + 0.4：客厅盒侧 AI 事件轻量轮询 + 主动播报闭环
        # 播报两条路并存（DCD 20261007 §四 Q1 = 裁 B）：HA 直发优先，缺 tts 实体/播放设备时
        # 回落投 butler/inbox/speak。桥在这里就交给它——`self.mqtt.enabled` 才是回落路的开关。
        announcer = Announcer(
            self.ha, self.store,
            tts_entity=getattr(self.config, "announce_tts_entity", "") or "",
            enabled=getattr(self.config, "announce_enabled", False),
            cooldown_sec=getattr(self.config, "announce_cooldown_sec", 30),
            target=getattr(self.config, "announce_target", "") or "",
            mqtt=self.mqtt,
        )
        self._livingroom_ai = LivingRoomAIIngest(
            self.ha, self.store,
            interval_seconds=getattr(self.config, "livingroom_ai_interval_seconds", 10),
            announcer=announcer,
            vision=self.vision,
            omni_enabled=getattr(self.config, "livingroom_ai_omni_enabled", False),
            agent_memory=self.agent_memory,
            alert_dispatcher=self.alert_dispatcher,
        )
        self._livingroom_ai_task = task_registry.create(
            self._periodic_livingroom_ai(), name="runtime.livingroom_ai"
        )
        # Phase 2.1 视觉行为事实候选区晋升：定期扫描候选区，晋升满足条件的事件
        self._candidate_promoter = get_promoter(self.store)
        self._candidate_promotion_task = task_registry.create(
            self._periodic_candidate_promotion(), name="runtime.candidate_promotion"
        )
        # P5e 因果归因主动告警：每日扫描成员行为变化，显著变化写入 behavior_events
        self.causal_scanner = CausalScanner(self.store)
        self._causal_scan_task = task_registry.create(
            self._periodic_causal_scan(), name="runtime.causal_scan"
        )

        # 记忆研究员（v0.8）：按 researcher_scheduler_time 每日低峰定期洞察
        self.researcher.start()

        # 自我日记（家庭人格化实验）：每天 23:00 自动生成
        self._self_diary_task = task_registry.create(
            self._periodic_self_diary(), name="runtime.self_diary"
        )
        print("[Runtime] 启动完成")

    async def _periodic_self_diary(self) -> None:
        """常驻任务：每天 23:00 自动生成自我日记。"""
        try:
            await asyncio.sleep(10)  # 启动稍延
            while True:
                # 算到下一个 23:00 的秒数（家庭墙钟口径：日记是给这个家庭写的）
                from datetime import timedelta
                now_dt = now_local(self.config.tz_offset_hours)
                next_23 = now_dt.replace(hour=23, minute=0, second=0, microsecond=0)
                if next_23 <= now_dt:
                    next_23 += timedelta(days=1)
                wait_sec = (next_23 - now_dt).total_seconds()
                await asyncio.sleep(wait_sec)
                # 生成日记
                try:
                    # 读昨天日记
                    all_mem = await asyncio.to_thread(
                        self.store.list_agent_memories, "all", "", 500, ""
                    )
                    diaries = [m for m in all_mem if m.get("topic_key") == "self_diary"]
                    diaries.sort(key=lambda x: x.get("created_at", ""))
                    yesterday_text = diaries[-1]["text"][:200] if diaries else "（还没有日记）"

                    # 从当天 events 提取摘要（start/end 与 ts 列同为家庭墙钟）
                    today = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d")
                    events = await asyncio.to_thread(
                        self.store.query_events,
                        start=f"{today}T00:00:00", end=f"{today}T23:59:59", limit=100,
                    )
                    summary_lines = []
                    for e in events[:50]:
                        t = e.get("ts", "")[11:16]
                        room = e.get("room", "")
                        state = e.get("new_state") or ""
                        if room and state:
                            summary_lines.append(f"{t} {room}: {state}")
                    summary = "\n".join(summary_lines[:30])

                    prompt = f"""你是这个家庭里的一个"存在"。用第一人称写今天的日记。
要求：
- 开头引用昨天日记的一句话（"昨天我说…"）
- 300-500 字
- 只写观察到的，不下结论、不做诊断
- 用"我"视角，不用"这个家庭"

昨天日记：{yesterday_text}

今天的事件摘要（脱敏后）：
{summary}

请写今天的日记："""

                    resp = await self.llm.chat(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=800,
                        temperature=0.7,
                    )
                    diary_text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")

                    # 写入 staging
                    await asyncio.to_thread(
                        self.store.add_agent_memory,
                        "self_diary", diary_text, "self_diary",
                        "[]", "[]", 365, "staging", 1,
                    )
                    print(f"[SelfDiary] 日记已生成 ({len(diary_text)} 字)")
                except Exception as e:
                    print(f"[SelfDiary] 生成失败: {e}")
                await asyncio.sleep(60)  # 防止重复触发
        except Exception as e:
            print(f"[SelfDiary] 任务异常: {e}")

    async def _periodic_livingroom_ai(self) -> None:
        """常驻任务：轻量轮询客厅盒侧 AI 事件，落 perception_events（source=edge_ai）。

        间隔由 ``livingroom_ai_interval_seconds`` 控制（默认 10s）；总开关
        ``livingroom_ai_enabled`` 默认开。单次失败仅记录，不影响主流程。
        """
        interval = max(1, int(getattr(self.config, "livingroom_ai_interval_seconds", 10) or 10))
        try:
            await asyncio.sleep(5)  # 启动稍延，避开采集/对账争抢
            while True:
                if getattr(self.config, "livingroom_ai_enabled", True):
                    try:
                        n = await asyncio.to_thread(self._livingroom_ai.run)
                        if n:
                            print(f"[LivingRoomAI] 盒侧 AI 事件 +{n}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[LivingRoomAI] 轮询异常: {exc}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    def _publish_health_changes(self, res: dict) -> None:
        """把对账中发生变化的设备健康状态推到 MQTT（A3 告警出口）。

        `stable_id` 必须跟着走：契约表 §1.2 把它列进 `ma/device-health`，DB 用它认
        "同一台物理设备换了实体"。改前这一跳把 `_mark_stale` 的身份丢了，实发恒为空串。
        """
        if not self.mqtt.enabled:
            return
        for ch in res.get("health_changes") or []:
            self.mqtt.publish_health_change(
                ch.get("entity_id") or "", ch.get("from") or "", ch.get("to") or "",
                stable_id=ch.get("stable_id") or "",
            )

    async def _periodic_candidate_promotion(self) -> None:
        """Phase 2.1 常驻任务：定期扫描候选区，晋升满足条件的低置信度事件。

        间隔默认 300 秒（5 分钟），总开关 candidate_promotion_enabled 默认开。
        单次失败仅记录，不影响主流程。

        vMA-1.2.1 §5.1：负样本聚类→规则建议**搭载在本任务内轮转**（不新建裸
        asyncio task），间隔 negative_sample_interval_seconds（默认 86400=每日），
        总开关 negative_sample_enabled 默认关（显式开启后建议只落 staging）。
        """
        interval = max(60, int(getattr(self.config, "candidate_promotion_interval_seconds", 300) or 300))
        neg_interval = max(600, int(getattr(self.config, "negative_sample_interval_seconds", 86400) or 86400))
        neg_last = 0.0
        try:
            await asyncio.sleep(30)  # 启动稍延
            while True:
                if getattr(self.config, "candidate_promotion_enabled", True):
                    try:
                        stats = await asyncio.to_thread(self._candidate_promoter.process_candidates)
                        if stats["promoted"] > 0:
                            print(f"[CandidatePromotion] 扫描 {stats['scanned']}，晋升 {stats['promoted']}，失败 {stats['failed']}")
                    except Exception as exc:
                        print(f"[CandidatePromotion] 周期任务异常: {exc}")
                if getattr(self.config, "negative_sample_enabled", False):
                    now_mono = asyncio.get_event_loop().time()
                    if now_mono - neg_last >= neg_interval:
                        neg_last = now_mono
                        try:
                            from .negative_samples import run_negative_sample_analysis
                            res = await asyncio.to_thread(run_negative_sample_analysis, self.store)
                            if res.get("written"):
                                print(f"[NegativeSamples] 负样本 {res['total_negative']}，"
                                      f"成簇 {res['cluster_count']}，写入建议 {res['written']}（staging）")
                        except Exception as exc:
                            print(f"[NegativeSamples] 周期任务异常: {exc}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _periodic_causal_scan(self) -> None:
        """P5e 常驻任务：每日因果归因扫描，检测成员行为显著变化并写入 behavior_events。

        间隔默认 86400 秒（24 小时），总开关 causal_scan_enabled 默认开。
        单次失败仅记录，不影响主流程。
        """
        interval = max(3600, int(getattr(self.config, "causal_scan_interval_seconds", 86400) or 86400))
        try:
            await asyncio.sleep(120)  # 启动稍延，等大库初始化和采集稳定
            while True:
                if getattr(self.config, "causal_scan_enabled", True):
                    try:
                        stats = await asyncio.to_thread(self.causal_scanner.scan)
                        sm = stats["scanned_members"]
                        sx = stats["scanned_metrics"]
                        aw = stats["alerts_written"]
                        sd = stats["skipped_dedup"]
                        if aw > 0 or sm > 0:
                            print(f"[CausalScan] 扫描 {sm} 人 / {sx} 指标，告警 {aw}，去重跳过 {sd}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[CausalScan] 周期任务异常: {exc}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
    def adm_caps(self) -> dict:
        """本仓能力摘要（契约表 §三 caps 的形状：``{mcp, tools, version}``）。

        工具名取 ``tool_schema.TOOL_NAMES``——那份就是"MCP 面暴露了哪些工具"的唯一真源，
        不在那里登记的工具 DB 也调不到，写进 caps 只会让探测方打 404。

        ``version`` 报**计划号**（``PLAN_VERSION``）而不是包版本：DCD 裁定 20261002 Q7。
        包版本是 1.0.0 这种内部实现号，各仓对外沟通用的是 vMA-x.y.z，DB/AF 读 caps 要看的是后者。
        """
        from . import PLAN_VERSION, tool_schema

        return {
            "mcp": True,
            "tools": list(tool_schema.TOOL_NAMES),
            "version": PLAN_VERSION,
        }

    async def _periodic_mqtt_presence(self) -> None:
        """常驻任务：周期推送成员在场快照，**只在内容变化时发**，避免刷屏。"""
        interval = max(
            15, int(getattr(self.config, "ma_mqtt_presence_interval", 60) or 60)
        )
        last_key = ""
        while True:
            try:
                await asyncio.sleep(interval)
                # presence 先于在场快照：探测方读的是 retained status/caps，
                # 断连重连或 broker 重启后（retained 丢失）靠这里重发。
                self.mqtt.ensure_advertised(self.adm_caps())
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

    async def _first_identity_reconcile(self) -> None:
        """启动后立刻补一次对账，让模板/查询尽快拿到逻辑映射——但不阻塞 startup。

        原来这段写在 ``startup()`` 里：``res`` 只在 try 内赋值，一旦
        ``reconcile()`` 抛异常，紧跟着的 ``self._publish_health_changes(res...)``
        就踩在未绑定的名字上（UnboundLocalError），把"失败不影响启动"
        变成"启动直接炸"。收进后台协程后这条路径不存在了。
        """
        try:
            res = await asyncio.to_thread(self.identity_reconciler.reconcile)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 对账失败不应阻断启动
            print(f"[Identity] 首次对账失败（不影响启动）: {exc}")
            return
        res = res if isinstance(res, dict) else {}
        print(
            f"[Identity] 首次对账完成：实体 {res.get('entities', 0)}，"
            f"逻辑设备 {res.get('devices', 0)}，合并 {res.get('merged', 0)}，"
            f"重匹配 {res.get('remapped', 0)}，失效 {res.get('stale', 0)}"
        )
        self._publish_health_changes(res)

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

    async def _periodic_device_feed(self) -> None:
        """常驻任务：设备事件批量扫描（DCD 裁定 20261001 §二 Q1=B / Q3=(i)）。

        **任务常驻、开关每轮重读**：注册时按 ``device_feed_enabled`` 决定是否建任务，
        等于把「打开通道」这件运维动作绑死在一次重启上；这里让它当班值只在每轮
        开头读一次配置，env/`config.json` 改完下一轮即生效。

        扫描是同步 SQLite + 同步匹配，跑在 ``asyncio.to_thread`` 里——批量扫描器的
        全部意义就是**不占用感知链路同进程**（Q1 不选 A 的理由），不能反过来把
        在线事件循环拖住。响应滞后一个轮询周期是裁定接受的代价。

        触发历史按裁定 §Q3.1（保留 7 天 + 上限 10 万行）每日裁一次。
        """
        feed = self._device_feed
        purge_day = ""
        try:
            await asyncio.sleep(60)  # 首跑延时，避开启动期采集/对账争抢
            while True:
                await asyncio.sleep(feed.interval_seconds())
                if not feed.enabled():
                    continue
                try:
                    res = await asyncio.to_thread(feed.run_once)
                    print(
                        f"[DeviceFeed] {res['window']['start']}→{res['window']['end']}："
                        f"扫描 {res['scanned']}，二元变化 {res['kept']}，命中 {res['matched']}，"
                        f"派发 {res['dispatched']}，仅记录 {res['logged_only']}"
                        f"{'，截断' if res['truncated'] else ''}"
                        f"{'，异常 ' + str(len(res['errors'])) if res['errors'] else ''}"
                        f"（dry_run={res['dry_run']}）"
                    )
                    day = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d")
                    if day != purge_day:
                        purge_day = day
                        p = await asyncio.to_thread(feed.purge_trigger_history)
                        if not p.get("ok"):
                            print(f"[DeviceFeed] 触发历史裁剪失败: {p.get('error')}")
                        else:
                            print(
                                f"[DeviceFeed] 触发历史裁剪：删 {p.get('deleted_expired', 0)} 过期 "
                                f"+ {p.get('deleted_over_cap', 0)} 超额，余 {p.get('remaining', 0)} 行"
                                f"（保留误报标注 {p.get('kept_labeled', 0)} 行）"
                            )
                except Exception as exc:  # noqa: BLE001 - 旁路通道不得打死常驻任务
                    print(f"[DeviceFeed] 扫描异常: {exc}")
        except asyncio.CancelledError:
            pass

    async def _periodic_activity_inference(self) -> None:
        """常驻任务：周期跑行为推断（v0.9.5），产出 canonical 状态写 ``behavior_states``。

        间隔由 ``activity_interval_seconds`` 控制（默认 300s）；MA 定位为权威记忆
        而非实时触发（实时反应由管家 websocket 承担），故低频批处理即可。
        总开关 ``activity_inference_enabled`` 默认开；单次失败仅记录不影响主流程。
        """
        interval = max(60, int(getattr(self.config, "activity_interval_seconds", 300) or 300))
        last_habit_day = ""
        last_process_day = ""
        last_idle_day = ""
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
                        day = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d")
                        # 空转必须自己开口。过去只有产出非零才打印，于是「规则结构性
                        # 够不着」和「家里确实安静」在日志里长得一模一样——生产实测
                        # behavior_states 连续一个月 0 行，没有任何一行日志提示过。
                        if (res.get("ok") and res.get("scanned")
                                and not res.get("persisted") and not res.get("candidates")
                                and day != last_idle_day):
                            last_idle_day = day
                            print(
                                f"[Activity] 空转告警：窗口 {res.get('window_minutes_used')} 分钟"
                                f"内扫 {res.get('scanned')} 事件"
                                f"（带规则标签 {res.get('tagged')} 个），0 状态 0 候选；"
                                f"规则最长跨度 {res.get('rule_horizon_minutes')} 分钟"
                                + ("，窗口低于规则跨度" if res.get("window_below_horizon") else "")
                                + ("，事件数触顶已截断" if res.get("truncated") else "")
                            )
                        # 任务 D：每日一次从 behavior_states 沉淀长期习惯
                        if day != last_habit_day:
                            hres = await asyncio.to_thread(self.activity.infer_habits)
                            last_habit_day = day
                            if hres.get("saved"):
                                print(f"[Activity] 习惯沉淀：{hres.get('saved')} 条")
                        # P1.1：每日一次过程挖掘（行为过程模型 + 一致性检验 → 行为异常）
                        if day != last_process_day:
                            last_process_day = day
                            if getattr(self.config, "process_mining_enabled", True):
                                try:
                                    keep = int(getattr(
                                        self.config, "process_mining_retention_days", 90) or 90)
                                    before = (
                                        now_local(self.config.tz_offset_hours)
                                        - timedelta(days=keep)
                                    ).strftime("%Y-%m-%d")
                                    await asyncio.to_thread(
                                        self.store.purge_behavior_anomalies, before)
                                except Exception as _exc:  # noqa: BLE001
                                    # 稳定性审计缺陷4：purge 失败不再静默，打日志可观测
                                    print(f"[Runtime] purge_behavior_anomalies 失败: {_exc}")
                                pres = await asyncio.to_thread(
                                    self.activity.mine_process,
                                    None, None,
                                    int(getattr(self.config, "process_mining_days", 7) or 7),
                                )
                                if pres.get("ok"):
                                    print(
                                        f"[Activity] 过程挖掘：{pres.get('cases')} case，"
                                        f"异常 {pres.get('anomaly_count')}（落库 {pres.get('persisted')}），"
                                        f"候选规则 {pres.get('candidates')}"
                                    )
                            # P1.2：每日一次在线异常 + 概念漂移检测（river）
                            if getattr(self.config, "drift_enabled", True):
                                try:
                                    keep_d = int(getattr(
                                        self.config, "drift_retention_days", 90) or 90)
                                    bd = (
                                        now_local(self.config.tz_offset_hours)
                                        - timedelta(days=keep_d)
                                    ).strftime("%Y-%m-%d")
                                    await asyncio.to_thread(
                                        self.store.purge_behavior_drifts, bd)
                                except Exception as _exc:  # noqa: BLE001
                                    # 稳定性审计缺陷4：purge 失败不再静默，打日志可观测
                                    print(f"[Runtime] purge_behavior_drifts 失败: {_exc}")
                                dres = await asyncio.to_thread(
                                    self.activity.mine_drift, None, None,
                                    int(getattr(self.config, "drift_days", 14) or 14),
                                )
                                if dres.get("ok"):
                                    print(
                                        f"[Activity] 漂移检测：{dres.get('points')} 点，"
                                        f"漂移 {dres.get('drift_count')}，"
                                        f"异常时段 {dres.get('anomaly_count')}"
                                        f"（落库 {dres.get('persisted')}）"
                                    )
                            # P1.4：每日一次规则召回审计（补召回，不替换规则）
                            if getattr(self.config, "rule_recall_enabled", True):
                                rres = await asyncio.to_thread(
                                    self.activity.audit_rule_recall,
                                    None, None,
                                    int(getattr(self.config, "rule_recall_days", 14) or 14),
                                    None,
                                    min_near_miss=int(getattr(
                                        self.config, "rule_recall_min_near_miss", 2) or 2),
                                )
                                if rres.get("ok"):
                                    worst = min(
                                        (a for a in (rres.get("audit") or [])
                                         if a.get("estimated_recall") is not None),
                                        key=lambda a: a["estimated_recall"], default=None)
                                    print(
                                        f"[Activity] 召回审计：{rres.get('rules')} 条规则，"
                                        f"缺口建议 {rres.get('gap_count')} 条"
                                        + (f"，召回最低 {worst['rule']}="
                                           f"{worst['estimated_recall']}" if worst else "")
                                    )
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
        # 路线图 3.2：全部后台任务经 TaskRegistry 收口，统一取消并等待退出
        try:
            cancelled = await task_registry.cancel_all()
            if cancelled:
                print(f"[Runtime] 已取消 {cancelled} 个后台任务")
        except Exception as exc:  # noqa: BLE001
            print(f"[Runtime] 取消后台任务异常: {exc}")
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
            self.llm.close()
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
        # 机制层时钟的接缝（第七轮 CRITICAL-1 同形态）：库可能是刚 pip install 进来的，
        # 进程里缓存的「不可用」判定必须解锁，否则主路径永远接不上。
        house_time.reset_homesdk_probe()
        self.config = get_config()
        self.auth.config = self.config
        self.auth.users_file = self.config.users_file
        self.ha = HAClient(self.config)
        # 稳定性审计缺陷5：覆盖 ha_db 前关闭旧连接，避免僵尸 pymysql 连接
        _old_ha_db = getattr(self, "ha_db", None)
        if _old_ha_db is not None:
            try:
                _old_ha_db.close()
            except Exception as _exc:
                print(f"[Runtime] 关闭旧 HA MariaDB 客户端异常: {_exc}")
        self.ha_db = self._build_ha_db(self.config)
        self.history.config = self.config
        # chroma 地址（host/port）或 embedding 端点变化时，重置连接缓存，让下次访问用新地址重连，
        # 否则运行时会一直卡在「首次连接失败」的状态，健康页/写入都报错。
        _embed_changed = (old.embedding_base_url, old.embedding_model, old.embedding_api_key) \
            != (self.config.embedding_base_url, self.config.embedding_model, self.config.embedding_api_key)
        if self.history is not None and (
            (old.chroma_host, old.chroma_port)
            != (self.config.chroma_host, self.config.chroma_port)
            or _embed_changed
        ):
            try:
                self.history.reset_chroma()
                print("[Runtime] chroma / embedding 配置变更，已重置连接与嵌入缓存")
            except Exception as exc:
                print(f"[Runtime] 重置 chroma 连接缓存异常: {exc}")
        if _embed_changed:
            # 同一个「试一次就定终身」的形态（第七轮 CRITICAL-2 的姊妹处）：
            # 语义去重的 embedding 探针失败后永不重试，端点修好了也不回头。
            self.semantic_dedup.reset_embedding_probe()
        self.tokens.config = self.config
        self.llm.reconfigure(self.config)
        self.arena.config = self.config
        self.identity.config = self.config
        self.identity.tz_offset_hours = self.config.tz_offset_hours
        self.identity_reconciler.tz_offset_hours = self.config.tz_offset_hours
        self.mqtt.config = self.config
        # homesdk 可能是热更新之后才到位的（与 house_time 同一口径：探测缓存必须可解）
        reset_presence_probe()
        adm_linkage.reset_errors_probe()
        self.insights.reload_config(self.config)
        self.analysis.config = self.config
        self.agent_memory.config = self.config
        self.collector.reconfigure(self.config, self.ha, self.ha_db)
        self.vision.reconfigure(self.config)
        self.vision.ha = self.ha  # HA 客户端已重建，灯态查询须跟随
        self.tv.reconfigure(self.config, self.ha, self.vision)
        self.store.tz_offset_hours = self.config.tz_offset_hours
        # 第七轮审计 CRITICAL-1：配置→组件的接缝原先漏了这 5 条。它们在构造时抓住
        # `runtime.config` 的**对象引用**，而 reload 换的是新对象，于是设置页提示"已生效"、
        # 行为推断仍按旧时区归档、备份仍读旧开关（backup_enabled 关了也可能继续跑）、
        # HA 辅助仍用旧的 ha_assist_memory_top_k。这 5 个对象都是「调用时 getattr(self.config…)」，
        # 重指向即可整条跟随，不需要重建。
        self.ha_assist.config = self.config
        self.backup.config = self.config
        self.semantic_dedup.config = self.config
        self.activity.config = self.config
        self.researcher.config = self.config
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
            # 实体目录是洞察链路的地基：加载失败时查询一律返回空表，
            # 没有这个字段就只能靠人肉翻日志发现（审计 20261002 · 新发现 2）。
            "insights": self.insights.status(),
            # 行为推断的产出读数：behavior_states 是习惯沉淀/过程挖掘/成员日序列的
            # 共同上游，它空转时下游一律是「结构性的空」，得能从健康面直接看出来。
            "activity": self.activity.status(),
            # 家庭墙钟现在由哪套机制说了算、依据哪个键（契约 §四 的对外回执）。
            "house_clock": house_time.status(),
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
    async with _lock:
        if _runtime is not None:
            await _runtime.shutdown()
