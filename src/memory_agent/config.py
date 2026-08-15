#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置管理

配置文件是本项目最重要的持久化资产（含 HA 长期令牌与 LLM 密钥），
因此写入必须原子化，读取必须对未知字段容错 —— 否则一次版本升级
或一次写入中断就会让整个服务起不来。
"""
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List

CONFIG_FILE = "/data/config.json"


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

    # JWT配置
    jwt_secret: str = "change_this_to_random_string"

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

    # 存储
    db_path: str = "/data/memory_agent.db"
    tz_offset_hours: float = 8.0  # 容器内通常无 TZ，显式声明本地时区偏移

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

    return config
