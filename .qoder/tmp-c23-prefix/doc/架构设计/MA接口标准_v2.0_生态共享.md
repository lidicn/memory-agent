# Memory-Agent 接口标准 v2.0（生态共享版）

> 版本：v2.0 | 日期：2026-09-18 | 维护方：豆包管家（PM）
> 适用项目：TVPilot / DeskPilot / 小甜菜 / 豆包管家 / FFL 测试团队
> MA 地址：`http://192.168.2.200:8086`
> MA 版本：v2.0（主动感知分层管线 + 记忆晋升 + 规则引擎 + 离家安防）

---

## 一、概述

Memory-Agent（MA）是豆包管家生态的**家庭行为记忆中枢**，提供：

1. **设备使用时长统计**（HDMI3/Xbox 防沉迷等）—— 免 ADB，直接 HTTP 查询
2. **逻辑设备身份层**—— entity_id 漂移免疫，按逻辑设备名查询
3. **成员在场实时推送**—— 人脸识别结果，MQTT 订阅
4. **记忆统一入库**—— 生态各项目写入记忆，按 source 隔离
5. **设备健康监控**—— 失效设备清单，主动告警
6. **主动感知分层管线**（v2.0 新增）—— Gate/Identity/Omni 三层，edge_ai 事件零 VLM 直写
7. **记忆晋升与家庭画像**（v2.0 新增）—— 视觉行为事实跨天≥3天自动晋升候选，habit 记忆注入 VLM prompt
8. **主动规则引擎**（v2.0 新增）—— STATIC/DYNAMIC 拆分 + 滑窗去抖
9. **离家安防 + 日常画像**（v2.0 新增）—— 长时间无人→离家模式→陌生人立即告警；作息规律学习

---

## 二、鉴权

### 2.1 令牌类型

| 令牌类型 | 用途 | 访问范围 | 获取方式 |
|---|---|---|---|
| **app_token** | 应用层查询（TV/DP/小甜菜） | 仅 `/api/insights/query` + `/api/agent/memories` | MA WebUI → 设置 → 应用令牌，或 `POST /api/config/app-tokens` |
| **JWT（WebUI）** | 管理端 | 全部接口 | WebUI 登录获取 |
| **butler_token** | 豆包管家专用 | 全部接口 | 管家配置 |
| **mcp_token**（v2.0 新增） | MCP 客户端（FFL 测试/生态 Agent） | 按 scope 控制（read/write/admin） | MA WebUI → 设置 → MCP 令牌，或 `POST /api/mcp/tokens` |

### 2.2 app_token 特性

- **多令牌**：可为每个客户端（TV/DP/小甜菜）创建独立令牌，可单独吊销
- **按 token 派生 source**：令牌绑定 source（如 `vision`/`butler`），写入记忆时**强制采用**令牌的 source
- **来源白名单**：默认 `["ma","butler","vision","manual"]`
- **永不过期**，仅可手动吊销

### 2.3 mcp_token 特性（v2.0 新增）

- **scope 控制**：`read`（只读查询）/ `write`（可写记忆）/ `admin`（全部）
- **Streamable HTTP**：端点 `http://192.168.2.200:8086/mcp`
- **65 个 MCP 工具**：覆盖设备查询、记忆管理、感知事件、行为分析、视觉识别等
- **权限隔离**：read scope 拒绝所有写操作（已验证）

### 2.4 鉴权示例

