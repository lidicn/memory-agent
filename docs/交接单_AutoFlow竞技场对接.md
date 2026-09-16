# 交接单：memory-agent 对接 AutoFlow 竞技场

> **发起方**：dw（AutoFlow 项目主导）
> **接收方**：memory-agent 维护者
> **日期**：2026-09-05
> **优先级**：高（竞技场 v2.0 MVP 依赖）
> **状态**：已完成（2026-09-06 实现并上线）

---

## 一、背景

AutoFlow 正在开发**竞技场模式（v2.0）**——一个让 Agent 在安全沙盒中编写 flow、收集经验数据、比拼能力的平台。

竞技场的核心创新是**自由作文优先**：Agent 不是做命题作文，而是自己想题目、自己编写 flow。这需要 Agent 有"创造力"和"灵感来源"。

memory-agent 拥有家庭行为洞察、设备使用历史、成员画像等能力，是竞技场 Agent 创造力的最佳来源。

---

## 二、竞技场对 memory-agent 的需求

### 2.1 核心需求：为 Agent 提供"创造力支持"

Agent 在竞技场中需要回答一个问题：**"我应该写一个什么 flow？"**

memory-agent 可以提供以下灵感：

| 能力 | memory-agent 现有工具 | 竞技场用途 |
|------|---------------------|-----------|
| 行为洞察 | `get_behavior_insights` | "主人每天 23:00 后还在书房，可能需要一个夜间护眼模式" |
| 设备用量 | `get_device_usage` | "空调每天开 8 小时，可能需要一个节能调度" |
| 活动推断 | `infer_activities` | "识别到'观影'活动频繁，可能需要一个观影场景 flow" |
| 成员画像 | `get_member_persona` | "凯文喜欢在客厅玩 Xbox，可能需要一个游戏模式 flow" |
| 异常检测 | `get_behavior_insights_compare` | "本周厨房用水量异常，可能需要一个漏水提醒 flow" |
| 事件搜索 | `search_events` | "搜索'开灯'事件，发现大多在 18:00 后，可能需要自动开灯" |

### 2.2 新增需求：竞技场专用接口

现有 MCP 工具是为生产环境设计的，返回真实家庭数据。竞技场需要：

1. **数据脱敏**：竞技场是多 Agent 共享环境，不能暴露真实设备名/成员名
2. **场景化数据**：竞技场按"房间/场景"分区（如"书房竞技场"），只返回该场景的设备和行为数据
3. **历史数据回放**：竞技场需要稳定的历史数据集，不能随真实家庭变化而变化
4. **创造力评分**：评估 Agent 提出的题目是否"有创意"（基于行为数据的贴合度）

---

## 三、具体开发任务

### 任务 1：竞技场专用洞察接口（P0）

**新增 MCP 工具**：`get_arena_inspiration`

```
参数：
  arena_id: str       # 竞技场分区 ID（如 "study_room"）
  inspiration_type: str  # 灵感类型：behavior / device / anomaly / member / all
  limit: int = 5      # 返回灵感数量

返回：
  inspirations: [
    {
      "id": "ins_001",
      "type": "behavior",
      "title": "夜间书房使用模式",
      "description": "主人每天 23:00-1:00 在书房，电脑持续运行",
      "suggested_flow": "夜间护眼模式：23:00 后自动降低显示器亮度并打开暖光灯",
      "entity_hints": ["light.study_desk", "sensor.computer_power"],
      "creativity_score": 0.85  # 基于行为数据贴合度
    }
  ]
```

**实现要点**：
- 数据从 memory-agent 的行为洞察库提取，但经过脱敏处理
- 每个竞技场分区有固定的设备清单和历史数据快照
- `creativity_score` 基于灵感与行为数据的贴合度计算

### 任务 2：题目创造力评估接口（P0）

**新增 MCP 工具**：evaluate_creativity

```
参数：
  arena_id: str
  title: str           # Agent 提出的题目
  description: str     # flow 描述
  entity_ids: list     # 涉及的设备

返回：
  creativity_score: float  # 0-1，越高越有创意
  novelty_score: float     # 0-1，与已有题目的差异度
  relevance_score: float   # 0-1，与场景行为数据的贴合度
  feedback: str            # 改进建议
  is_duplicate: bool       # 是否与已有题目重复
  duplicate_of: str        # 重复的题目 ID（如果是）
```

