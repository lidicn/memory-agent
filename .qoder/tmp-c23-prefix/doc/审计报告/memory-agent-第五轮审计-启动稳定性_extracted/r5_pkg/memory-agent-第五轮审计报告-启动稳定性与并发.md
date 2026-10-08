# memory-agent 第五轮审计报告：启动稳定性与并发功能性

> 审计日期：2026-10-01
> 审计方法：**sota-async-concurrency skill（AUDIT 模式）** + 沙箱实测
> 代码基线：`src/memory_agent`（main 分支）
> 测试库：`/tmp/ma30.db`（30 天规模，103.9 MB / events 30 万行）

---

## 〇、本轮方法论

按 skill 规范执行 AUDIT 模式，逐项走完并发风险清单：

| 检查项 | 结果 |
|---|---|
| fire-and-forget（create_task 未存句柄） | **命中：4 个常驻 task 从未 cancel** |
| sync-call-in-async（async 内同步阻塞） | **命中：32 处未被 `to_thread` 保护的 DB 调用** |
| lock-across-await（锁跨 await） | 0 处 ✅ |
| unbounded-queue（无界队列） | 命中 2 处 |
| async-time-sleep（async 内 time.sleep） | 命中 2 处 → **已排除为误报** |
| check-then-act | 未发现高危组合 |

---

## 一、🔴 CRITICAL　4 个常驻任务从不取消，shutdown 被阻塞

**位置**：`runtime.py` `startup()` vs `shutdown()`

`startup()` 创建 **11 个**常驻后台任务，`shutdown()` 只 cancel **7 个**：

| Task | 是否 cancel | 说明 |
|---|---|---|
| `_sweep_task` | ✅ | |
| `_identity_task` | ✅ | |
| `_mqtt_task` | ✅ | |
| `_backup_task` | ✅ | |
| `_tpl_validate_task` | ✅ | |
| `_activity_task` | ✅ | |
| `_livingroom_ai_task` | ✅ | |
| **`_retention_task`** | ❌ | **执行 `purge_old`——单事务删 50 万行、持全局锁 5.2 秒** |
| **`_candidate_promotion_task`** | ❌ | |
| **`_causal_scan_task`** | ❌ | |
| **`_self_diary_task`** | ❌ | 每日 23:00 调 LLM（付费 API） |

**实测 shutdown 竞态**：模拟 `_retention_task` 持锁跑 purge 3 秒时调用 `store.close()`——

```
close() 被阻塞 2.7 秒才完成
```

真实 `purge_old` 持锁 **5.2 秒**（第三轮实测），大库更久。

**失败模式**：
- 重启 / `docker compose restart` 时进程实际无法及时退出，被 `docker stop` 强杀（默认 10 秒宽限期）
- 强杀可能中断正在进行的 DELETE，留下半清理状态
- `_self_diary_task` 泄漏后，容器已"停止"却仍可能在 23:00 触发 LLM 调用（付费 API）

**修复**：`shutdown()` 补上这 4 个 task 的 cancel，并在 `store.close()` 前 `await asyncio.gather(*tasks, return_exceptions=True)`。

---

## 二、🔴 CRITICAL　32 处 async 内同步 DB 调用，阻塞事件循环

**实测对照**（同一 416 ms 级 `entity_catalog` 查询，30 万行库）：

| 写法 | 查询耗时 | 期间事件循环调度次数 |
|---|---|---|
| `await asyncio.to_thread(...)`（正确） | 371 ms | **71 次** ✓ |
| async 内直接同步调用（MA 现状） | 363 ms | **9 次** ✗ |

（两轮重复测试稳定：correct 70–73 次 / blocking 9 次）

**事件循环调度能力下降约 87%**——查询期间所有 WebUI、MCP、ACP、采集请求基本停滞。

**关键佐证：这不是"作者不懂"，而是 DB 路径的遗漏。**

项目自身在视觉/电视路径上**正确使用了 `to_thread`**：

```python
# vision_service.py:1121
res = await asyncio.to_thread(self.analyze_room, room, camera=camera, trigger="patrol")

# vision_service.py:1151（注释写明有这个意识）
"""P1-19: 改 async + 并发查询，避免 N 路摄像头串行同步 HTTP 阻塞事件循环。"""

# api/tv_routes.py:85
result = await asyncio.to_thread(rt.tv.analyze, ...)
```

