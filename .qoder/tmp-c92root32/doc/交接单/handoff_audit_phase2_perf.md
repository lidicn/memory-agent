# 交接卡：审计 Phase 2（性能）

- 工单：PROJECT-20260914-001 · Phase 2 实施
- 日期：2026-09-14
- 范围：I1 `get_behavior_insights` 冷启动、S1 `list_members` N+1、M2 `MCP_SLOW_MS` 阈值
- 部署：已 scp 至 NAS `/vol1/1000/docker/memory-agent/src/memory_agent/` 并 `docker restart memory-agent`，实测通过

---

## 一、改动清单（3 文件）

| 问题 | 文件 | 改动 |
|---|---|---|
| **I1** 洞察全窗口重扫描、无缓存 | `insights.py` | `InsightService.__init__` 增结果缓存 `_result_cache`（TTL 默认 60s，容量 64）；新增 `_cache_get/_cache_put`（deepcopy 隔离，防调用方改坏缓存）；`infer_activities` 与 `get_behavior_insights` 命中缓存即返回 |
| **S1** `list_members` N+1 | `store.py` | 逐成员各查 3 次（1+3N）→ **一次性批量拉取** member_rooms/devices/tags 三表，Python 侧按 `member_id` 分组（共 4 次 SQL） |
| **M2** 慢阈值单级、冷启动淹没慢查询 | `mcp_server.py` | 拆两级：`MCP_SLOW_MS`（默认 5000，INFO）与 `MCP_HEAVY_MS`（默认 30000，WARNING） |

### I1 关键细节
- **缓存 key 抖动修复**：`infer_activities` 在 `days` 语义下窗口终点= `now`（逐秒变化），会让 key 每次不同导致永不命中。改为按**分钟**粒度对齐 key（`start_iso[:16]/end_iso[:16]`，≤60s 陈旧与 TTL 同量级）。显式 `start/end`（环比内部 cur/prev 为日对齐）时量化无影响。
- `get_behavior_insights` 按 `compare_days` 缓存（窗口为自然日对齐，key 天然稳定）。

---

## 二、验证记录（NAS 实测）

| 项 | 方法 | 结果 |
|---|---|---|
| 语法 | `python -m py_compile` | ✅ PY_COMPILE_OK；lint 0 |
| **S1** 结构 | `list_members()` | ✅ 3 成员，`rooms/devices/tags/profile` 键完整（样本 rooms=1/devices=26/tags=0） |
| **I1** infer 缓存 | 连续两次 `infer_activities(2)` | ✅ `1st=1.15s → 2nd=0.0009s`，`same=True` |
| **I1** gbi 缓存 | 连续两次 `get_behavior_insights(3)` | ✅ `1st=4.96s → 2nd=0.0003s`，`same=True` |
| **M2** 分级日志 | `_record_mcp_call(40s/6s)` | ✅ 输出 `[MCP-HEAVY]`(WARNING) / `[MCP-SLOW]`(INFO)；常量 `5000.0/30000.0` |
| 服务健康 | `/health`、`/api/auth/status` | ✅ 200、`initialized:true` |

---

## 三、已知风险 / 注意事项

1. **缓存陈旧性**：结果 TTL 60s，期间数据变化不体现（家庭洞察场景可接受）；进程内状态，容器重启清零。
2. **首调成本仍在**：缓存只消除**重复调用**成本；真正的首次全量扫描（本例 3 天窗口 ~5s，7 天冷启动仍可能数十秒）未改变。彻底优化需「事件扫描在活动识别/温控/噪声之间共享」（较大重构），本次未做。
3. **deepcopy 开销**：命中缓存时深拷贝返回，毫秒级，远小于重算。
4. **部署观察**：`docker restart` 后 ~80s 才 `/health=200`（lifespan 做 HA 采集/对账），属既有行为，非本次改动引起。

---

## 四、后续（本次未做）

- **I1 深化**：共享全窗口事件扫描（`_iter_all_events` 结果在 `infer_activities`/`_noise_entities`/`climate_sessions` 之间复用），降低真正首调耗时
- **S3/S2**：SQL f-string 列名白名单断言、IN 子句实体数上限
- **Phase 3**：宽泛 `except Exception`（~70 处）、`print→logging`、大文件拆分（insights 3733 / store 2565 / mcp_server 1904）、AR1 config 更新鉴权收紧、文档补全

---

## 五、合并影响

- 3 个文件、纯性能优化；**无 API/返回结构变化**（`list_members` 输出结构不变，`get_behavior_insights`/`infer_activities` 返回内容不变，仅加缓存）。
- 对外可见变化：MCP 慢调用日志分级（`[MCP-SLOW]` 降为 INFO，新增 `[MCP-HEAVY]` WARNING）。
