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
    generated: bool = False   # True=可由 register_simple_tools 动态注册；False=MCP 端手写函数体（自定义逻辑）
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
                "返回执行计划——route/intent（该走哪条路、判定为何）、entity_ids/time_range（已解析出的设备与窗口）、"
                "hints（候选追问）。模型只需照 route 去调对应工具，避免从零自选。",
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
        pitfall="这是规划工具不是数据工具：返回体里没有'下一步工具名'那样的键。要看的键是 route/intent——"
                "取值是意图名（device_usage/behavior/anomaly/rhythm/activity/persona，认不出来时 auto），"
                "再按那一类去调对应数据工具（如 device_usage → get_device_usage）才能真正取数。",
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
    ToolSpec(
        name="get_device_usage_summary",
        summary="设备用量精简汇总：总开启时长/开关次数/平均单次时长（分钟），无时间线。",
        description=(
            "设备用量精简汇总（路线图 3.5 只读工具）：只要三个汇总数、不要逐次会话时间线与每日分布时用它，"
            "比 get_device_usage 省 token。时长口径：events 状态变化序列积分（开→关为一个片段；"
            "窗口前已开启从窗口左沿起算，窗口末未闭合截断到右沿并标记 window_open_session）；"
            "短于 debounce_seconds 的抖动不计。窗口内无任何事件时三个指标为 null（no_data=true）。"
        ),
        group="定量",
        # service/method 描述的是 dispatch 派发目标；这三条（3.5 只读汇总工具）
        # 只有 MCP 手写函数体、没有 rt.store.<method>，因此按既有约定标 static。
        # validate_specs() 会在导入期拦住「service 有值但 method 为空」这类自相矛盾的登记。
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("entity_id", "string", "精确 entity_id，逗号分隔可传多个（汇总为并集）", required=True),
            _p("start", "string", "本地 ISO 开始时间或日期 YYYY-MM-DD（可选）"),
            _p("end", "string", "本地 ISO 结束时间或日期 YYYY-MM-DD（可选）"),
            _p("days", "integer", "start/end 均为空时的兜底天数，默认 7", default=7),
            _p("debounce_seconds", "integer", "去抖秒数，短于该时长的片段丢弃，默认 5", default=5),
        ],
        example="get_device_usage_summary(entity_id='light.desk_lamp', start='2026-09-01', end='2026-09-07')",
        pitfall="要时间线/每日分布用 get_device_usage；不确定 entity_id 先用 get_entity_catalog。",
    ),
    ToolSpec(
        name="get_room_behavior_summary",
        summary="房间行为汇总：活动标签分布（推断活动+视觉动作计数）+ 24 小时活跃度直方图。",
        description=(
            "房间行为汇总（路线图 3.5 只读工具）：回答「某房间这段时间都在干嘛」。"
            "活动分布来自 behavior_states（canonical 推断活动）与 behavior_events（视觉动作，仅 status=ok）"
            "按标签计数；小时直方图取设备事件并默认剔除遥测域（功率/温湿度），与其余行为工具 behavior_only 口径一致。"
        ),
        group="定量",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("room", "string", "区域(area)名，原样使用真实区域名", required=True),
            _p("start", "string", "本地 ISO 开始时间或日期 YYYY-MM-DD（可选）"),
            _p("end", "string", "本地 ISO 结束时间或日期 YYYY-MM-DD（可选）"),
            _p("days", "integer", "start/end 均为空时的兜底天数，默认 7", default=7),
        ],
        example="get_room_behavior_summary(room='客厅', start='2026-09-20', end='2026-09-20')",
        pitfall="活动标签口径=推断活动+视觉动作，不是设备事件本身；房间名要用配置里的真实区域名。",
    ),
    ToolSpec(
        name="get_member_daily_pattern",
        summary="成员当日行为序列：推断活动状态+视觉动作+具名设备事件按时间升序合并。",
        description=(
            "成员日行为序列（路线图 3.5 只读工具）：回答「TA 今天做了什么、几点在哪」。"
            "合并 behavior_states（member=成员姓名口径）、behavior_events（persons 含姓名）"
            "与 events（person 字段，通常为空）。成员隔离 fail-closed：member_id 必填，"
            "不存在全成员视图。"
        ),
        group="定量",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("member_id", "string", "成员 UUID（list_members 可查）", required=True),
            _p("date", "string", "日期 YYYY-MM-DD，留空取今天"),
        ],
        example="get_member_daily_pattern(member_id='abc123', date='2026-09-20')",
        pitfall="人员归属按成员姓名匹配，姓名改动会断链；events.person 通常为空，别指望设备事件人人归位。",
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
        name="get_member_persona",
        summary="成员生活习惯画像：档案 + 全屋画像 +（已绑定房间时）房间定向洞察 + 已存档标签。",
        description=(
            "拉取某成员的生活习惯画像：成员档案（name/rooms/avatar）+ 全屋行为画像 + "
            "若已绑定房间则附该房间定向洞察 + 已确认的存档标签。"
            "ADM 联动计划第 4 步点名给 DB 消费的工具之一，签名以本条为唯一事实源。"
        ),
        group="洞察",
        # 与 3.5 只读汇总工具同口径：只有 MCP 手写函数体，没有 rt.<service>.<method>，
        # 所以标 static（validate_specs 会拦「service 有值但 method 为空」的自相矛盾登记）。
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("member_id", "string", "成员 ID（不是姓名），如 member:xxxx", required=True),
            _p("days", "integer", "画像统计窗口，默认 14", default=14),
        ],
        example="get_member_persona(member_id='member:abc', days=30)",
        pitfall="member_id 必填且必须存在，不存在返回 {ok:false, error:'成员不存在'}——"
                "成员隔离是 fail-closed，没有「全部成员」视图。姓名版指标请用 get_user_persona。",
    ),
    ToolSpec(
        name="analyze_behavior_change",
        summary="行为变化因果归因：检测某人某指标是否发生拐点，并搜索验证可能原因（P5a+P5b）。",
        description=(
            "P5a 检测变化点 → P5b 分组比较法验证每个候选原因的因果性（有事件天 vs 无事件天）。"
            "支持 metric：arrival_time（到家时间）、activity_count（日活动量）、"
            "active_duration（日活跃时长）、room_distribution（房间分布，需指定 room）。"
            "ADM 联动计划第 4 步点名给 DB 消费的工具之一，签名以本条为唯一事实源。"
        ),
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("person", "string", "成员名称，如 lidicn/Kevin/Emily", required=True),
            _p("metric", "string", "行为指标", default="arrival_time",
               enum=["arrival_time", "activity_count", "active_duration", "room_distribution"]),
            _p("days", "integer", "回溯总天数，默认 30", default=30),
            _p("lookback_days", "integer", "变化点前搜索候选原因的天数，默认 7", default=7),
            _p("room", "string", "room_distribution 指标时指定房间"),
        ],
        example="analyze_behavior_change(person='Kevin', metric='arrival_time', days=30)",
        pitfall="metric=room_distribution 必须带 room，否则结果无指向；归因给的是**相关性验证过的候选原因**，"
                "不是因果定论。person 用姓名口径（与 get_member_persona 的 member_id 不同）。",
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
        pitfall="insight_id 只有两类来源：infer_activities 每个活动的 id（detected_activity 行），"
                "以及 add_semantic_memory 返回的记忆 id；其余 id 查不到，返回 ok=false。"
                "不存在会返回错误。",
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
            _p("return_hints", "boolean", "True 时保留顶层 hints（规划阶段生成的候选追问，调试用）", default=False),
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
            "规则落库 activity_rules；infer_activities 每次推断都读取启用中的规则并套用，"
            "读数见返回体 rule_sources，无标签设备仍由时段启发式兜底。"
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
        pitfall="这是'执行'模板，不是导出配置；拿到 summary_text/entities 直接作答，不要重新统计。"
                "内置模板 id（如 ac_runtime_daily 空调运行时长、water_purifier_daily 净水器出水量）"
                "以 list_analysis_templates 返回为准，不要凭记忆猜。",
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
            _p("member_id", "string", "成员归属（WO-MA-005）；写入时记录该记忆属于哪个成员"),
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
        name="submit_recipe",
        summary="三路径·路径3回填：将探索出的工具序列提交为查询剧本（recipe），恒为 staging。",
        description=(
            "路径3 Agent 全自主探索成功后，将工具调用序列沉淀为 recipe 供路径2召回。"
            "同构查询（同 intent+object_type+metric+time_window+person+tool_sequence）"
            "收敛到同一 recipe_id，重复提交自动累加 sample_count。写入恒为 staging，需晋升后才被 match_recipe 召回。"
        ),
        group="三路径",
        service="agent_memory", method="submit_recipe",
        expose=("mcp",),
        generated=False,
        params=[
            _p("intent", "string", "意图类型：device_usage/compare/anomaly/presence/routine/arrival", required=True),
            _p("tool_sequence", "array", "工具调用序列，如 [{\"tool_name\":\"get_device_usage\",\"params\":{\"query\":\"{entity_id}\",\"days\":7}}]", required=True),
            _p("object_type", "string", "对象类型：device/room/activity/person/whole_house，默认 device", default="device"),
            _p("metric", "string", "指标：duration/count/numeric_sum/state_share/none，默认 duration", default="duration"),
            _p("time_window", "string", "时间窗口：today/yesterday/last_7_days/last_30_days/week_over_week/custom，默认 last_7_days", default="last_7_days"),
            _p("person", "string", "关联成员（可选）"),
            _p("confidence", "number", "置信度 0-1，默认 0.5", default=0.5),
            _p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp"),
        ],
        example="submit_recipe(intent='device_usage', tool_sequence=[{'tool_name':'get_device_usage','params':{'query':'{entity_id}','days':7}}], confidence=0.7)",
        pitfall="写入恒为 staging；需 promote / sweep 晋升为 live 后才能被 match_recipe 召回。",
    ),
    ToolSpec(
        name="match_recipe",
        summary="三路径·路径2召回：根据问题或槽位匹配已晋升的查询剧本（recipe），返回 tool_sequence。",
        description=(
            "路径2入口：命中 recipe 后返回 tool_sequence，Agent 只需按序列调用对应工具，无需从全量工具面自选。"
            "未命中返回空列表，Agent 应降级到路径3（全自主探索），探索成功后用 submit_recipe 回填。"
        ),
        group="三路径",
        service="agent_memory", method="match_recipe",
        expose=("mcp", "builtin"),
        generated=False,
        params=[
            _p("question", "string", "自然语言问题（优先，做语义召回）"),
            _p("intent", "string", "意图类型（可选，精确过滤）"),
            _p("object_type", "string", "对象类型（可选，精确过滤）"),
            _p("top_k", "integer", "返回条数，默认 3", default=3),
        ],
        example="match_recipe(question='书房空调开了多久', intent='device_usage') → 返回 recipe + tool_sequence",
        pitfall="只召回已晋升 live 的 recipe；staging 的不会返回。未命中时降级路径3并回填。",
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
            _p("question", "string", "当初用户问的原话（👎 时必填才能进 vMA-2.0 badcase 取料）",
               default=""),
            _p("comment", "string", "补充说明", default=""),
        ],
        example="feedback_memory(memory_id='mem_xxx', useful=False, question='昨晚谁在客厅开会')",
        pitfall="useful=False 会逐渐降低该记忆信任。",
    ),
    ToolSpec(
        name="list_agent_memories",
        summary="列出 agent 记忆（按状态 + 成员归属过滤）。",
        description="列出 agent 记忆（state=staging|live|revoked|pending_review|all；"
                    "member_id 过滤成员）。revoked 永不经本工具返回。",
        group="记忆",
        service="agent_memory", method="list_agent_memories",
        expose=("mcp",),
        generated=False,
        params=[
            _p("state", "string", "状态过滤，默认 'live'", default="live"),
            _p("member_id", "string", "成员归属过滤；普通令牌必填，admin 缺省也只返回公共记忆（无全量视图）"),
        ],
        example="list_agent_memories(state='live', member_id='member:abc')",
        pitfall="member_id 是隐私面硬门：普通令牌不传 member_id 直接 403（不会退回全量）；"
                "state 默认 live 而非 all，取历史需显式传。",
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
        summary="语义检索 agent 记忆（带信任阈值、成员归属与 top_k/limit）。",
        description="语义检索 agent 记忆（带信任阈值与 top_k），用于为回答提供证据。"
                    "返回 {ok, schema:'ma-recall/1', count, memories:[...]}。",
        group="记忆",
        service="agent_memory", method="retrieve",
        expose=("mcp",),
        generated=False,
        params=[
            _p("question", "string", "检索问题", required=True),
            _p("trust_min", "number", "最低信任分过滤，默认 -1（不限制）", default=-1.0),
            _p("top_k", "integer", "返回条数，默认 5", default=5),
            _p("member_id", "string", "成员归属过滤；普通令牌必填，缺省仅 admin scope 可跨成员"),
            _p("query", "string", "question 的兼容别名（butler 旧参数名）"),
            _p("limit", "integer", "top_k 的兼容别名（butler 侧命名）；非 0 时覆盖 top_k", default=0),
        ],
        example="retrieve_agent_memories(question='昨晚空调开了几次', top_k=5, member_id='member:abc')",
        pitfall="trust_min 低于 0 表示不限制；过高会召回过少。member_id 缺失时普通令牌直接 ok:false。",
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
            _p("dry_run", "boolean", "true=只校验参数不写入，默认 False", default=False),
            _p("session_id", "string", "会话 ID，默认 'mcp'", default="mcp"),
        ],
        example="teach_signal(entity_id='light.xiaomi_speaker', scope='wake_anchor', kind='hard', reason='定时播报是自动化信号，不是起床')",
        pitfall="kind='hard' 幂等（同 entity_id+scope 复用一条）；kind='soft' 必须提供 text，且 source_refs 不能是假 id。"
                "先探边界用 dry_run=true：它只回「参数校验通过，未写入」，不落任何一行。",
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
        pitfall="时间默认按天切片；精确到小时需传 start/end。结果行里的时间键是 time（服务端接收时刻，"
                "落库列名 server_ts），按它倒序。",
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
    ToolSpec(
        name="query_unified_events",
        summary="vMA-1.3 统一事件查询：跨设备/视觉/感知三源的统一时间线（只读 VIEW）。",
        description=(
            "查询 unified_events 视图，聚合 events（设备事件）+ behavior_events（视觉行为）"
            "+ perception_events（感知总线）三源数据。支持按 person/room/start/end/source 过滤。"
            "用于跨模态时间线查询、跨源统计等场景。只读，不写任何数据。"
        ),
        group="统一查询",
        service="store", method="query_unified_events",
        expose=("mcp",),
        params=[
            _p("person", "string", "成员名过滤，如 lidicn/Emily/Kevin"),
            _p("room", "string", "房间名过滤，如 客厅/书房/卧室"),
            _p("start", "string", "起始时间 ISO8601，如 2026-09-28T00:00:00"),
            _p("end", "string", "结束时间 ISO8601"),
            _p("days", "integer", "start/end 都留空时的回溯天数，默认 7", default=7),
            _p("source", "string", "来源过滤：device/vision/perception"),
            _p("limit", "integer", "返回条数上限，默认 200", default=200),
            _p("offset", "integer", "分页偏移，默认 0", default=0),
        ],
        example="query_unified_events(room='客厅', days=1) → 客厅最近一天的所有设备+视觉+感知事件",
        pitfall="这是只读工具，走 read scope。source=vision 只返回 status=ok 的视觉事件，失败行已过滤。"
                "days 只在 start/end 都留空时生效，且默认窗口按**家庭墙钟**起算（源表的 ts/server_ts 是 +8 墙钟，"
                "按 UTC 取窗会把最近 tz_offset 小时整段切在窗口外）。排序不可指定：底层 Store 方法有 order 参数，"
                "MCP 面没有暴露——目录里曾经写着 order，是照着 Store 抄出来的假参数。",
    ),

    # ── 以下 35 条为 MCP 手写工具的补登记 ────────────────────────────────────
    # 判据来自实测：容器内 `list_tools()` 返回 90 个工具，`SPEC_BY_NAME` 只覆盖 55 个，
    # 差的 35 个全是 `@mcp.tool()` 手写函数。它们在 MCP 面上可调、可被 DB/AF 集成，
    # 但 `help`/`describe` 查不到、presence 的 `caps.tools`（取 TOOL_NAMES）系统性少报
    # ——探测方照 caps 建集成会打 404。登记口径：`generated=False`（保留手写函数体，
    # 只有目录元信息同源），`service="static"`（不走内置派发，expose 只有 mcp）。
    # 参数名与默认值逐条对齐 `list_tools()` 实测的 inputSchema，不做美化。

    # ── 成员与档案 ────────────────────────────────────────────────────────────
    ToolSpec(
        name="list_members",
        summary="列出全部家庭成员（轻量字段：id/name/rooms/devices/tags）。",
        description="成员名册。返回每个成员的 id、显示名、关联房间、专属设备与习惯标签，"
                    "头像/人脸/嵌入向量等大字段已剔除；要完整档案用 get_member_persona(member_id)。",
        group="成员",
        service="static", method="",
        expose=("mcp",),
        params=[],
        example="list_members() → {ok: true, members: [...], total: N}",
        pitfall="tags 只保留 label/category/confidence；profile_json、appearance_json、embedding 都不在这里，"
                "别把本工具当完整档案用。",
    ),
    ToolSpec(
        name="create_member",
        summary="创建家庭成员（name 必填）。返回新成员行。",
        description="新建成员档案。id 由服务端生成 UUID，返回行里 rooms/devices/tags 均为空集合，"
                    "建好后用 assign_member_room / assign_member_device 补关联。",
        group="成员",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("name", "string", "显示名，必填且不可为空白", required=True),
            _p("avatar_emoji", "string", "头像 emoji，如 🦉"),
            _p("avatar_bg", "string", "头像底色，默认 #0EA5E9", default="#0EA5E9"),
            _p("note", "string", "备注"),
        ],
        example="create_member(name='Emily', avatar_emoji='🐰')",
        pitfall="同名不查重——连调两次会建出两个同名成员，人员归属又确实按姓名匹配 behavior 表，之后很难分辨。"
                "name 去掉空白后为空返回 INVALID_PARAM。",
    ),
    ToolSpec(
        name="delete_member",
        summary="删除家庭成员（不可恢复）。member_id 为成员 UUID。",
        description="按 UUID 删除成员，并级联清掉其关联房间、专属设备与习惯标签行。",
        group="成员",
        service="static", method="",
        expose=("mcp",),
        params=[_p("member_id", "string", "成员 UUID", required=True)],
        example="delete_member(member_id='3f2c…')",
        pitfall="不可恢复，且不会回滚该成员已有的历史行为数据；成员不存在返回 NOT_FOUND（先查后删，不会写空）。"
                "要并人不要用它。",
    ),
    ToolSpec(
        name="assign_member_room",
        summary="设置成员关联房间（全量覆盖）。rooms 为房间名数组。",
        description="覆盖式写入 member_rooms。传 ['主卧','书房'] 就是把该成员的房间集合换成这两间。",
        group="成员",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("member_id", "string", "成员 UUID", required=True),
            _p("rooms", "array", "房间名数组，如 ['主卧','书房']；留空 [] 清空"),
        ],
        example="assign_member_room(member_id='3f2c…', rooms=['主卧'])",
        pitfall="全量覆盖不是追加——想保留原房间必须一起传。房间名不与 HA 校验，写错的名字会安静地存着并匹配不到数据。"
                "成员不存在返回 NOT_FOUND。",
    ),
    ToolSpec(
        name="assign_member_device",
        summary="设置成员专属设备（全量覆盖）。entity_ids 为 entity_id 数组。",
        description="覆盖式写入 member_devices，保存的是 HA 的 entity_id 原值。",
        group="成员",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("member_id", "string", "成员 UUID", required=True),
            _p("entity_ids", "array", "entity_id 数组，如 ['light.study_desk']；留空 [] 清空"),
        ],
        example="assign_member_device(member_id='3f2c…', entity_ids=['media_player.tv_livingroom'])",
        pitfall="全量覆盖不是追加。存的是 entity_id 字面值，HA 集成重登导致 entity_id 漂移后这里不会自动跟——"
                "按逻辑设备名取数请用 query_device_usage。成员不存在返回 NOT_FOUND。",
    ),
    ToolSpec(
        name="confirm_member_tag",
        summary="把推断出的生活习惯标签写回成员档案（仅在用户明确确认后调用）。",
        description="写入 member_tags：标签名 + 类别 + emoji + 置信度 + 证据列表，来源标记为 agent。"
                    "属于「人已确认」的落库动作，不是机器建议。",
        group="成员",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("member_id", "string", "成员 UUID", required=True),
            _p("tag", "string", "标签名，如 夜猫子", required=True),
            _p("category", "string", "sleep/diet/activity/media/hygiene/other", default="other"),
            _p("emoji", "string", "标签 emoji，如 🦉"),
            _p("confidence", "number", "置信度 0~1", default=0.0),
            _p("evidence", "array", "证据字符串列表"),
        ],
        example="confirm_member_tag(member_id='3f2c…', tag='夜猫子', category='sleep', emoji='🦉', confidence=0.8)",
        pitfall="受 config.member_tag_agent_writeback 管制：该开关为 false 时直接返回 DENIED 并提示去 WebUI 开启或"
                "手动加标签，不会静默丢弃。",
    ),

    # ── 行为分析与异常/漂移/召回 ─────────────────────────────────────────────
    ToolSpec(
        name="mine_behavior_process",
        summary="过程挖掘（只读）：把「房间·天」当轨迹做一致性检验，找异常的一天。",
        description="以设备标签为步骤挖行为过程模型，返回变体统计、DFG 规模、一致性率与异常清单（含稀有边证据）。"
                    "某天出现平时几乎不走的转移（如平时只 门→灯→电脑，某天多插了空调）即判为异常。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("days", "integer", "回溯天数，默认 7", default=7),
            _p("rooms", "string", "逗号分隔的房间白名单，留空=全部"),
        ],
        example="mine_behavior_process(days=14, rooms='客厅,书房')",
        pitfall="只读、不写库；要落库供 WebUI 复核并产出候选规则，用 refresh_behavior_anomalies。"
                "与序列规则（n-gram 频次）互补：这里做的是一致性检验。",
    ),
    ToolSpec(
        name="refresh_behavior_anomalies",
        summary="重算并落库行为异常，同时把高频过程变体写成候选规则（写工具）。",
        description="同 mine_behavior_process 的算法，但把异常写入 behavior_anomalies 供 WebUI 复核，"
                    "并把「房间高频过程变体」写 candidate_rules（source=process，staging 待人工审核）。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("days", "integer", "回溯天数，默认 7", default=7),
            _p("rooms", "string", "逗号分隔的房间白名单，留空=全部"),
        ],
        example="refresh_behavior_anomalies(days=7) → 落库异常清单 + 候选规则提示",
        pitfall="需要 read+write 令牌。产出的是 staging 候选，不会自动改动线上规则；"
                "已人工复核过的异常状态不会被重跑洗掉。",
    ),
    ToolSpec(
        name="list_behavior_anomalies",
        summary="列出已落库的行为异常（按严重度降序）。",
        description="读 behavior_anomalies 表，可按复核状态与所属日期过滤。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("status", "string", "new（未复核）| confirmed | ignored；留空=全部"),
            _p("days", "integer", "只取最近 N 天（按异常所属日期），默认 14", default=14),
            _p("limit", "integer", "返回上限，默认 50，最大 500", default=50),
        ],
        example="list_behavior_anomalies(status='new', days=14)",
        pitfall="只列已落库的——没跑过 refresh_behavior_anomalies 时这里是空的，空不等于「没有异常」。"
                "days 按家庭墙钟日期裁窗。",
    ),
    ToolSpec(
        name="review_behavior_anomaly",
        summary="复核行为异常：confirmed（确属异常）/ ignored（误报）/ new（复位）。",
        description="把人工结论写回 behavior_anomalies 的 status 字段，供后续重跑与出证使用。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("anomaly_id", "string", "异常 ID", required=True),
            _p("status", "string", "new / confirmed / ignored", required=True,
               enum=["new", "confirmed", "ignored"]),
        ],
        example="review_behavior_anomaly(anomaly_id='anom_2026_09_30_01', status='ignored')",
        pitfall="status 只接受这三个值，其他取值直接返回错误（不落库）。异常不存在返回 ok=false 并带上 ID。"
                "复核结果在重跑挖掘时会被保留。",
    ),
    ToolSpec(
        name="get_behavior_drift",
        summary="在线异常 + 概念漂移检测（只读）：把每小时行为活跃度当时间序列评估。",
        description="Half-Space Trees 给无监督异常分（哪些时段不像平时），ADWIN 检测活跃度分布的突变"
                    "（最近作息/活跃度变了）。适用『最近作息是不是变了』『有没有异常时段』。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[_p("days", "integer", "回溯天数，默认 14（1 小时分桶，14 天≈336 点）", default=14)],
        example="get_behavior_drift(days=21) → 异常时段 + 突变点",
        pitfall="只算不落库；要沉淀成记录用 refresh_behavior_drift。与 mine_behavior_process 互补："
                "后者按天做事后一致性检验，本工具看时序突变。",
    ),
    ToolSpec(
        name="refresh_behavior_drift",
        summary="重算并落库漂移点/异常时段（写工具）→ behavior_drifts。",
        description="同 get_behavior_drift 的算法，但把结果写入 behavior_drifts 表供 WebUI 与后续审计读取。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[_p("days", "integer", "回溯天数，默认 14", default=14)],
        example="refresh_behavior_drift(days=30)",
        pitfall="需要 read+write 令牌。落库后用 list_behavior_drifts 读，别把两个工具当成同一件事。",
    ),
    ToolSpec(
        name="list_behavior_drifts",
        summary="列出已落库的漂移/异常时段。",
        description="读 behavior_drifts，按 kind（drift=活跃度分布突变 / anomaly=异常时段）与最近 N 天过滤。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("days", "integer", "只取最近 N 天，默认 14", default=14),
            _p("kind", "string", "drift | anomaly；留空=全部"),
            _p("limit", "integer", "返回上限，默认 50，最大 500", default=50),
        ],
        example="list_behavior_drifts(kind='drift', days=30)",
        pitfall="只读已落库的；refresh_behavior_drift 没跑过时为空。",
    ),
    ToolSpec(
        name="audit_rule_recall",
        summary="序列规则召回审计（只读）：找出『该判没判』的场景并诊断卡在哪一步。",
        description="以「房间·天」为单位：当天出现了规则所有步骤所需的标签（eligible）却没命中，记为召回缺口"
                    "（near_miss），给出第一个匹配不上的步骤（blocker）与估计召回率。"
                    "适用『就寝识别是不是漏了很多』『房间移动规则为什么很少触发』。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("days", "integer", "回溯天数，默认 14", default=14),
            _p("rooms", "string", "逗号分隔房间白名单，留空=全部"),
        ],
        example="audit_rule_recall(days=30, rooms='卧室') → 就寝规则 near_miss 清单",
        pitfall="只审计内置序列规则（书房工作/就寝/房间移动），persist=False 不落库；"
                "要产出放宽建议并落库用 refresh_rule_recall_gaps。",
    ),
    ToolSpec(
        name="refresh_rule_recall_gaps",
        summary="重算并落库召回放宽建议（写工具），产出 candidate_rules 待人工审核。",
        description="对反复卡在同一步的缺口，产出「去掉该步骤」的宽松变体，写 candidate_rules"
                    "（source=recall_gap，staging）。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[_p("days", "integer", "回溯天数，默认 14", default=14)],
        example="refresh_rule_recall_gaps(days=21)",
        pitfall="需要 read+write 令牌。不会自动改动线上规则——产出的是 staging 候选，"
                "确认与晋升另走 confirm_candidate_rule / promote_candidate_rule。",
    ),
    ToolSpec(
        name="counterfactual_query",
        summary="反事实查询：如果没有这个事件，行为指标会怎样？",
        description="基于分组比较法，用「无该事件的天」的分布作为反事实估计，返回实际值、反事实预测值、差异、"
                    "95% 置信区间、因果效应量与显著性。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("person", "string", "成员名称", required=True),
            _p("event_type", "string", "事件类型，如 tv_on/light_on/aircon_on/door_open/face_known", required=True),
            _p("metric", "string", "行为指标，默认 arrival_time", default="arrival_time"),
            _p("days", "integer", "回溯天数，默认 30", default=30),
            _p("room", "string", "metric=room_distribution 时指定房间"),
        ],
        example="counterfactual_query(person='Kevin', event_type='tv_on', metric='arrival_time', days=45)",
        pitfall="事件数据不足（<14 条）直接返回错误并给出 event_count，不要把它当成「没有影响」。"
                "这是分组比较的统计估计，不是真值实验。",
    ),
    ToolSpec(
        name="get_behavior_prediction",
        summary="行为预测：基于历史事件预测某人的到家时间与日常作息。",
        description="从 behavior_events 统计该人的到家时间分布与逐时段作息，可按星期几筛选。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("person", "string", "人名，如 Kevin/Emily/lidicn", required=True),
            _p("weekday", "integer", "0=周一…6=周日；-1=用所有日期统计（默认）", default=-1),
        ],
        example="get_behavior_prediction(person='Kevin', weekday=0) → 周一的到家时间预测",
        pitfall="读的是 behavior_events（最近 5000 条）；表里没数据时返回「无历史行为事件数据」。"
                "人名按事件里记录的人字段匹配，写错名字只会得到空统计而不是报错。",
    ),
    ToolSpec(
        name="infer_behavior_intent",
        summary="意图推断（无 LLM 快路径）：从最近的行为事件推断用户意图。",
        description="基于行为规则匹配（开灯+开电视=想看电视），毫秒级响应，返回按置信度排序的意图列表，"
                    "每个含 intent/label/confidence/evidence/suggestions。",
        group="洞察",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("person", "string", "限定某人，如 Kevin；留空=不限"),
            _p("window_min", "integer", "时间窗口（分钟）。给定则只用这一个窗口"),
            _p("limit", "integer", "返回意图数量，默认 3，最多 5", default=3),
        ],
        example="infer_behavior_intent(person='Emily', window_min=15)",
        pitfall="给了 window_min 就只用这一个窗口、最多返回 1 个意图；不给则按 5/15/30 三窗各试一遍再排序。"
                "limit 超过 5 会被截到 5。",
    ),
    ToolSpec(
        name="execute_intent_actions",
        summary="意图→动作执行：推断意图后执行（或预览）建议动作。",
        description="内置 7 个意图的常识动作映射（看电视/工作/睡觉/出门/回家/吃饭/运动）。"
                    "默认 dry_run=true 只预览；dry_run=false 时只执行 auto=true 的动作（TTS 播报/告警）。",
        group="运维",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("intent", "string", "意图名称，如 watch_tv/sleep/arrive_home", required=True),
            _p("dry_run", "boolean", "True=只预览（默认），False=执行自动动作", default=True),
            _p("person", "string", "触发意图的人（仅用于日志）"),
        ],
        example="execute_intent_actions(intent='watch_tv', dry_run=True)",
        pitfall="dry_run=false 也只动 auto=true 的动作——灯光/摄像头等待确认类不会被自动执行。"
                "预览结果和真实执行结果形状相近，别把预览当成已执行。",
    ),

    # ── 规则生命周期（DCD R3 生效通道）───────────────────────────────────────
    ToolSpec(
        name="list_candidate_rules",
        summary="列出候选规则建议（vMA-1.2.1）。",
        description="按状态取 candidate_rules：staging（待确认）/ confirmed（已确认）/ rejected（已拒绝）。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[_p("status", "string", "staging|confirmed|rejected", default="staging")],
        example="list_candidate_rules(status='staging')",
        pitfall="这里看到的是「机器建议」，不是生效规则；生效面看 list_rule_channel。"
                "晋升前提是 accepted（由 confirm_candidate_rule 落的状态）。",
    ),
    ToolSpec(
        name="confirm_candidate_rule",
        summary="确认或拒绝一条候选规则（vMA-1.2.1 DCD 红线）。",
        description="confirmed=true 落 status='accepted' 且 user_confirmed=1（与 WebUI 同一状态字）；"
                    "false 落 'rejected'，不再出现在建议列表。本步会留生命周期审计记录。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("rule_id", "string", "候选规则 ID", required=True),
            _p("confirmed", "boolean", "true=接受，false=拒绝", default=True),
        ],
        example="confirm_candidate_rule(rule_id='cand_2026_09_30_003', confirmed=True)",
        pitfall="确认只表示「人已同意」，**不等于已生效**：还需 promote_candidate_rule 过证据门槛才会进引擎，"
                "且先进试运行。候选不存在返回 ok=false。",
    ),
    ToolSpec(
        name="promote_candidate_rule",
        summary="把一条 accepted 候选规则晋升进引擎（DCD R3）。",
        description="过证据门槛后写入 active_rules（mode='dry_run'，只记录不触发），并留审计。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("rule_id", "string", "候选规则 ID", required=True),
            _p("reason", "string", "晋升理由（写入审计）"),
            _p("cooldown_seconds", "integer",
               "这条规则的吵人上限（每 N 秒最多产出一条告警）。这里没有默认值："
               "不传则取候选行已设定的值，两者都空则拒绝晋升"),
        ],
        example="promote_candidate_rule(rule_id='cand_2026_09_30_003', reason='证据 4 天且人已确认', "
                "cooldown_seconds=300)",
        pitfall="门槛：accepted + user_confirmed + ≥MA_RULE_MIN_EVIDENCE 个独立证据日 + 事件类型在引擎实时 feed "
                "词表内 + **带冷却上限**（DCD 20261004 MA-裁1 Q1 缺省即拒）；不满足会拒绝并返回 blockers。"
                "晋升后一律 dry_run，转正要等观察期满；试运行同样占用冷却窗口（同裁定 Q3）。",
    ),
    ToolSpec(
        name="advance_rule_to_live",
        summary="试运行规则转正为 live（红线『观察期』）。",
        description="观察期满且期间零误报才放行，否则拒绝并给出 blockers。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("rule_id", "string", "规则 ID", required=True),
            _p("reason", "string", "转正理由（写入审计）"),
        ],
        example="advance_rule_to_live(rule_id='ar_1', reason='观察 7 天零误报')",
        pitfall="判据是 MA_RULE_DRY_RUN_DAYS 天 + 误报清零，两者都不满足就会拒绝；"
                "操作人身份取自调用令牌，不是参数——伪造不了是谁转正的。",
    ),
    ToolSpec(
        name="revoke_active_rule",
        summary="撤销一条生效规则（红线『可回滚』）。",
        description="规则置为 mode='revoked'、enabled=0，并删除它经 infer_activity 产生的推断活动，"
                    "回滚条数写入审计。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("rule_id", "string", "规则 ID", required=True),
            _p("reason", "string", "撤销理由（写入审计）"),
            _p("rollback_inferences", "boolean", "是否回滚该规则推断出的活动，默认 True", default=True),
        ],
        example="revoke_active_rule(rule_id='ar_1', reason='误报 3 次')",
        pitfall="rollback_inferences=false 会把历史推断活动留在库里继续影响下游读数——只有在「规则判错但活动要留」时"
                "才这么传。",
    ),
    ToolSpec(
        name="flag_rule_false_positive",
        summary="把一条规则触发记录判为误报（观察期的红判据）。",
        description="对 rule_trigger_history 的单条记录打误报标记，误报未清零时该规则不能转正。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("rule_id", "string", "规则 ID", required=True),
            _p("trigger_id", "integer", "触发记录 ID（来自 list_rule_channel / list_rule_lifecycle_audit）",
               required=True),
            _p("reason", "string", "判误报的理由（写入审计）"),
        ],
        example="flag_rule_false_positive(rule_id='ar_1', trigger_id=17, reason='那晚是客人不是本人')",
        pitfall="trigger_id 必填且指向具体一条触发，不是整条规则；判误报会挡住转正，但不会自动撤销规则。",
    ),
    ToolSpec(
        name="list_rule_channel",
        summary="DCD R3 生效通道全景（只读）。",
        description="candidate_id 非空时只返回该候选的门槛判据（eligibility 预演）；否则返回 accepted 候选的晋升预演"
                    "清单 + 试运行/已转正/已撤销规则与观察读数。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[_p("candidate_id", "string", "只看这一条候选的门槛判据；留空返回全景")],
        example="list_rule_channel(candidate_id='cand_2026_09_30_003') → 还差哪一项",
        pitfall="纯预演、不改任何状态。判据为 accepted + user_confirmed + 独立证据日数 + 事件类型在实时 feed 词表内。",
    ),
    ToolSpec(
        name="list_rule_lifecycle_audit",
        summary="列出规则生命周期审计（机器建议→人工确认→生效→转正/撤销全链路留痕）。",
        description="读 rule_lifecycle 审计表，可按规则 ID 过滤，返回每条动作的时间、操作人与说明。",
        group="规则",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("rule_id", "string", "只看这条规则的审计轨迹；留空返回全部"),
            _p("limit", "integer", "返回上限，默认 50", default=50),
        ],
        example="list_rule_lifecycle_audit(rule_id='ar_1')",
        pitfall="ok=false 表示有审计行缺关键字段（rule_id/action/created_at），是留痕质量问题，不是「没有记录」。",
    ),
    ToolSpec(
        name="revoke_signal_rule",
        summary="撤销一条信号硬排除（标记为 revoked）。",
        description="把 teach_signal 建出的硬排除置为 revoked，使其不再参与排除判定。",
        group="学习",
        service="static", method="",
        expose=("mcp",),
        params=[_p("exclusion_id", "string", "排除规则 ID，来自 teach_signal 的返回值", required=True)],
        example="revoke_signal_rule(exclusion_id='sig_ex_12')",
        pitfall="是标记 revoked 不是删行（保留可追溯）；软记忆不在这里撤。ID 不存在返回 NOT_FOUND。",
    ),

    # ── 运维与设备健康 ───────────────────────────────────────────────────────
    ToolSpec(
        name="list_device_health",
        summary="实体健康 / 失效清单（A3）。",
        description="按身份层健康状态列实体：active（确认在线）/ unknown（短暂失联）/ stale（长期失效），"
                    "并给出逻辑设备总数。",
        group="运维",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("state", "string", "active|unknown|stale；留空返回全部"),
            _p("limit", "integer", "每页行数，默认 500、上限 2000", default=500),
            _p("offset", "integer", "分页偏移", default=0),
            _p("fields", "string", "lean（默认，精简投影）| full（全量字段值）",
               enum=["lean", "full"], default="lean"),
        ],
        example="list_device_health(state='stale') → 长期失效实体；"
                "list_device_health(fields='full', limit=2000) → 要整段中文说明时",
        pitfall="依赖身份层；未启用时返回 ok=false「身份层未启用」。"
                "referenced=1 表示该实体仍被某个模板引用，它一旦失效就会让洞察失真，应优先处理。"
                "DCD 20261004 MA-裁4：``total`` 是**分页前**的全量条数，取完一页要看 has_more/next_offset，"
                "别把 count 当 total 做统计；默认 lean 会截 stable_id 到 40 字并把非 referenced 的 note 置空"
                "（键仍在），要原值传 fields='full'。",
    ),
    ToolSpec(
        name="report_bug",
        summary="上报一条 bug（agent 使用 MA 时发现的功能问题）。",
        description="写入 bug_reports（status=open）：出问题的工具名、描述、期望行为、实际行为与严重度。",
        group="运维",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("tool_name", "string", "出问题的 MCP 工具名"),
            _p("description", "string", "问题描述"),
            _p("expected", "string", "期望行为"),
            _p("actual", "string", "实际行为"),
            _p("severity", "string", "minor|major|critical", default="minor"),
        ],
        example="report_bug(tool_name='get_device_usage', description='空 entity_id 时报 INTERNAL', severity='major')",
        pitfall="上报人（reporter）由调用令牌自动带上，不需要也不能自己填；"
                "落库默认 status=open，处理完由 WebUI 侧改 resolved。",
    ),
    ToolSpec(
        name="list_bug_reports",
        summary="列出已上报的 bug。",
        description="读 bug_reports，按状态（open|resolved|all）与条数上限返回，按创建时间倒序。",
        group="运维",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("status", "string", "open|resolved|all", default="open"),
            _p("limit", "integer", "返回上限，默认 50", default=50),
        ],
        example="list_bug_reports(status='all', limit=100)",
        pitfall="默认只看 open——统计总量或复盘时必须显式传 status='all'，否则看不到已解决的。",
    ),
    ToolSpec(
        name="query_device_usage",
        summary="按「逻辑设备名」查询用量 / 时长 / 计数（v0.3 语义工具）。",
        description="与 get_device_usage 的区别：本工具接受逻辑设备名（如「客厅电视」「游戏机」），"
                    "由身份层解析为当前 entity_id，因此 HA 集成重登、双集成并存导致 entity_id 漂移后依然稳定。",
        group="定量",
        service="static", method="",
        expose=("mcp",),
        params=[
            _p("logical_device", "string", "逻辑设备名，如 客厅电视（与 entity_id 二选一）"),
            _p("entity_id", "string", "直接指定 entity_id"),
            _p("attribute", "string", "属性名，默认 state", default="state"),
            _p("value", "string", "属性值，如 HDMI 3"),
            _p("pattern", "string", "匹配方式，默认 equals", default="equals"),
            _p("metric", "string", "duration（时长）| count（次数）| numeric_sum（数值累计）", default="duration"),
            _p("days", "integer", "回溯天数，默认 7", default=7),
            _p("start", "string", "起始时间 ISO8601（与 days 二选一）"),
            _p("end", "string", "结束时间 ISO8601"),
            _p("include_timeline", "boolean", "是否附带时间线，默认 False", default=False),
        ],
        example="query_device_usage(logical_device='客厅电视', attribute='source', value='HDMI 3', metric='duration', days=2)",
        pitfall="logical_device 与 entity_id 必须给一个，都不传直接报错。include_timeline 很吃 token，"
                "只要汇总就别开；只要三个汇总数时用 get_device_usage_summary。",
    ),

    # ── 自我日记（家庭人格化实验）────────────────────────────────────────────
    ToolSpec(
        name="write_self_diary",
        summary="写一段自我日记（第一人称视角），落入 staging。",
        description="把正文以 topic_key='self_diary' 写入 agent_memory，state=staging 且 "
                    "auto_promote_blocked=1（永不自动晋升），TTL 365 天。",
        group="记忆",
        service="static", method="",
        expose=("mcp",),
        params=[_p("text", "string", "日记正文（第一人称，如「今天晚上客厅很安静…」）", required=True)],
        example="write_self_diary(text='今天书房到深夜还亮着。')",
        pitfall="这是人格化实验文本，不是事实记忆：不参与自动晋升，也不该被当成行为证据引用。",
    ),
    ToolSpec(
        name="read_self_diary",
        summary="读取最近 N 天的自我日记。",
        description="筛 topic_key='self_diary' 的记忆，按家庭墙钟时间裁窗并升序返回（date + text）。",
        group="记忆",
        service="static", method="",
        expose=("mcp",),
        params=[_p("days", "integer", "回溯天数，默认 7", default=7)],
        example="read_self_diary(days=14) → 最近两周的日记",
        pitfall="底层先取 list_agent_memories(state='all', limit=500) 再筛主题：记忆总量超过 500 条时，"
                "最早的日记可能不在视野里。days 裁的是家庭墙钟 created_at，不是 UTC。",
    ),
    ToolSpec(
        name="generate_self_diary",
        summary="自动生成今天的自我日记（调 LLM，写 staging）。",
        description="读昨天日记作开头引用，从当天 events 提取脱敏摘要（只保留时间+房间+有意义的实体，"
                    "过滤功率/温湿度等传感器），调 LLM 生成 200-400 字第一人称日记后落 staging。",
        group="记忆",
        service="static", method="",
        expose=("mcp",),
        params=[],
        example="generate_self_diary() → {ok: true, memory_id, diary: 前 200 字}",
        pitfall="会调用付费 LLM。runtime 里已有每天 23:00（家庭墙钟）的常驻生成任务，"
                "手动再调会多写一条 staging 日记——同一天可能不止一篇。LLM 失败返回 ok=false 且不落库。",
    ),
]

