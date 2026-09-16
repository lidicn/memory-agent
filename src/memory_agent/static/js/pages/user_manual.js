/* 用户手册页：以问答（Q&A）形式指导用户与其 AI Agent 使用 MCP 全部工具。
 *
 * 每条 Q 都是「用户会对 AI 助手说的话」，A 里给出 Agent 应调用的 MCP 工具、
 * 参数示例与注意事项——与 20 提示词实测风格一致，即查即用。
 */

const CATEGORIES = [
  { id: 'start', label: '🚀 接入上手' },
  { id: 'devices', label: '🔌 设备与用量' },
  { id: 'insights', label: '📊 行为洞察' },
  { id: 'activities', label: '🏃 活动识别' },
  { id: 'events', label: '🔍 事件查询' },
  { id: 'members', label: '👨‍👩‍👧 家庭成员' },
  { id: 'memory', label: '🧠 记忆库' },
  { id: 'signals', label: '🎚️ 信号规则' },
  { id: 'templates', label: '📐 洞察模板' },
  { id: 'skills', label: '🎓 技能' },
  { id: 'collect', label: '⬇️ 数据采集' },
];

const QA = [
  // ── 接入上手 ──────────────────────────────────────────────
  {
    c: 'start',
    q: '什么是 MCP？怎么把我的 AI 助手（DeepSeek / Claude / CodeBuddy 等）接入 Memory Agent？',
    a: `Memory Agent 在 **/mcp** 端点提供 MCP（Model Context Protocol）服务，支持 Streamable HTTP 与 SSE 两种传输。接入步骤：

1. 在 WebUI「MCP 接入」页**签发一个 mcp_ 令牌**（点「新建令牌」）；
2. 在你的 AI 客户端里添加 MCP 服务器，地址填：

    http://192.168.2.200:8086/mcp

3. 请求头带鉴权：

    Authorization: Bearer mcp_xxxxxxxxxxxxxxxx

典型客户端配置（JSON）：

    {
      "mcpServers": {
        "memory-agent": {
          "url": "http://192.168.2.200:8086/mcp",
          "headers": { "Authorization": "Bearer mcp_xxxx" }
        }
      }
    }

注意：mcp_ 令牌只用于 /mcp，与 WebUI 的 JWT 登录体系完全隔离；令牌可在 MCP 页随时吊销。`,
  },
  {
    c: 'start',
    q: 'Agent 刚接入，应该先做什么？',
    a: `先调用 **help** 工具看全景（新会话第一个调用它，能省掉大量试错）：

    help()                    // 全部工具分组索引 + 推荐调用流程 + 常见坑
    help(tool_name="get_device_usage")   // 单个工具的参数、示例与坑

help 返回的 recommended_flow 就是官方推荐路径：

    get_entity_catalog(room="主卧")      # 用人话找设备
    get_behavior_insights(days=7)        # 直接拿作息与异常
    get_device_usage(query="书房空调")   # 具体设备用了多久
    search_events(room="客厅", summarize=True)
    save_analysis_template(...)          # 沉淀结论`,
  },
  {
    c: 'start',
    q: '怎么知道有哪些工具？每个工具的参数是什么？',
    a: `三种方式：

1. **help()** —— 全景索引，按分组返回工具名与一句话摘要；
2. **help(tool_name="xxx")** —— 单工具详情：参数、示例、注意事项；
3. WebUI「MCP 接入」页也能看到已注册工具列表；「/api/mcp/stats?sort_by=calls」可看每个工具的调用次数/耗时/错误率（效率可观测）。

经验：找设备类问题先 get_entity_catalog / search_events，作息类直接 get_behavior_insights，不要自己拉原始事件硬算。`,
  },

  // ── 设备与用量 ────────────────────────────────────────────
  {
    c: 'devices',
    q: '书房里有哪些设备？',
    a: `调用 **get_entity_catalog**，用房间名即可，不用背 entity_id：

    get_entity_catalog(room="书房")

返回每个实体的友好名 / entity_id / 类别 / 最后在线 / 近期活跃度。也可以按类别过滤：

    get_entity_catalog(category="climate")   # 所有空调地暖
    get_entity_catalog(domain="media_player")

category 可选：climate / lighting / media / presence / appliance / security / telemetry。
要房间清单（不要活跃度）可用轻量的 **list_rooms_entities**。`,
  },
  {
    c: 'devices',
    q: '帮我找"主卧空调"的 entity_id',
    a: `用 **get_entity_catalog** 的关键词搜索：

    get_entity_catalog(query="主卧空调")

返回结果里的 entity_id 可直接喂给其他工具（get_device_usage、query_events 等）。
如果实体曾因 HA 重登 / 换集成导致 entity_id 漂移，建议改用**逻辑设备名**查用量——见下一条 query_device_usage，身份层会自动解析当前 entity_id，不怕漂移。`,
  },
  {
    c: 'devices',
    q: '客厅电视这周开了多久？书房空调呢？',
    a: `设备用量统计用 **get_device_usage**（总开启时长 / 开关次数 / 平均单次 / 每日分布 / 时间线）：

    get_device_usage(query="书房空调", days=7)
    get_device_usage(entity_id="media_player.xxx", days=7)

它会自动处理三件容易算错的事：窗口开始前就已开启、窗口结束时仍未关闭、以及短于 5 秒的误触抖动。climate 的 heat/cool、media_player 的 playing 都被正确判为「开启」。

定位方式二选一：entity_id（可逗号分隔多个）或 query/room/category 语义定位。`,
  },
  {
    c: 'devices',
    q: '电视 HDMI3（Xbox）用了多长时间？',
    a: `按「属性 + 取值」算时长用 **query_device_usage**——它接受**逻辑设备名**，由身份层解析，entity_id 漂移也不失效：

    query_device_usage(
      logical_device="lidicn的电视电视",
      attribute="source", value="HDMI 3",
      metric="duration", days=7
    )

metric 可选：duration（时长）/ count（次数）/ numeric_sum（数值累计，如耗电量）。
与 get_device_usage 的区别：本工具吃逻辑名 + 属性条件，适合"HDMI3 信源时长"这类按属性拆分的场景。`,
  },
  {
    c: 'devices',
    q: '家里有没有失联 / 没电 / 长期没数据的设备？',
    a: `两个工具配合用：

    get_device_health(days=7)          # 健康探测：healthy / no_data / stale 三类
    get_device_health(room="卧室")     # 聚焦某房间

    list_device_health(state="stale")  # 身份层视角：active/unknown/stale

- no_data = 从未采到数据（可能 entity_id 错或未启用）；
- stale = 最近 N 天无数据（可能没电或离线，默认 stale_days=3）；
- list_device_health 里 referenced=1 的实体仍被洞察模板引用，**失效会让洞察失真，优先处理**。
另返回 data_quality_issues：电量倒灌 / 单位冲突 / 心跳计数器异常。`,
  },

  // ── 行为洞察 ──────────────────────────────────────────────
  {
    c: 'insights',
    q: '给我一份本周家庭行为报告（作息、房间活跃、异常）',
    a: `直接用 **get_behavior_insights**，服务端直出报告，Agent 不要自己拉原始事件算：

    get_behavior_insights(days=7)
    get_behavior_insights(days=7, rooms="主卧,书房")   # 聚焦房间

包含：作息节律、各房间活跃时段与 Top 设备、跨设备状态转移、每日事件量、异常检测（单实体占比过高的噪声源会被自动剔除出房间使用率）。

注意：首次调用可能较慢（冷启动全量扫描，实测可达几十秒），客户端请把超时调大，避免超时重试雪上加霜。`,
  },
  {
    c: 'insights',
    q: '我只要一眼概况：事件量、房间分布、24 小时分布、Top 实体',
    a: `用轻量总览 **get_behavior_summary**（比完整洞察便宜很多，适合快速摸底）：

    get_behavior_summary(days=7)
    get_behavior_summary(days=7, behavior_only=False)   # 含 sensor/number 遥测

返回：事件总量、房间分布、24 小时分布、Top 实体。
与 get_behavior_insights 的区别：本工具只给"量"的分布概况；要作息节律、状态转移、异常检测等结论性的报告用 get_behavior_insights。`,
  },
  {
    c: 'insights',
    q: '和上周相比，这个星期生活有什么变化？',
    a: `环比对比用 **get_behavior_insights_compare**（自然日对齐，各恰好 compare_days 天）：

    get_behavior_insights_compare(compare_days=7)

除各活动的次数/天数外，还带**累计时长 delta** 与**温控维度**（空调开启时长、平均设定/室温的对比），能回答「空调是不是开得更猛 / 设得更低」这类问题。弱信号（如"次数没变"）不要过度推断结论，优先看时长与温控。`,
  },
  {
    c: 'insights',
    q: '总结一下主人的生活习惯画像',
    a: `用 **get_user_persona** 拿滚动合成画像（做个性化推荐、健康提醒时直接调它）：

    get_user_persona(days=14)

基于活动识别聚合：每个活动的出现天数 / 频次 / 时段 / 房间 / 置信度 + 房间活跃度。
要**某个成员**的画像（档案 + 全屋行为 + 绑定房间的定向洞察 + 已存档标签）用：

    get_member_persona(member_id="xxx", days=14)

成员 id 先用 list_members 查。`,
  },
  {
    c: 'insights',
    q: '空调最近开得多吗？设定温度是多少？',
    a: `气候会话用 **get_climate_sessions**，把空调/地暖开启时段拼成「设定温度 + 室温 + 运行时长」：

    get_climate_sessions(query="空调", days=7)
    get_climate_sessions(room="主卧")

它直接解析事件里的 current_temperature（室温）与 temperature（设定温度）。
要按时段拆（如只看夜间 22:00-07:00）可改用洞察模板：save_analysis_template 时给实体加 time_range（支持跨午夜），再 run_analysis_template。`,
  },
  {
    c: 'insights',
    q: '数据从哪天开始有？前几天是不是空白？',
    a: `取数前先确认窗口完整性，用 **get_data_coverage**：

    get_data_coverage(days=7)

返回每天的事件量与 has_data 标记、first/last 有数据日期、missing_days 列表。避免把"没采集"误当"没行为"。
数据质量整体问题（含记忆库镜像缺口）用：

    get_data_quality(days=30)`,
  },
  {
    c: 'insights',
    q: 'AI 说的这个结论，证据是什么？',
    a: `证据溯源用 **explain_insight**：

    explain_insight(insight_id="cooking_20260912_...")

insight id 来自 infer_activities 返回的每个活动记录 id 字段；agent 记忆 id 来自 add_semantic_memory 的返回。它会解析底层触发事件与 source_refs 引用链，方便人工核验"为什么判定我在做饭"。`,
  },

  // ── 活动识别 ──────────────────────────────────────────────
  {
    c: 'activities',
    q: '这周我做过哪些活动（做饭 / 洗澡 / 睡觉 / 看电视 / 离家 / 工作）？',
    a: `活动识别用 **infer_activities**，服务端基于设备共现与时段直接给语义结论：

    infer_activities(days=7)
    infer_activities(days=7, activities=["cooking", "bathing"])   # 只看指定活动

返回每个活动在窗口内各天的发生记录，含置信度与证据链。洗澡由卫生间增压泵功率（≥50W）识别用水，占用推断只作回退；睡觉/离家基于强人类活动静默判定，加湿器报警等噪声不会污染。`,
  },
  {
    c: 'activities',
    q: '教 AI 识别新活动：「工作日下午 1-3 点在卧室安静 = 午休」',
    a: `注册自定义活动规则用 **define_activity**：

    define_activity(
      name="nap",
      room="卧室",
      tags=["presence"],
      start_hour=13, end_hour=15,
      min_events=2,
      note="工作日下午在卧室安静休息"
    )

注册后 infer_activities 自动套用。两个注意点：
1. 注册时会做**覆盖预检**：若房间窗口内没有 tags 对应传感器的事件，会显式警告"缺实体类型"（而不是悄悄 0 结果）；
2. 若卧室无 presence 传感器，可用代理条件：改绑 door（门磁关闭）或 climate（空调开着）等该房间真实存在的信号。

识别出的活动 id 可用 source_refs="insight:<id>" 写回记忆库。`,
  },
  {
    c: 'activities',
    q: '为什么自定义活动注册了却识别不出来？',
    a: `现在会显式诊断而不是静默失败。看 infer_activities 返回的 **detector_report** 字段：未命中的自定义规则若因"房间缺实体类型"（如房间没有 presence 传感器），会明确报「缺实体类型：房间X无 presence 传感器（窗口内无对应事件），规则无法命中」。

排查步骤：
1. detector_report 里看该规则的 missing_tags；
2. get_entity_catalog(room="卧室") 确认该房间有哪些类别设备；
3. 用真实存在的传感器类别改写规则 tags（door / climate / appliance / media 是常见代理信号）。`,
  },

  // ── 事件查询 ──────────────────────────────────────────────
  {
    c: 'events',
    q: '昨晚客厅都发生了什么？',
    a: `语义化事件搜索用 **search_events**，无需 entity_id：

    search_events(room="客厅", days=1)
    search_events(room="客厅", category="media", summarize=True)

**summarize=True 强烈推荐**：返回"每台设备变化多少次、变成什么、24 小时分布"的压缩摘要 + 50 条样本，比几千条裸事件省 token 且更易读。返回的 total / has_more / next_offset 告诉你有没有取全。
entity_id 可逗号分隔多个；显式传 entity_id 时忽略 room/category 的语义解析。`,
  },
  {
    c: 'events',
    q: '精确拉取某几个实体的原始事件',
    a: `已知 entity_id 时用 **query_events**（精确层）：

    query_events(
      entities=["sensor.xxx", "switch.yyy"],
      days=3, limit=500
    )

- days=最近 N 天，或 start/end 传本地 ISO 时间；都不传默认最近 7 天；
- behavior_only=true（默认）剔除 sensor/number 等每分钟遥测，避免污染行为分布；
- 分页：按 total 与 next_offset 循环取全，单页上限 2000。

不知道 entity_id 就先 search_events 或 get_entity_catalog。`,
  },
  {
    c: 'events',
    q: '入户门最后一次关是什么时候？灯最后关是什么时候？',
    a: `用 **get_last_event** 查某实体/某类设备的最近一次状态变化：

    get_last_event(entity_id="binary_sensor.door_xx", transition="off", days=30)
    get_last_event(domain="light", room="客厅", transition="off")

transition 可填 off / on / any。适合"出门前门锁了吗""灯关了没"这类确认型问题。`,
  },
  {
    c: 'events',
    q: '导出 30 天原始事件，我要离线分析',
    a: `用 **export_history**：

    export_history(days=30, limit=2000)

导出原始事件用于离线分析。注意：日常洞察请优先 get_behavior_insights / infer_activities（服务端算好结论）；裸导出只在需要自行建模时用。按人的历史可用 get_person_history(person="lidicn", days=7)。`,
  },

  // ── 家庭成员 ──────────────────────────────────────────────
  {
    c: 'members',
    q: '家里有哪些成员档案？',
    a: `用 **list_members**：

    list_members()

返回全部成员，含关联房间（rooms）、专属设备（devices）、生活习惯标签（tags，带置信度/证据/来源）。
这些档案也能在 WebUI「家庭成员」页维护：增删成员、关联房间/设备、手动加标签、上传头像、填外观特征（让摄像头识别到 TA 时显示姓名）。`,
  },
  {
    c: 'members',
    q: '新建一个成员"妈妈"',
    a: `用 **create_member**：

    create_member(name="妈妈", avatar_emoji="👩", note="生活习惯待观察")

WebUI 里也可以直接点「添加成员」，还能填外观特征（性别/年龄段/身材/穿搭/发型/常出现区域），供多模态命名识别使用。`,
  },
  {
    c: 'members',
    q: '把"书房、客厅"关联给爸爸，电脑是爸爸的专属设备',
    a: `两个工具（都是全量覆盖语义）：

    assign_member_room(member_id="xxx", rooms=["主卧室", "书房"])
    assign_member_device(member_id="xxx", entity_ids=["media_player.pc_xx", ...])

member_id 用 list_members 查。关联房间会影响 get_member_persona 的房间定向洞察，专属设备用于"这是谁的设备"归因。`,
  },
  {
    c: 'members',
    q: '分析爸爸的习惯，把结论写回他的档案（如"夜猫子"）',
    a: `两步走：

    # 1) 拉画像并分析
    get_member_persona(member_id="xxx", days=14)

    # 2) 用户明确确认后，写回标签
    confirm_member_tag(
      member_id="xxx", tag="夜猫子",
      category="sleep", emoji="🦉",
      confidence=0.8,
      evidence=["连续7天 0 点后仍有电视播放事件", ...]
    )

category 可选 sleep / diet / activity / media / hygiene / other。**仅当用户明确确认后才调用写回**，不要擅自写。WebUI 家庭成员页也能手动加/删标签。`,
  },
  {
    c: 'members',
    q: '家庭成员重复了（如 lidicn 和"爸爸"是同一人），怎么合并？',
    a: `在 WebUI「家庭成员」页操作：

1. 点开任意一张成员卡片进详情抽屉；
2. 点右上角**合并按钮**（删除按钮旁）；
3. 选择合并到的目标成员，确认。

合并策略（非破坏式，目标优先）：
- 关联房间与专属设备：取并集；
- 生活习惯标签：同名标签保留置信度更高的一条，证据合并去重；
- 外观特征 / 人脸 / 管家档案 / 备注：目标为空才继承；
- 合并后源成员删除。操作不可撤销。`,
  },

  // ── 记忆库 ────────────────────────────────────────────────
  {
    c: 'memory',
    q: '记住一个结论："lidicn 喜欢睡前看 30 分钟纪录片"',
    a: `写回向量记忆库用 **add_semantic_memory**（参与式迭代）：

    add_semantic_memory(
      text="lidicn 喜欢睡前看约 30 分钟纪录片（23:00 前后电视 HDMI 播放）",
      source_refs=["insight:watching_tv_20260912_xxx"],
      tags=["media", "sleep"],
      topic_key="media_habit",
      dry_run=True     # 默认先自检冲突/重复，不落库
    )

关键约束：
- source_refs 必须是**可解析的真实引用**：event:<event_id> 或 insight:<activity_id>；
- dry_run=True 先验证，确认无误再 dry_run=False 正式写入；
- 写入恒为 **staging**，永不自动进 live，需 promote_memory / sweep_promote_candidates 晋升。`,
  },
  {
    c: 'memory',
    q: 'staging 记忆怎么晋升成 live？信任机制是什么？',
    a: `记忆有信任闭环：写入 → staging → 晋升 → live（参与检索）。

    promote_memory(memory_id="xxx", corroborating_insight_id="insight:...")   # 带佐证晋升
    sweep_promote_candidates()      # 手动触发自动晋升扫描 + 镜像 reconcile
    get_session_trust(session_id="mcp")   # 看会话声誉：avg_trust / live 占比

晋升前自动做矛盾/重复检测：重复禁止晋升，冲突挂起 pending_review。
普通会话需满足：提供高置信佐证 insight_id，或跨 N 天反复观测。force=True 强推仅限 privileged_sessions 白名单会话。`,
  },
  {
    c: 'memory',
    q: '查记忆：关于空调习惯有哪些已沉淀的结论？',
    a: `检索已晋升 live 的记忆用 **retrieve_agent_memories**（相似度 + trust 重排）：

    retrieve_agent_memories(question="空调使用习惯", top_k=5)
    retrieve_agent_memories(question="空调", trust_min=0.6)   # 过滤低信任

自然语言问答也可以直接 **ask_memory**（先走结构化洞察，模糊问法回落向量检索）：

    ask_memory(question="空调最近设定几度", days=7)
    ask_memory(question="上周三晚上家里在干嘛", route="semantic")   # 纯语义 + agent 记忆`,
  },
  {
    c: 'memory',
    q: '这条记忆不对 / 过时了，怎么删？',
    a: `记忆不做硬删，全部走软删（墓碑）+ 审计轨迹：

    revoke_memory(memory_id="xxx")                    # 软删一条
    rollback_agent_memory(session_id="mcp")           # 某会话整段回滚（一键撤销）
    feedback_memory(memory_id="xxx", useful=False)    # 反馈"无用"，压低信任与 TTL

审计视图：

    list_agent_memories(state="all")   # staging | live | revoked | pending_review | all
    agent_memory_health()              # 各状态数量、镜像缺口、chroma 可用性`,
  },

  // ── 信号规则 ──────────────────────────────────────────────
  {
    c: 'signals',
    q: '书房门窗传感器老是把"开门"误判成"在工作室"，教系统别把它当信号',
    a: `用 **teach_signal** 做信号纠正（学习策略，持久化）：

    # 硬排除：无歧义误报，直接从该检测维度剔除
    teach_signal(
      entity_id="binary_sensor.study_door",
      scope="all", kind="hard",
      reason="门磁开关频繁，误判 working/presence"
    )

    # 软记忆：带条件的判断经验，进信任闭环
    teach_signal(
      entity_id="binary_sensor.study_door", kind="soft",
      text="夜间门磁触发多为风，不应计为有人活动",
      source_refs=["event:12345"]
    )

kind='hard' 写入 signal_exclusions 表，生效于起床锚定 / 在房与工作判定 / 看电视识别；kind='soft' 走 agent 记忆（此时 text 必填）。
查看已学会的规则：list_signal_rules(include_revoked=False)。WebUI「信号规则」页同样可管理与吊销。`,
  },

  // ── 洞察模板 ──────────────────────────────────────────────
  {
    c: 'templates',
    q: '把"电视 HDMI3 每日使用时长"沉淀成可复用的分析模板',
    a: `用 **save_analysis_template** 沉淀结论（分析历史数据发现模式后调用）：

    save_analysis_template(
      id="xbox_daily_usage",
      name="客厅电视 HDMI3 使用时长",
      description="按信源统计 HDMI3(Xbox) 每日时长",
      category="media",
      entities=[{
        "entity_id": "media_player.xxx_play_control",
        "logical_id": "lidicn的电视电视",
        "attribute": "source", "pattern": "equals", "value": "HDMI 3",
        "time_range": "", "metric": "duration"
      }],
      pattern="source_equals_hdmi3",
      confidence=0.9,
      default_days=7,
      interpretation="近{window}共使用{total_human}，{count}天有记录。"
    )

- category：sleep / media / lighting / climate / appliance / security / other；
- metric：duration / count / numeric_sum / state_share；
- time_range 支持"22:00-02:00"这类跨午夜窗口；
- entities 里可用 logical_id 走身份层自愈（entity_id 漂移不怕）。`,
  },
  {
    c: 'templates',
    q: '用某个模板直接算结果（不要只给配置）',
    a: `用 **run_analysis_template**，服务端按模板里的实体条件与指标直接算：

    run_analysis_template(template_id="xbox_daily_usage", days=7)
    run_analysis_template(template_id="xbox_daily_usage", start="2026-09-01", end="2026-09-07")
    run_analysis_template(template_id="xbox_daily_usage", include_timeline=False)

返回每实体累计值、分日明细、时间轴与解读话术 summary_text，Agent 基于返回直接作答即可，不要再自己反推。
执行有闸门（v0.7）：模板引用的实体已失效会被**硬阻止**并给出 suggestions，避免静默返回误导性 0。先 list_analysis_templates 看有哪些模板。`,
  },
  {
    c: 'templates',
    q: '列出 / 导出 / 删除模板；模板失效了怎么办？',
    a: `模板管理四件套：

    list_analysis_templates()                    # 全部模板（含内置标记）
    list_analysis_templates(category="media")    # 按类别筛选
    export_insight(template_id="xxx")            # 导出配置 + Node-RED 实现逻辑
    delete_analysis_template(template_id="xxx")  # 删除（内置模板不可删）

模板失效（实体失联/被删）时：
1. run_analysis_template 会被闸门硬阻止，返回 status 与 suggestions；
2. 按 suggestions 换 entity_id 或改用 logical_id；
3. WebUI「行为洞察」页有模板校验 / 停用 / 一键修复入口（validate / disable / fix）。`,
  },

  // ── 技能 ──────────────────────────────────────────────────
  {
    c: 'skills',
    q: '把这次的分析经验保存成"技能"，下次复用',
    a: `用 **save_skill** 把经验写回网关（唯一真源，自增 version）：

    save_skill(
      name="sleep_pattern_analysis",
      title="睡眠模式分析套路",
      category="insight",
      content="---\\nname: sleep_pattern_analysis\\n...\\n---\\n\\n1. 先 infer_activities(days=14)...\\n2. ..."
    )

name 只能含字母数字 _ -；content 是完整 markdown（frontmatter 会自动补齐/续版本）。
下次拉取最新版：

    list_skills()                    # 看哪些 skill 有新版本
    get_skill(name="sleep_pattern_analysis")`,
  },

  // ── 数据采集 ──────────────────────────────────────────────
  {
    c: 'collect',
    q: '现在采集到多少数据了？采集任务在跑吗？',
    a: `用 **get_collect_status**：

    get_collect_status()

返回采集服务状态、当前任务进度、数据库双层统计（关系库为主 / 向量库为辅）。
WebUI「数据采集」页还能看采集日历、任务历史、事件浏览器，并支持手动触发 / 回填 / 取消。`,
  },
  {
    c: 'collect',
    q: '立刻采一次数据 / 刚才那段没采到，补采',
    a: `触发采集（异步，立刻返回任务号）：

    trigger_collection()                        # 标准增量：从上次成功点到现在
    trigger_incremental_collection(since_minutes=120)   # 强制补采最近 120 分钟（写入幂等，重复无害）

用 get_collect_status 轮询进度。正常情况下系统自动周期采集，手动触发只在"刚配好新设备""发现数据缺口"时需要。`,
  },

  // ── 常见坑（综合） ─────────────────────────────────────────
  {
    c: 'start',
    q: '有哪些高频踩坑点？Agent 使用注意事项汇总',
    a: `1. **遥测污染**：功率/温湿度每分钟一条，会污染行为分布——保持 behavior_only=true，或直接用洞察工具；
2. **不要背 entity_id**：先用 get_entity_catalog / search_events 语义定位；担心漂移就用 logical_device；
3. **分页**：所有列表返回都带 total / has_more / next_offset，据此取全，不要只取一页就下结论；
4. **首调慢**：get_behavior_insights 首次调用是冷启动全量扫描，可能几十秒，客户端超时要调大；进程内有 60s 结果缓存，同参数重复调用秒回；
5. **写操作需确认**：confirm_member_tag、add_semantic_memory、teach_signal、promote 等写回类工具，务必在用户明确确认后调用；
6. **记忆晋升有闸门**：staging 不会自动变 live，需要佐证或反复观测；
7. **弱信号别过度推断**：说"生活极规律"前先看时长与温控维度的 delta，不要只用次数。`,
  },
];

