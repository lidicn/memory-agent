# memory-agent MCP 十六轮黑盒审计汇总报告

- 审计对象：memory-agent MCP（MCP SSE 接入）
- 审计方式：**纯黑盒**——仅通过 MCP 工具调用，不读源码
- 审计周期：2026-10-08，共 16 轮
- 审计人：deepseek++2
- 累计登记 bug：26 条（本轮 21 条 + 历史 5 条），覆盖 17 个工具

---

## 一、执行摘要

本轮 16 轮审计覆盖 memory-agent MCP 全部 14 个工具组中约 60 个工具，累计登记 **21 条新 bug**（含 1 critical / 15 major / 4 minor / 1 info）。缺陷高度集中在三类：

1. **写接口契约不一致**（dry_run 真伪、schema 与运行期校验冲突）
2. **口径不统一**（同一指标在不同工具算法/过滤不一致）
3. **崩溃与依赖缺失**（SSE 断流、river 未安装、裸异常无诊断）

---

## 二、Bug 清单（按严重度）

### Critical（1）

| # | 工具 | 问题 | bug_id |
|---|---|---|---|
| 1 | query_device_usage | 逻辑设备名（身份层）解析失效：logical_device=卧室空调 返回 stale/设备已失效，而同刻 get_entity_catalog 能命中、entity_id 直连正常。工具相对 get_device_usage 的唯一卖点不可用 | bug_75033e255c |

### Major（15）

| # | 工具 | 问题 | bug_id |
|---|---|---|---|
| 2 | get_data_coverage | 窗口 off-by-one：days=N 恒返回 N+1 天（1→2、3→4、7→8） | bug_c1d718247f |
| 3 | list_device_health | stale 口径矛盾：state=stale→0，get_device_health→6；total 1776 vs 112 不可比 | bug_adf2558881 |
| 4 | list_rooms_entities | 调用直接崩溃：SSE stream ended without a matching response | bug_6b644de8d2 |
| 5 | infer_activities | rule_sources 自相矛盾：声称 custom_applied=5，输出全 heuristic；空证据 confidence=0.95 | bug_eb6e47e587 |
| 6 | get_device_usage_summary | 与 get_device_usage 同窗口开关次数 2 vs 3 不一致 | bug_ecbc646492 |
| 7 | add_semantic_memory | 运行期强制 source_refs 非空与 schema required=[text] 冲突；低信任 session(0.08) 不拦截；conflict_scan 不可用 | bug_f6df71db6a |
| 8 | get_behavior_summary | 与 get_behavior_insights 同窗口 total_events 12706 vs 7906（差 4800 噪声），top_entities 被噪声实体污染 | bug_65f8d7b2e9 |
| 9 | get_user_persona | 未剔除遥测：作息类型/峰值小时与另两工具矛盾（5/18/19），设备交互强度用含遥测 raw 总量 | bug_1bc8d1fc69 |
| 10 | get_behavior_insights_compare | 窗口 off-by-one 复发：compare_days=3 实覆盖 4 个自然日 | bug_0843ae13b4 |
| 11 | get_behavior_drift | 直接崩溃：底层依赖 river 未安装，功能完全不可用 | bug_ed57d64396 |
| 12 | list_candidate_rules | 候选规则被遥测污染：卧室/房间移动步骤为空调室外机电流传感器，两步同一实体 | bug_d9aec621c4 |
| 13 | audit_rule_recall | 核心内置规则召回率 0（书房工作/就寝 14 天 0 命中）；与 list_candidate_rules 对召回缺口结论矛盾 | bug_091dce70d9 |
| 14 | infer_behavior_intent | 无参调用直接崩溃：裸异常无 error code/detail | bug_be2d44d3ee |
| 15 | get_room_behavior_summary | activity_distribution 恒空、state_count=0，与文档承诺不符 | bug_745d5396cd |
| 16 | get_climate_sessions | 间歇性崩溃：同参数早前可返回，后 SSE 断流 | bug_ca0ef6e343 |
| 17 | read_self_diary | 日记存在但正文全为空字符串（count=2，text 均空） | bug_a1965e8c35 |
| 18 | ask_memory | 未回答核心主语（谁）；烹饪活动时长被语义规则夸大至 364 分钟、置信度 1.0 | bug_516a1bf033 |

### Minor / Info（4）

| # | 工具 | 问题 | bug_id |
|---|---|---|---|
| 19 | query_device_usage | 错误字段名错误：报 logical_id，真实字段是 logical_device | bug_6663327d02 |
| 20 | get_behavior_prediction | 无数据静默返回 ok=true 与全 null，未提示不支持人员归属 | bug_56762923f9 |
| 21 | get_behavior_insights_compare | 事件量口径用含遥测原始量，与同族工具不一致 | （随 bug_0843ae13b4 记录） |

