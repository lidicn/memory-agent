# 架构设计：Memory Agent 对外接口与设备身份层

> 版本：v0.1（设计稿，未实现）
> 范围：本期只产出本设计文档，不写代码。实现顺序由评审后另定。
> 关联需求：HA 设备动荡治理（A1 重登漂移 / A2 双集成冗余 / A3 失效清单）、豆包管家生态联动（B）、对外接口选型（MCP / HTTP / MQTT）、记忆统一入库（C，留待下一期）、最优先功能（D = 身份层）。

---

## 1. 背景与目标

`memory-agent`（MA）当前的能力是**家庭行为记忆 + 洞察计算**：它从 HA 采集实体状态变化、落库、并按"行为洞察模板"算出使用时长 / 频次等结构化结论。对外，它已经通过三条通道提供服务：

- **WebUI**（JWT 鉴权）：人工查看与管理。
- **MCP**（`/mcp`，独立令牌中间件）：供外部 Agent（如豆包管家）以工具调用方式查询。
- **受限 HTTP 白名单**（`butler_token` / `arena_token`）：供特定外部服务窄接口访问。

但 MA 内部存在一个**结构性脆弱点**：行为洞察模板（`src/memory_agent/templates.py`）直接持有 HA 的原生 `entity_id`（如 `media_player.xiaomi_rmh1_6103_play_control`）。一旦 HA 侧实体发生动荡（重登、集成替换、双集成并存），`entity_id` 会漂移或倍增，模板与历史查询随之全部失效——这正是此前 "hdmi3 模板突然查不到数据" 的根因。

本设计的目标：

1. 在 MA 内部建立**设备身份 + 健康层（Entity Identity & Health）**，把"物理设备"与"易漂移的 `entity_id`"解耦，对外一律以**稳定逻辑设备名**为契约。
2. 定义 MA 向**豆包管家（MCP）/ TVPilot / DeskPilot（HTTP）/ 实时场景（MQTT）** 提供查询能力的统一接口与鉴权模型。
3. 明确生态边界：**MA = 家庭行为记忆单一事实源；豆包管家 = LLM 编排大脑；TVPilot / DeskPilot = 应用层消费者**。记忆统一入库（C）不在本期。

设计原则：**不暴露数据库**、**传输层可插拔但语义单一来源**、**逻辑身份稳定优先于底层实体稳定**。

---

## 2. 问题域：HA 实体动荡三场景

| 场景 | 现象 | 当前影响 | 目标行为 |
|------|------|----------|----------|
| **A1 重登漂移** | xiaomi miot 集成退出重登，`entity_id` 改变，`friendly_name` 接近或不变 | 模板写死的 `entity_id` 指向幽灵实体，查询返回空/0 | 按 `friendly_name` + `device_class` 自动重匹配到新实体，模板零断裂 |
| **A2 双集成冗余** | 同时接入 `[xiaomi home]` + `[xiaomi miot]`，同一台客厅电视出现两个实体 | 查询重复计数 / 用户困惑 | **全自动合并**为一个逻辑设备（按规则选主），用户无感 |
| **A3 失效 / 离线** | 模板引用的设备长期离线或已删除 | 静默产出错误洞察，无人知情 | 进入健康 / 墓碑表，WebUI 单列"失效设备"清单，可一键重匹配或禁用 |

> 注：A2 用户已确认采用**全自动合并**策略（高相似即合并，按规则选主）。需配合"误并兜底"（见 §8）降低风险。

---

## 3. 总体架构

