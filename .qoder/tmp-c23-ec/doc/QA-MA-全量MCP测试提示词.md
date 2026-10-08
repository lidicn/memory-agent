# Memory Agent MCP 全量功能测试提示词

## 你的角色

你是 memory-agent 的 QA 测试员。MA 是一个家庭记忆管理系统，运行在 NAS 上（J3455 低功耗 CPU，SQLite 471MB，112万条事件）。

**重要约束（必须严格遵守）：**
- **严格单发串行**：一次只调一个 MCP 工具，等返回后再调下一个。绝对不要并发。
- **缩窗查询**：大聚合工具先用 days=1 或 days=3 测试，不要直接 days=30。
- **写操作只读测试**：report_bug 可以用（这是新功能），其他写操作（create_member/assign_member_room/promote_memory/revoke_memory 等）**只验证参数校验，不要真的写生产数据**。
- **超时处理**：如果工具超时（>60s），记录下来继续下一个，不要重试。
- **记录格式**：每个工具测试完记一行：工具名 | ✅/❌/⏱️ | 返回摘要

## MCP 连接信息

- URL: http://192.168.2.200:8086/mcp
- Token: 用你现有的 MCP token
- 健康检查: GET http://192.168.2.200:8086/health （免鉴权，应返回 {"ok":true}）

---

## 测试分组（按顺序执行）

### P0：基础连通性（先跑）

1. **get_vision_status** — 看视觉服务是否开启
2. **get_collect_status** — 看数据采集状态
3. **get_data_coverage** — days=1，看最近一天数据覆盖
4. **get_data_quality** — days=7，看数据质量
5. **get_last_event** — 看最近一条事件
6. **list_signal_rules** — 看规则列表
7. **list_bug_reports** — status=open，看 bug 列表

### P1：成员管理（刚修过 list_members 截断）

8. **list_members** — 重点测试！确认能看到全部 3 个成员（lidicn/Kevin/Emily），每个有 id/name/rooms，tags 只有 label+category+confidence
9. **get_member_persona** — 传 lidicn 的 member_id，days=7，看详情是否正常返回
10. **list_agent_memories** — state=live，看记忆列表
11. **agent_memory_health** — 看记忆健康度
12. **get_session_trust** — 看会话信任

### P2：行为洞察

13. **get_behavior_summary** — days=3
14. **get_behavior_insights** — days=3
15. **get_behavior_insights_compare** — compare_days=3
16. **list_behavior_drifts** — days=7
17. **list_behavior_anomalies** — days=7
18. **mine_behavior_process** — days=3, rooms=客厅
19. **get_behavior_drift** — days=7
20. **audit_rule_recall** — days=3

### P3：设备与实体

21. **list_rooms_entities** — only_enabled=true
22. **list_device_health** — state=""
23. **get_device_usage** — days=3（不传 entity_id）
24. **query_device_usage** — days=3（不传 entity_id）
25. **get_climate_sessions** — days=3
26. **get_entity_catalog** — 看实体目录

### P4：事件检索

27. **search_events** — days=1, room=客厅, limit=10
28. **query_events** — days=1, limit=10
29. **get_person_history** — days=3

### P5：活动与意图

30. **infer_activities** — days=3, rooms=客厅
31. **get_behavior_prediction** — days=3
32. **infer_behavior_intent** — 传一个简单场景描述

### P6：记忆操作

33. **ask_memory** — 问一个简单问题（如"家庭成员有哪些"）
34. **add_semantic_memory** — dry_run=true，加一条测试记忆，不真正写入
35. **list_candidate_rules** — status=staging
36. **sweep_promote_candidates** — 看晋升候选
37. **feedback_memory** — 传一个不存在的 memory_id，验证参数校验

### P7：规则引擎

38. **teach_signal** — dry_run=true，教一条简单规则
39. **list_candidate_rules** — status=all

### P8：因果与反事实

40. **counterfactual_query** — 传一个简单问题（如"如果客厅灯不开，会怎样"）
41. **explain_insight** — 传一个不存在的 insight_id，验证参数校验
42. **analyze_behavior_change** — days=3

### P9：Bug 上报通道（新功能，重点测）

43. **report_bug** — 上报一条测试 bug：
    - title: "MCP测试：list_members tags结构验证"
    - severity: "info"
    - description: "验证report_bug通道可用，tags已裁剪为label+category+confidence"
44. **list_bug_reports** — status=open，确认刚才上报的 bug 在列表里

### P10：数据采集

45. **trigger_incremental_collection** — since_minutes=5000，触发增量采集

### P11：写操作参数校验（只验证不真写）

46. **create_member** — 传空 name=""，验证必填校验
47. **assign_member_room** — 传不存在的 member_id，验证报错
48. **revoke_memory** — 传不存在的 memory_id，验证报错
49. **promote_memory** — 传不存在的 memory_id，验证报错

---

## 输出要求

测试完输出一份汇总表：

```
| # | 工具名 | 状态 | 返回摘要 |
|---|--------|------|----------|
| 1 | get_vision_status | ✅ | ... |
| 2 | get_collect_status | ✅ | ... |
...
```

然后列：
1. **通过的工具数 / 总数**
2. **超时的工具列表**（工具名 + 调用参数）
3. **报错的工具列表**（工具名 + 错误信息）
4. **发现的问题**（任何异常行为、数据不一致、奇怪返回）
5. **list_members 专项**：确认能看到几个成员？第3个成员是谁？绑定了哪个房间？

---

## 注意事项

- 如果某个工具返回数据很大（>10KB），记录大小但不要完整展开
- 如果工具报 401，说明 token 过期了，停下来报告
- 如果工具报 500，记录完整错误信息
- 不要调 export_history / trigger_collection（全量采集会锁住 SQLite）
- 不要调 review_behavior_anomaly / confirm_candidate_rule（写操作）
