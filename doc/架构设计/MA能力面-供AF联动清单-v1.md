# MA 能力面清单（供 AF 高阶自动化联动）v1

> 出品：MA 席位 ｜ 2026-11-17 ｜ 依据：现仓 `src/memory_agent`（HEAD 见 `git log -1`）＋ MCP `TOOL_NAMES` 92 枚实测分组（18 组）＋ 199 条 HTTP 路由现读 ＋ MQTT 主题现读。
> 目的：回答"AF 要把 MA 用深，能用哪些面"。每条给「能力 / 现成接口 / 数据基础 / AF 用法 / 现成度」。
> 现成度图例：● 已上线可直接用｜◐ 已上线但需开关或配置｜○ 仅 staging／需人工确认｜△ 内部能力、暂无对外接口（要新契约）

---

## 0. 现状差距（先说结论）

- 现有 AF↔MA 联动路径只有：MQTT `ma/insights`（一条）+ butler 白名单里 4 个只读端点（成员／在场／视觉最新／成员作息）+ `GET /api/behaviors`。
- MA 对外**92 个 MCP 工具**里，绝大多数**没有**进入这条链；30+ 个情报类 HTTP 端点（行为预测、意图、因果、过程挖掘、漂移、候选规则、记忆检索）也基本未被消费。
- ⇒ 当前是"告警通知级"联动；要上"高阶自动化"，AF 需要的是**习惯模型 + 预测 + 意图 + 规则候选 + 归因**这几类面，恰好都在 MA 已有能力里，但多数只能经 MCP/HTTP 取，⛔ 只靠 MQTT。

---

## 1. 数据底座（事实源，AF 写自动化前先看"有哪些事实"）

| # | 能力 | 现成接口 | 数据基础 | AF 用法 | 现成度 |
|---|---|---|---|---|---|
| 1.1 | 原始事件检索 | MCP `search_events`／`query_events`／`get_last_event`；HTTP `GET /api/collect/events` | `events` 表（HA 状态变化，按天分片采集） | 规则触发条件的事实核验；"上次开灯是几点" | ● |
| 1.2 | 设备用量 | `get_device_usage`／`get_device_usage_summary`／`query_device_usage` | 开关时长/次数/每日分布 | 判断"这台设备值不值得做自动化" | ● |
| 1.3 | 气候会话 | `get_climate_sessions` | 空调/暖气/地暖开关区间＋温度曲线 | 温度类自动化的历史依据 | ● |
| 1.4 | 数据覆盖/质量 | `get_data_coverage`／`get_data_quality` | 各房间事件量、时间缺口、采样率、镜像缺口 | **规则上线前的准入闸**：数据不够就不该生成规则 | ● |
| 1.5 | 设备健康 | `get_device_health`／`list_device_health`；MQTT `ma/device-health` | 在线/失联/失效、`stable_id` | 规则前置条件与"别对坏设备做自动化" | ● |
| 1.6 | 成员与在场 | `list_members`；HTTP `GET /api/vision/presence`；MQTT `ma/presence` | 成员档案＋识别来源（TV 端脸/视觉） | "谁在家"作为自动化触发/抑制条件 | ● |
| 1.7 | 视觉事实 | `query_behavior_events`；HTTP `GET /api/vision/latest`、`/api/vision/scene_graph`；`analyze_camera`（按需） | 视觉记录（谁/哪间/何时/动作） | 场景级触发（不是设备状态级） | ●（按需分析受节流与光线门槛约束） |
| 1.8 | 统一事件时间线 | `query_unified_events`（vMA-1.3 VIEW） | 设备/视觉/感知三源合并 | 跨源因果与"同一时刻发生了什么" | ◐（VIEW 取决于库版本） |
| 1.9 | 电视状态 | HTTP `GET /api/tv/state`、`/api/tv/screenshot`、`POST /api/tv/analyze` | 电视 App/画面 | 影音场景自动化 | ● |

## 2. 洞察（告诉 AF「什么在发生、什么变了、为什么」）