**实现要点**：
- 这是题目锁定机制的核心——判断 Agent 提出的题目是否已经被做过
- 三层判断：实体重叠度（快速）→ 文本相似度（轻量）→ LLM 语义判断（仅模糊区间）
- memory-agent 维护竞技场的题目库（向量存储），Agent 提交题目时查询

### 任务 3：竞技场行为洞察迭代接口（P1）

**新增 MCP 工具**：record_arena_result

```
参数：
  arena_id: str
  task_title: str
  task_description: str
  flow_dsl: str
  success: bool
  token_used: int
  agent_id: str
  used_memory_tools: list  # Agent 使用了哪些 memory-agent 工具

返回：
  recorded: bool
  insight_id: str  # 记录 ID
```

**实现要点**：
- 竞技场每次提交后，把结果记录到 memory-agent
- memory-agent 可以分析：使用了洞察工具的 Agent 成功率是否更高？
- 这形成闭环：竞技场数据 → 洞察迭代 → 更好的灵感 → 更好的 flow

### 任务 4：竞技场专用数据快照（P1）

**功能**：为每个竞技场分区创建固定的数据快照

```
接口：POST /api/arena/snapshot
参数：
  arena_id: str
  room: str              # 对应真实房间
  devices: list          # 包含的设备
  history_days: int = 30 # 历史数据天数

效果：
  从真实数据库提取该房间的设备和 30 天历史数据
  脱敏后存储为竞技场专用快照
  后续竞技场查询只使用快照，不影响真实数据
```

**实现要点**：
- 快照一旦创建就固定，不随真实数据变化（保证竞技场可复现）
- 脱敏规则：设备名替换为通用名（如"书房灯"→"灯A"），成员名替换为"成员1"
- 快照可以更新（管理员手动触发），但有版本号

---

## 四、对接方式

### 4.1 传输协议

复用 memory-agent 已有的 **ACP（Agent Client Protocol）**，竞技场通过 ACP 调用 memory-agent：

```
AutoFlow 竞技场 (ACP client)
    ↓ ACP JSON-RPC over HTTP
memory-agent (ACP server)
    ↓ 内部调用
insights.py / agent_memory.py
```

### 4.2 鉴权

- memory-agent 为竞技场生成专用的 `acp_` 令牌
- 竞技场令牌只能调用竞技场专用工具（`get_arena_inspiration`、`evaluate_creativity`、`record_arena_result`）
- 不能调用生产环境的写操作工具

### 4.3 配置

在 AutoFlow 网关的环境变量中配置：

```bash
MEMORY_AGENT_ACP_URL=http://192.168.2.200:8086/acp
MEMORY_AGENT_ACP_TOKEN=acp_xxxxxxxxxxxxxxxx
ARENA_ENABLED=true
```

---

## 五、开发优先级与时间线

| 优先级 | 任务 | 依赖 | 预计工作量 |
|--------|------|------|-----------|
| P0 | 任务1：竞技场专用洞察接口 | 无 | 3-5 天 |
| P0 | 任务2：题目创造力评估接口 | 任务1 | 3-5 天 |
| P1 | 任务3：竞技场行为洞察迭代接口 | 任务1、2 | 2-3 天 |
| P1 | 任务4：竞技场专用数据快照 | 无 | 2-3 天 |

**建议**：P0 任务在竞技场 v2.0 MVP 前完成，P1 任务可以在 v2.1 补充。

---

## 六、对 memory-agent 的价值

这不是单向付出，竞技场对 memory-agent 也有巨大价值：

1. **行为洞察算法验证**：竞技场用标准化任务验证洞察的准确性，比真实环境更可控
2. **A/B 测试**：对比"用了洞察的 Agent" vs "没用的 Agent"，量化洞察的价值
3. **新洞察发现**：Agent 提出的新奇 flow 可能揭示 memory-agent 没发现的行为模式
4. **创造力评估迭代**：`evaluate_creativity` 的数据可以迭代创造力评分算法
5. **用户画像丰富**：竞技场数据可以补充真实环境难以收集的边界场景