SPEC_BY_NAME: dict = {s.name: s for s in TOOL_SPECS}

# MCP 暴露的工具名集合（供 describe / selftest / help 使用，与内置同源）
TOOL_NAMES: list = [s.name for s in TOOL_SPECS if "mcp" in s.expose]


def validate_specs(specs: list | None = None) -> None:
    """导入期自检：工具登记表内部不允许自相矛盾。

    审计报告 20261002 · 新发现 3 + 5 的共同根因是「目录说有、实现没有」，而这类
    割裂过去只在运行时以 NOT_FOUND / 空 method 报错的形式暴露。把判据前置到导入期：
    - 重名：`SPEC_BY_NAME` 会静默丢弃后来的那一条（P0-4 的重复注册形态）；
    - 非 static 却没有 method：`dispatch()` 只能 `getattr(svc, "")` → None；
    - `generated=True` 却没有 method：`register_simple_tools` 注册出一个必然失败的工具；
    - 对内置/竞技场开放却无派发目标：MCP 能用、内置答不出，正是割裂复现。
    """
    problems: list = []
    items = TOOL_SPECS if specs is None else specs
    seen: set = set()
    for spec in items:
        if spec.name in seen:
            problems.append(f"{spec.name}: 工具名重复登记")
        seen.add(spec.name)
        if spec.service != "static" and not spec.method:
            problems.append(f"{spec.name}: service={spec.service} 但 method 为空")
        if spec.generated and not spec.method:
            problems.append(f"{spec.name}: generated=True 但 method 为空")
        if ({"builtin", "arena"} & set(spec.expose)) and (
                spec.service == "static" or not spec.method):
            problems.append(f"{spec.name}: 对内置/竞技场开放，但没有派发目标")
    if problems:
        raise ValueError("工具 schema 登记不一致：" + "；".join(problems))