**分布（32 处）**：

| 文件 | 处数 | 典型 |
|---|---|---|
| `api/insight_routes.py` | 8 | `researcher_job_save` / `delete` / `toggle` / `run_now` |
| `mcp_server.py` | 5 | `query_unified_events:1249` 的 `rt.store.connect()`、`get_collect_status:2594` |
| `api/behavior_routes.py` | 5 | `behaviors_bad_cases_list:455` 的 `query_events()` |
| `researcher.py` | 4 | `run_job:319/321`、`run_all:340/344` |
| `api/collect_routes.py` | 3 | `collect_status` 三连 |
| `api/face_routes.py` | 3 | `set_member_face_feature` / `create_member` |
| `api/identity_routes.py` | 3 | `list_device_health` / `split_logical_device` |
| `api/events_routes.py` | 1 | `butler_events_push:81` 的 `insert_behavior_event` |

**修复**：一律改为 `await asyncio.to_thread(rt.store.xxx, ...)`。

---

## 三、🟡 MEDIUM　其余项

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| 1 | 无界队列 | `acp_server.py:452`、`api/debug_routes.py:356` | `asyncio.Queue()` 无 `maxsize`；ACP 事件流若消费慢于生产会无限堆积内存 |
| 2 | 无节流后台识别 | `vision_service.py:1078` | 每次 TV 人脸事件都 `create_task(to_thread(analyze_room))`，无并发上限；高频上报时任务堆积 |
| 3 | fire-and-forget 无句柄 | `acp_server.py:441`、`vision_routes.py:154` | 属**有意设计**（后台执行、立即返回），但无句柄 → 无法 cancel、无法观测"当前几个识别在跑" |

---

## 四、✅ 已排除的误报（避免维护者白改）

| 项 | 结论 |
|---|---|
| `tv_service.py:168` 的 `time.sleep` | 在同步函数 `_refresh_capture_entity` 内，调用链 `capture()→analyze()` 由 `api/tv_routes.py:39/58` 用 `to_thread` 卸载，**不阻塞事件循环** |
| `vision_service.py:241` 的 `time.sleep(0.5*(attempt+1))` | 在同步函数 `fetch_frame` 内（go2rtc 取帧重试退避）；其调用方 `analyze_room` / `test_camera` / `test_llm` 均为同步函数，且 `patrol_loop:1121` 已用 `to_thread` 卸载 |
| 锁跨 await | 全项目 0 处 |

---

## 五、修复优先级

| 优先级 | 缺陷 | 动作 | 收益 |
|---|---|---|---|
| 🔴 CRITICAL | 4 个 task 从不 cancel | 补 cancel + `gather` 等待 | 重启不再被强杀、无残留 LLM 调用 |
| 🔴 CRITICAL | 32 处同步 DB 调用 | 改 `asyncio.to_thread` | 并发吞吐恢复（调度 9→71） |
| 🟡 MEDIUM | 无界队列 | 加 `maxsize` + 丢弃策略 | 防内存堆积 |
| 🟡 MEDIUM | 无节流后台识别 | 加信号量/并发上限 | 防任务堆积 |

**一句话**：这一轮的共性很清楚——**项目在视觉/电视路径上做对了（`to_thread` 卸载），但 DB 路径和后台任务生命周期管理漏了**。修法也直接：DB 调用套 `to_thread`，shutdown 补齐 4 个 cancel。

---

## 附：复现脚本

| 脚本 | 用途 |
|---|---|
| `final_block2.py` | ★核心证据：同步 vs to_thread 的事件循环调度对照（71 vs 9） |
| `audit_async_db_calls.py` | 32 处同步 DB 调用精确清单（AST 扫描） |
| `task_leak.py` | 4 个泄漏 task 的静态+运行时验证 |
| `shutdown_race.py` | shutdown 与 retention 持锁竞态（阻塞 2.7s） |
| `mk30d.py` | 构造 30 天测试库（104 MB / 30 万行） |

> ⚠️ 沙箱重建会清空 `/tmp`，重跑前需先执行 `mk30d.py`。