| # | 能力 | 现成接口 | 数据基础 | AF 用法 | 现成度 |
|---|---|---|---|---|---|
| 2.1 | 行为洞察报告 | `get_behavior_insights` | 作息节律、房间活跃时段、Top 设备、状态转移、异常 | 自动化的"现状基线" | ● |
| 2.2 | 环比变化 | `get_behavior_insights_compare` | 当前窗口 vs 上个等长窗口 | 检测"习惯变了"→ 触发规则重评 | ● |
| 2.3 | 活动识别 | `infer_activities`／`get_room_behavior_summary` | 做饭/洗澡/睡眠/看电视/离家＋置信度与证据 | 活动级自动化（比设备级高一阶） | ● |
| 2.4 | 画像 | `get_user_persona`／`get_member_persona`／`get_person_history`／`get_member_daily_pattern` | 作息类型、活跃房间、常用设备、规律性、当日行为序列 | **"想用户所想"的人格化输入** | ● |
| 2.5 | 行为总览 | `get_behavior_summary` | 事件量/Top 实体/小时分布（剔遥测噪声） | 快速概览与噪声过滤口径 | ● |
| 2.6 | 变化归因 | `analyze_behavior_change`；`GET /api/behaviors/causal/analyze` | 拐点检测＋原因搜索 | 自动化上线后"效果异常"的归因 | ● |
| 2.7 | 反事实 | `counterfactual_query`；`GET /api/behaviors/causal/counterfactual` | "没有这个事件会怎样" | 评估自动化收益/副作用 | ● |
| 2.8 | 过程挖掘 | `mine_behavior_process`；`POST /api/behaviors/mine-process` | 房间·天轨迹一致性、异常的一天 | 发现"流程断点"（哪一步常被手动补） | ●（POST 落库开关默认按日跑） |
| 2.9 | 异常/漂移 | `list_behavior_anomalies`／`get_behavior_drift`／`list_behavior_drifts`／`refresh_*` | 行为异常、概念漂移点 | 规则失效/环境变化检测 | ● |
| 2.10 | 洞察解释 | `explain_insight` | 口径/来源/置信度/使用建议 | 让 AF 的生成结果可解释、可审计 | ● |

## 3. 预测与意图（无感自动化的核心）

| # | 能力 | 现成接口 | 数据基础 | AF 用法 | 现成度 |
|---|---|---|---|---|---|
| 3.1 | 行为预测 | `get_behavior_prediction`；`GET /api/behaviors/predictions` | 历史事件→到家时间与日常作息 | **提前**开灯/开空调/预热，而不是"到了才开" | ●（模型简单，精度以实测为准） |
| 3.2 | 意图推断 | `infer_behavior_intent`；`GET /api/behaviors/intent` | 最近事件→意图（无 LLM 快路径） | 从"设备状态"升到"用户想干什么" | ● |
| 3.3 | 意图→动作 | `execute_intent_actions`；`POST /api/behaviors/intent/execute` | 建议动作/预览/执行 | 自动化草稿的候选动作 | ○（写向，需审批） |
| 3.4 | 当前行为状态 | `GET /api/behaviors`、`/api/behaviors/states` | canonical 状态机（周期推断落库） | 自动化的实时上下文 | ● |
| 3.5 | 习惯沉淀 | 内部 `activity.infer_habits`（日任务） | 长期习惯（非单日） | 跨周/月的"习惯型自动化" | △（无独立工具，需新接口） |

## 4. 规则与学习闭环（AF 生成自动化，MA 提供候选与评估）

| # | 能力 | 现成接口 | 数据基础 | AF 用法 | 现成度 |
|---|---|---|---|---|---|
| 4.1 | 候选规则 | `list_candidate_rules`／`confirm_candidate_rule`／`promote_candidate_rule` | 召回缺口/负样本/过程变体产出 | **AF 的自动化素材库**（不是从零编规则） | ○（需人工确认，DCD 红线） |
| 4.2 | 规则生命周期 | `advance_rule_to_live`／`revoke_active_rule`／`flag_rule_false_positive`／`list_rule_lifecycle_audit`／`GET /api/behaviors/rule-channel*` | 建议→确认→观察期→转正/撤销全程留痕 | 自动化上线/回滚的治理轨道 | ● |
| 4.3 | 召回审计 | `audit_rule_recall`／`refresh_rule_recall_gaps` | 「该判没判」场景与卡点 | 直接告诉 AF"还缺哪条自动化" | ● |
| 4.4 | 负样本聚类 | `POST /api/behaviors/negative-samples/suggestions` | 误报聚簇→放宽建议 | 降误报、提召回 | ◐（默认关，需开关） |
| 4.5 | 信号学习 | `teach_signal`／`list_signal_rules`／`revoke_signal_rule` | 哪些实体信号可当自动化信号（硬排除/软记忆） | 给 AF 一份"可用信号白/黑名单" | ● |
| 4.6 | 触发历史 | 内部 `device_feed`／`rule_trigger_history` | 触发流水（保留 7 天/10 万行） | 自动化是否真在跑、是否误触发 | ◐（总开关 + dry_run） |

## 5. 写回与记忆（AF 执行结果回流，形成经验；也是"越用越懂"的闭环）