const TPL = `
<div class="space-y-5">
  <!-- 顶部栏 -->
  <div class="flex items-center justify-between gap-3">
    <div>
      <h2 class="text-xl font-semibold">用户手册</h2>
      <p class="text-[12px] text-txt-3 mt-0.5">以问答形式教你（和你的 AI 助手）用好 MCP 的全部能力——每条 Q 都是可以直接对 AI 说的话</p>
    </div>
    <div class="text-[11px] text-txt-3 shrink-0" x-text="filtered.length + ' / ' + qa.length + ' 条'"></div>
  </div>

  <!-- 搜索 + 分类 -->
  <div class="flex flex-col gap-2.5 sticky top-16 z-10 -mx-1 px-1 py-1">
    <input class="inp" placeholder="🔍 搜索问题 / 工具名 / 关键词，如：空调、电视时长、记忆…" x-model="search">
    <div class="flex flex-wrap gap-1.5">
      <button class="chip text-[11px]" :class="cat==='all' ? 'chip-active' : 'opacity-60 hover:opacity-100'" @click="cat='all'">全部</button>
      <template x-for="c in cats" :key="c.id">
        <button class="chip text-[11px]" :class="cat===c.id ? 'chip-active' : 'opacity-60 hover:opacity-100'" @click="cat=c.id" x-text="c.label"></button>
      </template>
    </div>
  </div>

  <!-- 问答列表 -->
  <div class="space-y-2">
    <template x-for="(item, i) in filtered" :key="cat + '-' + i">
      <div class="card overflow-hidden !p-0">
        <button class="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-white/5 transition" @click="toggle(i)">
          <span class="w-6 h-6 rounded-md bg-brand-500/15 text-brand-300 text-[11px] font-bold grid place-items-center shrink-0" x-text="i+1"></span>
          <span class="text-[13px] font-medium flex-1 leading-snug" x-text="item.q"></span>
          <span class="badge shrink-0 hidden sm:inline-flex" x-text="catLabel(item.c)"></span>
          <svg viewBox="0 0 24 24" class="w-4 h-4 text-txt-3 shrink-0 transition-transform" :class="isOpen(i) && 'rotate-180'" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <div x-show="isOpen(i)" x-cloak class="px-4 pb-4 pt-2 border-t border-white/5">
          <div class="manual-answer text-[13px] leading-relaxed text-txt-2" x-html="render(item.a)"></div>
        </div>
      </div>
    </template>
    <div x-show="!filtered.length" class="card p-10 text-center">
      <div class="text-4xl mb-2">🔍</div>
      <p class="text-sm text-txt-3">没有匹配的问答，换个关键词试试</p>
    </div>
  </div>
</div>
`;

export const userManualPage = () => ({
  tpl: TPL,
  search: '',
  cat: 'all',
  openItems: [],
  cats: CATEGORIES,
  qa: QA,

  get filtered() {
    const kw = (this.search || '').trim().toLowerCase();
    return QA.filter((item) => {
      if (this.cat !== 'all' && item.c !== this.cat) return false;
      if (!kw) return true;
      return item.q.toLowerCase().includes(kw) || item.a.toLowerCase().includes(kw);
    });
  },

  isOpen(i) {
    return this.openItems.includes(i);
  },

  toggle(i) {
    const idx = this.openItems.indexOf(i);
    if (idx >= 0) this.openItems.splice(idx, 1);
    else this.openItems.push(i);
  },

  catLabel(id) {
    const c = CATEGORIES.find((x) => x.id === id);
    return c ? c.label : id;
  },

  render(md) {
    try {
      if (window.marked && typeof window.marked.parse === 'function') {
        return window.marked.parse(md);
      }
    } catch (e) { /* 走降级 */ }
    const div = document.createElement('div');
    div.textContent = md;
    return '<pre class="whitespace-pre-wrap text-[12px] font-mono">' + div.innerHTML + '</pre>';
  },

  init() {},
});
