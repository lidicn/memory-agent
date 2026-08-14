# memory-worker 向量库 Agent 参与式迭代 — 可行性报告

> 提交对象：memory-worker 开发者
> 提交方：CodeBuddy Agent（基于真实 MCP 测试体验）
> 日期：2026-08-04
> 说明：本报告"最小安全 MCP 接口集"与"Agent 期望新增功能"建议直接落地，可通过 `save_skill` 持续修订。

## 1. 背景与现状

memory-worker 定位为"行为洞察层"：关系/时序事件表为主库，chroma 向量库为语义"副驾"，由 `semantic_used` 标识是否启用向量召回。

经多轮 MCP 实测确认：
- 向量库**已连通**（`ask_memory` 返回 `semantic_used:true` + `semantic_hints`）。
- 但**语义召回质量弱**：hints 仅为"每日房间粗汇总"（如"08-04 客厅 2 次"）；语义问题（如"他午睡吗"）只拿到 1 天窗口且无午睡洞察。
- 真正的用户行为价值来自 `infer_activities` / `get_behavior_insights` 等**结构化**路由，而非向量匹配。

最关键事实（见 `help` 工具全景）：
- 「语义」分组下**唯一工具是 `ask_memory`，纯读**。
- 写类工具 `save_analysis_template` / `export_insight` 落**模板库（Node-RED）**；`save_skill` 落**网关技能真源**。
- **没有任何工具把内容写入 chroma 向量库。**

结论：当前向量库是"开发者单向喂、agent 只读"的黑盒，质量完全由 poller 单方面决定。

## 2. 核心问题诊断

向量库"怎么迭代都别扭"不是工程细节问题，而是**粒度与定位错误**：

1. **索引粒度错配**：当前嵌入的是日级房间汇总，但语义问题需要"时段 + 占用静默区间"——这正是 `infer_activities` 的产出，却没被嵌进去。
2. **重复而非补充**：向量层用更粗粒度重复结构化层，而非补充其模糊跨日/跨用户模式。
3. **参数全写死**：嵌入模型、top-k、阈值、hint 拼装均由开发者固定。

## 3. 方案总览：安全可控的 Agent 写回

目标：让 agent 把挖掘出的行为洞察回流进向量库，同时**物理上不可能让整个库报废**。

四道保险：

### 3.1 命名空间隔离
agent 写入独立的 `agent_memory` collection（或 metadata 打 `source=agent`）。`system_memory`（系统种子）永不被 agent 触碰。`ask_memory` 在 agent 库全损时降级回纯系统库。

### 3.2 只追加 + 墓碑（append-only + tombstone）
agent 写入**永不硬删**，仅标 `revoked=true`。任意记忆可按 `memory_id` 或整个 `session_id` 一键回滚，并保留完整审计轨迹。回滚成本 = 一次元数据更新。

### 3.3 暂存区再晋升（staging → live）
新记忆先落 `agent_memory_staging`，**默认不参与检索**。满足晋升条件才进 live。agent 写再多垃圾，检索流量也看不到。

### 3.4 TTL 自动过期
agent 记忆带 `ttl`（如 7/30 天），到期自动失效，除非被重新确认。用时间框住爆炸半径。

## 4. 置信度鉴定（不让 agent 自报）

agent 自报 `confidence` 不可信（会过度自信）。正确做法是用**外部信号推导**：

- **溯源绑定**：每条记忆必须带 `source_refs`（依据的原始事件 / `infer_activities` 结论）。无溯源 = 直接拒收。
- **交叉印证才晋升**：staging 记忆仅当 (a) `infer_activities` 已高置信产出它，或 (b) 跨 N 天反复观测，或 (c) 二次独立校验通过，才晋升 live。
- **检索实效性反推信任（reputation）**：live 记忆被真正召回且 `feedback_memory(useful=true)` 确认 → 信任分涨、TTL 延长；被踩/长期无召回 → 加速衰减。某 session 产出多被踩 → `agent_trust` 下降，后续写入进更严 staging。
- **矛盾检测**：晋升前与高信任记忆做相似度比对，冲突（"睡 8h" vs "睡 3h"）挂起待审，不直接插入。

## 5. 最小安全 MCP 接口集

```
add_semantic_memory(text, tags, source_refs, ttl, dry_run=True)
   → 仅写 staging；dry_run 返回"将嵌入内容 + 与现有记忆的冲突/重复检测"，不落库，agent 可先自检
promote_memory(memory_id)              # 满足条件自动晋升，或人工确认
revoke_memory(memory_id)               # 软删（墓碑）
rollback_agent_memory(session_id)      # 整段回滚
feedback_memory(memory_id, useful)     # 喂信任分 + TTL
list_agent_memories(state=staging|live|revoked)  # 审计视图
get_agent_trust(agent_id)              # 声誉
```
检索侧改动：`ask_memory` 查 `system_memory`（永远）+ `agent_memory`（仅 live，按 trust 加权）；staging 永不被检索。

## 6. 落地节奏

- **MVP（报废不了就靠它）**：独立 collection + 追加墓碑 + TTL + `dry_run` 冲突检测。四件齐了，agent 再怎么乱写也只是污染一个可回滚的暂存区。
- **Phase 2（置信度变真）**：staging→live 晋升、trust 声誉、feedback 衰减、矛盾检测。

## 7. Agent 期望新增的 MCP 功能（基于真实测试痛点）

除第 5 节写回接口外，作为使用者，我另希望增加：

**A. 行为画像与对比**
- `get_user_persona(days)` — 一站式合成滚动行为画像（我们手搓的工作时段/双峰做饭/睡眠/夜猫子画像），避免每次重新推导。
- `get_behavior_insights(compare_days=N)` — 周/月环比 delta，目前为单窗口，无法看趋势。
- `explain_insight(insight_id)` — 给定洞察返回底层证据事件（溯源），便于引用与置信度判定。

**B. 自定义活动/模式（与写回双通道）**
- `define_activity(name, rule)` — agent 教系统新行为模式（如"午睡 = 主卧 occupancy 静默 12:00–15:00 >30min"），让 `infer_activities` 可识别。这比纯语义写回更精确。
- `infer_activities(activities=[...])` — 指定要识别的活动列表。

**C. 数据质量一体视图**
- `get_data_quality()` — 把分散的 `battery_backflow` / `unit_conflict` / `heartbeat_counter` / `stale` 合成一份质量报告（测试时它们是散落 P2 项，难以一眼掌握）。

**D. 主动推送（事件驱动）**
- `subscribe_insights(topics=[anomaly, climate_session])` / webhook — 新异常或气候会话出现时主动推，agent 从轮询变事件驱动。

**E. 检索路由可控**
- `ask_memory(q, route='auto|structured|semantic', return_hints=true)` — agent 可指定引擎并拿回 `semantic_hints` / `route_used`，便于调试与混合。

**F. 全量遍历**
- `search_events(all=True)` — 自动翻页（曾遇分页被 `min(limit,5000)` 吃掉的隐患，静默丢数据）。

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| agent 幻觉写入毒化库 | 溯源绑定 + staging + 矛盾检测 |
| 整库报废 | 命名空间隔离 + 追加墓碑 + 一键回滚 |
| 置信度虚高 | 外部信号鉴定（交叉印证 + 检索实效性） |
| 陈旧结论误导 | TTL 自动过期 + 重新确认机制 |
| 与结构化层重复计数 | agent 记忆打 `source=agent` 标签，检索时与结构化结论去重 |