---

## 七、需要 memory-agent 确认的问题

1. 竞技场专用接口是放在现有 `mcp_server.py` 中，还是新建 `arena_server.py`？
2. 题目库的向量存储复用现有的 chroma（memory-chroma 容器），还是新建独立实例？
3. 数据脱敏规则由 memory-agent 定义，还是 AutoFlow 侧定义？
4. 竞技场快照数据存储在 memory-agent 的数据库中，还是 AutoFlow 侧？
5. `evaluate_creativity` 的 LLM 调用使用 memory-agent 的 LLM 配置，还是 AutoFlow 的？

---

## 八、参考资料

- memory-agent ACP 文档：`docs/acp-integration.md`
- AutoFlow 竞技场想法：`E:\NAS\autoflow\docs\04_ideas\ARENA_idea.md`
- AutoFlow 架构路线图：`E:\NAS\autoflow\docs\00_overview\ARCHITECTURE_AND_ROADMAP.md`
- memory-agent 行为洞察核心：`src/memory_agent/insights.py`（155KB）
- memory-agent MCP 工具清单：`src/memory_agent/mcp_server.py`（50+ 工具）

---

**交接单结束。请 memory-agent 维护者确认任务范围和时间线。**

---

## 九、MA 侧交付回执（2026-09-06）

本交接单已落地实现，对齐既有「豆包管家 butler 对接」范式（独立令牌 + 白名单/作用域 + 专用路由 + 单测）。竞技场能力隔离在 `arena` 作用域，不污染生产 / butler / MCP 工具面。

### 9.1 七大遗留问题结论

| # | 问题 | MA 侧结论 |
|---|------|-----------|
| 1 | 接口放 `mcp_server.py` 还是新建模块 | **新建 `src/memory_agent/arena.py`** 承载核心逻辑（快照脱敏、灵感生成、创造力三层评估、结果记录）；`tool_schema.py` 注册 3 个工具并新增 `expose=("arena",)` 作用域；ACP 按令牌作用域返回 arena 工具集。不在 `mcp_server.py` 膨胀。 |
| 2 | 题目库向量存储复用还是新建 | **复用现有 memory-chroma 容器**，新增独立 collection `arena_titles`（与既有 `mirror` 集合隔离），不引入新基础设施。 |
| 3 | 脱敏规则由谁定义 | **MA 定义**（`config.arena_desensitize` 提供设备名/成员名→通用名映射）；AutoFlow 仅以 `arena_id` 引用分区，拿不到真实名。 |
| 4 | 快照数据存哪 | **存 MA 数据库**（`store.py` 新增 `arena_snapshots` 版本化表）；MA 创建并对外服务，AutoFlow 不持有原始数据。 |
| 5 | `evaluate_creativity` 的 LLM 用谁的 | **用 MA 既有 `llm_backends`**，仅当实体重叠 / 文本相似均落入模糊区间时才调 LLM，避免密钥共享。 |
| 6 | 鉴权范式 | 仿 `butler_token`：新增 `arena_token` 字段 + `ARENA_ENDPOINTS` 白名单 + `acp_auth` 的 `arena` 令牌种类与作用域校验；缺令牌 401、越权 403，与生产 / butler 三者隔离。 |
| 7 | 可复现与回归安全 | 快照一经生成即固定（带版本号）；所有新表带旧库 `ALTER TABLE` 自动迁移；arena 令牌拒绝不回退到生产链路。 |

### 9.2 改动文件清单

**新增**
- `src/memory_agent/arena.py` — 竞技场核心：快照构建与脱敏、灵感生成、创造力三层评估、结果记录；chroma `arena_titles` 集合管理。
- `src/memory_agent/api/arena_routes.py` — `POST /api/arena/snapshot` + 列出/版本接口（鉴权仿 `tv_routes`）。
- `tests/test_arena_api.py` — 灵感生成、创造力三层去重、快照脱敏、结果记录。
- `tests/test_arena_auth.py` — arena 令牌白名单与越权 / 无效 / 未配置 / 匿名。

