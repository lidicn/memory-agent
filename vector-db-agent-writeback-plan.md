# 向量库 Agent 参与式写回 — 落地实施方案（v2，已按评审修订）

> 状态：提案（待评审，未写代码）
> 依据：`vector-db-agent-iteration-feasibility.md` + 对 `history.py` / `insights.py` 现状的逐行核对（行号于 2026-08-04 复核，全部成立，见 §1）
> 范围：在现有 `memory-worker` 上引入"Agent 安全写回向量库"能力；与「全栈重构」计划正交，作为独立工作流
> 修订记录：v2 补入 #1 矛盾检测、#2 trust 重排、#3 声誉回路、#4 自动晋升 sweep；夯实 #5 真溯源、#6 promote 联动、#7 force 权限；修正 #8 metadata 类型、#9 两写一致性、#10 命名。

---

## 0. 一句话目标

让 Agent 把从结构化洞察中挖出的行为知识**安全回流进 chroma**，且**物理上不可能搞垮整库**；同时把现有"裸 `col.query`"升级为带命名空间 / 状态 / 信任加权的可控检索。**关键新增（v2）**：一条能让写回"真的被用上"的自动晋升与重排链路，否则系统"能写但不能用"。

---

## 1. 现状接线（已核实，作为方案基线）

下表行号于 2026-08-04 对照真实源码复核，全部成立（评审 #11 已结清）：

| 关注点 | 现状（file:line） | 问题 |
|---|---|---|
| 集合 | `history.py:28` `COLLECTION_NAME = "behavior_history"`，单一集合 | 无命名空间，系统种子与 agent 写入混在一起 |
| 写入方 | `history.py:261-294` `mirror_days()` 仅 poller 调用，写「天×房间」摘要 | agent 零写入通道；嵌入粒度=日级房间，过粗 |
| 语义检索 | `history.py:296-309` `semantic_search()` 直接 `col.query(query_texts=[q])` | 无 `where` 过滤、无 trust 加权、无状态隔离；staging 垃圾直接污染 |
| 问答集成 | `insights.py:2058-2102` `ask_memory()`：cooking/climate/media 分支提前 `return`，`semantic_hints` 仅在默认分支由 `rt.history.semantic_search` 附加 | 向量库只当副驾证据，`semantic_used` 看有无命中 |

> 默认分支证据（`insights.py:2086-2094`）：命中结构化路由后、合成回答前，才跑 `semantic_search` 挂 hints。这印证"向量库只是副驾"，且 agent 完全无法写回。

结论：报告第 1–2 节诊断**准确**，方案必须新增一套与 `behavior_history`（=系统种子）隔离的 agent 记忆子系统。

---

## 2. 架构决策

### 2.1 命名空间隔离（报告 3.1）
- **保留** `behavior_history` 集合作为 `system_memory`，**永不**被 agent 触碰。
- **新增** `agent_memory` 集合，所有 agent 写入落这里；`ask_memory` 检索时 `behavior_history`（永远）+ `agent_memory`（仅 live）并联。
- `agent_memory` 集合内用 `metadata.state` ∈ {`staging`, `live`, `revoked`, `pending_review`} 区分；`col.query(where={"state":"live"})` 天然排除 staging/revoked/pending_review。
- **trust 加权机制（v2 #2，修正原"按 metadata 加权"的不实说法）**：chroma `query` 只能按 `where` 做相等过滤，**不能**按 metadata 数值做相似度加权。因此检索分两步——(1) `col.query(where={"state":"live"}, n_results=RETRIEVE_K=20)` 先**过取** top-20；(2) 客户端按 `final = α·cosine_sim + β·trust_norm`（α=0.7, β=0.3，trust 映射到 [0,1]）**重排**取 top-5。重排逻辑在 `AgentMemoryService.rerank()`，不依赖 chroma 的加权能力。另提供 `trust_min` 硬过滤开关（`where trust >= trust_min`）作为可选简化路径。

