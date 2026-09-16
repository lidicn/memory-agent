"""单一工具 schema 真源（Tool Schema Single Source of Truth）。

内置 LLM（WebUI 通用对话 / 语音问答）与 MCP 服务器**共用同一份工具定义**：
- 内置 LLM 通过 ``build_openai_tools(("builtin",))`` 拿到 OpenAI function-calling 列表；
- MCP 的 ``help`` / ``describe`` / ``selftest`` 通过 ``build_catalog`` / ``TOOL_NAMES`` 拿到元信息；
- 内置运行时通过 ``dispatch(rt, name, kwargs)`` 同进程调用后端方法。

每个工具含完整元信息：name / summary / description / group / params / service /
method / expose（安全分级）/ example / pitfall。新增工具只需在此加一条，
内置与 MCP 自动同步，杜绝"同一问题一边能答一边答不出"的割裂。

分层
----
- ``expose=("mcp","builtin")``：只读查询 / 洞察 / 报告类，内置对话与 MCP 都能用；
- ``expose=("mcp",)``：写 / 变更 / 采集 / 运维 / 记忆类，仅 MCP（外部 Agent）可用，
  内置对话 LLM 不持有这些危险能力。

``generated=True`` 的工具由 MCP 端通过 ``register_simple_tools`` 动态注册（签名与
文档均来自本 schema）；``generated=False`` 的工具保留 MCP 端手写函数体（自定义逻辑），
但其目录元信息同样来自本 schema，保证 help/describe 与内置同源。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any

# ── 参数 / 工具规格 ────────────────────────────────────────────────────────


@dataclass
class ParamSpec:
    name: str
    type: str  # string | integer | boolean | number | array
    desc: str
    required: bool = False
    default: Any = None
    enum: list | None = None


@dataclass
class ToolSpec:
    name: str
    summary: str
    description: str
    group: str
    params: list
    service: str  # insights | history | agent_memory | collector | templates | store | static
    method: str = ""          # service != static 时的后端方法名（rt.<service>.<method>）
    expose: tuple = ("mcp",)  # ("mcp",) | ("mcp", "builtin")
    example: str = ""
    pitfall: str = ""
    generated: bool = True    # True=可由 register_simple_tools 动态注册；False=MCP 端手写
    force: dict = field(default_factory=dict)  # 派发时强制注入的 kwargs（如 include_timeline=False）


# ── 工具定义 ────────────────────────────────────────────────────────────────

def _p(name, type, desc, required=False, default=None, enum=None) -> ParamSpec:
    return ParamSpec(name=name, type=type, desc=desc, required=required,
                     default=default, enum=enum)


TOOL_SPECS: list = [
    # ── 入口层（规划优先）────────────────────────────────────────────────────
    ToolSpec(
        name="route_question",
        summary="拿到用户问题后**第一步**调用：用确定性逻辑摸排问题（时间窗口/设备/意图/模板），"
                "返回执行计划——推荐工具、参数建议与步骤。模型只需照计划调用具体工具，避免从零自选。",
        description="路由/规划工具。内置 LLM 拿到任意家庭数据类问题时应首先调用它，拿到结构化执行计划后再逐步调用数据工具。"
                    "若用户说'用/按 xxx 模板分析'，计划会直接推荐 run_analysis_template(template_id=..., days=模板默认天数) 执行模板出结果；"
                    "其余周期性报告类诉求仍提示复用 export_insight。",
        group="入口",
        service="insights", method="plan_question",
        generated=True,
        expose=("mcp", "builtin"),
        params=[
            _p("question", "string", "用户的原始问题", required=True),
            _p("days", "integer", "不确定时间窗口时的兜底天数，默认7", default=7),
        ],
        example="route_question(question=\"昨天书房电脑开了多久\") → 计划：get_device_usage(query='书房电脑', days=2)",
        pitfall="这是规划工具不是数据工具，调用后必须接着按计划调用 recommended_tool 才能真正取数。",
    ),
    ToolSpec(
        name="get_entity_catalog",
        summary="设备目录：按房间/类别/关键词列出设备，含友好名、房间、类别、最后在线。",
        description=(
            "设备目录：按房间/类别/关键词列出设备，含友好名、房间、类别、最后在线与近期活跃度。"
            "当用户提到某个设备但不确定其确切名称时，先调用它确认 entity_id/友好名，"
            "再交给 get_device_usage 等精确工具。请只使用当前工具表里列出的工具，"
            "不要假设存在 get_media_playback_history 等未列出的工具。"
        ),
        group="目录",
        service="insights", method="entity_catalog",
        expose=("mcp", "builtin"),
        params=[
            _p("room", "string", "区域(area)名，必须原样使用 HA 中真实存在的名字，如'书房'"),
            _p("category", "string", "设备类别：climate/lighting/media/presence/appliance/security/telemetry"),
            _p("domain", "string", "可选领域，如 light/switch/sensor/media_player"),
            _p("query", "string", "自由关键词，如'书房空调'"),
            _p("only_enabled", "boolean", "是否只看已启用采集的设备，默认 True", default=True),
            _p("days", "integer", "活跃度统计窗口，默认 7", default=7),
        ],
        example="列出书房所有设备 / 有哪些照明设备",
        pitfall="默认 only_enabled=True 只看已启用采集的设备；要看全部（含禁用/未纳管）传 only_enabled=False。设备太多时先用 query 或 room 缩小范围。",
    ),
    ToolSpec(
        name="get_behavior_insights",
        summary="行为洞察报告：作息节律、各房间活跃时段、Top 设备、状态转移、异常。",
        description=(
            "生成行为洞察报告：作息节律、各房间活跃时段、Top 设备、状态转移、异常。"
            "适用于'昨天家里整体情况'、'书房有人多久'等宏观问题。"
        ),
        group="洞察",
        service="insights", method="behavior_insights",
        expose=("mcp", "builtin"),
        params=[
            _p("days", "integer", "分析窗口，默认 7", default=7),
            _p("rooms", "string", "逗号分隔房间名，如'书房'（可选）"),
            _p("behavior_only", "boolean", "是否剔除 sensor/number 等纯遥测域，默认 True", default=True),
            _p("start", "string", "本地 ISO 开始时间（可选，覆盖 days）"),
            _p("end", "string", "本地 ISO 结束时间（可选，覆盖 days）"),
        ],
        example="昨天家里整体情况 / 书房最近一周活跃情况",
        pitfall="默认 days=7；精确区间用 start/end 覆盖 days。behavior_only 控制是否剔除遥测噪声。",
    ),
    ToolSpec(
        name="get_behavior_insights_compare",
        summary="行为洞察环比：对比当前窗口与上一个等长窗口，输出差值与趋势。",
        description=(
            "行为洞察报告（环比）：对比当前窗口与上一个等长窗口，输出差值与趋势，"
            "适用于'和上周比有什么变化'、'这周对比上周'。"
        ),
        group="洞察",
        service="insights", method="get_behavior_insights",
        expose=("mcp", "builtin"),
        params=[
            _p("compare_days", "integer", "环比窗口长度（与上一个等长窗口比较），默认 7", default=7),
        ],
        example="和上周比有什么变化 / 这周对比上周",
        pitfall="compare_days 指定环比窗口长度；结果与上一个等长窗口比较，非自然周。",
    ),
    ToolSpec(
        name="get_device_usage",
        summary="查询设备开关/运行时长、开关次数、每日分布。",
        description=(
            "查询设备开关/运行时长、开关次数、每日分布。适用于电脑、电视、媒体播放器、灯、空调等设备的"
            "'开机多久'、'观看时长'、'运行时长'等问题。不确定设备名时先用 get_entity_catalog 查询。"
        ),
        group="定量",
        service="insights", method="device_usage",
        expose=("mcp", "builtin"),
        force={"include_timeline": False},
        params=[
            _p("entity_id", "string", "精确 entity_id（可选）；不确定名称时优先用 get_entity_catalog"),
            _p("query", "string", "语义定位，如'书房电脑'、'主卧空调'；不确定名称时先用 get_entity_catalog"),
            _p("room", "string", "区域(area)名，原样使用真实区域名；'房间'等口语词若是真实区域名也要照传，不要理解成泛指"),
            _p("category", "string", "设备类别"),
            _p("days", "integer", "窗口天数，默认 7", default=7),
            _p("start", "string", "本地 ISO 开始时间（可选）"),
            _p("end", "string", "本地 ISO 结束时间（可选）"),
            _p("on_states", "string", "视为'开启'的状态值，逗号分隔（可选）"),
            _p("debounce_seconds", "integer", "去抖秒数（可选）"),
            _p("include_timeline", "boolean", "是否返回逐次会话时间线，默认 True（内置对话自动关闭以省 token）", default=True),
        ],
        example="书房电脑昨天开了多久 / 主卧空调运行时长",
        pitfall="不确定设备名时先用 get_entity_catalog 找到 entity_id/友好名。用水/净水器类出水量问题应优先用 ask_memory（净水器分支）。",
    ),
    # 注：v0.3 新增的 MCP 语义工具 query_device_usage / list_device_health 是
    # mcp_server 内手写的 @mcp.tool()（嵌套在 _build_server 中），**不在本 schema
    # 登记**——register_simple_tools 只按显式 names 注册，且 TOOL_NAMES 会被
    # 目录一致性测试用来断言 mcp_server 存在同名模块级函数，嵌套函数不满足。
    # 待 v0.6 把这两个工具改为模块级函数（或服务方法 + generated=True）后再登记。
    ToolSpec(
        name="search_events",
        summary="语义化搜索设备事件，按房间/类别/状态过滤。",
        description=(
            "语义化搜索设备事件，按房间/类别/状态过滤。适用于'昨天书房发生了什么'、"
            "'某人什么时候回家'等事件检索。"
        ),
        group="定量",
        service="insights", method="search_events",
        expose=("mcp", "builtin"),
        params=[
            _p("room", "string", "区域(area)名，原样使用真实区域名；'房间'等口语词若是真实区域名也要照传，不要理解成泛指"),
            _p("category", "string", "设备类别"),
            _p("domain", "string", "领域，如 light/switch/sensor"),
            _p("query", "string", "自由关键词"),
            _p("entity_id", "string", "精确 entity_id（可选）"),
            _p("state", "string", "状态过滤，如'on'/'off'（可选）"),
            _p("days", "integer", "窗口天数，默认 7", default=7),
            _p("start", "string", "本地 ISO 开始时间（可选）"),
            _p("end", "string", "本地 ISO 结束时间（可选）"),
            _p("limit", "integer", "返回条数，默认 200", default=200),
            _p("offset", "integer", "偏移，默认 0", default=0),
            _p("order", "string", "排序 desc/asc，默认 desc", default="desc"),
            _p("behavior_only", "boolean", "是否剔除纯遥测域，默认 True", default=True),
            _p("summarize", "boolean", "true 时返回压缩摘要，省 token，默认 False", default=False),
        ],
        example="昨天书房发生了什么 / 某人什么时候回家",
        pitfall="默认 behavior_only=True 会过滤 sensor/number 等纯遥测；要看全量传 False。limit 太大可能超 token，建议 summarize=True。",
    ),
    ToolSpec(
        name="get_device_health",
        summary="设备健康探测：揪出失联/没电/长期静默的设备。",
        description=(
            "设备健康探测：基于 entity_catalog 的 has_data / last_seen / stale_days，"
            "主动揪出失联 / 没电 / 长期静默的设备。"
        ),
        group="运维",
        service="insights", method="device_health",
        expose=("mcp", "builtin"),
        params=[
            _p("room", "string", "区域(area)名，原样使用真实区域名；'房间'等口语词若是真实区域名也要照传，不要理解成泛指"),
            _p("category", "string", "设备类别"),
            _p("query", "string", "自由关键词"),
            _p("days", "integer", "统计窗口，默认 7", default=7),
            _p("stale_days", "integer", "超过该天数未上报视为陈旧，默认 3", default=3),
            _p("only_enabled", "boolean", "是否只看已启用采集的设备，默认 True", default=True),
        ],
        example="哪些设备失联了 / 有没有没电的设备",
        pitfall="默认 only_enabled=True 与 get_entity_catalog 口径一致；要看禁用/未纳管设备传 only_enabled=False。stale_days 控制'陈旧'阈值。",
    ),
    ToolSpec(
        name="get_data_coverage",
        summary="数据覆盖诊断：各房间/类别事件量、时间缺口、采样率。",
        description=(
            "数据覆盖诊断：各房间/类别事件量、时间缺口、采样率。定位'为什么某天没数据'。"
        ),
        group="运维",
        service="insights", method="data_coverage",
        expose=("mcp", "builtin"),
        params=[
            _p("days", "integer", "窗口天数，默认 7", default=7),
            _p("start", "string", "本地 ISO 开始时间（可选）"),
            _p("end", "string", "本地 ISO 结束时间（可选）"),
        ],
        example="最近三天数据采集正常吗 / 上周数据缺口",
        pitfall="默认 days=7；精确区间用 start/end 覆盖。覆盖低可能意味着设备离线或采集未启用。",
    ),
    ToolSpec(
        name="get_climate_sessions",
        summary="空调/暖气/地暖的开关区间与温度曲线，识别'制冷/制热'会话。",
        description=(
            "空调/暖气/地暖的开关区间与温度曲线，识别'制冷/制热'会话。"
        ),
        group="定量",
        service="insights", method="climate_sessions",
        expose=("mcp", "builtin"),
        params=[
            _p("query", "string", "关键词，如'空调/暖气/地暖'"),
            _p("room", "string", "区域(area)名，原样使用真实区域名；'房间'等口语词若是真实区域名也要照传，不要理解成泛指"),
            _p("days", "integer", "窗口天数，默认 7", default=7),
            _p("start", "string", "本地 ISO 开始时间（可选）"),
            _p("end", "string", "本地 ISO 结束时间（可选）"),
        ],
        example="昨天客厅空调开了几次 / 卧室暖气温度",
        pitfall="仅覆盖 climate 域；其他开关设备用 get_device_usage。query 可填'空调/暖气/地暖'等。",
    ),
    ToolSpec(
        name="infer_activities",
        summary="识别活动：做饭/洗澡/睡眠/看电视/离家等，带置信度与证据。",
        description=(
            "识别活动：做饭/洗澡/睡眠/看电视/离家等，带置信度与证据。"
            "适用于'昨天做了什么'、'几点睡的'。"
        ),
        group="洞察",
        service="insights", method="infer_activities",
        expose=("mcp", "builtin"),
        params=[
            _p("days", "integer", "窗口，默认 7", default=7),
            _p("rooms", "string", "逗号分隔房间（可选）"),
            _p("start", "string", "本地 ISO 开始时间（可选）"),
            _p("end", "string", "本地 ISO 结束时间（可选）"),
            _p("activities", "string", "活动类型白名单（可选）"),
        ],
        example="昨天做了什么 / 几点睡的",
        pitfall="activities 为空识别全部；指定可缩小范围。置信度低于阈值的结果会被过滤。",
    ),
    ToolSpec(
        name="get_user_persona",
        summary="用户画像：作息类型、活跃房间、常用设备、规律性。",
        description=(
            "用户画像：作息类型（晨型/夜猫）、活跃房间、常用设备、规律性。"
        ),
        group="洞察",
        service="insights", method="get_user_persona",
        expose=("mcp", "builtin"),
        params=[
            _p("days", "integer", "统计窗口，默认 14", default=14),
        ],
        example="我是什么作息类型 / 我一般几点活跃",
        pitfall="days 越大画像越稳；默认 14 天。画像基于行为统计，非绝对标签。",
    ),
    ToolSpec(
        name="explain_insight",
        summary="解读某条洞察：返回其计算口径、数据来源、置信度与使用建议。",
        description=(
            "解读某条洞察：返回其计算口径、数据来源、置信度与使用建议。"
        ),
        group="洞察",
        service="insights", method="explain_insight",
        expose=("mcp", "builtin"),
        params=[
            _p("insight_id", "string", "洞察 ID，如 insight:2026-...-behavior-insights", required=True),
        ],
        example="解释一下这条洞察 / 这条报告是怎么算的",
        pitfall="insight_id 来自 get_behavior_insights 等返回的 id 字段；不存在会返回错误。",
    ),
    ToolSpec(
        name="ask_memory",
        summary="自然语言问答：把口语问法映射到既有洞察工具，模糊时回落向量库语义检索。",
        description=(
            "自然语言问答：把口语问法映射到既有洞察工具（净水器出水量/做饭/空调/电视/有人/电脑等），"
            "模糊问法回落到向量库语义检索。适合'昨天书房电脑开了多久'、'昨晚空调开了几次'、"
            "'昨天净水器出水量'等口语问题。"
        ),
        group="统一问答",
        service="insights", method="ask_memory",
        expose=("mcp", "builtin"),
        params=[
            _p("question", "string", "口语问题，如'昨天书房电脑开了多久'、'昨天净水器出水量'", required=True),
            _p("days", "integer", "回溯窗口，默认 7", default=7),
            _p("route", "string", "auto/structured/semantic；auto=结构化+语义副驾，semantic=纯语义检索", default="auto"),
            _p("return_hints", "boolean", "True 时保留 semantic_hints/agent_memory_hints（调试用）", default=False),
        ],
        example="昨天书房电脑开了多久 / 昨晚空调开了几次 / 昨天净水器出水量",
        pitfall="route=semantic 只走向量库，可能漏掉结构化统计；默认 auto 已兼顾两者。模糊问法（如'最近怎么样'）会回落到行为总览+语义副驾。",
    ),
    ToolSpec(
        name="get_data_quality",
        summary="数据质量聚合：电量倒流/单位冲突/心跳/陈旧等问题 + agent 记忆镜像缺口。",
        description=(
            "数据质量聚合：电量倒流/单位冲突/心跳/陈旧等问题 + agent 记忆镜像缺口。"
        ),
        group="运维",
        service="insights", method="get_data_quality",
        expose=("mcp", "builtin"),
        params=[
            _p("days", "integer", "统计窗口，默认 30", default=30),
        ],
        example="数据质量怎么样 / 有没有异常数据",
        pitfall="默认 days=30；仅聚合已记录的 issues，不重新扫描全库。",
    ),

    # ── 精确层 / 沉淀层 / 运维层 / 技能层（仅 MCP，内置对话不持有）─────────────
    ToolSpec(
        name="help",
        summary="查看工具目录与用法；不传 tool_name 返回全部工具。",
        description="查看工具目录与用法。不传 tool_name 返回全部工具列表；传入则返回该工具的参数、示例与陷阱。",
        group="系统",
        service="static", method="",
        expose=("mcp",),
        generated=False,
        params=[_p("tool_name", "string", "工具名（可选）；省略则返回全部工具")],
        example="help() / help('get_device_usage')",
        pitfall="help 仅列出已注册工具；若某工具未出现，说明未注册。",
    ),
    ToolSpec(
        name="define_activity",
        summary="注册/更新一条自定义活动识别规则（agent 教系统识别新行为）。",
        description=(
            "注册/更新一条自定义活动识别规则（agent 教系统识别新行为，如午睡/健身）。"
            "注册后 infer_activities 自动套用。"
        ),
        group="沉淀",
        service="insights", method="define_activity",
        expose=("mcp",),
        generated=False,
        params=[
            _p("name", "string", "活动类型名（英文 snake_case），作为 infer_activities 的 activity 类型", required=True),
            _p("room", "string", "房间子串（可选），命中才计"),
            _p("tags", "array", "需命中的设备标签列表（可选，如 presence/door/media）"),
            _p("start_hour", "integer", "生效开始小时，默认 0", default=0),
            _p("end_hour", "integer", "生效结束小时，默认 23", default=23),
            _p("min_events", "integer", "最小触发次数，默认 1", default=1),
            _p("confidence", "number", "置信度，默认 0.6", default=0.6),
            _p("note", "string", "备注"),
        ],
        example="define_activity(name='nap', room='卧室', tags=['presence'], start_hour=12, end_hour=15)",
        pitfall="name 是 infer_activities 的 activity 类型名；room/tags 为空表示全局生效。",
    ),
    ToolSpec(
        name="query_events",
        summary="精确事件检索：按 entity_id/状态/时间区间拉原始事件（需精确参数）。",
        description=(
            "精确事件检索：按 entity_id/状态/时间区间拉原始事件。适合已拿到 entity_id 后的精确查询，"
            "或需要原始事件做自定义统计。"
        ),
        group="精确",
        service="static", method="",
        expose=("mcp",),
        generated=False,
        params=[
            _p("days", "integer", "窗口天数，默认 0（0=从上次采集点至今）", default=0),
            _p("start", "string", "本地 ISO 开始时间（可选）"),
            _p("end", "string", "本地 ISO 结束时间（可选）"),
            _p("rooms", "string", "逗号分隔房间（可选）"),
            _p("entities", "string", "逗号分隔 entity_id（可选）"),
            _p("state", "string", "状态过滤（可选）"),
            _p("limit", "integer", "返回条数，默认 500", default=500),
            _p("offset", "integer", "偏移，默认 0", default=0),
            _p("order", "string", "排序 desc/asc，默认 desc", default="desc"),
            _p("behavior_only", "boolean", "是否剔除纯遥测域，默认 True", default=True),
        ],
        example="query_events(entities='light.kitchen', days=1)",
        pitfall="需要精确 entity_id；先用 get_entity_catalog 或 search_events 找到名称。",
    ),
    ToolSpec(
        name="get_behavior_summary",
        summary="行为总览：事件总量、Top 实体、小时分布（剔除遥测噪声）。",
        description=(
            "行为总览：事件总量、Top 实体、小时分布（默认剔除 sensor/number 等纯遥测域）。"
        ),
        group="精确",
        service="history", method="get_behavior_summary",
        expose=("mcp",),
        generated=False,
        params=[
            _p("days", "integer", "窗口天数，默认 7", default=7),
            _p("behavior_only", "boolean", "是否剔除纯遥测域，默认 True", default=True),
        ],
        example="get_behavior_summary(days=7)",
        pitfall="behavior_only=True 默认剔除遥测；要看原始全量传 False。",
    ),
    ToolSpec(
        name="get_person_history",
        summary="某人行为历史：出现次数、时段、关联房间与设备。",
        description="某人行为历史：出现次数、时段、关联房间与设备。",
        group="精确",
        service="history", method="get_person_history",
        expose=("mcp",),
        generated=False,
        params=[
            _p("person", "string", "人物名；'all' 或空表示全部", default="all"),
            _p("days", "integer", "窗口天数，默认 7", default=7),
            _p("limit", "integer", "返回条数，默认 200", default=200),
        ],
        example="get_person_history(person='小明', days=7)",
        pitfall="person 需与系统中记录的人物名一致；'all' 查看全部。",
    ),
    ToolSpec(
        name="list_rooms_entities",
        summary="列出所有房间及其下的实体（轻量目录）。",
        description="列出所有房间及其下的实体（轻量目录），用于快速浏览家中有哪些设备。",
        group="目录",
        service="static", method="",
        expose=("mcp",),
        generated=False,
        params=[_p("only_enabled", "boolean", "是否只看已启用采集的设备，默认 True", default=True)],
        example="list_rooms_entities()",
        pitfall="与 get_entity_catalog 不同，这里只列房间结构不返回活跃度。",
    ),
    ToolSpec(
        name="get_collect_status",
        summary="采集状态：各采集器健康、最近采集时间、事件总量、向量库状态。",
        description="采集状态：各采集器健康、最近采集时间、事件总量、向量库（Chroma）状态。",
        group="运维",
        service="static", method="",
        expose=("mcp",),
        generated=False,
        params=[],
        example="get_collect_status()",
        pitfall="只读诊断；触发采集请用 trigger_collection / trigger_incremental_collection。",
    ),
    ToolSpec(
        name="trigger_collection",
        summary="立即触发一次全量采集（拉取 Home Assistant 近期事件）。",
        description="立即触发一次全量采集（拉取 Home Assistant 近期事件）。",
        group="运维",
        service="collector", method="trigger",
        expose=("mcp",),
        generated=False,
        params=[],
        example="trigger_collection()",
        pitfall="会发起外部请求；频繁调用可能触发 Home Assistant 限流。",
    ),
    ToolSpec(
        name="trigger_incremental_collection",
        summary="触发增量采集：只拉取最近 since_minutes 分钟内的新事件。",
        description="触发增量采集：只拉取最近 since_minutes 分钟内的新事件，比全量更轻。0（默认）表示从上次成功采集点补采。",
        group="运维",
        service="collector", method="trigger_incremental",
        expose=("mcp",),
        generated=False,
        params=[_p("since_minutes", "integer", "增量窗口起点（分钟），默认 0=从上次采集点补采", default=0)],
        example="trigger_incremental_collection(since_minutes=30)",
        pitfall="增量采集依赖 Home Assistant 的 recent 时间窗；窗口过大等价于全量。",
    ),
    ToolSpec(
        name="export_history",
        summary="导出某时间窗事件为 CSV 文件，供离线分析。",
        description="导出某时间窗事件为 CSV 文件，供离线分析。",
        group="运维",
        service="history", method="export_history",
        expose=("mcp",),
        generated=False,
        params=[
            _p("days", "integer", "窗口天数，默认 30", default=30),
            _p("person", "string", "人物过滤，默认 'all'", default="all"),
            _p("limit", "integer", "返回条数，默认 2000", default=2000),
        ],
        example="export_history(days=7, person='all', limit=2000)",
        pitfall="导出文件写在服务端文件系统；注意路径权限与磁盘空间。",
    ),
    ToolSpec(
        name="save_analysis_template",
        summary="把行为洞察模板（实体查询 + 模式 + 触发规则）存为可复用模板。",
        description=(
            "把行为洞察模板（实体查询 + 模式 + 触发规则）存为可复用模板，供 Node-RED 等调用。"
        ),
        group="沉淀",
        service="templates", method="save",
        expose=("mcp",),
        generated=False,
        params=[
            _p("id", "string", "模板唯一 ID（如 'water_purifier_daily'）", required=True),
            _p("name", "string", "展示名（如 '净水器每日饮水统计'）", required=True),
            _p("description", "string", "模板说明"),
            _p("category", "string", "分类：sleep/media/lighting/climate/appliance/security/other"),
            _p("entities", "array", "实体查询列表，每项 {entity_id,attribute,pattern,value,time_range,metric}；metric 可选 duration/count/numeric_sum/state_share，默认 duration"),
            _p("pattern", "string", "行为模式描述"),
            _p("confidence", "number", "置信度，默认 0.0", default=0.0),
            _p("sample_days", "integer", "样本天数，默认 0", default=0),
            _p("nr_condition", "string", "Node-RED 触发条件（可选）"),
            _p("nr_action", "string", "Node-RED 触发动作（可选）"),
            _p("default_days", "integer", "模板默认时间窗天数（run_analysis_template 兜底）", default=0),
            _p("interpretation", "string", "给 Agent 的解读话术模板，{window}/{total_human}/{count}/{total_l} 可占位"),
        ],
        example="save_analysis_template(id='water_purifier_daily', name='净水器每日饮水统计', category='appliance', entities=[...])",
        pitfall="id 唯一；重复保存会覆盖。entities 需含真实 entity_id。",
    ),
    ToolSpec(
        name="list_analysis_templates",
        summary="列出全部已存分析模板（可按 category 筛选）。",
        description="列出全部已存分析模板（名称、分类），可按 category 筛选。",
        group="沉淀",
        service="templates", method="list_templates",
        expose=("mcp",),
        generated=False,
        params=[_p("category", "string", "分类过滤（可选）")],
        example="list_analysis_templates() / list_analysis_templates(category='appliance')",
        pitfall="只读；新增/更新用 save_analysis_template。",
    ),
    ToolSpec(
        name="export_insight",
        summary="导出某条行为洞察模板为文件（含实体查询与 Node-RED 实现逻辑）。",
        description="导出某条行为洞察模板为文件，包含实体查询条件与 Node-RED 实现逻辑。",
        group="沉淀",
        service="templates", method="export_insight",
        expose=("mcp",),
        generated=False,
        params=[_p("template_id", "string", "模板 ID", required=True)],
        example="export_insight(template_id='water_purifier_daily')",
        pitfall="template_id 需存在；导出写在服务端文件系统。",
    ),
    ToolSpec(
        name="delete_analysis_template",
        summary="删除指定分析模板（内置模板不可删除）。",
        description="删除指定分析模板（按 template_id；内置模板不可删除）。",
        group="沉淀",
        service="templates", method="delete",
        expose=("mcp",),
        generated=False,
        params=[_p("template_id", "string", "模板 ID", required=True)],
        example="delete_analysis_template(template_id='water_purifier_daily')",
        pitfall="删除不可恢复；确认 template_id 正确；内置模板受保护。",
    ),
    ToolSpec(
        name="run_analysis_template",
        summary="按行为洞察模板直接算出结果（服务端计算，非仅导出配置）。",
        description=(
            "用户说'用 xxx 模板分析 / 按 xxx 模板看…'时调用：服务端根据模板声明的实体条件与指标"
            "（duration 时长 / count 次数 / numeric_sum 数值求和 / state_share 占比）复用既有算力，"
            "返回每实体累计值、分日明细、时间轴与解读话术 summary_text，Agent 只需转述结论，不要另写查询或自己反推。"
        ),
        group="沉淀",
        service="templates", method="run",
        expose=("mcp",),
        generated=False,
        params=[
            _p("template_id", "string", "模板 ID（如 xbox_daily_usage）", required=True),
            _p("days", "integer", "时间窗天数；不填按模板 default_days", default=0),
            _p("start", "string", "起始 ISO 时间（与 days 二选一，优先）"),
            _p("end", "string", "结束 ISO 时间"),
            _p("include_timeline", "boolean", "是否返回会话时间轴，默认 True", default=True),
        ],
        example="run_analysis_template(template_id='xbox_daily_usage', days=2)",
        pitfall="这是'执行'模板，不是导出配置；拿到 summary_text/entities 直接作答，不要重新统计。",
    ),
    ToolSpec(
        name="save_skill",
        summary="把技能（经验）写回网关并自增版本，供其他 Agent 拉取。",
        description=(
            "把技能（经验）写回网关并自增版本，实现跨 Agent 技能同步。"
            "网关是技能唯一真源，Agent 通过 get_skill 拉取最新版本。"
        ),
        group="技能",
        service="agent_memory", method="save_skill",
        expose=("mcp",),
        generated=False,
        params=[
            _p("name", "string", "技能名（唯一，只能含字母数字 _ -）", required=True),
            _p("content", "string", "完整 markdown 正文", required=True),
            _p("title", "string", "标题（可选，用于 frontmatter）"),
            _p("category", "string", "分类，默认 'insight'", default="insight"),
        ],
        example="save_skill(name='morning_brief', content='# 晨间播报 ...')",
        pitfall="name 唯一且只能含字母数字 _ -；保存后 version 自增，其他 Agent 下次 get_skill 拿到新版本。",
    ),
    ToolSpec(
        name="list_skills",
        summary="列出网关上所有技能（名称、版本、更新时间）。",
        description="列出网关上所有技能（名称、版本、更新时间），可按 category 筛选，便于 Agent 比对哪些需更新。",
        group="技能",
        service="agent_memory", method="list_skills",
        expose=("mcp",),
        generated=False,
        params=[_p("category", "string", "分类过滤（可选）")],
        example="list_skills() / list_skills(category='insight')",
        pitfall="只读；新增/更新用 save_skill。",
    ),
    ToolSpec(
        name="get_skill",
        summary="按名拉取技能最新版本（含 version/updated_at）。",
        description="按名拉取技能最新版本（含 version/updated_at），Agent 应据此与本地缓存比对更新。",
        group="技能",
        service="agent_memory", method="get_skill",
        expose=("mcp",),
        generated=False,
        params=[
            _p("name", "string", "技能名", required=True),
            _p("version", "string", "版本，默认 'latest'（当前保存版）", default="latest"),
        ],
        example="get_skill(name='morning_brief')",
        pitfall="返回带 version；本地缓存版本落后时应更新。",
    ),
    ToolSpec(
        name="add_semantic_memory",
        summary="把挖掘出的行为洞察写回向量库（Agent 参与式迭代，恒为 staging）。",
        description=(
            "把挖掘出的行为洞察写回向量库（Agent 参与式迭代）。写入恒为 staging，永不自动进 live；"
            "需 promote / sweep 晋升。dry_run=True（默认）只做冲突/重复自检不落库。"
        ),
        group="记忆",
        service="agent_memory", method="add_semantic_memory",
        expose=("mcp",),
        generated=False,
        params=[
            _p("text", "string", "记忆正文（行为洞察/结论）", required=True),
            _p("source_refs", "array", "真实引用列表，如 ['event:xxx','insight:yyy']"),
            _p("tags", "array", "标签列表"),
            _p("ttl_days", "integer", "存活天数，默认 0（不过期）", default=0),
            _p("topic_key", "string", "主题键（可选）"),
            _p("dry_run", "boolean", "True（默认）只自检不落库；False 才写入", default=True),
            _p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp"),
        ],
        example="add_semantic_memory(text='书房电脑通常 23:50 关机', source_refs=['insight:...'], dry_run=False)",
        pitfall="dry_run 默认 True 不落库；要真正写入需显式 dry_run=False。写入恒为 staging。",
    ),
    ToolSpec(
        name="promote_memory",
        summary="晋升一条 staging 记忆为 live（参与主检索）。",
        description="晋升一条 staging 记忆为 live（参与主检索）。普通会话需满足晋升条件；force 仅限特权会话。",
        group="记忆",
        service="agent_memory", method="promote_memory",
        expose=("mcp",),
        generated=False,
        params=[
            _p("memory_id", "string", "记忆 ID", required=True),
            _p("force", "boolean", "强制晋升（仅特权会话），默认 False", default=False),
            _p("corroborating_insight_id", "string", "佐证洞察 ID（可选）"),
            _p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp"),
        ],
        example="promote_memory(memory_id='mem_xxx', corroborating_insight_id='insight:...')",
        pitfall="晋升前自动做矛盾/重复检测；重复禁止晋升，冲突挂起 pending_review。",
    ),
    ToolSpec(
        name="revoke_memory",
        summary="软删一条记忆（墓碑），保留审计轨迹。",
        description="软删一条记忆（墓碑），保留审计轨迹，不硬删。",
        group="记忆",
        service="agent_memory", method="revoke_memory",
        expose=("mcp",),
        generated=False,
        params=[_p("memory_id", "string", "记忆 ID", required=True)],
        example="revoke_memory(memory_id='mem_xxx')",
        pitfall="软删不硬删；必要时重新 add。",
    ),
    ToolSpec(
        name="rollback_agent_memory",
        summary="回滚某会话写入的记忆（一键纠错）。",
        description="把某 session 下所有未 revoked 记忆整段回滚为 revoked（一键回滚）。",
        group="记忆",
        service="agent_memory", method="rollback_agent_memory",
        expose=("mcp",),
        generated=False,
        params=[_p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp")],
        example="rollback_agent_memory(session_id='sess_xxx')",
        pitfall="回滚该会话写入的全部记忆。",
    ),
    ToolSpec(
        name="feedback_memory",
        summary="对一条记忆标注有用/无用，用于信任调权。",
        description="对一条记忆标注有用/无用，用于信任调权。",
        group="记忆",
        service="agent_memory", method="feedback_memory",
        expose=("mcp",),
        generated=False,
        params=[
            _p("memory_id", "string", "记忆 ID", required=True),
            _p("useful", "boolean", "是否有用，默认 True", default=True),
        ],
        example="feedback_memory(memory_id='mem_xxx', useful=True)",
        pitfall="useful=False 会逐渐降低该记忆信任。",
    ),
    ToolSpec(
        name="list_agent_memories",
        summary="列出 agent 记忆（按状态过滤）。",
        description="列出 agent 记忆（state=staging|live|revoked|pending_review|all）。",
        group="记忆",
        service="agent_memory", method="list_agent_memories",
        expose=("mcp",),
        generated=False,
        params=[_p("state", "string", "状态过滤，默认 'all'", default="all")],
        example="list_agent_memories(state='live')",
        pitfall="默认列出全部状态；用 state 缩小范围。",
    ),
    ToolSpec(
        name="get_session_trust",
        summary="查看某会话的信任快照。",
        description="查看某会话的信任快照（写入的记忆与信任变化）。",
        group="记忆",
        service="agent_memory", method="get_session_trust",
        expose=("mcp",),
        generated=False,
        params=[_p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp")],
        example="get_session_trust(session_id='sess_xxx')",
        pitfall="仅诊断用。",
    ),
    ToolSpec(
        name="sweep_promote_candidates",
        summary="扫描并晋升高置信候选记忆为 live。",
        description="扫描并晋升高置信候选记忆为 live，回收低质记忆（含镜像 reconcile）。",
        group="记忆",
        service="agent_memory", method="sweep_and_reconcile",
        expose=("mcp",),
        generated=False,
        params=[],
        example="sweep_promote_candidates()",
        pitfall="批量操作；会改动记忆状态。",
    ),
    ToolSpec(
        name="retrieve_agent_memories",
        summary="语义检索 agent 记忆（带信任阈值与 top_k）。",
        description="语义检索 agent 记忆（带信任阈值与 top_k），用于为回答提供证据。",
        group="记忆",
        service="agent_memory", method="retrieve",
        expose=("mcp",),
        generated=False,
        params=[
            _p("question", "string", "检索问题", required=True),
            _p("trust_min", "number", "最低信任分过滤，默认 -1（不限制）", default=-1.0),
            _p("top_k", "integer", "返回条数，默认 5", default=5),
        ],
        example="retrieve_agent_memories(question='昨晚空调开了几次', top_k=5)",
        pitfall="trust_min 低于 0 表示不限制；过高会召回过少。",
    ),
    ToolSpec(
        name="agent_memory_health",
        summary="agent 记忆健康度：总量、状态分布、信任分布。",
        description="agent 记忆健康度：总量、状态分布、信任分布、缺口。",
        group="记忆",
        service="agent_memory", method="health",
        expose=("mcp",),
        generated=False,
        params=[],
        example="agent_memory_health()",
        pitfall="只读诊断。",
    ),
    ToolSpec(
        name="get_last_event",
        summary="查询某实体/某类设备最近一次状态变化（最后关闭/打开/任意变化）。",
        description=(
            "返回指定实体、域或房间最近一次状态变化事件。常用于回答『防盗门最后一次打开/关闭是什么时候』、"
            "『卧室吸顶灯最后关闭时间』等问题。transition='off' 找『从开到关』的切换，transition='on' 找『从关到开』的切换。"
        ),
        group="事件",
        service="insights", method="get_last_event",
        expose=("mcp", "builtin"),
        generated=False,
        params=[
            _p("entity_id", "string", "具体实体 ID，如 sensor.xiaomi_cn_xxx_door_state。知道 entity_id 时优先用它。"),
            _p("domain", "string", "实体域，如 binary_sensor / switch / light / sensor。用于查一类设备。"),
            _p("room", "string", "房间名，如 卧室 / 客厅。与 domain 联用缩小范围。"),
            _p("transition", "string", "要找的转换：off=关闭（默认），on=开启，any=任意变化", default="off", enum=["off", "on", "any"]),
            _p("days", "integer", "查询最近多少天，默认 30", default=30),
        ],
        example="get_last_event(entity_id='sensor.xiaomi_cn_xxx_door_state', transition='on')",
        pitfall="传感器状态值可能用 open/closed 或 on/off 表示；transition 按『是否 off/closed』判定关闭，不依赖字面量。",
    ),
    ToolSpec(
        name="teach_signal",
        summary="（学习策略）教系统：某实体在某检测维度是/不是自动化信号（硬排）或写带条件软记忆。",
        description=(
            "学习策略入口：把『某些信号是自动化信号 / 某些信号不能作为某类判定依据』的纠正持久化，"
            "使后续检测自动尊重。kind='hard' 写入 signal_exclusions 表（无歧义硬排，优先级高于软记忆）；"
            "kind='soft' 走 agent 记忆（topic_key=signal_trust，参与信任闭环，用于带条件软判）。"
            "硬排生效于 infer_activities 的起床锚定(wake_anchor)、在房/工作判定(working/presence)、看电视(watching_tv)。"
        ),
        group="学习",
        service="signal_learning", method="teach_signal",
        expose=("mcp",),
        generated=False,
        params=[
            _p("entity_id", "string", "实体 ID（如 light.xiaomi_speaker），被纠正的实体", required=True),
            _p("scope", "string", "检测维度：all|wake_anchor|presence|working|watching_tv，默认 'all'", default="all", enum=["all", "wake_anchor", "presence", "working", "watching_tv"]),
            _p("kind", "string", "hard=硬排落表 / soft=软记忆落向量库，默认 'hard'", default="hard", enum=["hard", "soft"]),
            _p("reason", "string", "纠正理由（如『小爱音箱定时模式切换是自动化信号，不是起床』）"),
            _p("text", "string", "kind='soft' 时必填：软记忆正文"),
            _p("source_refs", "array", "kind='soft' 时的真实引用列表，如 ['event:xxx']"),
            _p("exclusion_type", "string", "hard 时：exclude(默认)|is_automation|not_automation", default="exclude", enum=["exclude", "is_automation", "not_automation"]),
            _p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp"),
        ],
        example="teach_signal(entity_id='light.xiaomi_speaker', scope='wake_anchor', kind='hard', reason='定时播报是自动化信号，不是起床')",
        pitfall="kind='hard' 幂等（同 entity_id+scope 复用一条）；kind='soft' 必须提供 text，且 source_refs 不能是假 id。",
    ),
    ToolSpec(
        name="list_signal_rules",
        summary="（学习策略）列出已学会的信号规则：硬排除 + 软记忆。",
        description=(
            "列出学习策略已持久化的信号规则：硬排除（signal_exclusions 表）与软记忆（topic_key=signal_trust 的 agent 记忆）。"
            "用于回顾系统已学到的纠正，或排查某实体是否已被排除。"
        ),
        group="学习",
        service="signal_learning", method="list_rules",
        expose=("mcp",),
        generated=False,
        params=[
            _p("include_revoked", "boolean", "是否包含已撤销的硬排除，默认 False", default=False),
        ],
        example="list_signal_rules()",
        pitfall="软记忆不参与硬排除逻辑，仅作推理上下文参考；硬排除优先级更高。",
    ),

    # ── 视觉识别（MCP + 内置 LLM 共用）─────────────────────────────────────
    ToolSpec(
        name="list_vision_cameras",
        summary="列出已配置的视觉识别摄像头（房间/流名/是否启用/无TV/光线门槛）。",
        description=(
            "列出当前已配置的视觉识别摄像头：房间名、go2rtc 流名、是否启用、是否无 TV（靠 VLM 识别身份）、"
            "是否开启光线门槛。调用 analyze_camera 前建议先确认房间/流名。"
        ),
        group="视觉",
        service="vision", method="list_cameras",
        expose=("mcp", "builtin"),
        generated=True,
        params=[
            _p("only_enabled", "boolean", "是否只看已启用摄像头，默认 True", default=True),
        ],
        example="list_vision_cameras() / list_vision_cameras(only_enabled=False)",
        pitfall="房间名用 HA 真实区域名；analyze_camera 需要它来定位流。",
    ),
    ToolSpec(
        name="get_vision_status",
        summary="视觉识别服务运行状态：各房间光线门槛、冷却、退避、最近识别结果。",
        description=(
            "返回视觉识别服务运行状态：总开关、光线门槛开关、巡检间隔，以及每个房间的光线门槛结果、"
            "本小时调用次数/上限、冷却剩余、退避剩余、最近一次识别结果摘要。"
        ),
        group="视觉",
        service="vision", method="status",
        expose=("mcp", "builtin"),
        generated=True,
        params=[],
        example="get_vision_status()",
        pitfall="这是只读诊断；主动取帧/识别请用 analyze_camera。",
    ),
    ToolSpec(
        name="analyze_camera",
        summary="取一帧并用多模态模型识别，返回文字描述（默认不含图片，隐私优先）。",
        description=(
            "按房间或 go2rtc 流名取一帧 → 多模态模型(VLM)识别 → 返回文字描述。"
            "preset 选 people(人员活动)/security(安全异常)/object(物品宠物快递)；"
            "custom 或显式提供 prompt 时以 prompt 为准。"
            "显式调用默认绕过光线门槛与每小时调用上限（bypass_limits=True），立即取帧。"
        ),
        group="视觉",
        service="vision", method="analyze_scene",
        expose=("mcp", "builtin"),
        generated=True,
        params=[
            _p("room", "string", "房间名(area)，与 stream 二选一；不区分大小写", required=False),
            _p("stream", "string", "go2rtc 流名，与 room 二选一", required=False),
            _p("prompt_preset", "string", "people/security/object/custom，默认 people", default="people",
               enum=["people", "security", "object", "custom"]),
            _p("prompt", "string", "自定义提示词；preset!=custom 时若提供以它为准"),
            _p("bypass_limits", "boolean", "是否绕过光线门槛/小时上限，默认 True", default=True),
            _p("include_preview", "boolean", "是否附 base64 缩略图，默认 False（隐私优先）", default=False),
        ],
        example=(
            "analyze_camera(room='客厅', prompt_preset='people') / "
            "analyze_camera(room='门口', prompt_preset='security') / "
            "analyze_camera(room='玄关', prompt_preset='object')"
        ),
        pitfall="room/stream 都不传时取首个启用摄像头；纯遥测类问题请用 get_device_usage 等，不要滥用取帧。"
                "bypass_limits=True 会忽略光线门槛与冷却，频繁调用有 VLM 费用，合理节制。",
    ),
    ToolSpec(
        name="query_behavior_events",
        summary="查询多模态视觉识别的历史记录：谁在哪个房间、什么时间。",
        description=(
            "按房间、成员、时间区间查询 VLM 多模态识别产生的行为事件。"
            "返回每条记录的本地时间、房间、识别到的人员姓名、场景描述、动作。"
            "适用于『昨天下午四点有谁在书房』、『昨晚客厅有谁』等历史在场查询。"
        ),
        group="视觉",
        service="insights", method="query_behavior_events",
        expose=("mcp", "builtin"),
        generated=True,
        params=[
            _p("room", "string", "房间名(area)，如'书房'；留空查全部房间", required=False),
            _p("member", "string", "成员姓名过滤，如'lidicn'；留空查全部", required=False),
            _p("days", "integer", "最近 N 天，默认 7", default=7),
            _p("start", "string", "本地 ISO 开始时间（可选，如 2026-08-30T16:00:00）", required=False),
            _p("end", "string", "本地 ISO 结束时间（可选）", required=False),
            _p("limit", "integer", "返回条数，默认 50，最大 200", default=50),
        ],
        example="query_behavior_events(room='书房', start='2026-08-30T15:00:00', end='2026-08-30T17:00:00')",
        pitfall="时间默认按天切片；精确到小时需传 start/end。结果按 server_ts 倒序。",
    ),
    # ── AutoFlow 竞技场（外部服务窄接口，见 docs/交接单_AutoFlow竞技场对接.md）────
    # 仅对持有 arena_ 令牌（kind=arena）的竞技场开放，与生产 / butler / ACP 令牌隔离。
    # expose=("arena",)：dispatch 同进程调用 rt.arena.<method>，ACP 按令牌作用域暴露。
    ToolSpec(
        name="get_arena_inspiration",
        summary="为竞技场 Agent 提供脱敏的「创造力灵感」：基于分区固定快照的行为模式。",
        description=(
            "按竞技场分区（arena_id）返回脱敏灵感列表，每条含 title/description/"
            "suggested_flow/entity_hints/creativity_score。数据来自该分区版本化快照，"
            "不暴露真实设备名/成员名。灵感类型：behavior/device/member/anomaly/all。"
        ),
        group="竞技场",
        service="arena", method="get_arena_inspiration",
        expose=("arena",),
        generated=False,
        params=[
            _p("arena_id", "string", "竞技场分区 ID，如 study_room", required=True),
            _p("inspiration_type", "string", "灵感类型：behavior/device/member/anomaly/all，默认 all", default="all"),
            _p("limit", "integer", "返回灵感数量，默认 5", default=5),
        ],
        example="get_arena_inspiration(arena_id='study_room', inspiration_type='all', limit=5)",
        pitfall="分区需先经 POST /api/arena/snapshot 生成快照；无快照时返回空 items 与 hint。",
    ),
    ToolSpec(
        name="evaluate_creativity",
        summary="评估竞技场题目的创造力/新颖度/贴合度，并判定是否与题目库重复（锁定机制）。",
        description=(
            "三层判定：实体重叠(快速)→文本相似(轻量)→LLM 语义(仅模糊区间)。"
            "返回 creativity_score / novelty_score / relevance_score / feedback / "
            "is_duplicate / duplicate_of。非重复题目会被写入题目库（向量去重）。"
        ),
        group="竞技场",
        service="arena", method="evaluate_creativity",
        expose=("arena",),
        generated=False,
        params=[
            _p("arena_id", "string", "竞技场分区 ID", required=True),
            _p("title", "string", "Agent 提出的题目", required=True),
            _p("description", "string", "flow 描述", required=True),
            _p("entity_ids", "array", "涉及的设备标识列表（建议用灵感里的 entity_hints）", required=True),
        ],
        example="evaluate_creativity(arena_id='study_room', title='夜间护眼模式', description='...', entity_ids=['设备1'])",
        pitfall="entity_ids 用灵感返回的 entity_hints（脱敏通用名），不要传真实 entity_id。",
    ),
    ToolSpec(
        name="record_arena_result",
        summary="记录竞技场一次提交结果，沉淀洞察迭代闭环数据。",
        description=(
            "竞技场每次提交后把结果写回：成功与否、token 消耗、使用了哪些 memory 工具。"
            "用于分析『用了洞察的 Agent 是否更强』，形成闭环。"
        ),
        group="竞技场",
        service="arena", method="record_arena_result",
        expose=("arena",),
        generated=False,
        params=[
            _p("arena_id", "string", "竞技场分区 ID", required=True),
            _p("task_title", "string", "题目", required=True),
            _p("task_description", "string", "flow 描述", required=True),
            _p("flow_dsl", "string", "提交的 flow DSL 文本", required=True),
            _p("success", "boolean", "是否成功运行", required=True),
            _p("token_used", "integer", "消耗 token 数", default=0),
            _p("agent_id", "string", "提交 Agent 标识", required=True),
            _p("used_memory_tools", "array", "使用了哪些 memory-agent 工具", default=[]),
        ],
        example="record_arena_result(arena_id='study_room', task_title='...', task_description='...', flow_dsl='...', success=True, agent_id='agent-1', used_memory_tools=['get_arena_inspiration'])",
        pitfall="仅记录，不影响快照与题目库；建议每次提交后调用以沉淀数据。",
    ),
]

SPEC_BY_NAME: dict = {s.name: s for s in TOOL_SPECS}

# MCP 暴露的工具名集合（供 describe / selftest / help 使用，与内置同源）
TOOL_NAMES: list = [s.name for s in TOOL_SPECS if "mcp" in s.expose]


# ── 生成器 ──────────────────────────────────────────────────────────────────

_JSON_TYPE = {
    "string": "string",
    "integer": "integer",
    "boolean": "boolean",
    "number": "number",
    "array": "array",
}

_PY_TYPE = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "number": float,
    "array": list,
}


def build_openai_tools(surfaces=("builtin", "mcp")) -> list:
    """生成 OpenAI function-calling 工具列表（内置 LLM 用 surfaces=('builtin',)）。"""
    tools = []
    for spec in TOOL_SPECS:
        if not set(surfaces) & set(spec.expose):
            continue
        props = {}
        required = []
        for p in spec.params:
            pdef: dict = {"type": _JSON_TYPE.get(p.type, "string"),
                          "description": p.desc}
            if p.enum:
                pdef["enum"] = p.enum
            if not p.required and p.default is not None:
                pdef["default"] = p.default
            props[p.name] = pdef
            if p.required:
                required.append(p.name)
        tools.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return tools


def build_catalog() -> list:
    """生成 MCP help/describe 用的工具目录（含完整元信息）。"""
    catalog = []
    for spec in TOOL_SPECS:
        if "mcp" not in spec.expose:
            continue
        catalog.append({
            "name": spec.name,
            "group": spec.group,
            "summary": spec.summary,
            "params": [
                {"name": p.name, "type": p.type, "description": p.desc}
                for p in spec.params
            ],
            "example": spec.example,
            "pitfall": spec.pitfall,
        })
    return catalog


async def dispatch(rt, name: str, args: dict | None) -> dict:
    """同进程派发：调用 rt.<service>.<method>(**kwargs)，供内置 LLM 使用。

    仅处理 generated 类（内置暴露）工具；MCP 专有的手写工具不走此路径。
    """
    spec = SPEC_BY_NAME.get(name)
    if spec is None:
        available = ", ".join(n for n in SPEC_BY_NAME if "builtin" in SPEC_BY_NAME[n].expose)
        return {"error": f"未知工具：{name}。内置可用工具只有：{available}。"}
    if not ({"builtin", "arena"} & set(spec.expose)):
        return {"error": f"工具 {name} 未对内置对话/竞技场开放（仅 MCP 可用）。"}
    # 必填参数校验：缺参时给出明确报错，避免把丑陋的 TypeError
    # （如「record_arena_result() missing 8 required positional arguments」）抛给调用方。
    missing = [p.name for p in spec.params if p.required and p.name not in (args or {})]
    if missing:
        return {"error": f"工具 {name} 缺少必填参数：{', '.join(missing)}"}
    svc = getattr(rt, spec.service, None)
    if svc is None:
        return {"error": f"后端服务不可用：{spec.service}"}
    method = getattr(svc, spec.method, None)
    if method is None:
        return {"error": f"后端未实现工具：{name}（{spec.service}.{spec.method}）"}
    allowed = {p.name for p in spec.params}
    kwargs = {k: v for k, v in (args or {}).items() if k in allowed}
    # 补齐 schema 声明的可选默认值：内置 LLM 路径常省略可选参，而部分后端方法
    # （如竞技场工具）对可选参没有 Python 侧默认值，缺失会直接 TypeError。
    for p in spec.params:
        if not p.required and p.name not in kwargs and p.default is not None:
            kwargs[p.name] = p.default
    kwargs.update(spec.force or {})
    try:
        # 后端方法可能是协程（如竞技场三个工具），协程必须直接 await；
        # 同步方法才丢线程池。否则 to_thread 只会返回一个未执行的协程对象。
        if inspect.iscoroutinefunction(method):
            return await method(**kwargs)
        return await asyncio.to_thread(method, **kwargs)
    except Exception as exc:
        return {"error": f"工具执行失败：{exc}"}


def register_simple_tools(mcp, runtime_getter, names=None) -> int:
    """为 generated 类工具动态注册 @mcp.tool()（签名与文档均来自本 schema）。

    返回成功注册的工具数。任何单个工具注册失败仅记录并跳过，不影响其余工具，
    以保证 MCP 端点始终可用（零风险保护 opencode 等外部 Agent）。
    names: 仅注册指定工具（缺省注册全部 generated 且 expose 含 mcp 的工具）。
    """
    logger = logging.getLogger(__name__)
    registered = 0
    for spec in TOOL_SPECS:
        if not spec.generated or "mcp" not in spec.expose:
            continue
        if names and spec.name not in names:
            continue
        try:
            params = []
            for p in spec.params:
                default = inspect.Parameter.empty if p.required else p.default
                params.append(inspect.Parameter(
                    p.name, inspect.Parameter.KEYWORD_ONLY,
                    default=default, annotation=_PY_TYPE.get(p.type, str),
                ))
            sig = inspect.Signature(params)

            doc = spec.summary + "\n\n" + spec.description + "\n\nArgs:\n"
            for p in spec.params:
                req = "（必填）" if p.required else ""
                doc += f"    {p.name}: {p.desc}{req}\n"
            if spec.example:
                doc += f"\nExample: {spec.example}"
            if spec.pitfall:
                doc += f"\nPitfall: {spec.pitfall}"

            async def _impl(**kwargs):
                rt = runtime_getter()
                return await dispatch(rt, spec.name, kwargs)

            _impl.__signature__ = sig
            _impl.__doc__ = doc
            _impl.__name__ = spec.name
            mcp.tool()(_impl)
            registered += 1
        except Exception as exc:  # pragma: no cover - 防御性兜底
            logger.warning("注册工具 %s 失败：%s", spec.name, exc)
    return registered