```bash
# app_token 查询设备使用时长
curl -X POST http://192.168.2.200:8086/api/insights/query \
  -H "Authorization: Bearer <app_token>" \
  -H "Content-Type: application/json" \
  -d '{"template_id":"xbox_daily_usage","start":"2026-09-09","end":"2026-09-10"}'

# mcp_token 调用 MCP tools/list
curl -X POST http://192.168.2.200:8086/mcp \
  -H "Authorization: Bearer <mcp_token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

---

## 三、核心接口（HTTP）

### 3.1 设备使用时长查询（防沉迷核心）

**`POST /api/insights/query`**

> HDMI3/Xbox 使用时长直接通过此接口查询，**不需要 TVPilot 的 ADB 查询**。

#### 模式一：按模板查询（推荐，语义最稳定）

```json
{
  "template_id": "xbox_daily_usage",
  "start": "2026-09-09T00:00:00",
  "end": "2026-09-10T00:00:00"
}
```

#### 模式二：按逻辑设备即时查询

```json
{
  "logical_id": "客厅电视",
  "attribute": "source",
  "value": "HDMI 3",
  "metric": "duration",
  "days": 2
}
```

> ⚠️ **注意**：逻辑设备查询依赖身份层匹配。若返回 `stale: true`（"设备已失效或待重匹配"），需在 MA WebUI → 设备身份层重新匹配逻辑设备。模板查询不受此影响。

#### 响应

```json
{
  "ok": true,
  "window": {"start": "...", "end": "...", "label": "近2天"},
  "entities": [
    {
      "entity_id": "media_player.live_9",
      "friendly_name": "客厅电视",
      "metric": "duration",
      "result": {"total_seconds": 7200, "total_hours": 2.0, "sessions": 3},
      "resolved": ["media_player.live_9"],
      "stale": false
    }
  ],
  "summary_text": "客厅电视近2天HDMI3使用时长：2.0小时（3次）"
}
```

### 3.2 记忆写入（生态统一入库）

**`POST /api/agent/memories`**

```json
{
  "text": "Kevin 2026-09-09 玩 Xbox 2.5小时，超过工作日限制",
  "source_refs": ["insight:2026-001"],
  "source": "butler",
  "dry_run": false
}
```

- 写入默认 `state=staging`，**永不自动进 live**，需 promote/sweep 晋升
- v2.0 新增：视觉行为事实跨天≥3天自动晋升候选（topic_key=`habit:/vision/`）

### 3.3 记忆检索

**`POST /api/agent/memories/retrieve`**

```json
{"question": "Kevin 最近玩 Xbox 多久了？", "source": "ma", "limit": 5}
```

### 3.4 记忆列表

**`GET /api/agent/memories?source=butler&limit=20&state=live`**

---

## 四、MCP 接口（v2.0 新增，65 个工具）

### 4.1 端点

- **URL**：`http://192.168.2.200:8086/mcp`
- **协议**：Streamable HTTP（JSON-RPC 2.0）
- **鉴权**：`Authorization: Bearer <mcp_token>`

### 4.2 工具分类（65 个）

| 分类 | 工具示例 | 说明 |
|---|---|---|
| **设备查询** | `query_device_usage`, `get_entity_catalog`, `list_members` | 设备使用时长、实体清单、成员列表 |
| **记忆管理** | `add_semantic_memory`, `list_agent_memories`, `promote_memory`, `search_memories` | 记忆写入/查询/晋升/检索 |
| **感知事件** | `query_events`, `query_behavior_events`, `get_behavior_summary`, `list_behavior_anomalies` | 感知事件查询、行为摘要、异常清单 |
| **行为分析** | `get_behavior_drift`, `mine_behavior_process`, `infer_activities` | 行为漂移、过程挖掘、活动推断 |
| **视觉识别** | `analyze_camera`, `list_vision_cameras`, `get_vision_status` | 摄像头分析、视觉状态 |
| **成员管理** | `create_member`, `list_members`, `get_member_persona` | 成员创建/查询/画像 |
| **配置管理** | `get_config`, `list_app_tokens`, `create_mcp_token` | 配置查询、令牌管理 |
| **数据采集** | `get_collect_status`, `trigger_collection` | 采集状态、触发采集 |

### 4.3 调用示例

```bash
# tools/list：列出所有工具
curl -X POST http://192.168.2.200:8086/mcp \
  -H "Authorization: Bearer <mcp_token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# tools/call：调用 query_device_usage
curl -X POST http://192.168.2.200:8086/mcp \
  -H "Authorization: Bearer <mcp_token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"query_device_usage","arguments":{"logical_id":"客厅电视","days":7}}}'
```

---

## 五、MQTT 实时推送

### 5.1 主题与载荷

| 主题 | 触发时机 | retain | 载荷 |
|---|---|---|---|
| `ma/presence` | 周期（默认 60s），内容变化才发 | ✅ | `{"members":[{"name","member_id","room","via","confidence","last_seen","trigger"}],"total":N,"ts"}` |
| `ma/device-health` | 对账发现状态变化 | ❌ | `{"entity_id","stable_id","from","to","ts"}` |

### 5.2 启用方式

MA 的 `.env`（`/vol1/1000/docker/memory-agent/.env`）：
```env
MA_MQTT_ENABLED=1
TV_MQTT_HOST=192.168.2.200
TV_MQTT_PORT=1883
TV_MQTT_USER=butler
TV_MQTT_PASS=<REDACTED-web口令len=24>
```

---

## 六、身份层只读接口（仅 JWT，app_token 不放行）

### 6.1 逻辑设备清单

**`GET /api/identity/devices`**

### 6.2 设备健康清单

**`GET /api/identity/health?state=stale`**

### 6.3 合并审计