```
                ┌──────────────────────────────────────────────┐
   HA 实体注册表 │  /api/states / /api/entities / /api/devices  │
   (friendly_    │  /api/areas  →  ha_client.discover_entities() │
    name/area/   └───────────────────────┬──────────────────────┘
    device_class)                         │ 对账任务(启动/周期/HA事件触发)
                                         ▼
                         ┌──────────────────────────────────────┐
                         │   Entity Identity & Health 层         │
                         │   • LogicalDevice 注册表             │
                         │   • 重匹配(A1) / 全自动合并(A2)      │
                         │   • 健康&墓碑表(A3)                  │
                         └───────────────┬──────────────────────┘
                                         │ 逻辑名→当前entity_id 解析
                                         ▼
                         ┌──────────────────────────────────────┐
                         │   模板 / 洞察引擎 (run_template)      │
                         │   唯一业务逻辑：时长/频次/会话计算    │
                         └───┬───────────────┬──────────┬───────┘
                             │               │          │
                ┌────────────▼──┐   ┌─────────▼──┐  ┌────▼─────────┐
                │ MCP /mcp      │   │ HTTP 查询   │  │ MQTT 推送    │
                │ 豆包管家       │   │ /api/insights/query │ ma/presence │
                │ (语义工具)     │   │ TVPilot/DeskPilot│ ma/device-  │
                └───────┬───────┘   └─────┬───────┘  │ usage       │
                        │                 │          └─────┬───────┘
                        ▼                 ▼                ▼
                  豆包管家(LLM)      TVPilot / DeskPilot   实时 UI 卡片
```

**单一事实源**：所有查询最终都经过 Identity 层解析 + 洞察引擎计算，不存在"绕过"的第二套逻辑。

---

## 4. Entity Identity & Health 层

### 4.1 LogicalDevice 模型

新增持久化模型（建议落 SQLite 表 `logical_devices`，与现有 `store.py` 同库）：

```text
LogicalDevice {
  stable_id        : str   # 稳定主键，如 "living_room_tv"（建时生成，永不改）
  display_name     : str   # 展示名，如 "客厅电视"（可改，仅展示用）
  device_class     : str   # 归一化类：tv / light / climate / purifier / sensor ...
  candidates       : list[{
                      entity_id,           # 当前绑定（或被候选）的 HA 实体
                      priority,            # 优先级，选主用
                      state,               # active / stale / disabled
                      last_seen,          # 最近一次在 HA 注册表出现的时间
                      last_data_ts,        # 最近一次在 MA 事件库有数据的时间
                      provenance           # auto-merged / user-pinned / discovered
                    }]
  primary_entity   : str   # 当前选主结果（查询解析用）
  created_at, updated_at
}
```

模板与查询**只引用 `stable_id` 或 `display_name`**，运行时由 Identity 层解析为 `primary_entity` 的 `entity_id`。

### 4.2 对账任务（数据来源已现成）

MA 已有 `ha_client.HAClient.discover_entities()`（`src/memory_agent/ha_client.py:261`），它枚举 HA 全部实体，并已经拿到：

- `friendly_name`（实体属性）
- `area`（来自 `/api/areas`、`/api/devices`、`/api/entities` 注册表，或 `area_name()` 模板回退）
- `domain`（即 `entity_id` 前缀，等价于粗粒度 `device_class`）

因此**无需新增 HA 取数代码**，对账任务直接复用 `discover_entities()` 的输出作为"HA 当前真实实体全集"。

新增 `IdentityReconciler`（建议放在 `src/memory_agent/identity.py`，或在 `poller.py` 同级的调度循环里加一个 `_reconcile_loop`），触发时机：

1. 应用启动后一次；
2. 周期触发（可复用 `CollectService._scheduler_loop` 的 30s tick，或在 `lifespan` 里另起低频任务，如每 10 分钟）；
3. HA 实体明显变化事件（可选：监听 HA `entity_registry_updated` 事件，或采集任务完成后顺带触发）。

对账是**后台低频只读任务**，不阻塞在线查询；解析走内存缓存 + 失效回源。

### 4.3 A1 重登漂移：重匹配规则

对每个 `LogicalDevice`，在 HA 实体全集中寻找"同一物理设备"的当前实体：

- **匹配键**：`normalized(friendly_name)` + `device_class(domain)`；同 area 内同名同类的实体视为同一设备。
- **相似度**：对 `friendly_name` 做归一化（去空格、去品牌前缀、小写）后做包含 / 编辑距离判定；同类设备 `friendly_name` 高度相似（≥ 阈值，如 0.85）即判定为同一设备。
- **行为**：若原 `primary_entity` 已不在 HA 注册表（A1 漂移），但在同 area + 同类中找到 `friendly_name` 高度相似的实体 → 自动把 `primary_entity` 指向新实体，记录 `provenance=auto-remapped`，写审计日志。
- **用户钉选优先**：若某候选 `provenance=user-pinned`，则不自动改，仅提示。

