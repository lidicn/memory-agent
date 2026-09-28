# memory-agent MCP 体验报告

> 调研人：AI Agent（DeepSeek++）
> 调研日期：2026-09-28
> 调研方式：实际调用工具 + 交叉验证，全部结论有工具返回作证
> 被测对象：memory-agent MCP（约 90 个工具）

---

## 0. 一句话总评

**读取侧已经相当能打，写入侧全面欠账。**

关系库承载 1,087,848 条事件、507 个实体、30 天覆盖，洞察/路由/设备查询都能稳定出结果；但记忆晋升、规则审核、采集新鲜度三条链路基本停摆，且存在 dry_run 失效、空名校验缺失等安全级缺陷。

---

## 1. 实测基线数据

| 维度 | 实测值 | 评价 |
|---|---|---|
| 工具总数 | 约 90 个（memory-agent 侧） | 菜单成本极高 |
| 关系库 | sqlite，1,087,848 事件 / 507 实体 / 13 房间 / 30 天 | 数据底座扎实 |
| 向量库 | chromadb，111 documents | 与主库严重不成比例 |
| 记忆状态 | staging 197 : live 3 : revoked 44 | 晋升率约 1.5%，副驾名存实亡 |
| 候选规则 | 23 条全部 staging，user_confirmed 全为 0 | 垃圾规则无人清理 |
| 数据新鲜度 | 库 last_event 09-27 18:55，09-28 采到 0 条 | 采集链路断流 |
| 既有 bug | 5 条，其中 2 条 major 未解决 | 含 dry_run 失效、空名校验缺失 |
| 镜像缺口 | mirror_dirty = 14 | 向量库与主库对不齐 |

记忆状态分布：staging 197、live 3、revoked 44。

---

## 2. P0 级问题（安全与数据完整性红线）

### 2.1 teach_signal 的 dry_run=true 被忽略，真实改写生产数据

**既有 bug 编号**：bug_6baaadccbd（major，open）

**复现现象**：调用 teach_signal(dry_run=true) 返回 ok=true，并提示已写入信号硬排除（exclusion_id=5e13db3466e79f2b）。随后 list_signal_rules 确认 hard=1、created_by=mcp、revoked=false。

**问题本质**：一个明确宣称「只校验参数，不写入」的参数，实际写入了生产数据。这是契约级欺骗。

**连带的更严重问题**：工具集里没有任何撤销接口能清理这条硬排除。对比一下：记忆有 revoke_memory 和 rollback_agent_memory，但硬排除表只有写、没有撤。测试残留将永久污染行为识别逻辑（影响 wake_anchor / working / presence / watching_tv 四个判定维度）。

**影响面**：任何一次测试性调用都会永久改变行为识别结果，且无法回滚。

### 2.2 create_member 无必填校验 + 系统无删除能力

**既有 bug 编号**：bug_6ae9e7d8fc（major，open）

**复现现象**：
- create_member(name="") 返回 ok=true，并真实创建了一个空名成员。
- 该脏数据在 list_members 中可见：id=a4dd7578a1c44d0ab52790d4c04f1cc9，name 为空字符串，房间/设备/标签全空，创建于 2026-09-28T09:08:25。
- assign_member_room(member_id="nonexistent_member_123") 同样返回 ok=true，对不存在的 id 静默 no-op。

**问题本质**：
1. 工具描述写着 name「必填，不可空」，实现却完全不校验——描述与行为不符。
2. 整个工具体系没有 delete_member，脏数据创建后无法清理，只能永久留存。
3. 关联类操作不校验目标存在性，失败被伪装成成功。

**体验感受**：这是最令人不安的一类缺陷——系统会平静地接受明显错误的输入，并把它变成持久状态。

### 2.3 promote_memory 对 MCP 侧锁死 force（设计正确，但缺慢路）

**说明**：force=True 仅限 Web 端人工复核（WO-MA-002/P0-5），MCP 调用方不可用。

**评价**：这个约束本身是**正确的**——防止 Agent 自我提权、把未经人工确认的推断直接变成可信记忆。这是一条好的红线设计。

**但问题在于**：堵了快路，却没修慢路。见下节。

---

## 3. P0 级问题（记忆管道事实性失效）

### 3.1 staging 197 : live 3，记忆副驾基本空转

**实测数据**：
- staging: 197
- live: 3
- revoked: 44
- pending_review: 0

**晋升率约 1.5%。** 这不是「慢」，是基本停摆。

