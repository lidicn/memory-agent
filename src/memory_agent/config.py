#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置管理

配置文件是本项目最重要的持久化资产（含 HA 长期令牌与 LLM 密钥），
因此写入必须原子化，读取必须对未知字段容错 —— 否则一次版本升级
或一次写入中断就会让整个服务起不来。
"""
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List

CONFIG_FILE = "/data/config.json"

# JWT 内置默认密钥（公开可预测）。get_config() 检测到仍为此值/为空时，
# 会自动生成强随机密钥并持久化，避免部署者忘记改密钥导致认证形同虚设。
DEFAULT_JWT_SECRET = "change_this_to_random_string"


@dataclass
class Config:
    # HA配置
    hass_server: str = "http://192.168.2.200:8123"
    hass_token: str = ""

    # NR配置
    nr_url: str = "http://192.168.2.200:1990"
    nr_user: str = "lidicn"
    nr_pass: str = ""

    # Redis配置
    redis_host: str = "redis"
    redis_port: int = 6379

    # Chroma配置
    chroma_host: str = "chroma"
    chroma_port: int = 8000
    chroma_mirror: bool = True  # 是否把事件镜像到向量库供语义检索

    # LLM配置（OpenAI 兼容）
    llm_provider: str = "openai-compatible"
    llm_api_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_timeout: int = 120
    # 多厂商代理池（fallback 池）：每条 = 一个后端（厂商 + key + 模型），
    # 按列表顺序作为优先级，上游调用遇 429/5xx/超时/鉴权失败自动切到下一个。
    # 旧版单组 llm_* 字段会在 get_config() 自动迁移成这里的第一条。
    llm_backends: List[dict] = field(default_factory=list)

    # ── 向量嵌入模型（OpenAI 兼容 /v1/embeddings，可选）──────────────────────
    # 缺省留空 → 使用 chroma 默认 MiniLM（零配置不破坏现有部署）。
    # 配置后，history 三集合注入该嵌入函数，提升中文语义检索质量
    # （SiliconFlow bge-m3 / Qwen3-Embedding / new-api 网关等）。
    # 切换模型后请运行 scripts/reindex_embeddings.py 重建集合（维度会变）。
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: str = ""

    # JWT配置（默认值见 DEFAULT_JWT_SECRET；get_config 检测到默认/空值会自动换成随机密钥）
    jwt_secret: str = DEFAULT_JWT_SECRET

    # MCP配置
    mcp_auth_token: str = ""
    # 升级后结构：{name: {"hash": sha256, "prefix": "mcp_xxxx", "created_at": ..., "last_used_at": ...}}
    # 兼容旧结构：{name: "mcp_明文"}，启动时由 MCPTokenStore.migrate_legacy() 一次性迁移
    agent_tokens: Dict[str, Any] = field(default_factory=dict)

    # ── ACP（Agent Client Protocol，拓扑 X peer-to-peer）出站委派配置 ──────
    # 本端作为 ACP client 主动委派任务给对端 autoflow 时使用的对端地址与令牌。
    # 入站（对端调本端）复用 agent_tokens 中 kind=acp 的 acp_ 令牌，不在此处。
    autoflow_acp_url: str = ""
    autoflow_acp_token: str = ""

    # 数据采集配置
    polling_enabled: bool = False
    polling_mode: str = "scheduled"  # "interval" | "scheduled" | "manual"
    polling_interval: int = 3600  # 采集间隔（秒），仅 interval 模式
    polling_time: str = "01:00"  # 每天采集时间 HH:MM，仅 scheduled 模式
    rooms: Dict[str, Any] = field(default_factory=dict)
    excluded_entities: List[str] = field(default_factory=list)
    last_poll_time: str = ""  # 上次采集时间
    data_retention_days: int = 90  # 数据保留天数
    first_run_lookback_hours: int = 24  # 首次采集回溯窗口，避免起点=当前时刻导致采到 0 条

    # ── 记忆研究员（v0.8：定向洞察 LLM 生成）全局安全闸 ─────────────────────
    researcher_enabled: bool = False                  # 总开关（默认关，v0.7.5 收口后开启）
    researcher_daily_token_budget: int = 80000        # 全局日 token 预算断路器
    researcher_scheduler_time: str = "03:00"          # 每日定时洞察时刻 HH:MM（低峰）
    researcher_unit_cap: int = 50                     # 单 Job 分析单元上限（4轴笛卡尔积截断）
    researcher_call_timeout: int = 30                 # 单次 LLM 调用超时（秒）
    researcher_max_consecutive_failures: int = 3      # 连败暂停阈值
    researcher_staging_ttl_days: int = 30             # staging 洞察自动归档天数

    # ── 主动感知·行为推断（v0.9.5：canonical 行为状态 + 序列规则）──────────────
    activity_inference_enabled: bool = True           # 周期行为推断总开关
    activity_window_minutes: int = 15                 # 序列匹配滑动窗口（分钟）
    activity_interval_seconds: int = 300              # 周期推断间隔（秒，默认 5min）
    pir_debounce_sec: int = 30                        # PIR/同实体连续触发去抖窗口（秒）
    activity_conf_threshold: float = 0.6              # 写权威状态的最低置信度（低于仅进候选）

    # ── 授权规则服务端化（v0.9）：Agent(MCP) 写回成员标签需管理员显式开启 ─────────
    member_tag_agent_writeback: bool = False          # 默认关；开启后 confirm_member_tag 才被放行

    # ── MCP 可维护性（v0.9 任务3）：响应体上限与故障注入 ──────────────────────
    mcp_response_max_bytes: int = 65536              # 单工具响应正文上限（0=不限制），超限截断并附摘要

    # ── HA MariaDB 直读（方案B 采集源，可选；未启用时回退 REST）─────────────
    ha_db_enabled: bool = False
    ha_db_host: str = "192.168.2.200"
    ha_db_port: int = 3306
    ha_db_name: str = "homeassistant"
    ha_db_user: str = "memory_agent_ro"
    ha_db_password: str = ""
    ha_db_query_batch: int = 500      # 单次 SQL 查询的实体批量大小
    ha_db_query_timeout: int = 30     # 单条查询读超时（秒）

    # ── Agent 记忆（参与式写回向量库）阈值 ─────────────────────────────────
    privileged_sessions: List[str] = field(default_factory=list)  # 可 force 晋升的会话
    agent_promote_min_days: int = 2          # 跨 N 天反复观测才自动晋升（主路径 (b) 备选）
    agent_trust_step: float = 0.2            # feedback 单次信任分增减
    trust_strict_threshold: float = -0.3     # 低于此值的 session 记忆锁自动晋升
    agent_corroborate_min_conf: float = 0.6  # 佐证 insight 晋升的最低置信度（主路径 (a)）
    agent_retrieve_k: int = 20               # 检索过取量（再重排）
    agent_dup_sim: float = 0.92              # 同 topic_key 相似度高于此值判重复
    agent_conflict_sim: float = 0.85         # 同 topic_key 相似度低于此值且主张相左判冲突
    agent_default_ttl_days: int = 30         # agent 记忆默认 TTL
    agent_sweep_interval_seconds: int = 86400  # 自动晋升 + 镜像 reconcile 扫描间隔

    # ── 家庭成员 / 生活习惯档案 ───────────────────────────────────────────
    auto_discover_persona: bool = False  # 是否主动把发现的标签推送给用户（默认关闭：仅记录、需确认才存档）

    # ── 电视截屏多模态（按需调用，docs/电视截屏多模态识别功能_交接单.md）────
    # 截图来自 xiaomi_miot 的 media_player 实体：attributes.capture 是电视
    # 自身的带签名 URL（有时效），因此**绝不能缓存**，每次都要重新取状态。
    tv_media_player_entity: str = "media_player.xiaomi_rmh1_6103_play_control"
    tv_capture_timeout_s: float = 15          # 从电视 6095 端口拉截图的超时
    tv_capture_refresh_wait_s: float = 2.0    # 强制 HA 刷新实体后，等其写出新 capture 属性的时间
    # TV 端状态广播（TV Cam 项目每 5s 发的 retained MQTT 消息），可选上下文
    tv_mqtt_enabled: bool = False
    tv_mqtt_host: str = "192.168.2.200"
    tv_mqtt_port: int = 1883
    tv_mqtt_user: str = ""
    tv_mqtt_pass: str = ""
    tv_mqtt_topic: str = "tv/livingroom/state"

    # ── MQTT 实时推送（v0.4：向 TVPilot / DeskPilot 推事件，见 docs/交接卡_v0.4_MQTT实时推送.md）──
    # broker 连接参数复用上面的 tv_mqtt_host/port/user/pass（与 TV Cam 同一 broker）。
    # 未启用时 publish() 直接空转返回 False，不影响任何主流程。
    ma_mqtt_enabled: bool = False
    ma_mqtt_topic_prefix: str = "ma"        # 主题前缀：ma/presence、ma/device-health
    ma_mqtt_presence_interval: int = 60     # 在场快照推送间隔（秒，最小 15）
    ma_mqtt_reconnect_interval: int = 30    # broker 不可达时重试建连的退避间隔（秒）
    tv_mqtt_timeout_s: float = 3.0            # 连上后等 retained 消息的时间

    # ── 豆包管家对接（外部服务调用本服务的窄接口，见 docs/交接单_MA对接_成员档案与在场查询.md）──
    # Bearer 令牌：管家凭它读写成员档案 + 查在场。未配置则该通道关闭。
    # 只允许访问 BUTLER_ENDPOINTS 白名单内的路径，拿不到 WebUI 其他接口。
    butler_token: str = ""

    # ── TVPilot / DeskPilot 对接（应用层消费者，见 docs/交接卡_v0.3_对外查询接口.md）──
    # Bearer 令牌：TV / PC 端凭它调用结构化洞察查询（POST /api/insights/query）。
    # 与 butler_token 同款隔离：只放行 APP_ENDPOINTS 白名单，未配置则通道关闭。
    app_token: str = ""
    # v0.6：多应用令牌（TVPilot / DeskPilot 各持一个），落盘为 {name: {hash, prefix, ...}}。
    # 与单 app_token 并存：校验时多令牌优先，遗留单令牌作为兜底。
    app_tokens: Dict[str, Any] = field(default_factory=dict)
    # v0.6 #3：记忆来源（source）取值白名单，防止来源伪造。可在此扩展新来源。
    agent_memory_sources: List[str] = field(default_factory=lambda: ["ma", "butler", "vision", "manual"])

    # ── AutoFlow 竞技场对接（外部服务调用本服务的竞技场窄接口，见 docs/交接单_AutoFlow竞技场对接.md）──
    # 专用 arena_ 令牌（kind=arena）：仅能调用 3 个 arena ACP 工具 + 快照接口，
    # 与生产 / butler / ACP 令牌三者隔离。脱敏映射可选，缺省按 arena 内稳定生成通用名。
    # 脱敏规则：{真实设备名/成员名: 通用名}；为空时 arena 服务按出现顺序自动生成「设备N/成员N」。
    arena_desensitize: Dict[str, Any] = field(default_factory=dict)

    # ── 视觉识别（多模态行为识别，vision-behavior-spec）───────────────────
    vision_enabled: bool = False
    vision_device_token: str = ""            # TV/巡检设备上报令牌（Bearer）
    go2rtc_base_url: str = "http://192.168.2.200:1984"
    go2rtc_user: str = ""
    go2rtc_pass: str = ""
    # 多模态 LLM（doubao2api 网关，独立于 llm_backends）
    vlm_base_url: str = "http://192.168.2.200:9090"
    vlm_api_key: str = ""
    vlm_model: str = "doubao"
    vlm_timeout_s: int = 25
    vlm_max_retries: int = 2
    # 多模态 VLM 端点路径：默认 OpenAI 兼容 /v1/chat/completions；
    # 使用 doubao2api 时可填其私有端点 /v1/images/analyses。
    vlm_endpoint_path: str = "/v1/chat/completions"
    # 摄像头注册表：[{room, stream, enabled, no_tv, light_gate, light_entities: []}]
    vision_cameras: List[dict] = field(default_factory=list)
    # 频率与门槛
    vision_cooldown_s: int = 60              # 同房间两次 VLM 最小间隔
    vision_max_per_hour: int = 20            # 每房间每小时硬上限
    vision_no_tv_interval_s: int = 300       # 无 TV 房间巡检周期
    vision_light_gate: bool = True           # 光线门槛全局总闸：房间开灯才轮询
    vision_snapshot_retention_days: int = 7

    # ── 人脸识别节点池（ArcFace 可插拔，face_node_pool_plan）────────────────
    # memory-agent 持有节点注册表与统一识别路由；节点按权重选路，失败降级 VLM。
    face_node_timeout_s: float = 3.0          # 转发到 Arcface 节点的调用超时（秒）
    face_min_conf: float = 0.6               # 生物识别覆盖 VLM 外观匹配的最低置信度

    # 存储
    db_path: str = "/data/memory_agent.db"
    tz_offset_hours: float = 8.0  # 容器内通常无 TZ，显式声明本地时区偏移

    # ── 备份 / 灾难恢复（先于 v0.8 存量记忆改写就位）──────────────────────────
    # SQLite 主库 VACUUM 快照 + chroma 数据目录快照（可选）+ 14 份轮转。
    backup_enabled: bool = False
    backup_dir: str = "/data/backups"
    backup_retention: int = 14
    # chroma 数据目录（可选）：若 MA 容器能访问 chroma 持久卷则整目录快照；
    # 否则跳过（chroma 可由 SQLite 通过 mirror + reindex 重建）。
    chroma_data_dir: str = ""

    # ── 在线更新（从 GitHub 拉取最新代码并自重启）──────────────────
    # 容器内需把宿主机仓库根挂载到 REPO_DIR（见 docker-compose.yml 的 .:/repo）。
    update_repo_url: str = "https://github.com/lidicn/memory-agent.git"
    update_branch: str = "main"
    restart_cmd: str = ""  # 更新后执行的重启命令；为空则通过 re-exec 重启本进程

    # 数据目录
    data_dir: str = "/data"
    exports_dir: str = "/data/exports"
    templates_dir: str = "/data/templates"
    imported_dir: str = "/data/imported"
    skills_dir: str = "/data/skills"  # 洞察 skill（SKILL.md）持久化目录，可被 MCP 工具读写

    # 用户数据
    users_file: str = "/data/users.json"

    # ── 持久化 ────────────────────────────────────────────────────────────

    def save(self) -> None:
        """原子写入：先写临时文件再 os.replace，避免中断导致配置损坏。"""
        directory = os.path.dirname(CONFIG_FILE) or "."
        os.makedirs(directory, exist_ok=True)
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONFIG_FILE)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    @classmethod
    def load(cls) -> "Config":
        """从文件加载，忽略未知字段（旧版本 config 不应导致启动失败）。"""
        if not os.path.exists(CONFIG_FILE):
            return cls()
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"[Config] 读取配置失败，使用默认值: {exc}")
            return cls()
        if not isinstance(data, dict):
            return cls()

        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            print(f"[Config] 忽略未知配置字段: {', '.join(sorted(unknown))}")
        try:
            return cls(**{k: v for k, v in data.items() if k in known})
        except Exception as exc:
            print(f"[Config] 配置字段类型异常，使用默认值: {exc}")
            return cls()


def get_config() -> Config:
    """获取配置（优先配置文件，环境变量仅作为初始默认值）"""
    config = Config.load()

    env_map = {
        "hass_server": "HASS_SERVER",
        "hass_token": "HASS_TOKEN",
        "nr_url": "NR_URL",
        "nr_user": "NR_USER",
        "nr_pass": "NR_PASS",
        "redis_host": "REDIS_HOST",
        "redis_port": "REDIS_PORT",
        "chroma_host": "CHROMA_HOST",
        "chroma_port": "CHROMA_PORT",
        "llm_provider": "LLM_PROVIDER",
        "llm_api_url": "LLM_API_URL",
        "llm_api_key": "LLM_API_KEY",
        "llm_model": "LLM_MODEL",
        "jwt_secret": "JWT_SECRET",
        "db_path": "DB_PATH",
        "skills_dir": "SKILLS_DIR",
        "ha_db_enabled": "HA_DB_ENABLED",
        "ha_db_host": "HA_DB_HOST",
        "ha_db_port": "HA_DB_PORT",
        "ha_db_name": "HA_DB_NAME",
        "ha_db_user": "HA_DB_USER",
        "ha_db_password": "HA_DB_PASSWORD",
        "ha_db_query_batch": "HA_DB_QUERY_BATCH",
        "ha_db_query_timeout": "HA_DB_QUERY_TIMEOUT",
        "autoflow_acp_url": "AUTOFLOW_ACP_URL",
        "autoflow_acp_token": "AUTOFLOW_ACP_TOKEN",
        "vlm_endpoint_path": "VLM_ENDPOINT_PATH",
        "update_repo_url": "UPDATE_REPO_URL",
        "update_branch": "UPDATE_BRANCH",
        "restart_cmd": "RESTART_CMD",
        "butler_token": "BUTLER_TOKEN",
        "app_token": "APP_TOKEN",
        "tv_media_player_entity": "TV_MEDIA_PLAYER_ENTITY",
        "tv_mqtt_host": "TV_MQTT_HOST",
        "tv_mqtt_topic": "TV_MQTT_TOPIC",
        "ma_mqtt_enabled": "MA_MQTT_ENABLED",
        "ma_mqtt_topic_prefix": "MA_MQTT_TOPIC_PREFIX",
        "ma_mqtt_presence_interval": "MA_MQTT_PRESENCE_INTERVAL",
        "ma_mqtt_reconnect_interval": "MA_MQTT_RECONNECT_INTERVAL",
        "tv_mqtt_user": "TV_MQTT_USER",
        # v0.7 修复：此前只映射了 user 未映射 pass，导致配了用户名却永远拿不到密码，
        # broker 一律返回「未授权」，ma/presence 推送形同虚设。
        "tv_mqtt_pass": "TV_MQTT_PASS",
        "embedding_base_url": "EMBEDDING_BASE_URL",
        "embedding_model": "EMBEDDING_MODEL",
        "embedding_api_key": "EMBEDDING_API_KEY",
        "backup_enabled": "BACKUP_ENABLED",
        "backup_dir": "BACKUP_DIR",
        "backup_retention": "BACKUP_RETENTION",
        "chroma_data_dir": "CHROMA_DATA_DIR",
        "researcher_enabled": "RESEARCHER_ENABLED",
        "researcher_daily_token_budget": "RESEARCHER_DAILY_TOKEN_BUDGET",
        "researcher_scheduler_time": "RESEARCHER_SCHEDULER_TIME",
        "researcher_unit_cap": "RESEARCHER_UNIT_CAP",
        "researcher_call_timeout": "RESEARCHER_CALL_TIMEOUT",
        "researcher_max_consecutive_failures": "RESEARCHER_MAX_CONSECUTIVE_FAILURES",
        "researcher_staging_ttl_days": "RESEARCHER_STAGING_TTL_DAYS",
    }

    for field_name, env_name in env_map.items():
        env_value = os.getenv(env_name)
        current = getattr(config, field_name)
        # 只在当前值为空时使用环境变量，不覆盖用户在 WebUI 保存的配置
        if env_value and (not current or current == ""):
            if isinstance(current, bool):
                setattr(config, field_name, env_value.lower() in ("1", "true", "yes", "on"))
            elif isinstance(current, int):
                try:
                    setattr(config, field_name, int(env_value))
                except ValueError:
                    pass
            elif isinstance(current, float):
                try:
                    setattr(config, field_name, float(env_value))
                except ValueError:
                    pass
            else:
                setattr(config, field_name, env_value)

    # 数值型环境变量即便已有默认值也允许覆盖（这些不属于「用户配置」范畴）
    for field_name, env_name in (
        ("llm_temperature", "LLM_TEMPERATURE"),
        ("tz_offset_hours", "TZ_OFFSET_HOURS"),
    ):
        raw = os.getenv(env_name)
        if raw:
            try:
                setattr(config, field_name, float(raw))
            except ValueError:
                pass

    # 向后兼容：旧版只有单组 llm_* 字段（无 llm_backends），
    # 自动迁移成代理池的第一条，保证升级后对话不中断。
    if not config.llm_backends and config.llm_model and config.llm_api_key and config.llm_api_url:
        config.llm_backends = [{
            "name": "默认模型",
            "provider": config.llm_provider or "",
            "model": config.llm_model,
            "api_url": config.llm_api_url,
            "api_key": config.llm_api_key,
            "temperature": config.llm_temperature,
            "max_tokens": config.llm_max_tokens,
            "timeout": config.llm_timeout,
            "enabled": True,
        }]

    # ── 安全加固（审计 C1）：JWT 密钥不得为空或为内置默认值 ──────────────
    # 默认值 "change_this_to_random_string" 公开可预测，任何拿到源码的人都能伪造
    # JWT 绕过认证。检测到默认/空值时自动生成强随机密钥并持久化，避免部署者忘记
    # 修改密钥导致认证形同虚设（只在首次触发一次）。
    if not config.jwt_secret or config.jwt_secret == DEFAULT_JWT_SECRET:
        config.jwt_secret = secrets.token_urlsafe(48)
        try:
            config.save()
            print("[Config] 已自动生成并持久化新的 JWT 密钥（原密钥为空或为默认值）")
        except Exception as exc:  # noqa: BLE001
            print(f"[Config] JWT 密钥写盘失败，本次运行使用内存态密钥: {exc}")

    return config