---

## 三、缺陷模式归纳

### 模式 A：口径不统一（8 条）
同一指标在多个工具用不同过滤/算法：
- total_events：summary(12706) vs insights(7906) vs compare(117238含遥测)
- 峰值小时：persona(5) vs summary(18) vs insights(19)
- 开关次数：usage_summary(2) vs usage(3)
- stale 数：list_device_health(0) vs get_device_health(6)
- 总实体数：1776 vs 112

### 模式 B：写接口契约不一致（2 条）
- add_semantic_memory：schema required=[text] 但运行期强制 source_refs
- 历史 teach_signal：dry_run=true 被忽略、真实写入生产

### 模式 C：崩溃与依赖缺失（4 条）
- list_rooms_entities（SSE 断流）
- get_climate_sessions（SSE 间歇断流）
- get_behavior_drift（river 未安装）
- infer_behavior_intent（裸异常无诊断）

### 模式 D：数据未落库导致字段恒空（3 条）
- get_room_behavior_summary.activity_distribution 恒空
- mine_behavior_process.anomaly_count=0
- get_behavior_prediction 全 null
- read_self_diary text 全空

### 模式 E：窗口 off-by-one（2 条）
- get_data_coverage：days=N → N+1 天
- get_behavior_insights_compare：compare_days=3 → active_days=4

---

## 四、分轮覆盖记录

| 轮次 | 覆盖工具组 | 本轮发现 |
|---|---|---|
| R1 | help/agent_memory_health/get_data_coverage/get_data_quality/list_device_health/get_device_health/get_entity_catalog/list_rooms_entities/query_device_usage/get_device_usage/get_device_usage_summary/get_climate_sessions/infer_activities/add_semantic_memory/report_bug/list_bug_reports | 8 条（B1-B8） |
| R2 | get_behavior_insights/get_behavior_insights_compare/get_behavior_summary/get_user_persona | 4 条 |
| R3 | list_behavior_anomalies/get_behavior_drift/mine_behavior_process/audit_rule_recall/list_rule_channel/list_candidate_rules/list_rule_lifecycle_audit | 3 条 |
| R4 | infer_behavior_intent/get_behavior_prediction/list_behavior_drifts | 2 条 |
| R5 | get_room_behavior_summary/get_climate_sessions/query_events | 2 条 |
| R6 | list_agent_memories/retrieve_agent_memories/list_signal_rules/get_session_trust | 0 条 |
| R7-R14 | list_analysis_templates/list_skills/get_vision_status/list_vision_cameras/read_self_diary/ask_memory | 2 条 |
| R15-R16 | 回归复核 + 报告落盘 | — |

---

## 五、可复现最小用例（Top 6）

1. query_device_usage(logical_device="卧室空调", days=2) → 期望时长，实得"设备失效"
2. get_data_coverage(days=3) → 期望 3 天，实得 4 天
3. list_device_health(state="stale") vs get_device_health() → 0 vs 6
4. get_behavior_summary vs get_behavior_insights 同窗口 → 12706 vs 7906
5. add_semantic_memory(text="x")（缺 source_refs）→ 与 schema required=["text"] 冲突
6. get_behavior_drift(days=14) → "river 未安装"

---

## 六、修复优先级建议

**P0（阻断核心能力）**
- query_device_usage 身份层解析失效（critical）
- get_data_coverage / get_behavior_insights_compare off-by-one
- get_behavior_drift river 依赖缺失

**P1（数据可信度）**
- 统一 total_events / 峰值小时 / 开关次数 / stale 数口径（建议建单一真源函数）
- add_semantic_memory 的 schema 与运行期校验对齐

**P2（稳定性与契约）**
- list_rooms_entities / get_climate_sessions 的 SSE 断流
- infer_behavior_intent 裸异常加诊断信息
- 空数据字段（日记正文、activity_distribution）明确标注状态

**三条建议红线**
1. 写接口 dry_run 必须真不落库
2. Schema 与运行期校验单一真源
3. 无删除接口的实体禁止创建

---

## 七、审计过程说明

- 全程零生产数据破坏：记忆写回仅 dry_run；缺陷仅 report_bug 登记
- 对 create_member / assign_member_* 等有历史破坏记录的工具未做写入测试，改用其历史 bug 做旁证
- 全量 26 条 bug 已通过 list_bug_reports 回读确认入库，审计闭环
- 局限：视觉组（analyze_camera）因需真实取帧未做主动调用；部分写接口（promote/advance/revoke 规则）因红线限制仅做只读探测