### 4.4 A2 双集成冗余：全自动合并（用户已确认）

当 HA 全集中出现**多个实体命中同一 LogicalDevice 的匹配键**时，执行合并：

1. 全部候选收进 `candidates[]`，状态置 `active`。
2. **选主规则**（按序，命中即停）：
   - (a) `state == online`（HA 当前 `state` 非 `unavailable/unknown`）者优先；
   - (b) 被模板 / 查询引用频次更高者优先（统计 `templates.entities` 中引用次数）；
   - (c) `priority` 显式设定者优先；
   - (d) `entity_id` 字典序最小者兜底。
3. 选主结果写入 `primary_entity`；落"合并审计日志"（谁并入谁、时间、触发源）。
4. 查询解析一律走 `primary_entity`，避免重复计数。

> 误并风险见 §8。兜底手段：合并审计日志可回滚（拆回独立 LogicalDevice）；用户可在 WebUI 手动"拆分"。

### 4.5 A3 失效 / 离线清单（健康 & 墓碑表）

复用现有 MCP 工具 `get_device_health`（`mcp_server.py` 已有，按 room/category/stale_days 返回健康）——在其结果上扩展"被模板引用但已失效"的标记。逻辑：

- 任一候选 `last_seen` 超过 `STALE_DAYS`（默认 30 天）未出现在 HA 注册表，或 `last_data_ts` 超过阈值无新事件 → 标记 `stale`。
- 若该 LogicalDevice 仍被某个模板 `entities` 引用 → 额外标记 `referenced_but_stale = true`，即"此模板可能已不准"。
- 新增持久化"墓碑 / 健康表"（可并入 `logical_devices` 的 `state` 字段，或独立 `device_health` 表），供 WebUI 与 MCP `get_device_health` 读取。

### 4.6 模板与查询改写

当前 `run_template`（`templates.py:399`）遍历 `tpl.entities`，直接用 `eq.entity_id` 调 `ins._usage_one(eid, ...)`。改造点：

- `EntityQuery`（`templates.py:13`）新增可选字段 `logical_id`（逻辑设备引用）。**兼容期**：`entity_id` 仍可用（灰度过渡），解析时若 `logical_id` 存在则先用 Identity 层解析为 `entity_id`（可能 1→N，则展开多实体分别计算后聚合）。
- `run_template` 入口加一步 `resolve_entity(eq)`：逻辑名 → 当前 `entity_id`（含 A2 多候选展开）。解析失败 / 全 stale → 在 `entities_out` 里返回 `{error: "设备已失效或待重匹配", stale: true}`，**不静默丢弃**，让上层知情。
- 内置模板（如 `xbox_daily_usage` 写死 `media_player.xiaomi_rmh1_6103_play_control`，`templates.py:108`）迁移为引用逻辑设备 `stable_id`，从此免疫 entity_id 漂移。

### 4.7 WebUI 呈现

在设置 / 设备页新增：

- **逻辑设备清单**：列出所有 LogicalDevice、主实体、候选数、状态（active/stale/disabled）、最近数据时间。
- **失效设备专区**：`referenced_but_stale=true` 的条目高亮，提供"重新匹配 / 禁用相关模板 / 拆分合并"操作。
- **合并审计**：展示历史自动合并记录，支持回滚。

---

## 5. 对外接口层

MA 已有完整的多令牌隔离鉴权（`app.py` 的 `AuthMiddleware` 支持 `butler` / `arena` 两套窄白名单令牌）。新增接口**完全复用该模式**，不引入新鉴权机制。

### 5.1 豆包管家 — MCP（已有，语义化增强）

MA 的 MCP 服务端 `src/memory_agent/mcp_server.py` 已暴露 `get_device_usage`、`get_device_health`、`get_entity_catalog`、`get_behavior_insights`、`list_members` 等工具。对豆包管家的衔接：