validate_specs()


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


def register_simple_tools(mcp, runtime_getter, names=None) -> dict:
    """为 generated 类工具动态注册 @mcp.tool()（签名与文档均来自本 schema）。

    返回 {name: func} 注册的函数字典。调用方应将其 update 到 module globals()，
    使 hasattr(module, name) 可检测（目录一致性测试依赖此约定）。

    names: 仅注册指定工具（缺省注册全部 generated 且 expose 含 mcp 的工具）。

    **注册失败一律上抛**（审计报告 20261002 · 新发现 5）：过去单工具失败只 warning
    并跳过，于是 TOOL_CATALOG 里挂着、客户端按目录调用却得到 NOT_FOUND——
    与 P0-2「登记了但没实现」是同一个形状。宁可启动即红，也不要把残缺工具面放出去。
    """
    logger = logging.getLogger(__name__)
    registered = {}
    failures: dict = {}
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

            async def _impl(_spec=spec, **kwargs):
                rt = runtime_getter()
                return await dispatch(rt, _spec.name, kwargs)

            _impl.__signature__ = sig
            _impl.__doc__ = doc
            _impl.__name__ = spec.name
            mcp.tool()(_impl)
            registered[spec.name] = _impl
        except Exception as exc:
            logger.exception("注册工具 %s 失败", spec.name)
            failures[spec.name] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise RuntimeError(
            "schema 工具注册失败：" + "；".join(f"{k}→{v}" for k, v in failures.items()))
    if names:
        # 请求过但一条都没匹配上 = 名字写错或该工具不是 generated，同样是目录与实现的割裂。
        unmatched = [n for n in names if n not in registered]
        if unmatched:
            raise RuntimeError(
                f"schema 工具未被注册：{', '.join(unmatched)}"
                "（请确认它们在 TOOL_SPECS 里登记且 generated=True）")
    return registered
