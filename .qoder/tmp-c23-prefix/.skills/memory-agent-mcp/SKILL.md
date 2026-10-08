---
name: memory-agent-mcp
description: Memory Agent (MA) 智能家居记忆系统 MCP 工具路由指南。当需要查询/写入家庭成员记忆、行为洞察、设备使用、人脸识别、规则引擎等操作时使用。
---

# Memory Agent MCP 路由指南

## 快速选择

### 查记忆（只读）
| 问题类型 | 用哪个 |
|---|---|
| 自然语言问答 | `ask_memory` |
| 精确检索记忆 | `retrieve_agent_memories` |
| 列记忆 | `list_agent_memories` |
| 记忆健康 | `agent_memory_health` |

### 写记忆（⚠️ 写操作）
| 操作 | 用哪个 |
|---|---|
| 写入新记忆 | `add_semantic_memory` |
| 晋升 staging→live | `promote_memory` |
| 软删记忆 | `revoke_memory` |
| 记忆反馈 | `feedback_memory` |

### 查行为（只读）
| 问题类型 | 用哪个 |
|---|---|
| 行为总览 | `get_behavior_insights` |
| 个人历史 | `get_person_history` |
| 行为预测 | `get_behavior_prediction` |
| 行为漂移 | `get_behavior_drift`（实时算）/ `list_behavior_drifts`（查历史） |

### 查设备（只读）
| 问题类型 | 用哪个 |
|---|---|
| 设备用量统计 | `get_device_usage`（用 entity_id） |
| 用人话查设备 | `query_device_usage`（如"客厅电视"） |
| 设备健康 | `get_device_health`（主动探测） |
| 实体清单 | `list_rooms_entities` |

### 成员管理（⚠️ 写操作）
| 操作 | 用哪个 |
|---|---|
| 列成员 | `list_members` |
| 创建成员 | `create_member` |
| 绑定房间 | `assign_member_room` |

### 规则引擎
| 操作 | 用哪个 |
|---|---|
| 列信号规则 | `list_signal_rules` |
| 教信号 | `teach_signal` |
| 列候选规则 | `list_candidate_rules` |
| 确认规则 | `confirm_candidate_rule`（⚠️ 写操作） |

### 运维
| 操作 | 用哪个 |
|---|---|
| 采集状态 | `get_collect_status` |
| 触发采集 | `trigger_collection`（⚠️ 写操作） |
| 导出历史 | `export_history` |

## 注意事项

1. **⚠️ 标"写操作"的工具**会改系统状态，调用前确认用户确实要改
2. **查设备优先用 `query_device_usage`**（人话设备名），entity_id 容易因 HA 重登漂移
3. **`get_behavior_drift` 是实时算不落库**，要历史记录用 `list_behavior_drifts`
4. **`list_device_health` 查注册表，`get_device_health` 主动探测**，两个不同维度
5. 记忆正文/家人身份敏感，不要回显到聊天
