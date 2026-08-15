# Memory Agent · memory-agent

家庭行为记忆中枢：采集 Home Assistant 设备状态 → 沉淀为行为事件库 → 通过 **MCP** 与**内置 LLM 助手**供 AI Agent 调用 → 联动 Node-RED 执行自动化。

```
            ┌─────────────┐
 Home Assis │ 采集服务     │  SQLite 事件库 ──┐
 (HA) ─────▶│ (任务化轮询) │                  │
            └─────────────┘                  │
                                             ├─▶ MCP 服务 (FastMCP / Streamable HTTP)
            ┌─────────────┐   Chroma 向量库 ──┤      (供 Claude / Cursor / opencode 等调用)
 Node-RED ◀─│ 自动化执行   │                  │
            └─────────────┘                  │
                                     内置 LLM 助手 (WebUI 对话 / 行为分析)
```

---

## 特性

- **采集流水线**：任务化调度（间隔 / 定时 / 手动）、历史回填、采集进度心跳、可取消。
- **MCP 接入**：基于官方 [`mcp`](https://github.com/modelcontextprotocol/python-sdk) SDK（Streamable HTTP，并保留 SSE 兼容回退），与 WebUI 的 JWT 体系**完全隔离**的 Token 鉴权；WebUI 内可一键生成 Token、获取客户端配置片段、并做握手自检。
- **内置 LLM**：OpenAI 兼容接口，WebUI 内置「通用对话」与「行为分析助手」（结构化洞察可存为模板）。
- **全新 WebUI**：无构建的静态前端（Tailwind CDN + Alpine.js + ES Modules），暗色玻璃拟态设计，含仪表盘 / 采集 / 助手 / 洞察 / MCP / 设置六大页面。
- **兼容红线**：保留 Node-RED 契约（`/api/nr/*`、`POST /api/analyze/water_purifier`）、Basic Auth 分支、旧 `agent-token` 别名端点。

---

## 快速开始

```bash
# 1. 准备环境变量（可选，仅在首次启动作为默认值）
cp .env.example .env
#   编辑 .env 填入 HASS_TOKEN / LLM_API_KEY / JWT_SECRET 等

# 2. 构建并启动（含 redis、chroma 依赖服务）
docker compose up -d --build

# 3. 访问 WebUI
#   浏览器打开 http://<宿主机IP>:8086
```

> 端口映射：`8086 → 8000`（容器内固定 8000，仅对外暴露 8086）。

### 首次启动

- 若 `/data/users.json` 不存在，系统处于「未初始化」状态，WebUI 会引导你**注册管理员账号**。
- 登录后进入「设置」页填写 Home Assistant 地址与长期令牌、配置 LLM（OpenAI 兼容）、Node-RED、向量库等。
- 进入「采集」页开启采集调度或手动触发首次采集（默认回溯 24 小时）。

---

## MCP 接入

1. 在 WebUI「MCP 接入」页点击**生成 Token**（明文仅展示一次，请妥善保存）。
2. 复制对应客户端的配置片段（Claude / Cursor / opencode），将 `<你的 Token>` 替换为实际 Token。
3. 点「握手自检」验证 `initialize` 能否成功（重构前此步必然失败）。

接入地址固定为：`http(s)://<host>/mcp`

两种传输均受支持，且 `/mcp` 根路径现已同时兼容两者：

- **Streamable HTTP**：`http(s)://<host>/mcp`（POST 握手）
- **SSE 兼容回退**：`http(s)://<host>/mcp/sse`（GET 建立 SSE 流并下发 endpoint）

> 已显式关闭 MCP SDK 默认的 DNS 重绑定保护（端点本身有 Bearer Token 鉴权），
> 局域网 / 容器 IP 访问不再被 `421` 拒绝；`GET /mcp` 也作为 SSE 端点，
> 兼容只连 base URL（不追加 `/sse`）的客户端。

部分客户端（如 DeepSeek++）对 MCP 2.x Streamable HTTP 的实现尚不稳定，若遇到
`MCP SSE stream ended without a matching response` 等解析类报错，优先改用 SSE 回退地址
`http(s)://<host>/mcp/sse`；若仍报 `did not provide a POST endpoint`，检查客户端是否把
base URL 直接填成 `/mcp`（现已支持）或 `/mcp/sse`。

> 若自检提示 MCP 不可用：需在构建环境中已安装 `mcp>=2.0.0`（见 Dockerfile / pyproject），并**重新构建镜像**。

### 暴露的 MCP 工具

| 工具 | 说明 |
|------|------|
| `help` | 工具索引与用法示例（`help('get_device_usage')` 看单工具详情） |
| `get_entity_catalog` | 设备目录：友好名 / 房间 / 类别 / 最后在线 / 活跃度 |
| `get_behavior_insights` | 行为洞察：节律 / 动线 / Top 实体 / 异常（含语义异常） |
| `get_device_usage` | 设备开关时长 / 次数 / 每日分布 / 时间线 |
| `search_events` | 语义化事件搜索（支持 `summarize` 压缩） |
| `get_data_coverage` | **数据覆盖报告**：窗口内实际有数据的日期（避免误判空白窗口） |
| `get_device_health` | **设备健康探测**：揪出失联 / 没电 / 长期静默设备 |
| `get_climate_sessions` | **气候会话**：拼出「设定温度 + 室温 + 运行时长」 |
| `infer_activities` | **活动识别**：做饭 / 洗澡 / 睡眠 / 看电视 / 离家（带置信度） |
| `ask_memory` | **自然语言问答**：口语问法映射到既有工具（关系库主力，向量库副驾） |
| `query_events` | 精确查询原始事件 |
| `get_behavior_summary` | 行为摘要 |
| `get_person_history` | 成员历史 |
| `list_rooms_entities` | 房间与实体清单 |
| `get_collect_status` | 采集状态 + **关系库（主）/ 向量库（辅）** 双层统计 |
| `trigger_collection` | 触发一次采集 |
| `export_history` | 导出历史记录 |
| `save_analysis_template` / `list_analysis_templates` / `export_insight` / `delete_analysis_template` | 行为模板沉淀（供 Node-RED） |
| `save_skill` / `list_skills` / `get_skill` | 洞察 skill 读写（网关为唯一真源，版本自增） |

> **存储分层**：事件查询与全部洞察计算走 **关系库（SQLite，主力）**；**向量库（Chroma，辅助）**
> 仅做「天×房间聚合摘要」镜像与语义检索副驾，并提供 `ask_memory` 的模糊回落。**没有**裸向量查询入口——
> 直接拿向量库当主存储是退步，关系库才是主力。两层状态可通过 `get_collect_status().storage` 一目了然。

### 洞察 Skill（网关为唯一真源，Agent 通过 MCP 拉取最新版）

memory-agent（网关）内置并维护「洞察 skill」，**是技能的唯一真源**；连接的 Agent 通过
MCP 工具 `get_skill` 从网关**拉取最新版本**，而不是在各处维护本地副本。

- `save_skill(name, content, title="", category="insight")` —— 把分析经验**写回网关**。
  网关会自增 `version`、刷新 `updated_at`，并写入 `<skills_dir>/<name>/SKILL.md`。
  `name` 只能含字母、数字、`_`、`-`（同时作为目录名）；`content` 为完整 markdown，
  若不带 `---` frontmatter 会自动补 `name`/`description`/`category`/`version`/`updated_at`。
- `list_skills(category="")` —— 列出网关上所有 skill 及 `version` / `updated_at`。
- `get_skill(name, version="latest")` —— Agent **从网关拉取某 skill 的最新版本**，返回
  完整正文与版本元数据。建议在每次会话开始时调用，与本地缓存的 `version` 比对，
  版本号更大即为有新版本，用返回正文覆盖本地即可。

> **版本机制**：每次 `save_skill` 都让 `version` +1，因此 Agent 只需比较 `get_skill` 返回的
> `version` 即可判断是否需要更新，无需逐字节 diff。网关当前只保留最新版（`get_skill` 的
> `version` 参数预留给未来多版本历史）。

> `skills_dir` 默认 `/data/skills`，可通过环境变量 `SKILLS_DIR` 覆盖。网关在启动时自动把
> 内置技能（`src/memory_agent/skills_bundle/`）种子化到该目录，确保 `get_skill` 开箱即可拉到。

内置示例技能位于
[`src/memory_agent/skills_bundle/insight/SKILL.md`](src/memory_agent/skills_bundle/insight/SKILL.md)，
它指导 Agent 如何按标准流程使用上述 MCP 工具做行为洞察分析。

---

## 配置

配置文件持久化在 `/data/config.json`，采用**原子写入**、对未知字段容错。WebUI 设置页修改后会即时重建下游客户端（热更新），无需重启。

环境变量仅在「对应配置文件字段为空」时作为初始默认值，不会覆盖你在 WebUI 保存的内容。可用变量见 `.env.example`：

- `HASS_SERVER` / `HASS_TOKEN` — Home Assistant
- `NR_URL` / `NR_USER` / `NR_PASS` — Node-RED
- `REDIS_HOST` / `REDIS_PORT` — Redis
- `CHROMA_HOST` / `CHROMA_PORT` — Chroma 向量库
- `LLM_PROVIDER` / `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TEMPERATURE`
- `TZ_OFFSET_HOURS` / `JWT_SECRET` / `DB_PATH`

---

## 主要 API

| 分组 | 端点 | 说明 |
|------|------|------|
| 认证 | `/api/auth/*` | 注册 / 登录 / 状态 / 当前用户 / 改密 / 账号管理 |
| 配置 | `/api/config`, `/api/config/test`, `/api/config/reveal`, `/api/health` | 读写配置、连通测试、密钥明文、健康检查 |
| 采集 | `/api/collect/*` | 触发 / 回填 / 进度 / 取消 / 任务 / 状态 / 启用 / 配置 / 日历 / 统计 / 事件 |
| 助手 | `/api/llm/models`, `/api/llm/chat`, `/api/llm/analyze*` | 模型列表、流式对话、行为分析（预览/运行/保存） |
| 洞察 | `/api/templates*`, `/api/insights` | 模板管理、洞察列表 |
| MCP | `/api/mcp/info`, `/api/mcp/tokens*`, `/api/mcp/selftest` | 接入信息、Token 管理、握手自检 |
| HA | `/api/ha/discover`, `/api/ha/rooms`, `/api/ha/state` | 实体发现、房间配置、状态 |
| Node-RED | `/api/nr/*`, `/api/analyze/water_purifier` | 兼容契约 |
| MCP 端点 | `/mcp` | 对外暴露的 MCP 服务 |

---

## 目录结构

```
memory-agent/
├── Dockerfile / docker-compose.yml   # 部署
├── pyproject.toml                    # Python 依赖（含 mcp）
├── .env.example                      # 环境变量模板
├── data/                             # 持久化数据（config.json / *.db / users.json）
└── src/memory_agent/
    ├── app.py                        # ASGI 入口（combined_app）+ 中间件 + 路由装配
    ├── runtime.py                    # AppRuntime 单例（配置/存储/采集/LLM/MCP Token）
    ├── config.py / auth.py / store.py
    ├── poller.py / ha_client.py / history.py / patterns.py / templates.py
    ├── llm_client.py / analysis.py   # LLM 客户端 + 行为分析（流式）
    ├── mcp_server.py / mcp_auth.py / mcp_tokens.py   # MCP（FastMCP）
    ├── api/                          # 各分组路由
    └── static/                       # 前端（index.html + css + js + vendor）
```

---

## 故障排查

- **MCP 握手失败 / 报错 SSE stream ended 或 did not provide a POST endpoint**：先点「握手自检」确认 Streamable HTTP 与 SSE 两个端点是否都返回成功（自检现含 SSE 连通性检查）。若 SSE 自检失败，检查客户端填的地址是 `/mcp` 还是 `/mcp/sse`（两者现已都支持）；若 Streamable HTTP 解析类报错，改用 SSE 回退地址 `/mcp/sse`。服务端日志（`mcp.dispatch` / `mcp.auth`）会记录每次 MCP 请求的传输类型与鉴权结果，便于定位。最终确认镜像安装了 `mcp>=2.0.0`。
- **WebUI 无法触发采集**：采集服务现在是进程级单例（`AppRuntime.collector`），在 lifespan 启动；确认「采集」页可正常触发，而非返回假消息。
- **改了配置不生效**：配置已支持热更新（保存后立即重建客户端），若仍不生效请检查 `/data/config.json` 是否被手动改坏（未知字段会被忽略，损坏会回退默认值）。
- **容器时区偏差**：事件时间依赖 `TZ_OFFSET_HOURS`（默认 8），容器内通常无 TZ，请确保该值正确。