- **现有契约**：管家经 `butler_token` 访问 `GET /api/members`、`/api/vision/presence`、`/api/insights/member-schedule`（见 `docs/交接单_MA对接_成员档案与在场查询.md`）。
- **增强方向（语义工具）**：让管家"免记 template_id / entity_id"，以逻辑设备名入参。在 `mcp_server.py` 增加或改造：
  - `query_device_usage(logical_device: str, window: str)`：入参 `"客厅电视"` → Identity 层解析 → `run_template` / `get_device_usage` 计算。
  - `get_presence()`、`get_member_schedule(name, days)`：已可用，确认管家侧封装即可。
- **身份层联动**：上述工具内部统一走 `resolve_entity`，自动享受 A1/A2/A3 治理。

> 管家作为 LLM 编排大脑，MCP 是其原生调用方式；MA 不反向依赖管家。

### 5.2 TVPilot / DeskPilot — HTTP 查询端点 + `app_token`

**缺口**：当前 `run_template` 只通过 MCP 暴露，HTTP 侧 `/api/templates`（`api/insight_routes.py`）仅有管理接口、无"运行"端点。TV/PC 这类非 LLM 应用不想塞一个 MCP client。

新增端点（镜像 `butler_token` 模式）：

```text
POST /api/insights/query
Authorization: Bearer <app_token>     # 新增，与 JWT/butler/arena/mcp 隔离
Content-Type: application/json

{
  "template_id": "xbox_daily_usage",  // 或 "logical_query": "客厅电视 上周末使用时长"
  "start": "2026-09-06T00:00:00",
  "end":   "2026-09-07T00:00:00",
  "include_timeline": true
}
→ 200 {
  "ok": true,
  "template": {...},
  "window": {...},
  "entities": [ { "entity_id", "friendly_name", "metric", "result":{...}, "stale": false } ],
  "summary_text": "..."
}
```

实现落点：`api/insight_routes.py` 新增 `insight_query` 路由；内部复用 `templates.run_template`。

鉴权：在 `config.py` 新增 `app_token`（环境变量 `APP_TOKEN`），`app.py` 新增 `APP_ENDPOINTS = ("/api/insights/query",)` + `_app_allowed` / `_app_matches`，完全照搬 `butler` 的常量时间比对 + 白名单拦截逻辑。未配置则通道关闭。

### 5.3 MQTT 实时推送（复用现有配置）

MA 已支持 `tv_mqtt_host` / `tv_media_player_entity`（`config.py:262`）。复用该 MQTT 客户端，在状态变化 / 新行为事件落库后 publish：

- `ma/presence`：`{member, room, state: enter/leave, ts}`
- `ma/device-usage`：`{logical_device, window, total_on_human, ts}`（如"孩子正在玩 HDMI3 已 45 分钟"）
- `ma/device-health`：`{logical_device, state: stale, ts}`（失效告警）

TVPilot / DeskPilot 订阅这些主题做**实时卡片**，无需轮询。该能力为**可选增强**（P4），不阻塞主链路。

### 5.4 鉴权隔离模型（总览）

| 令牌 | 消费方 | 白名单 | 现状 |
|------|--------|--------|------|
| JWT | WebUI | 全权限(管理员) | 已有 |
| `device_token` | TV APK / HA 自动化上报 | 设备端点 | 已有 |
| `butler_token` | 豆包管家 | `BUTLER_ENDPOINTS` | 已有 |
| `arena_token` | AutoFlow 竞技场 | `ARENA_ENDPOINTS` | 已有 |
| `mcp_token` | MCP 客户端 | `/mcp` (独立中间件) | 已有 |
| `app_token`（新增） | TVPilot / DeskPilot | `APP_ENDPOINTS` = `/api/insights/query` | **本期设计，下期实现** |

---

## 6. 生态边界与责任划分

