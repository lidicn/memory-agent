"""MCP 服务器 —— 基于官方 Python SDK（MCPServer + Streamable HTTP, MCP 2.x）

定位
----
这是**行为洞察层**，不是事件数据层。原则：

* 洞察由服务端算好（时长、作息、异常），不 dump 上万条原始事件让调用方硬算；
* 一切入口都能用人话（房间名 / 设备类别 / 关键词）定位，不逼调用方背 entity_id；
* 默认干净：功率、温湿度这类每分钟一条的遥测默认排除，避免污染行为分布；
* 一切查询返回 ``total`` / ``offset`` / ``has_more`` 与显式时区，杜绝「取没取全」的疑问。

工具分层
--------
1. 入口层（推荐）：``help`` → ``get_entity_catalog`` → ``get_behavior_insights``
2. 定量层：``get_device_usage``、``search_events``
3. 精确层：``query_events``、``get_behavior_summary``、``get_person_history``
4. 沉淀层：``save_analysis_template``、``export_insight`` …
5. 运维层：``get_collect_status``、``trigger_collection``、``export_history``
6. 技能层：``save_skill`` / ``list_skills`` / ``get_skill`` —— 网关作为技能唯一真源，
   Agent 通过 ``get_skill`` 从网关拉取最新版本（带 version/updated_at），``save_skill``
   把新经验写回网关并自增版本，实现跨 Agent 的技能同步。
7. 视觉层：``list_vision_cameras`` → ``analyze_camera`` —— 用自然语言「看」摄像头。

实现注意
--------
1. MCP 2.0 起 ``FastMCP`` 已移除，改用 ``MCPServer``；``stateless_http`` 与
   ``streamable_http_path`` 移到了 ``streamable_http_app(...)``。
2. 显式 ``streamable_http_path="/"`` 保持对外 URL 仍是 ``/mcp``。
3. 显式 ``host="0.0.0.0"`` 关闭 DNS 重绑定保护（端点已有 Bearer Token 鉴权）。
4. 父应用需串接子应用 lifespan，由 ``app.py`` 负责。

若运行环境尚未安装 ``mcp`` 包，本模块不会让整个服务崩溃，
而是把 ``mcp_app`` 置为 None 并暴露 ``MCP_IMPORT_ERROR``，由 WebUI 提示重建镜像。
"""

from __future__ import annotations

import asyncio
import os

from .runtime import get_runtime
from .tool_schema import build_catalog, TOOL_NAMES as TOOL_NAMES_FROM_SPEC, register_simple_tools  # noqa: F401

SERVER_NAME = "memory-agent"
SERVER_INSTRUCTIONS = """Memory Agent —— 家庭行为记忆与洞察中枢。

## 视觉识别（看摄像头）
用户问「看看谁在客厅」「客厅有没有异常」「玄关有没有快递」这类问题时使用：
1. `list_vision_cameras()` —— 确认有哪些房间/流名（房间名用配置里的真实区域名）。
2. `analyze_camera(room="客厅", prompt_preset="people")` —— 取一帧并让多模态模型识别，
   返回**文字描述**（默认不含图片，隐私优先）。
   - `prompt_preset`：`people`(人员活动) / `security`(安全异常) / `object`(物品宠物快递) / `custom`。
   - 需要自定义问题就传 `prompt="..."`，或 `prompt_preset="custom"` 再传 `prompt`。
   - 默认 `bypass_limits=True`：显式调用会绕过光线门槛与每小时调用上限，立即取帧。
   - 极少数场景需要图片时用 `include_preview=True`（返回 base64 缩略图，费 token）。
3. 把返回的文字描述 `description` 转述给用户即可；`ok=false` 时按 `error` 说明（多为
   go2rtc 凭据未配、流名错或 VLM 会话失效），并提示用户在设置页检查。

## 推荐流程（不要跳过第 1 步）
1. `get_entity_catalog(room="主卧")` —— 用人话找设备，拿到 entity_id 与友好名
2. `get_behavior_insights(days=7)` —— 服务端直接给作息、活跃时段、房间分布、异常
3. `get_device_usage(query="书房空调", days=7)` —— 开了多久、开关几次，无需手算
4. `search_events(room="客厅", category="media", summarize=true)` —— 语义过滤 + 摘要
5. `save_analysis_template(...)` / `export_insight(...)` —— 把结论沉淀给 Node-RED
6. `get_skill(name)` —— 网关是技能唯一真源，Agent 应通过它**拉取最新版本**（`version`、
   `updated_at`），与本地缓存比对后按需更新；`save_skill(name, content)` 把新经验写回网关、
   自增 `version`，供其他 Agent 下次 `get_skill` 拉取。

## 三条避坑提示
* 别一上来就 `query_events`：它要精确 entity_id，先用 catalog 或 search_events。
* 功率/温湿度每分钟一条，会把小时分布拍平。所有洞察工具默认 `behavior_only=true`，
  除非你就是要看遥测，否则别关掉。
* 时间统一用 `days`（最近 N 天）或 `start`/`end`（本地时区 ISO），返回里都带 `window.timezone`。

## 已知数据缺口
`person` 字段来自 HA 状态历史，通常为空 —— 当前**不支持人员归属**分析，
请改用房间/设备维度。媒体、语音类实体若无事件，多半是采集未启用该实体。

## 家庭成员与生活习惯档案
你可以为家人建立「生活习惯档案」，把从数据中看出的行为偏好（如夜猫子🦉、家庭主厨🍳）沉淀下来：

1. 先 `list_members()` 看是否已有成员；没有就 `create_member(name='爸爸', avatar_emoji='🦉')` 建一个。
2. `assign_member_room(member_id, rooms=['主卧','书房'])` 绑定 TA 常驻的房间；`assign_member_device(...)` 绑定专属设备（手机/电脑/按摩椅，或「谁做饭谁操作」的厨房设备）。
3. 调用 `get_member_persona(member_id, days=14)`（或全局 `get_user_persona`）拿到结构化画像：各活动的出现天数/频次/典型时段/房间/置信度与样本证据。
4. **由你（Agent）基于这些信号自主推断标签**，例如：
   - 主卧室 23:00–02:00 活跃占比高 → 「夜猫子 🦉」（category=sleep）
   - 厨房活动 ≥5 天/周且集中在饭点 → 「家庭主厨 🍳」（category=diet）
   - 客厅电视/游戏机日均活跃 >2h → 「重度媒体用户 📺」（category=media）
   并用 `persona.summary` / `sample_evidence` 作为支撑证据。
5. 用自然语言把发现讲给用户：**展示证据 + 询问是否存档到 TA 的生活习惯档案**。
6. **只有用户明确确认后**才调用 `confirm_member_tag(member_id, tag='夜猫子', emoji='🦉', confidence=0.9, category='sleep', evidence=[...])` 写回。
   同名标签会覆盖刷新；用户也可在 WebUI 的「家庭成员」页手动增删标签。

安全准则：绝不在未获确认时擅自写回标签；标签用语要温和、带人情味（emoji + 口语化），避免监控感。
"""

MCP_AVAILABLE = True
MCP_IMPORT_ERROR = ""

try:
    from mcp.server import MCPServer
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except Exception:  # 极少数旧版本没有该模块，退化为 None，下方用 host 隐式关闭兜底
        TransportSecuritySettings = None  # type: ignore[assignment]
except Exception as exc:  # pragma: no cover - 取决于运行环境
    MCPServer = None  # type: ignore[assignment]
    TransportSecuritySettings = None  # type: ignore[assignment]
    MCP_AVAILABLE = False
    MCP_IMPORT_ERROR = (
        f"未安装 mcp SDK（{exc}）。请在 Dockerfile 中加入 'mcp>=2.0' 并重新 "
        f"docker compose build 后再启动。"
    )
    print(f"[MCP] {MCP_IMPORT_ERROR}")


