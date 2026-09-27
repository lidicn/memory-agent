# 交接卡：Phase 3-lite 复核 + 统一日志配置

> 项目：memory-agent（PROJECT-20260914-001 审计后续）
> 日期：2026-09-17 ｜ 交付人：DEV（AI）｜ 状态：✅ 已部署验证

## 一、结论先行

用户决策「执行 Phase 3-lite（6 项，半天量低风险）」。**复核发现这 6 项在 2026-09-14 已交付并部署**，本次未重复施工，只做了复核 + 补齐 1 处真实缺陷（日志配置，见第四节）。

## 二、Phase 3-lite 6 项复核（全部在位）

| # | 项 | 复核证据 | 状态 |
|---|---|---|---|
| AR1 | config 更新改 admin 鉴权 | `api/config_routes.py:214,253` 用 `require_admin`（`update_config_api` / `reveal_secret`） | ✅ |
| S2 | IN 子句护栏 | `store.py:2721` `MAX_IN = 2000`，`:2727` 超限 `raise ValueError` | ✅ |
| S3 | 列名断言 | `store.py:2485` `update_member` 用 `allowed` 白名单 `assert`；`store.py:3145` `update_job` 用 `_JOB_COLS` `assert` | ✅ |
| M1 | MCP 统计上限防泄漏 | `mcp_server.py:649` `MCP_MAX_STATS_ENTRIES=500`（env 可调），`:696-697` 超限淘汰最旧 | ✅ |
| — | 删根级死代码 | `src/memory_agent/face_routes.py` **不存在**（仅保留 `api/face_routes.py`，被 `api/__init__.py` 导入） | ✅ |
| S8 | 语音缓存软上限 + TTL | `store.py:28` `_VOICE_CACHE_MAX=2000`，`:1392-1396` 超限时按 `created_at` 淘汰最旧 10%；`purge_old(:3205)` 清理过期 | ✅ |

## 三、本次新增：统一日志配置（真实缺陷）

### 问题

项目有 **16+ 个模块**使用 `logging.getLogger(__name__).info()/warning()`
（`announcer` / `livingroom_ai` / `algo_kernel` / `perception_ingest` / `vision_service` /
`llm_client` / `ha_assist` / `auth` / `mcp_*` / `acp_auth` / `tool_schema` / `app` …），
但**从未调用 `basicConfig` / `dictConfig`**：

- root logger 默认级别 `WARNING`，无 handler 时走 `logging.lastResort`（同样 WARNING）
- ⇒ **所有 `logger.info()` 被静默丢弃**，线上一句都看不到
- 而 `print()` 不受影响 ⇒ 造成"日志好像在，只是 info 没有"的错觉，排查时极易误判

**佐证**：`livingroom_ai` 的 `logger.info("发现 %d 个盒侧 AI 事件实体")` 此前在
`docker logs` 中完全看不到，但代码路径确实执行了（实体发现、事件落库都正常）。

### 改动

| 文件 | 变更 |
|---|---|
| `src/memory_agent/logging_setup.py`（新增） | `configure_logging(force=False)`：级别取 `MA_LOG_LEVEL`（默认 INFO，非法值回落 INFO）；root 无 handler 时添加 stdout handler（格式 `时间 级别 logger名 消息`）；给 `httpx/httpcore/chromadb/uvicorn.access/apscheduler` 抬到 WARNING 降噪；**幂等**（只配一次，避免与 uvicorn 冲突） |
| `src/memory_agent/app.py` | 导入期调用 `configure_logging()`（uvicorn 以 `memory_agent.app:combined_app` 启动，导入即生效，早于任何模块产生日志） |
| `tests/test_logging_setup.py`（新增） | 5 项：级别/handler 生效、info 能落到 stdout（回归核心缺陷）、非法级别回落、第三方降噪、导入 app 即配置 |

### 验证

```
docker logs memory-agent | grep INFO
→ livingroom_ai: 发现 17 个盒侧 AI 事件实体     ✅（此前完全丢失）
health=200 ✅
```

## 四、行为差异与风险

1. **日志量增加**：此前不可见的 info 现在会输出。第三方（`httpx` 等）已降噪，
   但 `memory_agent.*` 的 info 会明显变多，属预期。
2. **级别可调**：`MA_LOG_LEVEL=WARNING` 可回到接近旧行为（仍比旧版多出 warning 以上）。
3. **不改动任何业务逻辑**，只在 app 导入期配置 logging；失败不影响启动（无异常路径）。
4. **幂等设计**：重复调用只保证级别，不重复挂 handler。

## 五、遗留（未做，附建议）

| 项 | 实测规模 | 建议 |
|---|---|---|
| 宽泛 except | **裸 `except:` = 0**；`except Exception` 47 处 | 47 处基本都是可选子系统/降级路径的**有意**卫兵（含 noqa BLE001），逐一细化收益低、回归风险高 → **建议保持** |
| print → logging | `print(` 约 11–30 处（多为启动/周期任务提示，目前可见） | **现在具备前置条件**（日志已配置），可低风险分批改；优先级低，建议随模块改动顺手做 |
| 大文件拆分 | `insights.py` 192KB、`store.py` 167KB、`mcp_server.py` 107KB | 高风险大重构，收益是可维护性 → **建议单独立项**，勿与功能迭代并行 |

## 六、合并影响

- 无数据库 schema 变更、无 API 契约变更、无配置项新增（`MA_LOG_LEVEL` 为 env 可选，缺省 INFO）。
- 与 v2.0 主动感知改动无冲突（本轮 `livingroom_ai` / `algo_kernel` / `perception_ingest`
  的 logger 均借此首次可见）。