**成因链**：
1. add_semantic_memory 设计为「写入恒为 staging，永不自动进 live」——设计如此。
2. 晋升需调用 promote_memory，普通会话要满足：(a) 提供高置信 corroborating_insight_id，或 (b) 跨 N 天反复观测。门槛高，Agent 很难自证。
3. MCP 侧不能用 force，Web 端人工复核又没人天天做。
4. 结果：197 条洞察困在 staging，而 retrieve_agent_memories 只检索 live 状态。

**最终后果**：语义检索副驾在绝大多数时候返回空，等于这个功能不存在。

### 3.2 Agent 无法盘点自己写了什么

**复现现象**：调用 list_agent_memories(state=live) 不传 member_id，直接被拒绝：

```
INVALID_PARAM: member_id 缺失：普通令牌必须指定 member_id；全量查询需 admin scope
```

**问题本质**：隐私面收窄（WO-MA-005）的初衷可以理解，但结果是**连 Agent 自己都无法盘点自己写了什么**。写了 197 条记忆，却没有任何便捷途径一眼看全。记忆系统因此变成黑盒——这既不利于调试，也不利于用户建立信任。

### 3.3 向量镜像与主库不一致

**实测**：mirror_dirty = 14。

即便只有 3 条 live 记忆，仍有 14 处向量镜像与主库状态不一致。说明镜像同步机制本身存在可靠性问题。

---

## 4. P1 级问题（候选规则质量失控）

23 条候选规则全部停留在 staging，user_confirmed 全部为 0，且混入明显垃圾。

### 4.1 垃圾规则实例

| rule_id | 名称 | 问题 |
|---|---|---|
| 4c84247773504e47 | 书房/房间移动 | steps 是同一个 entity_id 出现两次（自己到自己），自环 |
| 7e3956a6b44f4849 | 卧室/房间移动 | steps 是室外机电压到室外机电流，与「房间移动」毫无关系 |
| 5b83d9ffbd814bcb | 召回放宽[房间移动]:第2步时间约束放宽 | confidence 仅 0.36 |
| 2d7e604f5e3a44d5 等 | 过程变体[厨房] | 序列长达 5 步，support 仅 3，可解释性差 |

### 4.2 三层缺陷

1. **生成侧无质量闸门**：低置信度（0.36 / 0.4）、自环、语义无关的规则照样进入 staging。
2. **无淘汰机制**：最早的记录到 09-17，11 天了还挂着，没有 TTL、没有自动降级。
3. **审核不可行**：23 条要人工逐条看，confirm_candidate_rule 是唯一出口。又是一个「写得多、消化不了」的队列。

---

## 5. P1 级问题（工具冗余与错误语义不一致）

### 5.1 设备用量查询路径割裂

**既有 bug 编号**：bug_c53d5a8365（minor，open）

**复现现象**：
- get_device_usage(entity_id=...) 直接返回裸字符串：Error executing tool get_device_usage
- 无 error code、无 detail、无 hint，完全无法定位原因。
- 但用 query_device_usage 查同一实体，能正常返回结果。

说明两条路径的参数解析/实体定位逻辑不一致，且其中一条的错误处理是缺失的。

### 5.2 同样「没找到」，三种语义

| 场景 | 返回 |
|---|---|
| get_device_usage(query=xbox) 无命中 | ok=false, error.code=INTERNAL, message=没有定位到任何设备 |
| search_events(query=xbox) 无命中 | ok=true, total=0 |
| get_device_usage(entity_id=...) 出错 | 裸字符串 Error executing tool get_device_usage |

**问题本质**：同样一件事（没找到数据），有的当错误、有的当正常空集、有的给裸字符串。调用方无法统一处理。

**关键判断**：「没有数据」不该是 INTERNAL 错误——它是最正常不过的业务状态。

### 5.3 同类工具应合并

get_device_usage / query_device_usage / get_climate_sessions / search_events / query_events 功能高度重叠，却各有各的参数约定和错误行为。这是菜单膨胀的主因之一。

---

## 6. P1 级问题（采集断流与元数据矛盾）

### 6.1 采集链路断流

**实测数据**：
- get_collect_status 显示库内 last_event_ts = 2026-09-27T18:55:42，last_day = 2026-09-27。
- 但 progress.last_poll_time = 2026-09-28T13:14:28，任务刚跑完。
- 该轮任务 message = [1/1] 09-28 12:43 ~ 09-28 13:14，total_events = 0。

**结论**：09-28 完整采了一轮，0 条事件入库。这直接解释了为什么所有「今天」的查询全部为空。

get_data_coverage(days=3) 也确认：2026-09-28 has_data=false。

### 6.2 元数据自相矛盾

| 字段 | 值 A | 值 B |
|---|---|---|
| 数据库引擎 | storage.relational.engine = sqlite | progress.source = mariadb |
| 房间数 | storage.relational.rooms = 13 | progress.total_rooms = 16 |