- **MA（单一事实源）**：采集、实体身份、行为洞察计算、历史库。对外只给"查"，不给"控"（不暴露 HA 写服务到外部消费者，避免越权）。
- **豆包管家（编排大脑）**：LLM 理解自然语言 → 经 MCP 向 MA 取记忆 / 在场 / 时长 → 织回对话或下发指令给 TVPilot/DeskPilot。
- **TVPilot（TV 端）/ DeskPilot（PC 端）**：经 MA 的 HTTP 查询端点取数，经 MQTT 收实时事件；**不直接连 HA**，不直接读 MA 数据库。
- **记忆统一入库（C）**：本期不动。MA 已有独立记忆子系统（`agent_memory_routes.py` / `arena_routes.py` 对应的平行表），统一进主库、加 `source`/`confidence` 字段留待下一期。

---

## 7. 分期路线（顺序待评审确定）

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **P0** | 本设计文档定稿 | — |
| **P1** | Entity Identity & Health 层：`logical_devices` 模型 + `IdentityReconciler`（A1/A2/A3）+ 模板/查询改写（`run_template` 解析逻辑名） | P0 |
| **P2** | `POST /api/insights/query` + `app_token` | P1（依赖解析） |
| **P3** | MCP 语义工具（`query_device_usage` 等，逻辑名入参） | P1 |
| **P4** | MQTT 实时推送 | P1/P2 |
| **下期** | 记忆统一入库（C） | — |

> 用户最优先项（D）= P1，因其治本；P2/P3 让新消费者真正能用起来。

---

## 8. 风险与兜底

| 风险 | 缓解 |
|------|------|
| **A2 全自动合并误并**（如两个真不同的同类设备同名） | ① 合并审计日志可回滚；② WebUI 支持手动拆分；③ 选主规则保守（同 area + 同类 + 高相似才合并）；④ 首版可对"高相似但不同 area"不自动合并，仅建议。 |
| **HA 短暂不可达**导致误判全部 stale | `last_seen` 以 HA 注册表为准，但 stale 判定叠加 `last_data_ts`（事件库侧）；HA 不可达期间不触发删除，仅标记 `unknown`，等恢复后复核。 |
| **合并 / 重匹配循环抖动** | 解析结果加内存缓存 + 短时间（如 5 分钟）TTL；对账周期低频；状态无变化不写库、不 publish。 |
| **逻辑名歧义**（用户自然语"电视"匹配多个） | 查询时若解析出多候选且无法唯一选主，返回候选列表让用户/管家二选一，不强行合并。 |
| **性能** | 对账后台低频；`run_template` 已用 `asyncio.to_thread` 隔离 DB；Identity 解析走缓存。 |

---

## 9. 改造落点文件清单（参考，本期不改）

| 文件 | 改动 |
|------|------|
| `src/memory_agent/identity.py`（新增） | `LogicalDevice` 模型、`IdentityReconciler` 对账与合并 |
| `src/memory_agent/store.py` | 新增 `logical_devices` / `device_health` 表与读写 |
| `src/memory_agent/templates.py` | `EntityQuery` 加 `logical_id`；`run_template` 加 `resolve_entity` |
| `src/memory_agent/config.py` | 新增 `app_token` |
| `src/memory_agent/app.py` | 新增 `APP_ENDPOINTS` + `_app_allowed` / `_app_matches` |
| `src/memory_agent/api/insight_routes.py` | 新增 `insight_query` 路由 |
| `src/memory_agent/mcp_server.py` | 新增/改造语义工具（逻辑名入参） |
| `src/memory_agent/ha_client.py` | 复用 `discover_entities()`，无需大改 |
| `src/memory_agent/poller.py` | 挂接 `_reconcile_loop`（或独立调度） |
| `src/memory_agent/static/...` | 新增"逻辑设备 / 失效清单"页面 |

---

## 10. 开放问题（评审待定）

1. 全自动合并（A2）首版是否对"跨 area 高相似"也自动合并，还是仅建议？（影响误并率）
2. `app_token` 是单一共享令牌，还是按 TVPilot / DeskPilot 各发一个（便于吊销）？
3. 逻辑设备名由 MA 自动从 `friendly_name` 生成，还是允许用户自定义展示名 / 手动建？
4. MQTT 推送的实时性要求（秒级还是分钟级），决定 publish 触发点。