#: 工具目录。``help`` 与 WebUI 的 MCP 接入页都直接消费这份元数据，
#: 避免「文档写一套、实现另一套」。
TOOL_CATALOG: list[dict] = [
    {
        "name": "help",
        "group": "入口",
        "summary": "工具索引与用法示例。help('get_device_usage') 查看单个工具详情",
        "example": "help()",
    },
    {
        "name": "get_entity_catalog",
        "group": "入口",
        "summary": "设备目录：友好名 + 房间 + 类别 + 最后在线 + 近期活跃度",
        "params": {
            "room": "房间名，模糊匹配（可选）",
            "category": "climate/lighting/media/presence/appliance/security/telemetry（可选）",
            "query": "自由关键词，如「主卧空调」（可选）",
            "days": "活跃度统计窗口，默认 7",
        },
        "example": "get_entity_catalog(room='书房')",
        "pitfall": "这是解决「记不住 entity_id」的第一站，几乎所有流程都从这里开始",
    },
    {
        "name": "get_behavior_insights",
        "group": "洞察",
        "summary": "服务端直出洞察：作息节律、各房间活跃时段与 Top 设备、状态转移、异常",
        "params": {
            "days": "分析窗口，默认 7",
            "rooms": "逗号分隔房间名（可选）",
            "behavior_only": "排除遥测，默认 true",
        },
        "example": "get_behavior_insights(days=7)",
        "pitfall": "想写周报直接用它，不要自己拉原始事件再算",
    },
    {
        "name": "get_device_usage",
        "group": "洞察",
        "summary": "设备开关时长/次数/每日分布/时间线，自动处理跨窗口截断与去抖",
        "params": {
            "entity_id": "精确实体（可逗号分隔多个）",
            "query": "或用语义定位，如「书房空调」",
            "room": "配合 category 批量统计整屋（可选）",
            "days": "窗口，默认 7",
            "on_states": "自定义视为开启的状态，逗号分隔（默认：非 off/unavailable 即为开）",
        },
        "example": "get_device_usage(query='书房空调', days=7)",
        "pitfall": "climate 的状态是 heat/cool 而非 on，默认规则已兼容，不用传 on_states",
    },
    {
        "name": "search_events",
        "group": "洞察",
        "summary": "语义化事件搜索：按房间/类别/状态过滤，支持 summarize 压缩返回",
        "params": {
            "room": "房间名（模糊）",
            "category": "设备类别",
            "state": "状态值，逗号分隔",
            "days": "窗口，默认 7",
            "limit": "默认 200，上限 2000",
            "offset": "分页游标",
            "summarize": "true 时只回摘要 + 50 条样本，省 token",
        },
        "example": "search_events(room='客厅', category='media', summarize=True)",
        "pitfall": "返回里的 total/has_more/next_offset 能告诉你有没有取全",
    },
    {
        "name": "get_device_health",
        "group": "洞察",
        "summary": "设备健康探测：揪出失联/没电/长期静默的设备（has_data/last_seen/stale_days）",
        "params": {
            "room": "房间名（可选）",
            "category": "设备类别（可选）",
            "query": "自由关键词（可选）",
            "days": "活跃度窗口，默认 7",
            "stale_days": "判定「失联」的天数阈值，默认 3",
        },
        "example": "get_device_health(stale_days=3)",
        "pitfall": "no_data=从未采到（可能实体 ID 错/未启用）；stale=最近 N 天静默（可能没电/离线）",
    },
    {
        "name": "get_data_coverage",
        "group": "洞察",
        "summary": "数据覆盖报告：明确窗口内实际有数据的日期，避免误判空白窗口",
        "params": {"days": "窗口，默认 7", "start": "本地 ISO（可选）", "end": "本地 ISO（可选）"},
        "example": "get_data_coverage(days=7)",
        "pitfall": "取数前先调它确认窗口完整性：missing_days 告诉你哪些天其实没数据",
    },
    {
        "name": "get_climate_sessions",
        "group": "洞察",
        "summary": "气候会话：拼出「设定温度+室温+运行时长」，补全原只有 hvac_action 的缺口",
        "params": {
            "query": "语义定位，如「主卧空调」（可选）",
            "room": "房间名（可选）",
            "days": "窗口，默认 7",
        },
        "example": "get_climate_sessions(query='主卧空调', days=7)",
        "pitfall": "室温/设定温度来自事件 attrs，需采集时抓到 current_temperature/temperature 属性才有值",
    },
    {
        "name": "infer_activities",
        "group": "洞察",
        "summary": "活动识别：识别做饭/洗澡/睡眠/看电视/离家及自定义活动，带置信度与证据",
        "params": {"days": "窗口，默认 7", "rooms": "逗号分隔房间（可选）", "activities": "类型白名单（可选）"},
        "example": "infer_activities(days=7)",
        "pitfall": "规则引擎产出语义层结论，不要再用原始动线自己拼活动时间",
    },
    {
        "name": "define_activity",
        "group": "洞察",
        "summary": "注册自定义活动识别规则：agent 教系统识别新行为（如午睡/健身）",
        "params": {"name": "活动类型名(snake_case)", "room": "房间子串(可选)", "tags": "设备标签(可选)",
                   "start_hour": "生效起", "end_hour": "生效止", "min_events": "最小次数", "confidence": "置信度"},
        "example": "define_activity(name='nap', room='卧室', tags=['presence'], start_hour=13, end_hour=15, min_events=1)",
        "pitfall": "注册后下次 infer_activities 才生效；识别出的活动可被 source_refs=insight:<id> 引用",
    },
    {
        "name": "get_user_persona",
        "group": "洞察",
        "summary": "合成用户滚动行为画像：活动聚合 + 房间活跃度",
        "params": {"days": "窗口，默认 14"},
        "example": "get_user_persona(days=14)",
        "pitfall": "聚合自 infer_activities + behavior_insights，不要自己算",
    },
    {
        "name": "get_behavior_insights",
        "group": "洞察",
        "summary": "行为环比：最近 compare_days 与上一等长窗口的 activity delta 与趋势",
        "params": {"compare_days": "对比窗口，默认 7"},
        "example": "get_behavior_insights(compare_days=7)",
        "pitfall": "两个窗口首尾相接不重叠；无数据时返回空对比",
    },
    {
        "name": "explain_insight",
        "group": "洞察",
        "summary": "证据溯源：给定 insight(活动id) 或 agent 记忆 id，返回底层事件与 source_refs 解析",
        "params": {"insight_id": "活动 id 或记忆 id"},
        "example": "explain_insight('cb0a6dc52c1eb4bb')",
        "pitfall": "id 来自 infer_activities 的 id 字段或 add_semantic_memory 返回的 memory_id",
    },
    {
        "name": "ask_memory",
        "group": "语义",
        "summary": "自然语言问答：口语问法映射到既有工具；模糊问法回落向量库语义检索",
        "params": {"question": "口语问句，如「用户上周三晚上在干嘛」", "days": "默认窗口，默认 7"},
        "example": "ask_memory('昨天谁在家做饭')",
        "pitfall": "关系库（SQL 映射）为主，向量库（semantic_search）仅作模糊副驾",
    },
    {
        "name": "query_events",
        "group": "精确",
        "summary": "原始事件查询。支持 days 或 start/end，返回 total 与分页游标",
        "params": {
            "days": "最近 N 天（与 start/end 二选一）",
            "start": "本地时区 ISO，如 2026-08-01T00:00:00",
            "end": "本地时区 ISO",
            "rooms": "房间名列表",
            "entities": "entity_id 列表",
            "behavior_only": "排除遥测，默认 true",
        },
        "example": "query_events(days=2, rooms=['书房'], limit=500)",
        "pitfall": "需要精确 ID；不知道 ID 就用 search_events",
    },
    {
        "name": "get_behavior_summary",
        "group": "精确",
        "summary": "轻量总览：事件量、房间分布、24 小时分布、Top 实体",
        "params": {"days": "默认 7", "behavior_only": "默认 true"},
        "example": "get_behavior_summary(days=7)",
        "pitfall": "behavior_only=false 会把功率传感器混进来，小时分布将失真",
    },
    {
        "name": "get_person_history",
        "group": "精确",
        "summary": "按人员查事件。注意：HA 状态历史通常不含操作者，person 多为空",
        "params": {"person": "人员名或 all", "days": "默认 7", "limit": "默认 200"},
        "example": "get_person_history(person='all', days=7)",
        "pitfall": "若 known_persons 为空说明数据源不支持人员归属，请改用房间维度",
    },
    {
        "name": "list_rooms_entities",
        "group": "精确",
        "summary": "房间与实体清单（get_entity_catalog 的轻量版）",
        "example": "list_rooms_entities()",
    },
    {
        "name": "save_analysis_template",
        "group": "沉淀",
        "summary": "把分析结论保存为行为洞察模板",
        "example": "save_analysis_template(id='study_ac_evening', name='书房晚间空调', ...)",
    },
    {
        "name": "list_analysis_templates",
        "group": "沉淀",
        "summary": "列出已保存模板，可按 category 过滤",
        "example": "list_analysis_templates()",
    },
    {
        "name": "export_insight",
        "group": "沉淀",
        "summary": "导出模板详情与 Node-RED 实现说明",
        "example": "export_insight(template_id='study_ac_evening')",
    },
    {
        "name": "delete_analysis_template",
        "group": "沉淀",
        "summary": "删除自定义模板（内置模板不可删）",
        "example": "delete_analysis_template(template_id='xxx')",
    },
    {
        "name": "get_collect_status",
        "group": "运维",
        "summary": "采集服务状态、当前任务进度与数据库统计",
        "example": "get_collect_status()",
    },
    {
        "name": "trigger_collection",
        "group": "运维",
        "summary": "立即触发一次增量采集，异步执行并返回任务号",
        "example": "trigger_collection()",
    },
    {
        "name": "export_history",
        "group": "运维",
        "summary": "导出原始事件用于离线分析",
        "example": "export_history(days=30)",
    },
    {
        "name": "save_skill",
        "group": "技能",
        "summary": "把分析经验写回网关（唯一真源）：自增 version、刷新 updated_at，供 Agent 通过 get_skill 拉取最新版",
        "example": "save_skill(name='sleep_pattern', content='...')",
    },
    {
        "name": "list_skills",
        "group": "技能",
        "summary": "列出网关上所有 skill 及其版本号/更新时间",
        "example": "list_skills()",
    },
    {
        "name": "get_skill",
        "group": "技能",
        "summary": "Agent 从网关拉取某 skill 的最新版本（默认 version='latest'），返回正文与版本元数据",
        "example": "get_skill(name='insight')",
    },
    {
        "name": "add_semantic_memory",
        "group": "Agent记忆",
        "summary": "把挖掘出的行为洞察写回向量库（参与式迭代）：source_refs 必须可解析，dry_run 先自检，恒落 staging",
        "params": {
            "text": "记忆正文",
            "source_refs": "可解析引用列表，event:<event_id> 或 insight:<activity_id>",
            "tags": "标签（仅存 SQL，不进 chroma）",
            "ttl_days": "默认 30",
            "topic_key": "矛盾分组键（默认取首个 tag）",
            "dry_run": "True 只自检不落库（默认 True）",
            "session_id": "默认 mcp",
        },
        "example": "add_semantic_memory(text='用户工作日 23 点后入睡', source_refs=['event:abc123'], dry_run=True)",
        "pitfall": "source_refs 编假 id 会被拒；dry_run=False 也永不自动进 live，需 promote/sweep",
    },
    {
        "name": "promote_memory",
        "group": "Agent记忆",
        "summary": "把 staging 记忆晋升 live（晋升前自动做矛盾/重复检测）",
        "params": {
            "memory_id": "记忆 id（add_semantic_memory 返回）",
            "force": "仅限特权会话强推，绕过护栏",
            "corroborating_insight_id": "佐证 insight id（满足晋升条件 a）",
            "session_id": "默认 mcp",
        },
        "example": "promote_memory(memory_id='...', corroborating_insight_id='...')",
        "pitfall": "重复→禁止晋升；同 topic 冲突→挂起 pending_review；force 非特权会话会被拒",
    },
    {
        "name": "revoke_memory",
        "group": "Agent记忆",
        "summary": "软删一条记忆（墓碑），保留审计轨迹，不硬删",
        "example": "revoke_memory(memory_id='...')",
    },
    {
        "name": "rollback_agent_memory",
        "group": "Agent记忆",
        "summary": "把某 session 下所有未 revoked 记忆整段回滚为 revoked（一键回滚）",
        "example": "rollback_agent_memory(session_id='mcp')",
    },
    {
        "name": "feedback_memory",
        "group": "Agent记忆",
        "summary": "对记忆反馈有用/无用，驱动 trust 与 TTL（外部信号鉴定置信度）",
        "params": {"memory_id": "记忆 id", "useful": "默认 True"},
        "example": "feedback_memory(memory_id='...', useful=True)",
    },
    {
        "name": "list_agent_memories",
        "group": "Agent记忆",
        "summary": "审计视图：列出 agent 记忆（state=staging|live|revoked|pending_review|all）",
        "params": {"state": "默认 all"},
        "example": "list_agent_memories(state='staging')",
    },
    {
        "name": "get_session_trust",
        "group": "Agent记忆",
        "summary": "查某 session 声誉：avg_trust / live 占比 / 是否被锁自动晋升",
        "params": {"session_id": "默认 mcp"},
        "example": "get_session_trust(session_id='mcp')",
    },
    {
        "name": "sweep_promote_candidates",
        "group": "Agent记忆",
        "summary": "手动触发自动晋升扫描 + 镜像 reconcile：把满足条件的 staging 记忆晋升 live",
        "example": "sweep_promote_candidates()",
        "pitfall": "没有这条 sweep，staging 记忆永远进不了检索——写回等于白做",
    },
    {
        "name": "retrieve_agent_memories",
        "group": "Agent记忆",
        "summary": "从已晋升 live 的 Agent 记忆中检索，按相似度+trust 重排",
        "params": {
            "question": "查询问题",
            "trust_min": "最低信任分（默认 -1 不限制）",
            "top_k": "返回条数（默认 5）",
        },
        "example": "retrieve_agent_memories(question='用户几点入睡', top_k=3)",
    },
    {
        "name": "agent_memory_health",
        "group": "Agent记忆",
        "summary": "Agent 记忆子系统健康：各状态数量、镜像缺口、chroma 可用性",
        "example": "agent_memory_health()",
    },
    {
        "name": "get_data_quality",
        "group": "洞察",
        "summary": "聚合数据质量：现有 data_quality_issues + agent 记忆镜像缺口（mirror_dirty）",
        "params": {"days": "默认 30"},
        "example": "get_data_quality(days=30)",
    },
    {
        "name": "list_members",
        "group": "家庭成员",
        "summary": "列出全部家庭成员，含关联房间/设备与已存档的生活习惯标签",
        "example": "list_members()",
    },
    {
        "name": "create_member",
        "group": "家庭成员",
        "summary": "创建家庭成员：姓名 + emoji 头像 + 底色 + 备注",
        "params": {
            "name": "显示名（必填）",
            "avatar_emoji": "头像 emoji，如 🦉",
            "avatar_bg": "头像底色 hex（可选，默认 #0EA5E9）",
            "note": "备注（可选）",
        },
        "example": "create_member(name='爸爸', avatar_emoji='🦉')",
    },
    {
        "name": "assign_member_room",
        "group": "家庭成员",
        "summary": "设置成员关联房间（全量覆盖，多房间传数组）",
        "params": {"member_id": "成员 id", "rooms": "房间名数组，如 ['主卧','书房']"},
        "example": "assign_member_room(member_id='abc', rooms=['主卧','书房'])",
    },
    {
        "name": "assign_member_device",
        "group": "家庭成员",
        "summary": "设置成员专属设备（全量覆盖，多设备传 entity_id 数组）",
        "params": {"member_id": "成员 id", "entity_ids": "entity_id 数组"},
        "example": "assign_member_device(member_id='abc', entity_ids=['device_tracker.dad_phone'])",
    },
    {
        "name": "confirm_member_tag",
        "group": "家庭成员",
        "summary": "把推断出的生活习惯标签写回成员档案（用户确认后调用）",
        "params": {
            "member_id": "成员 id",
            "tag": "标签名，如 夜猫子",
            "category": "分类：sleep/diet/activity/media/hygiene/other",
            "emoji": "标签 emoji，如 🦉",
            "confidence": "置信度 0~1",
            "evidence": "证据列表（来源活动/事件描述）",
        },
        "example": "confirm_member_tag(member_id='abc', tag='夜猫子', emoji='🦉', confidence=0.9, evidence=['主卧连续7天凌晨后熄灯'], category='sleep')",
        "pitfall": "只有用户明确确认才调用；同名标签会覆盖刷新",
    },
    {
        "name": "get_member_persona",
        "group": "家庭成员",
        "summary": "拉取某成员的生活习惯画像：成员档案 + 全屋/房间定向行为画像 + 已存档标签",
        "params": {"member_id": "成员 id", "days": "窗口，默认 14"},
        "example": "get_member_persona(member_id='abc', days=14)",
    },
    {
        "name": "teach_signal",
        "group": "学习",
        "summary": "（学习策略）教系统：某实体在某检测维度是/不是自动化信号（硬排）或写带条件软记忆",
        "params": {
            "entity_id": "实体 ID（被纠正的实体，必填）",
            "scope": "检测维度：all|wake_anchor|presence|working|watching_tv，默认 all",
            "kind": "hard=硬排落表 / soft=软记忆落向量库，默认 hard",
            "reason": "纠正理由",
            "text": "kind='soft' 时必填：软记忆正文",
            "source_refs": "kind='soft' 时的真实引用列表",
            "exclusion_type": "hard 时：exclude|is_automation|not_automation，默认 exclude",
            "session_id": "会话 ID，默认 mcp",
        },
        "example": "teach_signal(entity_id='light.xiaomi_speaker', scope='wake_anchor', kind='hard', reason='定时播报是自动化信号，不是起床')",
        "pitfall": "kind='hard' 幂等（同 entity_id+scope 复用一条）；kind='soft' 必须提供 text 且 source_refs 不能是假 id",
    },
    {
        "name": "list_signal_rules",
        "group": "学习",
        "summary": "（学习策略）列出已学会的信号规则：硬排除 + 软记忆",
        "params": {"include_revoked": "是否包含已撤销的硬排除，默认 False"},
        "example": "list_signal_rules()",
        "pitfall": "软记忆不参与硬排除，仅作推理上下文；硬排除优先级更高",
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]

# 网关随包发布的内置技能目录（Dockerfile 会随 src/ 一起打进镜像）
BUNDLED_SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_bundle")


def _read_skill_meta(path: str) -> dict:
    """从 SKILL.md 的 YAML frontmatter 解析出元数据（name/description/category/version 等）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        if not txt.startswith("---"):
            return {}
        end = txt.find("---", 3)
        if end == -1:
            return {}
        block = txt[3:end]
        meta: dict = {}
        for line in block.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        if "version" in meta:
            try:
                meta["version"] = int(meta["version"])
            except ValueError:
                pass
        return meta
    except Exception:
        return {}


def seed_builtin_skills(rt: "Runtime") -> int:
    """启动种子化：把包内内置技能（skills_bundle）首次写入网关 skills_dir。

    仅当目标技能不存在时才写入（不覆盖 Agent 已迭代出的更高版本），返回新写入的数量。
    内置技能即网关作为真源对外提供的最新版本，Agent 通过 get_skill 拉取。
    """
    seeded = 0
    if not os.path.isdir(BUNDLED_SKILLS_DIR):
        return seeded
    for name in sorted(os.listdir(BUNDLED_SKILLS_DIR)):
        src = os.path.join(BUNDLED_SKILLS_DIR, name, "SKILL.md")
        if not os.path.isfile(src):
            continue
        dst_dir = os.path.join(rt.config.skills_dir, name)
        dst = os.path.join(dst_dir, "SKILL.md")
        if os.path.isfile(dst):
            continue
        os.makedirs(dst_dir, exist_ok=True)
        with open(src, "r", encoding="utf-8") as f:
            data = f.read()
        with open(dst, "w", encoding="utf-8") as f:
            f.write(data)
        seeded += 1
    return seeded


def _build_server():
    if not MCP_AVAILABLE:
        return None

    mcp = MCPServer(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)

    # ── 入口层 ───────────────────────────────────────────────────────────

    @mcp.tool()
    def help(tool_name: str = "") -> dict:
        """工具索引与用法。不带参数看全景，带 tool_name 看单个工具的参数、示例与坑。

        新会话建议第一个调用它，可以省掉大量试错。
        """
        if tool_name:
            hit = next((t for t in TOOL_CATALOG if t["name"] == tool_name), None)
            if not hit:
                return {
                    "ok": False,
                    "error": f"没有名为 {tool_name} 的工具",
                    "available": TOOL_NAMES,
                }
            return {"ok": True, **hit}

        groups: dict[str, list[dict]] = {}
        for t in TOOL_CATALOG:
            groups.setdefault(t["group"], []).append(
                {"name": t["name"], "summary": t["summary"]}
            )
        return {
            "ok": True,
            "server": SERVER_NAME,
            "positioning": "行为洞察层：服务端算好结论，调用方不用背 entity_id、不用手算时长",
            "recommended_flow": [
                "get_entity_catalog(room='主卧')  # 用人话找设备",
                "get_behavior_insights(days=7)    # 直接拿作息与异常",
                "get_device_usage(query='书房空调', days=7)  # 具体设备用了多久",
                "search_events(room='客厅', category='media', summarize=True)",
                "save_analysis_template(...)      # 沉淀给 Node-RED",
            ],
            "common_pitfalls": [
                "功率/温湿度每分钟一条，会污染行为分布 —— 保持 behavior_only=true",
                "query_events 要精确 entity_id，不知道就先 search_events",
                "person 字段通常为空，当前不支持人员归属分析",
                "所有列表返回都带 total/has_more/next_offset，据此判断是否取全",
            ],
            "groups": groups,
            "tools": TOOL_NAMES,
        }

    @mcp.tool()
    async def get_entity_catalog(
        room: str = "",
        category: str = "",
        domain: str = "",
        query: str = "",
        only_enabled: bool = True,
        days: int = 7,
    ) -> dict:
        """设备目录：友好名 / 房间 / 类别 / 最后在线 / 近期活跃度。

        解决「实体 ID 反人类」的入口。支持用房间名、设备类别或自由关键词
        （如 query="主卧空调"）定位，返回结果里的 entity_id 可直接喂给其他工具。
        category 取值：climate / lighting / media / presence / appliance / security / telemetry。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.entity_catalog, room, category, domain, query, only_enabled, days
        )

    # ── 洞察层 ───────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_behavior_insights(
        days: int = 7,
        rooms: str = "",
        behavior_only: bool = True,
        start: str = "",
        end: str = "",
    ) -> dict:
        """服务端直出行为洞察报告：作息节律、各房间活跃时段与 Top 设备、
        跨设备状态转移、每日事件量与异常检测。

        写周报/做作息分析直接用它，不要自己拉原始事件再算。
        rooms 传逗号分隔的房间名可聚焦，留空则分析全屋。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.behavior_insights, days, rooms, behavior_only, start, end
        )

    @mcp.tool()
    async def get_device_usage(
        entity_id: str = "",
        query: str = "",
        room: str = "",
        category: str = "",
        days: int = 7,
        start: str = "",
        end: str = "",
        on_states: str = "",
        debounce_seconds: int = 5,
        include_timeline: bool = True,
    ) -> dict:
        """设备用量统计：总开启时长、开关次数、平均单次时长、每日分布、时间线。

        自动处理三件容易算错的事：窗口开始前就已开启的状态、窗口结束时仍未关闭的
        片段、以及短于 debounce_seconds 的误触抖动。
        climate 的 heat/cool、media_player 的 playing 都会被正确判定为「开启」。

        定位方式二选一：传 entity_id（可逗号分隔多个），或用 query/room/category 语义定位。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.device_usage,
            entity_id,
            room,
            category,
            query,
            days,
            start,
            end,
            on_states,
            debounce_seconds,
            include_timeline,
        )

    @mcp.tool()
    async def search_events(
        room: str = "",
        category: str = "",
        domain: str = "",
        query: str = "",
        entity_id: str = "",
        state: str = "",
        days: int = 7,
        start: str = "",
        end: str = "",
        limit: int = 200,
        offset: int = 0,
        order: str = "desc",
        behavior_only: bool = True,
        summarize: bool = False,
    ) -> dict:
        """语义化事件搜索：按房间 / 设备类别 / 状态过滤，无需背 entity_id。

        entity_id 可逗号分隔多个；显式指定时直接精确过滤，忽略 room/category/query
        的语义解析结果。
        summarize=true 时返回「每个设备变化了多少次、都变成了什么、24 小时分布」
        的压缩摘要 + 50 条样本，比几千条裸事件省 token 且更易读。
        返回的 total / has_more / next_offset 能明确告诉你有没有取全。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.search_events,
            room=room,
            category=category,
            domain=domain,
            query=query,
            entity_id=entity_id,
            state=state,
            days=days,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
            order=order,
            behavior_only=behavior_only,
            summarize=summarize,
        )

    @mcp.tool()
    async def get_device_health(
        room: str = "",
        category: str = "",
        query: str = "",
        days: int = 7,
        stale_days: int = 3,
        only_enabled: bool = True,
    ) -> dict:
        """设备健康探测：主动揪出失联 / 没电 / 长期静默的设备。

        基于实体目录的 has_data / last_seen / stale_days。返回 healthy / no_data /
        stale 三类实体清单。no_data=从未采到数据（可能实体 ID 错/未启用）；
        stale=最近 stale_days 天无数据（可能没电或离线）。
        另返回 data_quality_issues：电量倒灌 / state 与属性单位冲突 / 心跳计数器。
        only_enabled 默认 True，与 get_entity_catalog 口径一致；传 False 看全量实体。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.device_health,
            room, category, query, days, stale_days, only_enabled,
        )

    @mcp.tool()
    async def get_data_coverage(
        days: int = 7,
        start: str = "",
        end: str = "",
    ) -> dict:
        """数据覆盖报告：明确告诉你窗口内实际「有数据」的日期。

        避免误以为前几天的空白也是「有数据」。返回每天的 events 量与 has_data 标记、
        first/last 有数据的日期、以及 missing_days 列表。取数前先调它确认窗口完整性。
        """
        rt = get_runtime()
        return await asyncio.to_thread(rt.insights.data_coverage, days, start, end)

    @mcp.tool()
    async def get_climate_sessions(
        query: str = "",
        room: str = "",
        days: int = 7,
        start: str = "",
        end: str = "",
    ) -> dict:
        """气候会话：把空调/地暖等 climate 实体的开启时段拼成「设定温度 + 室温 + 运行时长」。

        直接解析事件中的 current_temperature（室温）与 temperature（设定温度），
        解决原先只有 hvac_action、缺温度的痛点。query/room 可聚焦。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.climate_sessions, query, room, days, start, end
        )

    @mcp.tool()
    async def infer_activities(
        days: int = 7,
        rooms: str = "",
        start: str = "",
        end: str = "",
        activities: list = None,
    ) -> dict:
        """活动识别：基于设备共现与时段，识别做饭 / 洗澡 / 睡眠 / 看电视 / 离家等活动，
        以及 define_activity 注册的自定义活动。

        返回每个活动在窗口内各天的发生记录（含置信度与证据），让 Agent 直接拿到
        语义层结论，而不用自己拼原始动线。
        activities：可选活动类型白名单（内置名或自定义名），只返回命中的类型。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.infer_activities, days, rooms, start, end, activities
        )

    @mcp.tool()
    async def define_activity(
        name: str,
        room: str = "",
        tags: list = None,
        start_hour: int = 0,
        end_hour: int = 23,
        min_events: int = 1,
        confidence: float = 0.6,
        note: str = "",
    ) -> dict:
        """注册/更新自定义活动识别规则：agent 教系统识别新行为（如午睡/健身）。

        name=活动类型名(英文snake_case)；room=房间子串(可选)；tags=需命中的设备标签(可选,如 presence/door/media)；
        start_hour/end_hour=生效时段；min_events=最小触发次数；confidence=置信度；note=人类可读说明。
        注册后 infer_activities 自动套用，识别出的活动可被 source_refs=insight:<id> 引用写回记忆。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.define_activity, name, room, (tags or []),
            start_hour, end_hour, min_events, confidence, note,
        )

    @mcp.tool()
    async def get_user_persona(days: int = 14) -> dict:
        """合成用户滚动行为画像：基于近期活动识别聚合（出现天数/频次/时段/房间/置信度）+ 房间活跃度。

        做长期用户理解、个性化推荐、健康提醒时直接调用，不要自己拉原始事件算。
        """
        rt = get_runtime()
        return await asyncio.to_thread(rt.insights.get_user_persona, days)

    # ── 家庭成员 / 生活习惯档案 ───────────────────────────────────────────

    @mcp.tool()
    async def list_members() -> dict:
        """列出全部家庭成员（含 rooms/devices/tags 聚合）。"""
        rt = get_runtime()
        members = await asyncio.to_thread(rt.store.list_members)
        return {"ok": True, "members": members, "total": len(members)}

    @mcp.tool()
    async def create_member(
        name: str,
        avatar_emoji: str = "",
        avatar_bg: str = "#0EA5E9",
        note: str = "",
    ) -> dict:
        """创建家庭成员。name 为显示名（必填）；avatar_emoji 为头像（如 🦉）。"""
        rt = get_runtime()
        member = await asyncio.to_thread(
            rt.store.create_member, name, avatar_emoji, avatar_bg, note
        )
        return {"ok": True, "member": member}

    @mcp.tool()
    async def assign_member_room(member_id: str, rooms: list = None) -> dict:
        """设置成员关联房间（全量覆盖）。rooms 为房间名数组，如 ['主卧','书房']。"""
        rt = get_runtime()
        await asyncio.to_thread(rt.store.set_member_rooms, member_id, rooms or [])
        return {"ok": True, "message": "关联房间已更新"}

    @mcp.tool()
    async def assign_member_device(member_id: str, entity_ids: list = None) -> dict:
        """设置成员专属设备（全量覆盖）。entity_ids 为 entity_id 数组。"""
        rt = get_runtime()
        await asyncio.to_thread(rt.store.set_member_devices, member_id, entity_ids or [])
        return {"ok": True, "message": "专属设备已更新"}

    @mcp.tool()
    async def confirm_member_tag(
        member_id: str,
        tag: str,
        category: str = "other",
        emoji: str = "",
        confidence: float = 0.0,
        evidence: list = None,
    ) -> dict:
        """把推断出的生活习惯标签写回成员档案。仅当用户明确确认后调用。

        tag 为标签名（如 夜猫子）；category 取 sleep/diet/activity/media/hygiene/other；
        emoji 如 🦉；confidence 为 0~1；evidence 为证据字符串列表。
        """
        rt = get_runtime()
        result = await asyncio.to_thread(
            rt.store.add_member_tag,
            member_id,
            tag,
            category,
            emoji,
            confidence,
            evidence or [],
            "agent",
        )
        return {"ok": True, "tag": result}

    @mcp.tool()
    async def get_member_persona(member_id: str, days: int = 14) -> dict:
        """拉取某成员的生活习惯画像：成员档案 + 全屋行为画像 +（若已绑定房间）房间定向洞察 + 已存档标签。"""
        rt = get_runtime()
        member = await asyncio.to_thread(rt.store.get_member, member_id)
        if not member:
            return {"ok": False, "error": "成员不存在"}
        persona = await asyncio.to_thread(rt.insights.get_user_persona, days)
        result = {
            "ok": True,
            "member_id": member_id,
            "name": member.get("name"),
            "avatar_emoji": member.get("avatar_emoji"),
            "rooms": member.get("rooms", []),
            "devices": member.get("devices", []),
            "archived_tags": member.get("tags", []),
            "persona": persona,
        }
        if member.get("rooms"):
            try:
                room_insights = await asyncio.to_thread(
                    rt.insights.get_behavior_insights, days, ", ".join(member["rooms"])
                )
                result["room_insights"] = room_insights
            except Exception:
                pass
        return result

    @mcp.tool()
    async def get_behavior_insights_compare(compare_days: int = 7) -> dict:
        """行为环比洞察：对比最近 compare_days 与上一个等长窗口，给出各活动的发生次数/天数 delta 与趋势摘要。

        做周报、习惯变化追踪时调用。
        """
        rt = get_runtime()
        return await asyncio.to_thread(rt.insights.get_behavior_insights, compare_days)

    @mcp.tool()
    async def explain_insight(insight_id: str) -> dict:
        """证据溯源：给定 insight(活动id) 或 agent 记忆 id，返回底层触发事件与 source_refs 解析。

        insight id 来自 infer_activities 的每个活动 id 字段；agent 记忆 id 来自 add_semantic_memory 返回。
        """
        rt = get_runtime()
        return await asyncio.to_thread(rt.insights.explain_insight, insight_id)

    @mcp.tool()
    async def ask_memory(
        question: str,
        days: int = 7,
        route: str = "auto",
        return_hints: bool = False,
    ) -> dict:
        """自然语言问答：用口语问法查询行为记忆。

        例：「用户上周三晚上在干嘛」「昨天谁在家做饭」「空调最近设定几度」。
        问法先映射到既有洞察工具（关系库主力）；模糊问法回落向量库语义检索（副驾）。
        route: auto=结构化+副驾证据, structured=仅结构化, semantic=纯语义+agent记忆。
        return_hints=True 时返回 semantic_hints / agent_memory_hints（调试/混合）。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.ask_memory, question, days, route, return_hints
        )

    # ── Agent 记忆（参与式写回向量库）─────────────────────────────────────

    @mcp.tool()
    async def add_semantic_memory(
        text: str,
        source_refs: list = None,
        tags: list = None,
        ttl_days: int = 0,
        topic_key: str = "",
        dry_run: bool = True,
        session_id: str = "mcp",
    ) -> dict:
        """把挖掘出的行为洞察写回向量库（Agent 参与式迭代）。

        - source_refs 必须是可解析的真实引用：event:<event_id> 或 insight:<activity_id>
          （insight id 来自 infer_activities 返回的每个活动 id 字段）。
        - dry_run=True（默认）：只做冲突/重复自检，不落库，agent 可先 verify。
        - 写入恒为 staging，永不自动进 live；需 promote / sweep 晋升。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.agent_memory.add_semantic_memory,
            session_id, text, (tags or []), (source_refs or []),
            (ttl_days or None), topic_key, dry_run,
        )

    # ── 事件：最后关闭/打开时间 ─────────────────────────────────────────────
    @mcp.tool()
    async def get_last_event(
        entity_id: str = "",
        domain: str = "",
        room: str = "",
        transition: str = "off",
        days: int = 30,
    ) -> dict:
        """查询某实体/某类设备最近一次状态变化（最后关闭/打开/任意变化）。"""
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.insights.get_last_event,
            (entity_id or None), (domain or None), (room or None),
            transition, days,
        )

    # ── 学习策略（信号纠正：硬排除 + 软记忆）──────────────────────────────
    @mcp.tool()
    async def teach_signal(
        entity_id: str,
        scope: str = "all",
        kind: str = "hard",
        reason: str = "",
        text: str = "",
        source_refs: list = None,
        exclusion_type: str = "exclude",
        session_id: str = "mcp",
    ) -> dict:
        """（学习策略）教系统：把『某实体在某检测维度是/不是自动化信号』的纠正持久化。

        - kind='hard'：写入 signal_exclusions 表（无歧义硬排，优先级高于软记忆）；
          生效于 infer_activities 的起床锚定(wake_anchor)、在房/工作判定(working/presence)、
          看电视(watching_tv)。
        - kind='soft'：走 agent 记忆（topic_key=signal_trust，参与信任闭环，用于带条件软判）；
          此时 text 必填，source_refs 须为可解析的真实引用。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.signal_learning.teach_signal,
            entity_id, scope, kind, reason, text, (source_refs or []),
            session_id, exclusion_type,
        )

    @mcp.tool()
    async def list_signal_rules(
        include_revoked: bool = False,
    ) -> dict:
        """（学习策略）列出已学会的信号规则：硬排除（signal_exclusions 表）+ 软记忆（topic_key=signal_trust）。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.signal_learning.list_rules, include_revoked)

    @mcp.tool()
    async def promote_memory(
        memory_id: str,
        force: bool = False,
        corroborating_insight_id: str = "",
        session_id: str = "mcp",
    ) -> dict:
        """把一条 staging 记忆晋升为 live（参与检索）。

        - 普通会话需满足晋升条件：(a) 提供佐证 insight_id 且高置信，或 (b) 跨 N 天反复观测。
        - force=True 仅限 config.privileged_sessions 中的会话，可强推（绕过护栏，慎用）。
        - 晋升前自动做矛盾/重复检测：重复禁止晋升，冲突挂起 pending_review。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.agent_memory.promote_memory, memory_id, session_id, force,
            corroborating_insight_id,
        )

    @mcp.tool()
    async def revoke_memory(memory_id: str) -> dict:
        """软删一条记忆（墓碑），保留审计轨迹，不硬删。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.revoke_memory, memory_id)

    @mcp.tool()
    async def rollback_agent_memory(session_id: str = "mcp") -> dict:
        """把某 session 下所有未 revoked 记忆整段回滚为 revoked（一键回滚）。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.rollback_agent_memory, session_id)

    @mcp.tool()
    async def feedback_memory(memory_id: str, useful: bool = True) -> dict:
        """对一条记忆反馈有用/无用，驱动 trust 与 TTL（外部信号鉴定置信度）。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.feedback_memory, memory_id, useful)

    @mcp.tool()
    async def list_agent_memories(state: str = "all") -> dict:
        """审计视图：列出 agent 记忆（state=staging|live|revoked|pending_review|all）。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.list_agent_memories, state)

    @mcp.tool()
    async def get_session_trust(session_id: str = "mcp") -> dict:
        """查某 session 声誉：avg_trust / live 占比 / 是否被锁自动晋升。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.get_session_trust, session_id)

    @mcp.tool()
    async def sweep_promote_candidates() -> dict:
        """手动触发自动晋升扫描 + 镜像 reconcile：把满足条件的 staging 记忆晋升 live。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.sweep_and_reconcile)

    @mcp.tool()
    async def retrieve_agent_memories(
        question: str, trust_min: float = -1.0, top_k: int = 5
    ) -> dict:
        """从 Agent 记忆库检索已晋升 live 的记忆（按相似度+trust 重排）。

        question: 查询问题或自然语言主题。
        trust_min: 最低信任分过滤（默认 -1 不限制）。
        top_k: 返回条数（默认 5，最多受配置 agent_retrieve_k 约束）。
        返回 {ok, count, memories: [{memory_id, text, similarity, trust, final_score, topic_key}]}。
        """
        rt = get_runtime()
        hits = await asyncio.to_thread(
            rt.agent_memory.retrieve,
            question,
            trust_min=trust_min if trust_min > -1 else None,
            top_k=max(1, int(top_k)),
        )
        return {"ok": True, "count": len(hits), "memories": hits}

    @mcp.tool()
    async def agent_memory_health() -> dict:
        """Agent 记忆子系统健康：各状态数量、镜像缺口、chroma 可用性。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.agent_memory.health)

    @mcp.tool()
    async def get_data_quality(days: int = 30) -> dict:
        """聚合数据质量：现有 data_quality_issues + agent 记忆镜像缺口（mirror_dirty）。"""
        rt = get_runtime()
        return await asyncio.to_thread(rt.insights.get_data_quality, days)

    # ── 精确层 ───────────────────────────────────────────────────────────

    @mcp.tool()
    async def query_events(
        days: int = 0,
        start: str = "",
        end: str = "",
        rooms: list[str] | None = None,
        entities: list[str] | None = None,
        state: str = "",
        limit: int = 200,
        offset: int = 0,
        order: str = "desc",
        behavior_only: bool = True,
    ) -> dict:
        """精确查询原始事件。

        时间语义与其他工具一致：``days`` 表示最近 N 天，或用 ``start``/``end``
        传本地时区 ISO 时间；两者都不传默认最近 7 天。
        返回带 ``total`` 与 ``next_offset``，分页取全不再靠猜。
        不知道 entity_id 时请改用 search_events。
        """
        rt = get_runtime()
        ins = rt.insights
        start_iso, end_iso, meta = await asyncio.to_thread(
            ins.resolve_range, days or 7, start, end
        )
        from .store import TELEMETRY_DOMAINS

        excl = list(TELEMETRY_DOMAINS) if behavior_only else None
        states = [s.strip() for s in str(state or "").split(",") if s.strip()] or None
        limit_val = max(1, min(int(limit or 200), 2000))
        offset_val = max(0, int(offset or 0))

        total = await asyncio.to_thread(
            rt.store.count_events,
            start_iso,
            end_iso,
            rooms or None,
            entities or None,
            None,
            None,
            states,
            excl,
        )
        rows = await asyncio.to_thread(
            rt.store.query_events,
            start_iso,
            end_iso,
            rooms or None,
            entities or None,
            None,
            None,
            limit_val,
            offset_val,
            order,
            states,
            excl,
        )
        decorated = await asyncio.to_thread(ins.decorate, rows)
        return {
            "ok": True,
            "window": meta,
            "behavior_only": behavior_only,
            "total": total,
            "count": len(decorated),
            "offset": offset_val,
            "has_more": offset_val + len(decorated) < total,
            "next_offset": (
                offset_val + len(decorated)
                if offset_val + len(decorated) < total
                else None
            ),
            "events": decorated,
        }

    @mcp.tool()
    async def get_behavior_summary(days: int = 7, behavior_only: bool = True) -> dict:
        """轻量总览：事件总量、房间分布、24 小时分布、Top 实体。

        behavior_only 默认 true，会剔除 sensor/number 等纯遥测；
        返回里的 telemetry_excluded 告诉你过滤掉了多少条。
        需要更深入的结论请用 get_behavior_insights。
        """
        rt = get_runtime()
        result = await asyncio.to_thread(
            rt.history.get_behavior_summary, max(1, min(int(days or 7), 365)), behavior_only
        )
        result["ok"] = True
        result["hint"] = (
            "已排除功率/温湿度等周期性遥测，小时分布反映的是真实活动"
            if behavior_only
            else "⚠️ 含遥测数据：功率传感器每分钟一条，小时分布会被拍平，建议 behavior_only=true"
        )
        return result

    @mcp.tool()
    async def get_person_history(
        person: str = "all", days: int = 7, limit: int = 200
    ) -> dict:
        """按人员查询行为历史。

        注意：HA 状态历史不含操作者信息，person 字段通常为空。
        若返回的 known_persons 为空数组，说明当前数据源不支持人员归属，
        请改用 get_behavior_insights 做房间/设备维度分析。
        """
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.history.get_person_history,
            person or "all",
            max(1, min(int(days or 7), 365)),
            max(1, min(int(limit or 200), 2000)),
        )

    @mcp.tool()
    async def list_rooms_entities(only_enabled: bool = True) -> dict:
        """房间与实体清单（含友好名）。需要活跃度与最后在线请用 get_entity_catalog。"""
        rt = get_runtime()
        rooms_out = {}
        for room, payload in (rt.config.rooms or {}).items():
            if not isinstance(payload, dict):
                continue
            if only_enabled and not payload.get("enabled", True):
                continue
            entities = []
            for entity_id, info in (payload.get("entities") or {}).items():
                info = info if isinstance(info, dict) else {}
                if only_enabled and not info.get("enabled", True):
                    continue
                entities.append(
                    {
                        "entity_id": entity_id,
                        "name": info.get("name", ""),
                        "domain": info.get("domain", entity_id.split(".")[0]),
                    }
                )
            rooms_out[room] = {
                "enabled": payload.get("enabled", True),
                "entities": entities,
            }
        stats = await asyncio.to_thread(rt.store.stats)
        return {"ok": True, "rooms": rooms_out, "stats": stats}

    # ── 运维层 ───────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_collect_status() -> dict:
        """采集服务状态、当前任务进度、数据库（关系库+向量库）统计。

        明确暴露「关系库（主）/ 向量库（辅）」双层状态：
        关系库承载全部事件查询与洞察计算；向量库仅做天×房间聚合摘要镜像与语义检索副驾。
        """
        rt = get_runtime()
        stats = await asyncio.to_thread(rt.store.stats)
        chroma = {}
        try:
            chroma = await asyncio.to_thread(rt.history.chroma_status)
        except Exception as exc:
            chroma = {"available": False, "error": str(exc)}
        return {
            "ok": True,
            "progress": rt.collector.get_progress(),
            "storage": {
                "relational": {"role": "主", "engine": "sqlite", **stats},
                "vector": {"role": "辅", "engine": "chromadb", **chroma},
            },
            "note": "关系库为主（全部事件/洞察计算），向量库为辅（语义检索/天摘要镜像）",
        }

    @mcp.tool()
    async def trigger_collection() -> dict:
        """立即触发一次增量采集（异步执行，立刻返回任务号）。"""
        return await get_runtime().collector.trigger("mcp")

    @mcp.tool()
    async def trigger_incremental_collection(since_minutes: int = 0) -> dict:
        """触发一次增量采集（异步，立刻返回任务号）。

        since_minutes: 增量窗口起点，从多少分钟前开始补采。
          - 0（默认）：从上次成功采集点 last_poll_time 到现在（标准增量，推荐）。
          - >0：强制重采「最近 since_minutes 分钟」，即使已采过也会兜底补齐（写入幂等，重复无害）。
        返回 {ok, job_id, source, message}；用 get_collect_status 轮询进度（progress.source 显示实际数据源）。
        """
        rt = get_runtime()
        result = await rt.collector.trigger_incremental(
            since_minutes=int(since_minutes or 0) or None, source="mcp"
        )
        if result.get("ok"):
            return {
                "ok": True,
                "job_id": result.get("job_id"),
                "source": rt.collector.current_source(),
                "message": "增量采集任务已启动（后台异步），可用 get_collect_status 查询进度",
            }
        return result

    @mcp.tool()
    async def export_history(
        days: int = 30, person: str = "all", limit: int = 2000
    ) -> dict:
        """导出最近 N 天原始事件用于离线分析。日常洞察请优先用 get_behavior_insights。"""
        rt = get_runtime()
        return await asyncio.to_thread(
            rt.history.export_history,
            max(1, min(int(days or 30), 365)),
            person or "all",
            max(1, min(int(limit or 2000), 10000)),
        )

    # ── 沉淀层 ───────────────────────────────────────────────────────────

    @mcp.tool()
    def save_analysis_template(
        id: str,
        name: str,
        description: str,
        category: str,
        entities: list[dict],
        pattern: str,
        confidence: float = 0.0,
        sample_days: int = 0,
        nr_condition: str = "",
        nr_action: str = "",
        default_days: int = 0,
        interpretation: str = "",
    ) -> dict:
        """保存行为洞察模板。分析历史数据发现行为模式后用它沉淀结论。

        category 可选值：sleep / media / lighting / climate / appliance / security / other。
        entities 每项形如 {"entity_id","attribute","pattern","value","time_range","metric"}，
        metric 可选 duration / count / numeric_sum / state_share（默认 duration）。
        default_days 为模板默认时间窗天数；interpretation 为给 Agent 的解读话术模板
        （{window}/{total_human}/{count}/{total_l} 可占位），供 run_analysis_template 直接转述。
        """
        from .templates import BehaviorInsight, EntityQuery

        rt = get_runtime()
        queries = [
            EntityQuery(
                entity_id=e.get("entity_id", ""),
                attribute=e.get("attribute", "state"),
                pattern=e.get("pattern", "equals"),
                value=e.get("value", ""),
                time_range=e.get("time_range", ""),
                metric=e.get("metric", "duration"),
            )
            for e in (entities or [])
            if isinstance(e, dict) and e.get("entity_id")
        ]
        insight = BehaviorInsight(
            id=id,
            name=name,
            description=description,
            category=category or "other",
            entities=queries,
            pattern=pattern,
            confidence=float(confidence or 0.0),
            sample_days=int(sample_days or 0),
            nr_condition=nr_condition,
            nr_action=nr_action,
            default_days=int(default_days or 0),
            interpretation=interpretation,
        )
        saved = rt.templates.save(insight)
        return {"ok": True, "id": saved.id, "name": saved.name, "message": "模板已保存"}

    @mcp.tool()
    def list_analysis_templates(category: str = "") -> dict:
        """列出所有已保存的行为洞察模板。可按 category 筛选。"""
        rt = get_runtime()
        items = rt.templates.list_all()
        if category:
            items = [t for t in items if t.category == category]
        return {
            "ok": True,
            "total": len(items),
            "templates": [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "category": t.category,
                    "confidence": t.confidence,
                    "sample_days": t.sample_days,
                    "default_days": t.default_days,
                    "interpretation": t.interpretation,
                    "entities_count": len(t.entities),
                    "builtin": rt.templates.is_builtin(t.id),
                }
                for t in items
            ],
        }

    @mcp.tool()
    def export_insight(template_id: str) -> dict:
        """导出行为洞察，包含实体查询条件与 Node-RED 实现逻辑。"""
        if not template_id:
            return {"ok": False, "error": "template_id 不能为空"}
        data = get_runtime().templates.export_insight(template_id)
        if not data:
            return {"ok": False, "error": f"模板不存在: {template_id}"}
        return {"ok": True, "insight": data}

    @mcp.tool()
    def delete_analysis_template(template_id: str) -> dict:
        """删除自定义行为洞察模板（内置模板不可删除）。"""
        if not template_id:
            return {"ok": False, "error": "template_id 不能为空"}
        if not get_runtime().templates.delete(template_id):
            return {"ok": False, "error": "模板不存在或为内置模板"}
        return {"ok": True, "message": f"模板已删除: {template_id}"}

    @mcp.tool()
    def run_analysis_template(
        template_id: str,
        days: int = 0,
        start: str = "",
        end: str = "",
        include_timeline: bool = True,
    ) -> dict:
        """按行为洞察模板直接算出结果（服务端计算，非仅导出配置）。

        当用户说"用 xxx 模板分析 / 按 xxx 模板看…"时调用：服务端根据模板里声明的
        实体条件与指标（duration 时长 / count 次数 / numeric_sum 数值求和 / state_share 占比）
        复用既有算力，返回每实体累计值、分日明细、时间轴与解读话术 summary_text，
        Agent 只需基于返回直接作答，不要另写查询或自己反推。

        days 与 start/end 二选一（都给时优先用 start/end）；不填则按模板 default_days。
        """
        if not template_id:
            return {"ok": False, "error": "template_id 不能为空"}
        from .templates import run_template
        try:
            return run_template(
                get_runtime(),
                template_id,
                days=days,
                start=start,
                end=end,
                include_timeline=include_timeline,
            )
        except Exception as exc:
            return {"ok": False, "error": f"模板执行失败：{exc}"}

    # ── 技能层 ───────────────────────────────────────────────────────────

    @mcp.tool()
    def save_skill(
        name: str,
        content: str,
        title: str = "",
        category: str = "insight",
    ) -> dict:
        """把分析经验写回网关（唯一真源）：自增 version、刷新 updated_at，供 Agent 通过 get_skill 拉取最新版。

        name 是 skill 唯一标识（作为目录名，只能含字母数字 _ -）；content 为完整 markdown；
        title/category 仅用于生成 frontmatter，未带 frontmatter 时自动补齐。
        网关保存后会把 version +1 并刷新 updated_at，Agent 下次 get_skill 即拿到最新版本。
        """
        import re

        if not name or not re.match(r"^[A-Za-z0-9_\-]+$", name):
            return {"ok": False, "error": "name 只能含字母、数字、下划线、连字符"}
        if not content or not content.strip():
            return {"ok": False, "error": "content 不能为空"}

        rt = get_runtime()
        base = os.path.join(rt.config.skills_dir, name)
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "SKILL.md")

        # 解析传入内容的已有 frontmatter，继承元数据并续版本号
        existing: dict = {}
        body = content.strip()
        if body.startswith("---"):
            end = body.find("---", 3)
            if end != -1:
                for line in body[3:end].splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        existing[k.strip()] = v.strip()
                body = body[end + 3:].strip()

        prev_version = 0
        if existing.get("version"):
            try:
                prev_version = int(existing["version"])
            except ValueError:
                prev_version = 0
        new_version = prev_version + 1

        from .store import now_local

        # Config 没有 timezone 属性；now_local 需要的是数值时区偏移（小时），
        # 与 Store 保持一致（默认东八区）。getattr 兜底防止未来字段改名导致再崩。
        updated_at = now_local(
            getattr(rt.config, "tz_offset_hours", 8.0)
        ).isoformat(timespec="seconds")
        description = title or existing.get("description") or name
        final_category = category or existing.get("category") or "insight"

        fm = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"category: {final_category}\n"
            f"version: {new_version}\n"
            f"updated_at: {updated_at}\n"
            f"---\n\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(fm + body + "\n")
        return {
            "ok": True,
            "name": name,
            "version": new_version,
            "updated_at": updated_at,
            "path": path,
            "message": "skill 已写回网关，Agent 通过 get_skill 即可拉取最新版本",
        }

    @mcp.tool()
    def list_skills(category: str = "") -> dict:
        """列出网关上所有 skill 及版本号/更新时间，便于 Agent 比对哪些需要拉取最新版。"""
        rt = get_runtime()
        base = rt.config.skills_dir
        if not os.path.isdir(base):
            return {"ok": True, "total": 0, "skills": []}

        out = []
        for name in sorted(os.listdir(base)):
            skill_dir = os.path.join(base, name)
            skill_path = os.path.join(skill_dir, "SKILL.md")
            if os.path.isdir(skill_dir) and os.path.isfile(skill_path):
                meta = _read_skill_meta(skill_path)
                if category and meta.get("category") and meta["category"] != category:
                    continue
                item = {
                    "name": name,
                    "version": meta.get("version", 1),
                    "updated_at": meta.get("updated_at", ""),
                }
                if meta.get("description"):
                    item["description"] = meta["description"]
                out.append(item)
        return {"ok": True, "total": len(out), "skills": out}

    @mcp.tool()
    def get_skill(name: str, version: str = "latest") -> dict:
        """Agent 从网关拉取某 skill 的最新版本（version 默认 'latest'，即网关当前保存版）。

        返回完整 markdown 正文及元数据（version / updated_at 等），便于 Agent 与本地缓存比对、按需更新。
        version 参数预留给未来多版本历史；当前网关只保留最新版，'latest' 或非法值均返回当前版。
        """
        if not name:
            return {"ok": False, "error": "name 不能为空"}
        rt = get_runtime()
        skill_path = os.path.join(rt.config.skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_path):
            return {"ok": False, "error": f"skill 不存在: {name}"}
        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()
        meta = _read_skill_meta(skill_path)
        extra = {k: v for k, v in meta.items() if k not in ("version", "updated_at")}
        return {
            "ok": True,
            "name": name,
            "version": meta.get("version", 1),
            "updated_at": meta.get("updated_at", ""),
            "content": content,
            "is_latest": True,
            **extra,
        }

    return mcp


mcp_server = _build_server()
# 显式关闭 DNS 重绑定保护：端点已有 Bearer Token 鉴权，且服务经局域网/容器暴露。
# 仅靠 host="0.0.0.0" 的隐式关闭不够稳健——部分 mcp 版本对默认 host 仍会自动启用
# 该保护并只允许 localhost，导致局域网客户端（如 DeepSeek++）被 421 拒绝、SSE 端点
# 永不发送 endpoint 事件。显式传 transport_security 可彻底规避此隐患。
_MCP_TRANSPORT_SECURITY = (
    TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if TransportSecuritySettings is not None
    else None
)

mcp_app = (
    mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        host="0.0.0.0",
        transport_security=_MCP_TRANSPORT_SECURITY,
    )
    if mcp_server is not None
    else None
)

# 额外提供旧版 SSE 传输兼容端点，供尚未完整支持 Streamable HTTP 的客户端使用
mcp_sse_app = (
    mcp_server.sse_app(
        sse_path="/sse",
        message_path="/messages/",
        host="0.0.0.0",
        transport_security=_MCP_TRANSPORT_SECURITY,
    )
    if mcp_server is not None
    else None
)

# 将单一 schema 中的 generated 工具动态注册到 MCP，与上方手写 @mcp.tool 并存。
# 这里显式列出需要 schema 自动注册的工具，避免与手写工具重名冲突。
if mcp_server is not None:
    try:
        register_simple_tools(
            mcp_server, get_runtime,
            names=["route_question", "list_vision_cameras", "get_vision_status", "analyze_camera"],
        )
    except Exception as _exc:  # pragma: no cover
        logging.getLogger(__name__).warning("注册 schema 工具失败：%s", _exc)


# ─────────────────────────────────────────────────────────────────────────────
# 单一工具 schema 真源（tool_schema.py）
# 以下两行覆盖上方手工 TOOL_CATALOG / TOOL_NAMES，使 help / describe / selftest
# 与内置 LLM 共用同一份工具定义（name/summary/params/example/pitfall）。
# 上方手工 TOOL_CATALOG / _TOOL_NAMES 为兼容历史保留，实际以 tool_schema.TOOL_SPECS 为准。
TOOL_CATALOG = build_catalog()
TOOL_NAMES = TOOL_NAMES_FROM_SPEC


def get_lifespan_context():
    """返回 MCP 子应用的 lifespan 上下文管理器工厂，供父应用串接。"""
    if mcp_app is None:
        return None
    router = getattr(mcp_app, "router", None)
    return getattr(router, "lifespan_context", None)


def describe() -> dict:
    """给 WebUI 的 MCP 接入页使用。"""
    return {
        "available": MCP_AVAILABLE,
        "error": MCP_IMPORT_ERROR,
        "server_name": SERVER_NAME,
        "tools": TOOL_NAMES,
        "catalog": TOOL_CATALOG,
        "transport": "streamable-http",
        "fallback_transport": "sse",
        "sse_endpoint": "/mcp/sse",
    }