**`GET /api/identity/merges`**
**`POST /api/identity/merges/split`**

---

## 七、v2.0 新增 API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/behaviors` | GET | 行为状态查询（activity_inference 产出） |
| `/api/vision/analyze` | POST | 视觉分析（取帧+VLM） |
| `/api/vision/cameras` | GET | 摄像头列表 |
| `/api/collect/status` | GET | 数据采集状态 |
| `/api/signal/exclusions` | GET/POST | 信号排除规则 |
| `/api/face/register` | POST | 人脸注册 |
| `/api/researcher/jobs` | GET/POST | 记忆研究员任务 |
| `/api/insights/templates` | GET/POST | 洞察模板管理 |
| `/api/mcp/tokens` | GET/POST/DELETE | MCP 令牌管理 |
| `/api/config/app-tokens` | GET/POST/DELETE | 应用令牌管理 |

---

## 八、各项目对接建议

### 8.1 TVPilot

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /api/insights/query` | HDMI3/Xbox 使用时长查询（替代 ADB） | P0 |
| `POST /api/agent/memories` | 写入电视端事件记忆（source=vision） | P1 |
| MCP `query_device_usage` | 逻辑设备名查询（漂移免疫） | P1 |

### 8.2 DeskPilot

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /api/insights/query` | 查询设备使用时长 | P2 |
| `POST /api/agent/memories` | 写入桌面操作记忆 | P1 |
| `ma/presence` MQTT | 订阅成员在场 | P2 |

### 8.3 小甜菜

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /api/insights/query` | 防沉迷核心：Kevin Xbox/HDMI3 使用时长 | P0 |
| `POST /api/agent/memories` | 写入防沉迷事件记忆 | P1 |
| 豆包管家 Bark/TTS API | 超时告警推送 | P0 |

### 8.4 豆包管家

| 接口 | 用途 | 优先级 |
|---|---|---|
| MCP 全套工具 | 记忆查询/写入、设备查询、行为分析 | P0 |
| `ma/presence` MQTT | 定位引擎信号源 | P0 |
| `POST /api/agent/memories` | 记忆统一入库 | P0 |

### 8.5 FFL 测试团队

| 接口 | 用途 | 优先级 |
|---|---|---|
| MCP `tools/list` + `tools/call` | MCP 接口回归测试 | P0 |
| mcp_token（read scope） | 只读测试令牌 | P0 |

---

## 九、注意事项

1. **app_token 只放行查询和记忆接口**，身份层/配置接口返回 403
2. **mcp_token 按 scope 控制**：read scope 拒绝所有写操作（已验证）
3. **v0.6 source 强制派生**：用 app_token 写入记忆时，body 中的 source 字段会被忽略
4. **设备漂移免疫**：用 logical_id 查询时，即使 HA entity_id 变化也能正确解析
5. **stale 不静默**：设备失效时返回 `stale: true` + `result.error`
6. **记忆默认 staging**：生态写入的记忆默认 state=staging，不自动进 live
7. **逻辑设备匹配**：若逻辑设备查询返回 stale，需在 WebUI 重新匹配身份层
8. **模板查询优先**：模板查询比逻辑设备查询更稳定，推荐使用

---

## 十、测试验证记录（2026-09-18）

### HDMI3 查询测试

| 测试 | 结果 | 说明 |
|---|---|---|
| 模板查询 `xbox_daily_usage` | ✅ 通过 | API 正常，实体合并正确，数据返回完整 |
| 逻辑设备查询 `客厅电视 HDMI3` | ⚠️ 部分通过 | API 调用成功，但返回 stale（逻辑设备待重匹配） |

### MCP 回归测试（FFL 团队，2026-09-18）

| 阶段 | 结果 | 说明 |
|---|---|---|
| Phase 1 连通性 | ✅ PASS | initialize 握手正常，tools/list 返回 65 个工具 |
| Phase 2 只读冒烟 | ✅ PASS | 9 个只读工具全部正常 |
| Phase 3 权限负向 | ✅ PASS | 4 个写工具被正确 DENIED（read scope） |

---

## 十一、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-10 | 初始版本：汇总 MA v0.2-v0.6 全部接口 |
| **v2.0** | **2026-09-18** | **重大更新**：① 版本升级到 v2.0；② 新增 MCP 接口章节（65 个工具）；③ 新增 mcp_token 鉴权；④ 新增 v2.0 API 端点清单；⑤ 新增主动感知/记忆晋升/规则引擎/离家安防能力说明；⑥ 新增测试验证记录；⑦ 补充逻辑设备 stale 处理说明 |