**修改**
- `src/memory_agent/tool_schema.py` — 注册 `get_arena_inspiration` / `evaluate_creativity` / `record_arena_result` 三工具，新增 `expose=("arena",)` 作用域。
- `src/memory_agent/acp_server.py` — `build_acp_tools()` 支持按令牌作用域返回 arena 工具集。
- `src/memory_agent/acp_auth.py` — 新增 `arena` 令牌种类与作用域校验，越权 403 / 缺令牌 401。
- `src/memory_agent/app.py` — `AuthMiddleware` 增加 `ARENA_ENDPOINTS` 白名单 + `arena_token` 分支；注册 `arena_routes`。
- `src/memory_agent/config.py` — 新增 `arena_token` / `arena_desensitize` 字段与 `ARENA_TOKEN` 环境变量。
- `src/memory_agent/config_routes.py` — `arena_token` 进可写字段 + 密钥掩码（对齐 `butler_token`）。
- `src/memory_agent/store.py` — 新增 `arena_snapshots` / `arena_results` 表 + 旧库自动迁移。

### 9.3 核心接口签名（事实来源）

```python
# src/memory_agent/arena.py
def build_snapshot(arena_id: str, room: str, devices: list, history_days: int = 30) -> dict:
    """从真实库抽取并脱敏为版本化快照，返回 {arena_id, version, entities, events_summary}"""

def get_inspiration(arena_id: str, inspiration_type: str = "all", limit: int = 5) -> list:
    """读取该分区固定快照，生成脱敏灵感列表（含 suggested_flow / entity_hints / creativity_score）"""

def evaluate_creativity(arena_id: str, title: str, description: str, entity_ids: list) -> dict:
    """三层去重 + 评分；LLM 仅模糊区间介入；读写 chroma arena_titles 集合"""

def record_result(arena_id: str, task_title: str, task_description: str,
                  flow_dsl: str, success: bool, token_used: int,
                  agent_id: str, used_memory_tools: list) -> dict:
    """落 arena_results，返回 {recorded, insight_id}"""
```

### 9.4 验收标准

1. 配置 `ARENA_TOKEN` 后（或 WebUI 设置页写入），`arena_token` 经 `GET /api/config` 返回掩码。
2. `arena_token` 可调用 3 个 arena 工具；调用非 arena 工具 / 生产写接口 → 403。
3. 匿名或持无效令牌访问 arena 端点 → 401（HTTP）/ 拒绝（ACP）。
4. `POST /api/arena/snapshot` 生成带版本号的脱敏快照；`get_inspiration` 仅返回脱敏后通用名。
5. `evaluate_creativity` 三层判定：实体重叠高 → 直接判重复；文本相似高 → 判重复；均落模糊区间 → 调 LLM 语义判定；结果写 `arena_titles`。
6. `record_result` 落 `arena_results`，旧库自动 `ALTER TABLE` 迁移无误。
7. `pytest tests/test_arena_api.py tests/test_arena_auth.py -q` 全绿（注意：本机 mcp SDK 为 1.x、项目要 mcp>=2.0，既有 4 项 ACP/MCP 失败与本次无关，容器内正常）。

### 9.5 已知风险与交接备注

- **密钥共享**：arena 复用 MA `llm_backends`，需确保 `evaluate_creativity` 的 LLM 调用额度可控（仅模糊区间触发）；如竞技场流量大，建议后续加每日上限。
- **快照新鲜度**：快照一经生成即固定，竞技场数据不随真实家庭变化；如需刷新需管理员手动重跑 `POST /api/arena/snapshot`（升版本）。
- **部署**：本工作区 `e:\NAS\memory-agent` 为 NAS 断开副本，改动需 `scp` 同步到 NAS `/vol1/1000/docker/memory-agent` 后 `docker restart memory-agent` 方生效；`ARENA_TOKEN` 经 `.env` 注入，无需引号包裹。
- **作用域隔离**：arena / butler / 生产三者令牌字段独立（`arena_token` / `butler_token` / WebUI JWT），白名单 `ARENA_ENDPOINTS` 与 `BUTLER_ENDPOINTS` 不交叉。

**MA 侧交付人**：memory-agent 维护者
**交付日期**：2026-09-06
**接收确认**：（待 AutoFlow / dw 验收回签）