### 2.2 元数据权威源放 SQLite，向量只存镜像（报告 3.2/3.4 落地关键）
- 新增 SQLite 表 `agent_memories` 存权威元数据（state / trust / ttl / source_refs / feedback / 时间戳 / topic_key / 晋升控制位 / 镜像脏位）。
- chroma `agent_memory` 仅存**标量** `metadata`（见 §3.2），作为检索镜像。
- 状态变更（晋升 / 墓碑 / 反馈）→ 先改 SQL → 再 `col.upsert(metadata=...)` 同步镜像。
- **两写一致性（v2 #9）**：SQL 改成功后若 chroma `upsert` 抛错，**不吞掉**——置 `mirror_dirty=1` 并记日志；另设 `reconcile_agent_memory()` 周期任务（与 #4 sweep 同节拍）把所有 `mirror_dirty=1` 的 SQL 行幂等重同步到 chroma。检索缺口在 `get_data_quality` 暴露。

### 2.3 TTL 与信任分（报告 3.4 + 第 4 节）
- `expires_at = created_at + ttl_days`；检索/列出前过滤已过期（软视为 revoked）。
- `trust` 由外部信号推导，初始 0：被 `feedback_memory(useful=true)` 确认 → +；被踩 → −；命中即延长 TTL。
- **声誉回路（v2 #3，接通原断点）**：`add_semantic_memory` 写入前先查 `get_session_trust(session_id)`。若 trust < `TRUST_STRICT_THRESHOLD`（默认 -0.3）→ 该记忆置 `auto_promote_blocked=1`，**自动晋升 sweep（#4）直接跳过它**，只能由特权会话显式 `promote(force=True)` 推进 → 实现"某 session 多被踩 → 后续进更严 staging"。

---

## 3. 数据模型

### 3.1 SQLite `agent_memories`（新增，建表在 `store.py`）
```sql
CREATE TABLE IF NOT EXISTS agent_memories (
    memory_id          TEXT PRIMARY KEY,              -- sha1(session_id|text|created_at)
    session_id         TEXT NOT NULL,
    text               TEXT NOT NULL,
    topic_key          TEXT NOT NULL DEFAULT '',      -- 矛盾分组键（v2 #1）：同 key 不同主张即冲突候选
    tags_json          TEXT NOT NULL DEFAULT '[]',     -- 仅存 SQL；chroma 不放 list（v2 #8）
    source_refs_json   TEXT NOT NULL DEFAULT '[]',     -- 必须非空且每条可解析（v2 #5），否则拒收
    state              TEXT NOT NULL DEFAULT 'staging',  -- staging|live|revoked|pending_review
    trust              REAL NOT NULL DEFAULT 0.0,
    ttl_days           INTEGER NOT NULL DEFAULT 30,
    auto_promote_blocked INTEGER NOT NULL DEFAULT 0,  -- v2 #3：声誉差则锁自动晋升
    mirror_dirty       INTEGER NOT NULL DEFAULT 0,    -- v2 #9：chroma 同步失败标记
    feedback_up        INTEGER NOT NULL DEFAULT 0,
    feedback_down      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    expires_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_mem_state ON agent_memories(state);
CREATE INDEX IF NOT EXISTS idx_agent_mem_session ON agent_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_mem_topic ON agent_memories(topic_key);
```

### 3.2 chroma `agent_memory` 集合（新增，**全部标量 metadata**，v2 #8）
- id = `memory_id`；document = `text`；
- metadata = `{kind:"agent_memory", state, trust, expires_at, session_id, memory_id, topic_key}`
- **tags 不放 chroma**（旧版 chroma 只接受 str/int/float/bool，list 会报错）；tags 仅存 SQL `tags_json`，需要时在 SQL 层过滤。

---