| # | 能力 | 现成接口 | 数据基础 | AF 用法 | 现成度 |
|---|---|---|---|---|---|
| 5.1 | Agent 记忆 | `add_semantic_memory`／`retrieve_agent_memories`／`promote_memory`／`revoke_memory`／`rollback_agent_memory`／`feedback_memory`／`list_agent_memories`／`agent_memory_health`；HTTP `/api/agent/memories*` | `agent_memories`＋向量库镜像（staging→live） | 把"这次自动化的效果"写成记忆，下次生成时被检索 | ○（写入恒 staging，晋升需条件） |
| 5.2 | 自我日记 | `write_self_diary`／`read_self_diary`／`generate_self_diary` | 每日第一人称日记（staging） | 长周期人格化素材 | ○ |
| 5.3 | 技能库 | `save_skill`／`list_skills`／`get_skill` | 技能＋版本（网关为唯一真源） | AF 生成的自动化套路沉淀成技能 | ● |
| 5.4 | 分析模板 | `save_analysis_template`／`list_analysis_templates`／`run_analysis_template`／`export_insight` | 模板（实体查询+模式+触发） | 把常用分析固化成可复用件 | ● |
| 5.5 | 三路径/问答 | `route_question`／`ask_memory`／`match_recipe`／`submit_recipe` | 意图路由、剧本召回 | AF 自然语言接口的入口层 | ●／○（recipe 恒 staging） |
| 5.6 | 成员标签回写 | `confirm_member_tag`；HTTP `POST /api/members/{id}/tags` | 习惯标签写回成员档案 | 人格化自动化的"人"维度 | ○（仅明确确认后） |

## 6. 对外通道（AF 实际怎么接；现在只用了一条）

| 通道 | 形态 | 覆盖 | 适用 |
|---|---|---|---|
| MQTT 上行 | `ma/presence`（每变化）、`ma/device-health`、`ma/insights`、`adm/memory-agent/status|caps` | 事件推送 | 实时触发（现联动走这条） |
| MQTT 下行 | `butler/inbox/notify`／`speak`／`tv` | 请求 DB/播报 | 自动化结果的人机反馈 |
| HTTP 窄接口 | app token：`/api/insights/query`、`/api/agent/memories`、`/api/agent/memories/recall`；butler token：成员/在场/视觉/作息/行为/metrics/tv | 结构化查询 | AF 拉数据（推荐主通道） |
| MCP | `/mcp`（Streamable HTTP＋SSE），92 工具 | 全部能力 | AF 作为 MCP 客户端直接用工具（最大面） |
| ACP | `/acp`（`acp_` 令牌，agent 间委派；arena 3 工具窄面） | 委派 | 让 AF 把任务反委给 MA |
| HA Assist | `/v1/chat/completions` | 家庭记忆问答 | 语音侧入口 |
| 采集控制 | `POST /api/collect/trigger|backfill|cancel`、`GET /api/collect/status|progress|jobs` | 采集运维 | 自动化前补数据 |

## 7. 面向"高阶自动化"的推荐接入顺序（供 AF/PM 讨论）

1. **先当预览层**：`get_behavior_prediction`＋`infer_behavior_intent`＋`GET /api/behaviors` —— 让 AF 草稿带"预测/意图"依据（现成，风险最低）。
2. **再当素材层**：`list_candidate_rules`＋`audit_rule_recall`＋`get_data_coverage` —— AF 不再凭空生成，而是从"MA 说缺什么、数据够不够"出发。
3. **第三层做闭环**：`exercise/feedback` 写入 `add_semantic_memory`＋`feedback_memory`，让"这次自动化有没有用"沉淀；配 `analyze_behavior_change` 做效果归因。
4. **最后才碰写向**：`execute_intent_actions`／规则晋升／成员标签写回——全部走人工确认闸。
5. **通道建议**：拉数据走 HTTP（app token，白名单需扩）；要"一个工具一个能力"就上 MCP 客户端；MQTT 只留实时触发与播报。

## 8. MA 侧还需补的（如果下一版主题定为高阶自动化）

- 习惯级接口：把内部 `infer_habits` 暴露成 `get_member_habits`（跨周/月，带稳定度）。
- 预测接口：`get_behavior_prediction` 目前只有到家时间/作息，需扩"设备级下一步预测"（什么时间会开什么）。
- 订阅式推送：现在只有 `ma/presence`／`ma/device-health`／`ma/insights` 三类；高阶自动化需要 `ma/insights` 之外的主题（如 `ma/behavior-state`、`ma/prediction`），需与 DB/AF 定契约。
- 批量取数：MCP 工具逐个调，跨 30 天多成员会慢；需要一个"一次取齐"的聚合端点。
- 沙箱/配额：AF 频繁拉数据需要速率与令牌作用域收口（现有 service token 作用域机制可复用）。

---

### 附：数据与口径注意

- 本清单里"现成度 ○"的条目都有**人工确认/观察期**闸（DCD 红线），⛔ 由 AF 直接调用即视为生效。
- 所有时间戳为家庭墙钟；跨仓载荷的 `ts` 口径见契约 §四。
- 实体 id、成员标识一律不落文档；本文只写工具名与端点。
