# memory-agent MCP 黑盒审计报告

- 审计对象：memory-agent MCP（MCP SSE 接入）
- 审计方式：**纯黑盒**——仅通过 MCP 工具调用，不读取任何源码
- 审计时间：2026-10-08
- 审计人：deepseek++2
- 结论：确认 7 个 bug（1 critical / 5 major / 1 minor），另交叉印证历史同类缺陷 2 条
- 本轮登记 bug_id：bug_75033e255c、bug_c1d718247f、bug_adf2558881、bug_6b644de8d2、bug_eb6e47e587、bug_ecbc646492、bug_f6df71db6a、bug_6663327d02

---

## 一、本轮新发现（已登记）

| # | 严重度 | 工具 | 问题 | bug_id |
|---|---|---|---|---|
| 1 | critical | query_device_usage | 逻辑设备名（身份层）解析失效：logical_device=卧室空调 返回 stale:true / 设备已失效或待重匹配，而同刻 get_entity_catalog(query=卧室空调) 能命中、entity_id 直连正常。工具相对 get_device_usage 的唯一卖点（漂移稳定）不可用 | bug_75033e255c |
| 2 | major | get_data_coverage | 窗口 off-by-one：days=N 恒返回 N+1 天（1→2、3→4、7→8，total_days 实测 2/4/8） | bug_c1d718247f |
| 3 | major | list_device_health | stale 口径矛盾：state=stale → total:0，get_device_health → stale:6；total 口径 1776 vs 112 不可比；文档未列的 state=all 才出数据 | bug_adf2558881 |
| 4 | major | list_rooms_entities | 调用直接崩溃：MCP SSE stream ended without a matching response（retryable 但复现） | bug_6b644de8d2 |
| 5 | major | infer_activities | rule_sources 自相矛盾 + 置信度虚高：声称 custom_applied=5，输出全 heuristic/custom=false；空证据下 confidence=0.95 | bug_eb6e47e587 |
| 6 | major | get_device_usage_summary | 开关次数口径不一致：同一空调同窗口 summary on_off_count=2 vs usage switch_on_count=3 | bug_ecbc646492 |
| 7 | major | add_semantic_memory | 运行期校验与 Schema 冲突 + 低信任不拦截：required=[text] 却强制 source_refs；avg_trust=0.08 仍放行；conflict_scan.available=false | bug_f6df71db6a |
| 8 | minor | query_device_usage | 错误字段名错误：报 缺少 entity_id 或 logical_id，而真实字段是 logical_device | bug_6663327d02 |

## 二、交叉印证（历史 bug 显示同类缺陷具有系统性）

- bug_6ae9e7d8fc（opencode）：create_member(name=空串) 不校验、真实建库；assign_member_room 对不存在 id 静默 no-op；无 delete_member 无法清理。
- bug_6baaadccbd（opencode）：teach_signal(dry_run=true) 被忽略、真实写入生产——与本轮 add_semantic_memory 的 dry_run/校验缺陷同源。

归纳：**写接口（dry_run / 参数校验）与身份层解析是缺陷密度最高的两块。**

## 三、可复现最小用例

1. query_device_usage(logical_device=卧室空调, days=2) → 期望时长，实得「设备失效」
2. get_data_coverage(days=3) → 期望 3 天，实得 4 天
3. list_device_health(state=stale) vs get_device_health() → stale 0 vs 6
4. get_device_usage_summary 与 get_device_usage 同参对比 → 2 vs 3
5. add_semantic_memory(text=x)（缺 source_refs）→ 与 schema required=[text] 冲突
6. list_rooms_entities() → SSE 崩溃

## 四、优先级建议

- P0：① query_device_usage 身份层解析失效（critical，直接废掉卖点）；② get_data_coverage off-by-one（污染所有时间窗统计）
- P1：③④⑤⑥（同指标不同口径 / 崩溃 / 置信度造假）会让下游 Agent 基于错数决策
- P2：⑦⑧（契约与文案）及历史 dry_run 失效
- 建议统一三条红线：写接口 dry_run 必须真不落库；Schema 与运行期校验单一真源；无删除接口的实体禁止创建

## 五、审计过程说明

- 全程零生产数据破坏：记忆写回仅 dry_run；缺陷仅 report_bug 登记
- 对 create_member / assign_member_* 等有历史破坏记录的工具未做写入测试，改用其历史 bug 做旁证
- 本轮共登记 8 条（含 1 条 minor），list_bug_reports 回读确认 reporter=deepseek++2 条目已入库，审计闭环