## 4. 模块与文件改动清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/memory_worker/store.py` | **新增** `agent_memories` 建表/索引 + CRUD（`add_agent_memory` / `set_agent_memory_state` / `record_agent_feedback` / `list_agent_memories` / `list_expired_agent_memories` / `mark_mirror_dirty` / `get_session_trust`） | 元数据权威源 |
| `src/memory_worker/agent_memory.py` | **新增** `AgentMemoryService`：封装工具逻辑 + **冲突/重复检测**（v2 #1）+ **溯源校验**（v2 #5）+ **晋升判定**（联动 insight，v2 #6）+ **re-rank**（v2 #2）+ `sweep_promote_candidates`（v2 #4）+ `reconcile`（v2 #9） | 核心业务 |
| `src/memory_worker/history.py` | `collection` 工厂增加 `agent_collection` getter；`semantic_search` 增加 `state` / `kind` / `trust_min` 过滤参数 | 检索侧隔离 |
| `src/memory_worker/insights.py` | `ask_memory` 接入 `agent_memory`（live + re-rank，v2 #2）；新增 `route` / `return_hints` 参数（报告 7.E）；`get_data_quality` 暴露 mirror_dirty 缺口（v2 #9） | 检索侧升级 |
| `src/memory_worker/mcp_server.py` | **新增 8 个 MCP 工具**（§5，含 `sweep_promote_candidates` 触发入口） | 写回通道 |
| `src/memory_worker/runtime.py` | `AppRuntime` 持有 `agent_memory: AgentMemoryService`；注册每日 sweep + reconcile 定时任务（v2 #4/#9） | 单例 + 调度打通 |
| `config`（新增项） | `PRIVILEGED_SESSIONS`、`AGENT_PROMOTE_MIN_DAYS=3`、`AGENT_TRUST_STEP=0.2`、`TRUST_STRICT_THRESHOLD=-0.3`、`RETRIEVE_K=20`、`CONFLICT_SIM=0.85`、`DUP_SIM=0.92` | 阈值集中管理 |

> 注：`mcp_server.py` 当前为手写 `MCPServer`，重构计划将整体迁 `FastMCP`。本方案工具直接挂在**届时已存在的 MCP 框架**上，不绑死当前/目标形态，迁移时平移即可。

---

## 5. 新增 MCP 工具接口（报告第 5 节，v2 补全缺失机制）

所有工具统一返回 `{"ok":bool, ...}`；失败不抛裸异常，返回 `{"ok":False,"error":...}`。

### 5.1 `add_semantic_memory(text, tags=[], source_refs=[], ttl_days=30, topic_key="", dry_run=True)`
- **溯源校验（v2 #5，真溯源而非非空即过）**：`source_refs` 每条须为 `event:<event_id>`（经 `store.get_event` 可解析）或 `insight:<insight_id>`（库内 insights 表存在）。非空 **且** 每条可解析才收，否则 `ok:False` 拒收（表演性溯源到此为止）。
- **声誉回路（v2 #3）**：写入前查 `get_session_trust(session_id)`；若 trust < `TRUST_STRICT_THRESHOLD` → `auto_promote_blocked=1`。
- `dry_run=True`（**默认**）：仅跑冲突/重复检测，**不落库**：
  - 对 `agent_memory` 集合 `query` 取 top-3 相似，附 `similarities` 与命中 `memory_id`；
  - 返回预览 `{will_embed, conflicts, is_duplicate, preview_id}`，agent 可先自检。
- `dry_run=False`：写 SQL（state=staging）+ chroma upsert（state=staging），返回 `memory_id`。**永不进 live**（晋升必须由 §5.2 / §5.8 完成）。

### 5.2 `promote_memory(memory_id, force=False, corroborating_insight_id=None)`
- **权限（v2 #7）**：`force=True` **仅限** `session_id ∈ PRIVILEGED_SESSIONS`，否则 `ok:False, error:"需要特权会话"`。非特权只能走条件晋升。
- **晋升条件（报告 4. 交叉印证）**，三者满足其一：
  - (a) **联动机制（v2 #6，已定义）**：传入 `corroborating_insight_id`，系统校验该 insight 存在、`confidence >= 0.7`、创建于记忆时间窗内（±1 天）、且 `embedding_sim(memory.text, insight.text) >= 0.6`；
  - (b) 跨 ≥ `AGENT_PROMOTE_MIN_DAYS=3` 天反复观测（按 `source_refs` 中 event 的日期跨度判定）；
  - (c) 特权会话 `force=True`（需 (a)/(b) 均不满足时显式兜底）。
- **矛盾检测（v2 #1，补原缺失）**：晋升前跑 `conflict_scan(memory)`——
  - 查 live 记忆中同 `topic_key` 者；
  - 相似度 `> DUP_SIM(0.92)` → 判为**重复**，返回 `is_duplicate`，不自动晋升（提示 merge/revoke）；
  - 同 `topic_key` 且文本 `sim < 0.5`（主张相左）→ 判为**冲突**，置 `state=pending_review`（人工/特权复核），不进 live。
  - > 全自动矛盾语义判定精度有限，MVP 以"同 topic_key + 主张相左"近似；精细语义矛盾推理列为 Phase 2。