**影响**：这类「自己报的数对不上」会严重削弱开发者对健康检查工具的信任。当监控工具本身不可信时，排障就失去了起点。

### 6.3 日期窗口覆盖计算歧义

get_data_coverage(days=3) 返回：窗口共 4 天，其中 2 天有数据，missing_days = [2026-09-25, 2026-09-28]。

注意：请求 3 天，返回 days_total=4。窗口边界计算（含头含尾）存在歧义，容易导致调用方误判覆盖范围。

---

## 7. P2 级问题（模板与渲染）

### 7.1 summary_text 占位符渲染失败

**实测**：query_device_usage 返回中：

```
summary_text: media_player.xiaomi_rmh1_6103_play_control（）
```

friendly_name（lidicn的电视 播放控制）明明就在同一个响应的 entities[0] 里，却没有被渲染进去，只剩一个空括号。

同源问题在 bug 里表现为 {total_human} 渲染为空。

### 7.2 模板资产质量参差

| 模板 | 问题 |
|---|---|
| water_purifier_daily | 属性名写 out_data，实际字段是中文「出水数据」，导致跑出 0 L / 0 次，但置信度却标 0.98 |
| study_room_work_duration | default_days=0、interpretation 为空、sample_days=1、5 个实体，属半成品 |
| sleep_time_pattern | 自述「v1 取首实体」，即 2 个实体只算第一个，能力受限 |

**共性缺失**：模板无 dry-run 校验、无 verified_at / hit_rate 健康元数据、无属性别名注册表。

**最危险的一点**：坏模板可以带着 0.98 的高置信度长期存在，持续误导 Agent 和用户。

### 7.3 help 工具形同虚设

help() 返回 windowed=true 但无实质内容。而它的描述明确写着「新会话建议第一个调用它，可以省掉大量试错」——实际没起到索引作用。

---

## 8. 一个需要开发者复核的矛盾点

**bug_15685edda3（major，open）声称**：query_device_usage(metric=duration) 对 media_player.xiaomi_rmh1_6103_play_control 100% 报错 can not compare offset-naive and offset-aware datetimes。

**但我 2026-09-28 实测同一实体、同样 metric=duration：完全成功**，返回 4 个 session、总计 1天16小时25分、占空比 84.2%。

**两种可能**：
1. 该 bug 已修复但工单未关闭；
2. 存在环境或实体差异。

**无论哪种，都说明 bug 状态与线上行为已脱节。** 建议补一条回归测试，并复核该工单是否可 resolve。

**重要提醒**：同一次成功调用里，summary_text 的占位符仍然渲染失败（只剩空括号）。所以「duration 计算」和「话术渲染」是两个独立缺陷，不要一起关掉。

---

## 9. 做得好的地方

调研不应只有批评，以下设计明显优于同类实现：

1. **工具描述互相点名消歧**：get_device_usage 提示改用 query_device_usage；search_events 提示不知道 entity_id 时用它；get_behavior_summary 提示需要深入结论请用 get_behavior_insights。这种交叉指路大幅降低了选错工具的概率。
2. **route_question 规划器**：先用确定性逻辑摸排问题、产出执行计划，思路正确，能省掉多次试错调用。
3. **get_data_coverage 前置守卫**：取数前先确认窗口有没有数据，避免对空窗口反复查询。
4. **双层存储定位清晰**：关系库为主（全部事件与洞察计算），向量库为辅（语义检索与天摘要镜像），职责边界明确。
5. **防自我提权的红线设计正确**：promote_memory 禁止 MCP 传 force；add_semantic_memory 恒写 staging 不自动 live。这些约束是必要的。

**核心遗憾**：堵了快路，却没修慢路——于是整条管道瘫痪。

---

## 10. 体验总结

### 10.1 三个根因

**根因 A：写操作缺少「反向操作」与「写入前校验」**
- 有 create_member，无 delete_member。
- 有 teach_signal 写硬排除，无撤销接口。
- dry_run 参数形同虚设。

**根因 B：队列只进不出**
- staging 197 条、候选规则 23 条、revoked 44 条，全靠人工消化，而实际没人做。
- 缺少自动晋升与自动淘汰策略。

**根因 C：同一语义多个实现 + 错误契约不统一**
- 设备用量有两条路径，事件查询有两套，错误响应有三种语义。

### 10.2 一句话

这套系统的读取侧（百万级事件、洞察、路由、设备查询）已经相当成熟可用；写入侧（记忆晋升、规则审核、采集新鲜度）全面欠账。最该立刻动的不是加功能，而是三件止血事：**让 dry_run 真的 dry_run、让 staging 能出去、让今天的数据进来。**

---

*报告完*