- 通过且无误 → SQL `state=live` + chroma upsert `state=live`；返回新 trust。

### 5.3 `revoke_memory(memory_id)`
- **墓碑**：SQL `state=revoked` + chroma upsert `state=revoked`（置 `mirror_dirty` 走同步）。不硬删，保留审计。

### 5.4 `rollback_agent_memory(session_id)`
- 该 session 下所有 memory 置 `revoked`（保留审计轨迹）；返回受影响条数。

### 5.5 `feedback_memory(memory_id, useful: bool)`
- `feedback_up/down ++`；`trust` 按 `useful` ±`AGENT_TRUST_STEP=0.2`（夹在 [-1,1]）；
- `useful=True` → `expires_at` 顺延 `ttl_days`；`useful=False` → 缩短一半；返回更新后 trust/expires_at。
- 若 `useful=False` 且 trust 跌破阈值，回写 `auto_promote_blocked`（闭环声誉回路，v2 #3）。

### 5.6 `list_agent_memories(state="all")`
- 审计视图：从 SQL 读（默认按 `updated_at` 倒序），含 trust / expires_at / feedback / mirror_dirty；`state` 可筛 `staging|live|revoked|pending_review|all`。

### 5.7 `get_session_trust(session_id)`  ←（v2 #10 重命名，原 `get_agent_trust(agent_id)`）
- **按 `session_id` 聚合**：平均 trust、live 占比、被踩率 → 评分；驱动 §5.1 的声誉回路（"某 session 多被踩 → 后续进更严 staging"）。
- 命名纠正原因：原 `agent_id` 既未存表、数据也按 session 聚合，改名避免误导。表内不新增 `agent_id` 字段（数据本质 session 作用域）。

### 5.8 `sweep_promote_candidates()`（v2 #4，救活写回的关键）
- **问题**：§5.1 写死"永不进 live"，§5.2 又依赖 (a)/(b)/(c)。若无定时 revisit，(a)/(b) 永远不被复评 → 所有 agent 记忆永久堆在 staging，`ask_memory` 永不可见 → **写回等于白做**。
- **机制**：每日定时（runtime 调度）扫描 `state=staging AND auto_promote_blocked=0` 的记忆，对每条重跑晋升条件——
  - (b) 跨天观测：按 `source_refs` 事件日期跨度是否达 `AGENT_PROMOTE_MIN_DAYS`；或
  - (a) 系统重跑 `infer_activities` 对同期数据，若高置信产出同 `topic_key` 结论（embedding 比对 ≥ 0.6）→ 视为印证；
  - 满足且 `conflict_scan` 通过 → 自动 `promote`（无需 force）。
- 提供手动触发入口（MCP 工具同名），便于测试与即时复评。
- 与 `reconcile_agent_memory()` 同节拍运行，保证晋升后镜像同步（v2 #9）。

---

## 6. 检索侧改动（让写回"被用上"）

`insights.ask_memory(question, days=7, route="auto", return_hints=False, trust_min=None)`：
- `route="auto|structured|semantic"`：auto=现有关键词路由；semantic=纯向量；structured=只走结构化工具。
- 向量检索改为并联 `behavior_history`(系统种子，永远) + `agent_memory(where={"state":"live"})`；
  - **re-rank（v2 #2）**：过取 top-`RETRIEVE_K` 后按 `α·sim + β·trust` 重排取 top-5；`trust_min` 给定时改走 `where trust >= trust_min` 硬过滤。
- `return_hints=True` 时额外回 `route_used` 与 `semantic_hints`（报告 7.E 调试/混合需求）。

---

## 7. 报告第 7 节功能映射（建议分期，非 MVP 必交付）

| 报告条目 | 落地建议 | 归属 |
|---|---|---|
| 7 个写回工具（add/promote/revoke/rollback/feedback/list/trust） | **MVP 核心** | 本方案 §5 |
| `sweep_promote_candidates` 自动晋升（v2 #4） | **MVP 必含**（否则写回失效） | §5.8 |
| `ask_memory` route / hints / re-rank 控制 | **MVP（低风险）** | §6 |
| A. `get_user_persona(days)` / `get_behavior_insights(compare_days=N)` / `explain_insight(id)` | Phase 2：复用 `infer_activities` / `behavior_insights` 聚合，加环比与证据溯源 | 扩展 `Insights` |
| B. `define_activity(name, rule)` / `infer_activities(activities=[...])` | Phase 2：规则注册到 config/SQLite，注入 `infer_activities` 检测器 | 扩展 `insights` |
| C. `get_data_quality()` | **MVP 顺手做**：现状 `anomaly_report` 已含 `battery_backflow/unit_conflict/heartbeat_counter/stale`，包一层聚合；**并暴露 `mirror_dirty` 缺口（v2 #9）** | 扩展 `Insights` |
| D. `subscribe_insights` webhook | Phase 3：事件驱动推送，需引入发布订阅，范围最大 | 独立工作流 |
| F. `search_events(all=True)` | **已具备**：`insights._iter_all_events` 已分页绕过 5000 截断，MCP 层加 `all` 开关即可 | 小改 |

> **MVP 必交付** = §3 数据模型 + §4 模块 + §5 全部（含 §5.8 sweep）+ §6 route/hints/re-rank + `get_data_quality`（含 mirror_dirty）。其余为后续 Phase。

---

## 8. 与全栈重构计划的关系 & 实施顺序

- 当前「全栈重构」正在进行（`runtime` / `store` / `api` / FastMCP 迁移）。**本方案不阻塞重构，也不被其阻塞**：
  - `store.py` 的 `agent_memories` 表建表放在重构已规划的 `store.py` 内，避免二次改动；
  - `mcp_server.py` 的 8 个工具在 FastMCP 迁移完成后再挂，平移成本低；
  - `runtime` 的每日 sweep/reconcile 调度挂在重构已有的调度器上。
- 建议执行顺序：先等 `runtime`+`store` 落地（重构 todo #1）→ 本方案 `store` 建表 + `agent_memory.py`（含 conflict/溯源/re-rank/sweep/reconcile）→ MCP 工具挂 FastMCP → `ask_memory` 升级 → sweep 调度接入 → 验证。

---

## 9. 风险与验证（v2 已对齐 §5 具体机制）

| 风险 | 缓解（对应 §5 具体落点） |
|---|---|
| agent 幻觉毒化库 | 真溯源校验（§5.1 每条 ref 可解析）+ staging 不进检索 + **矛盾检测（§5.2 conflict_scan → pending_review / 重复 merge）** |
| 整库报废 | 命名空间隔离（behavior_history 永不被碰）+ 追加墓碑（§5.3）+ 一键 rollback（§5.4） |
| 置信度虚高 | trust 由 feedback/命中率外部推导，非自报（§5.5）；声誉差锁自动晋升（§5.1/§5.7） |
| 陈旧误导 | TTL 自动过期 + feedback 顺延/缩短（§5.5） |
| 与结构化层重复计数 | `kind="agent_memory"` 标签，检索与 `behavior_history` 结果去重展示 |
| 写回"能写不能用"（隐性失效） | **自动晋升 sweep（§5.8）** 让 staging 真正流向 live；re-rank（§6）让 live 真正影响 hints |
| 两写不一致（SQL live / chroma 不可见） | **mirror_dirty + reconcile（§2.2/§5.8）**，缺口在 get_data_quality 暴露（§7） |
| force 绕过护栏强推垃圾 | **force 限特权会话（§5.2）** |

**验证方法**：
1. 单测：`add_semantic_memory(dry_run=True)` 返回冲突预览不落库；`dry_run=False` 后 `list_agent_memories(state="staging")` 可见，`ask_memory` 仍**不**命中（state≠live）。
2. `promote_memory` 后 `ask_memory(q)` 的 `semantic_hints` 出现该记忆（re-rank 生效）；`revoke` 后消失（墓碑仍在 SQL）。
3. `rollback_agent_memory(session_id)` 整段置 revoked，审计视图可查。
4. **sweep 验证（v2 关键）**：造一条带跨 3 天 `source_refs` 的 staging 记忆 → 跑 `sweep_promote_candidates` → 应自动变 live 并被 `ask_memory` 命中；声誉差（trust<-0.3）的 session 记忆应被 sweep 跳过。
5. 兼容性红线：`behavior_history` 在 agent 全损时 `ask_memory` 仍返回结构化结果（降级）。
