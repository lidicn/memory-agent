# memory-agent 代码审计报告（稳定性 / 功能性）

- **审计对象**：`https://github.com/lidicn/memory-agent`（main 分支，340 个文件，Python 约 4.8 万行 `src/`）
- **审计方式**：只读静态审计（AST 模式扫描 + 核心链路逐行阅读 + 跨模块一致性核对），未修改仓库任何文件
- **审计范围**：进程生命周期、采集链路、存储层、并发与异步模型、外部依赖调用、时间语义、路由装配
- **结论概览**：共发现 **4 个 P0（可致服务整体不可用或数据永久丢失）**、**8 个 P1**、**9 个 P2**
- **总体评价**：代码工程质量中上——异常处理、降级、注释密度都好于同类项目，且能看出此前已被审计过多轮（注释里保留大量"审计 P0-x 修复"痕迹）。剩余问题集中在**共享资源生命周期**（SQLite 单连接、fire-and-forget 任务、未取消的后台任务）和**时间源不一致**两类系统性问题上，单点看似无害，组合起来会放大成整机故障。

---

## 一、缺陷总览

| # | 级别 | 问题 | 位置 |
|---|---|---|---|
| 1 | **P0** | MCP 工具关闭了 Store 的**共享** SQLite 连接，一次调用即打挂全库 | `mcp_server.py:1249,1287` |
| 2 | **P0** | 启动流程 `res` 变量可能未绑定 → NameError → 服务起不来（与注释语义相反） | `runtime.py:183-193` |
| 3 | **P0** | HA 取数批次失败被静默吞掉，采集水位照常前进 → **事件永久丢失** | `ha_client.py:214-238` → `poller.py:384` |
| 4 | **P0** | 关闭流程漏取消 4 个后台任务 + 未停 researcher，且先关库 → 脏写与异常风暴 | `runtime.py:627-680` |
| 5 | P1 | 时间源双轨：`datetime.now()`(UTC) 与 `now_local()`(本地) 混用，8 小时偏移 | `causal_scanner.py:44` 等 46 处 |
| 6 | P1 | 共享 SQLite 连接在多个模块绕过 `Store._lock` 使用（跨线程并发 / 误提交） | `rule_engine.py` 等 12 处 |
| 7 | P1 | `asyncio.create_task` 未保存强引用 → 后台任务被 GC 中途销毁 | `vision_service.py:1078` 等 3 处 |
| 8 | P1 | SSE 流 `await queue.get()` 无超时无兜底 → 连接与协程永久泄漏；`_CONV` 无界增长 | `debug_routes.py:366,51` |
| 9 | P1 | MQTT 阻塞式建连被直接放进事件循环 → broker 不可达时全站卡死 | `mqtt_bridge.py:63-97` / `runtime.py:405` |
| 10 | P1 | `await self.llm.close()` 等待同步方法 → TypeError 被吞，LLM 连接从未关闭 | `runtime.py:664` / `llm_client.py:119,382` |
| 11 | P1 | 路由遮蔽：`/api/behaviors` 双注册，视觉行为列表接口永远不可达 | `behavior_routes.py:768` vs `vision_routes.py:415` |
| 12 | P1 | 阻塞式 SQLite 查询直接进入事件循环 | `behavior_routes.py:538-552` |
| 13 | P2 | `Store` 中 `add_bug_report` / `list_bug_reports` 各重复定义两遍 | `store.py:2127,2159,2145,2177` |
| 14 | P2 | HA 客户端每次调用新建 `httpx.Client()`，无连接池 | `ha_client.py` 全文 |
| 15 | P2 | MariaDB 客户端共享单连接且无锁，非线程安全 | `ha_db.py:92-96` |
| 16 | P2 | 每个请求两次读盘（config.json + users.json） | `app.py:286-288`、`auth.py:160-185` |
| 17 | P2 | 指标落盘非原子 read-modify-write，并发 POST 丢更新 | `app.py:452-475` |
| 18 | P2 | 自我日记排程用 UTC、取"昨天日记"逻辑可能在当天取到自己 | `runtime.py:248-260` |
| 19 | P2 | LLM 重试逻辑自相矛盾：4xx 也重试，且把刚 raise 的异常又抓回重试 | `llm_client.py:218-259` |
| 20 | P2 | DB 自检恢复不加锁；坏库无备份时仍带病运行 | `store.py:525-573` |
| 21 | P2 | 每次启动 DROP + 重建 FTS5 虚表；两次都失败则静默退化为纯向量检索 | `store.py:669-700` |

---

## 二、P0：可致服务整体不可用或数据永久丢失

### 1. MCP 工具关闭了 Store 的共享 SQLite 连接 —— 一次调用打挂全库

**位置**：`src/memory_agent/mcp_server.py:1249`（取连接）、`1287`（关闭）

```python
def _query():
    conn = rt.store.connect()      # ← 返回 Store 的进程级共享连接 self._conn
    try:
        ...
    finally:
        conn.close()               # ← 把共享连接关了，self._conn 仍指向它
```

**为什么是致命的**：`Store.connect()`（`store.py:502-517`）是"单例连接"模式——`self._conn` 非空就直接返回。`_query()` 关闭该连接后，`self._conn` **并没有被置空**，于是：

- 后续任何 `store.connect()` 都返回这个已关闭的连接；
- 全进程所有 DB 操作（采集入库、登录鉴权、WebUI 查询、MCP 工具）都会抛 `sqlite3.ProgrammingError: Cannot operate on a closed database.`；
- 恢复手段只有一个：**重启进程**。

**触发路径**：任意 MCP 客户端调用一次 `query_unified_events`（文档里标注的"vMA-1.3 多模态统一查询"入口工具）即可触发，无需特殊参数。

**修复**：

```python
def _query():
    with rt.store._db() as conn:   # 用现有上下文管理器，取锁且不负责关闭
        ...
```
或若确需独立连接，自行 `sqlite3.connect(rt.store.db_path)` 并在 finally 中只关自己那个（不要碰 `store._conn`）。**同时建议给 `Store` 增加一条自检**：`connect()` 返回前校验 `self._conn` 未关闭，避免同类问题再次静默扩散。

> 补充：这是全仓唯一一处 `store.connect()` 后 `conn.close()` 的地方（其余 30+ 处都只用不关），说明是单例实现与"用完即关"直觉的冲突，属于典型的资源所有权混淆。

---

### 2. 启动流程 `res` 可能未绑定 —— 首次对账失败直接导致启动失败

**位置**：`src/memory_agent/runtime.py:183-193`

```python
try:
    res = await asyncio.to_thread(self.identity_reconciler.reconcile)
    print(...)
except Exception as exc:  # noqa: BLE001 - 对账失败不应阻断启动
    print(f"[Identity] 首次对账失败（不影响启动）: {exc}")
self._publish_health_changes(res if isinstance(res, dict) else {})   # ← res 未绑定
```

只要 `reconcile()` 抛异常（HA 不可达是最常见场景，注释自己也承认是"常态"），`res` 从未被赋值，下一行立刻抛 `NameError`。该异常不在 try 内，会一路穿透 `startup()` → `start_runtime()` → ASGI lifespan → **uvicorn 启动失败**，表现为容器反复重启。

**讽刺之处**：这行代码上方三行明确写着"对账失败不应阻断启动"。

**修复**：

```python
res: dict = {}
try:
    res = await asyncio.to_thread(self.identity_reconciler.reconcile) or {}
    ...
except Exception as exc:
    print(...); res = {}
self._publish_health_changes(res)
```

---

### 3. HA 取数失败被静默吞掉，采集水位照常前进 —— 事件永久丢失

**位置**：`src/memory_agent/ha_client.py:196-238`（失败处理）+ `src/memory_agent/poller.py:384-385`（推进水位）

`get_history()` 中三类失败全部"吞掉"：

```python
except Exception as e:
    print(f"[HA] 历史查询失败({len(batch)}个实体): {e}")
    continue                                  # ① 单批请求异常 → 跳过该批

if response.status_code != 200:
    print(f"[HA] 历史查询 HTTP {response.status_code}")
    continue                                  # ② 单批 HTTP 429/5xx → 跳过该批

except Exception as e:
    print(f"[HA] 历史响应解析失败: {e}")
    continue                                  # ③ 响应解析失败 → 跳过该批
...
except Exception as e:
    print(f"获取历史数据失败: {e}")
    return {}                                 # ④ 整体异常 → 返回空，不抛错
```

调用方 `poller._fetch_history` 只在**异常**时才把错误塞进 `errors` 列表；而 `get_history` 从不抛异常，于是 `_execute()` 走到 `on_success()` → `_update_last_poll_time()` → **把 `last_poll_time` 推进到当前时刻**。

**后果**：这次失败窗口内的事件**永远不会再被采集**（增量采集只从水位往后取），且与"实体数量大 → 分批多 → 命中概率高"正相关——房间实体越多，丢数据越频繁、越静默。任务的 `result.errors` 始终为空，运维看到的是"采集成功，0 异常"。

**同类前科**：`ha_client.py:167` 的注释记录了另一个"返回 0 条但水位仍前进 → 事件永久丢失"的时区 bug（已修），说明这个失效模式在项目中反复出现，需要从机制上堵住。

**修复**（两处都要改）：

1. `get_history()` 返回 `(series, errors)` 或抛 `HAHistoryError`，让失败对上层可见；至少把失败批次记入返回值。
2. `poller._execute()` 增加"失败即不推进水位"的门禁：

```python
if errors:
    await self._finish(job_id, "done_with_errors", started,
                       result={..., "errors": errors[:50]})
    return          # 不调用 on_success，水位保持不动，下轮自动重试
```

---

### 4. 关闭流程漏取消后台任务 + 先关库后收尾 —— 脏写与异常风暴

**位置**：`src/memory_agent/runtime.py:627-680`

`shutdown()` 只取消了 7 个任务：`_sweep_task`、`_identity_task`、`_mqtt_task`、`_backup_task`、`_tpl_validate_task`、`_activity_task`、`_livingroom_ai_task`。

**漏掉的**：

| 未处理的后台任务 | 创建位置 |
|---|---|
| `_retention_task`（数据保留清理） | `runtime.py:175` |
| `_candidate_promotion_task` | `runtime.py:228` |
| `_causal_scan_task` | `runtime.py:231` |
| `_self_diary_task` | `runtime.py:237` |
| `ResearcherService._task`（`stop()` 存在但从未被调用） | `runtime.py:234` / `researcher.py:383` |

而 `shutdown()` 最后一步是 `await asyncio.to_thread(self.store.close)`（关闭共享连接）与 `ha_db.close()`、`mqtt.close()`（`_closed=True` 后 publish 一律空转）。

**后果**：滚动更新 / `docker compose restart` / SIGTERM 之后，上述 5 个任务仍在跑，并对**已关闭的 SQLite 连接**或**已关闭的 HA DB 连接**发起写操作 → 大量 `ProgrammingError` 刷屏；`purge_old` 这类 DELETE 若执行到一半进程退出，还可能留下 WAL 未 checkpoint 的状态。热重载（uvicorn `--reload`）场景下更会出现新旧两代任务同时写库的竞态。

**修复**：

```python
async def shutdown(self) -> None:
    for attr in ("_sweep_task", "_identity_task", "_mqtt_task", "_backup_task",
                 "_tpl_validate_task", "_activity_task", "_livingroom_ai_task",
                 "_retention_task", "_candidate_promotion_task",
                 "_causal_scan_task", "_self_diary_task"):
        task = getattr(self, attr, None)
        if task is not None:
            task.cancel()
    try:
        self.researcher.stop()
    except Exception:
        pass
    ...
    # 取消后统一 gather 等待落地，再关连接
    await asyncio.gather(*[t for t in cancelled if t], return_exceptions=True)
    await asyncio.to_thread(self.store.close)
```

另外建议 `stop_runtime()` 结束后把 `_runtime` 置回 `None`，否则"启动→关闭→再启动"会复用一个 `_started=False` 但资源已释放的半死实例。

---

## 三、P1：稳定性 / 功能性明显受损

### 5. 时间源双轨制：UTC 与本地时间混用，稳定偏移 8 小时

**根因**：`store.now_local(tz)`（`store.py:395`）显式按 `tz_offset_hours` 生成本地时间，而全仓另有 **46 处**使用 `datetime.now()`——容器通常无 TZ，返回的是 **UTC**。所有落库的 `day` / `server_ts` 都是本地时间，因此任何用 `datetime.now()` 构造的查询条件都会偏移一个时区。

**确凿的危害点**：

| 位置 | 现象 |
|---|---|
| `causal_scanner.py:44` | `today = datetime.now().strftime("%Y-%m-%d")` 拿 UTC 日期去匹配本地 `day` 列 → 当地 00:00–08:00 之间去重完全失效 → **同一条告警每天重复写入** |
| `mcp_server.py:1995` | `cutoff` 用 UTC 减 N 天，却与本地 `created_at` 比大小 → 日记列表多/少一天 |
| `mcp_server.py:2023` | `today` 用 UTC 日期查本地 `day` → 当天事件查不到（夜间尤甚） |
| `runtime.py:263` | 自我日记"今天"取 UTC 日期，夜间生成的日记内容取自昨天 |
| `daily_profile.py:86-87` | 画像窗口整体错位一天 |
| `behavior_routes.py:760` | 变化归因基线时间戳偏移 8 小时 |
| `store.py:2134` | `bug_reports.created_at` 用 `time.strftime`(=UTC)，其余表用本地时间，跨表排序错乱 |

**修复**：全局禁用裸 `datetime.now()`，统一 `now_local(cfg.tz_offset_hours)`。可用一条 CI 规则（`grep -rn "datetime.now()" src/` 非零即失败）长期守住——这是本项目已经踩过两次的坑（`ha_client.py:167` 注释即前科）。

---

### 6. 共享 SQLite 连接在多处绕过 `Store._lock`

**背景**：`Store` 是"**单连接 + `RLock`**"设计（`store.py:494-523`），`_db()` 上下文管理器负责取锁。但多个外部模块直接 `store.connect()` 后裸用：

| 模块 | 位置 |
|---|---|
| `rule_engine.py` | `195`（读）、`267`、`303`、`321`、`335`、`370`、`697`（**写 + commit**） |
| `candidate_promotion.py` | `1099`、`1391`、`1432` |
| `signal_learning.py` | `36` |
| `causal_scanner.py` | `46`（有锁，正确示范） |

**风险**：这些函数都经 `asyncio.to_thread` 在线程池里跑，与 Store 自身的加锁路径（采集 `insert_events` 大批量写、API 查询）**并发操作同一个 `sqlite3.Connection`**：

- 多线程并发 `execute()` 可能触发 `sqlite3.ProgrammingError` / `OperationalError: database is locked`；
- 更隐蔽的是**事务交叉**：`rule_engine.py:370` 的裸 `conn.commit()` 会把另一个线程正在进行的批量 `executemany` **提前提交**，破坏采集批次的原子性，在中断时留下半批数据。

此外 AST 扫描发现 `Store` 自身还有 **15 个方法**（`list_bug_reports`、`get_idempotency`、`list_agent_memories`、`search_agent_memories_fts`、`list_activity_rules`、`delete_activity_rule` 等）在既不走 `_db()` 也不持锁的情况下访问连接。

**修复**：对外暴露 `Store` 的 `_db()` 为公开的 `transaction()` 上下文管理器，并禁止模块外直接调用 `connect()`（可用 `Store._conn` 改名 `_conn_private` + lint 规则约束）；`rule_engine` / `candidate_promotion` / `signal_learning` 全部改走它。

---

### 7. `asyncio.create_task` 未保存强引用 —— 后台任务被 GC 中途销毁

**位置**：

- `vision_service.py:1078` —— `record_face_event()` 收到 TV 人脸事件后 `create_task(to_thread(analyze_room))`
- `api/debug_routes.py:330` —— 触发调试 LLM run
- `api/insight_routes.py:385`、`390` —— 触发研究员 job / run_all

asyncio 事件循环**只持有任务的弱引用**。没有外部强引用时，正在运行的 Task 可能被垃圾回收，表现为：

- "Task was destroyed but it is pending!" 警告；
- 人脸事件的 VLM 分析**静默不执行**（`record_face_event` 已返回 `{"vlm_dispatched": True}`，调用方以为成功）；
- 与第 8 条联动时还会让 SSE 流永久挂起。

**修复**（标准做法）：

```python
self._bg_tasks: set[asyncio.Task] = set()
task = asyncio.create_task(...)
self._bg_tasks.add(task)
task.add_done_callback(self._bg_tasks.discard)
```

---

### 8. SSE 流无超时、无兜底终止；`_CONV` 无界增长

**位置**：`api/debug_routes.py:354-372`、`115-129`、`51`

```python
while True:
    item = await own.get()      # ← 只有收到 _TERMINAL 才 break
    if item is _TERMINAL:
        break
    yield item
```

`_TERMINAL` 只在 `DebugRun.finish()` 中被投递。以下两种情况下 `finish()` **永远不会被执行**：

1. `_execute_run` 这个 Task 因第 7 条被 GC 销毁 —— 其 `run.finish()`（在函数末尾 / except 分支）不再执行；
2. `_register()`（`115-129`）在 `_RUNS` 超限时把 run 从字典里剔除——已在流式中的客户端持有的队列再也不会收到终止信号。

结果：每一个这样的请求都会留下一个**永久挂起的协程 + 一条不释放的 SSE 连接**，随时间累积直至耗尽连接/内存。

另外 `_CONV: dict[str, list]`（第 51 行）以 `conversation_id` 为键缓存完整消息上下文，**没有任何淘汰机制**，而 `_MAX_RUNS=200` 只约束 `_RUNS`。客户端若使用随机 `conversation_id`，内存无界增长（每条还含 `MAX_TOOL_RESULT_CHARS` 级别的工具输出）。

**修复**：

```python
while True:
    try:
        item = await asyncio.wait_for(own.get(), timeout=30)
    except asyncio.TimeoutError:
        yield sse_pack("ping", {})          # 心跳保活
        if run.terminal or run.run_id not in _RUNS:
            break
        continue
    ...
```
同时给 `_CONV` 加 LRU/TTL 淘汰，并让 `_register()` 淘汰 run 时同步 `finish()` 已订阅的流（剔除前先 `r.finish()`，目前只对"僵尸 run"做了，对**正常超限淘汰**没做）。

---

### 9. MQTT 阻塞式建连被直接放进事件循环 —— broker 不可达时全站卡死

**位置**：`mqtt_bridge.py:63-97`（`_default_client_factory`）+ `runtime.py:405`（调用点）

```python
client.connect(host, port, keepalive=60)   # paho 阻塞式 TCP 连接，无超时参数
```

调用链：`_periodic_mqtt_presence()`（async）→ `self.mqtt.publish_presence(...)` → `publish()` → `_ensure_client()` → 上述 `connect()`。

**对比**：同一函数里 `store.recent_presence` 老老实实用了 `await asyncio.to_thread(...)`，唯独紧邻的 `publish_presence` 没有。当 broker 主机不可达、或防火墙静默丢包时，TCP connect 会阻塞到系统超时（常见 2 分钟量级），**整个事件循环停摆**——所有 HTTP 请求、SSE 流、采集任务同时冻结。

**修复**：

```python
ok = await asyncio.to_thread(self.mqtt.publish_presence, members, now.isoformat())
```

并给 paho 的 `connect()` 加超时（`client.connect(host, port, keepalive=60)` 前设置 socket 超时，或改用 `connect_async()` + `loop_start()` 的异步建连模式）。

---

### 10. `await self.llm.close()` 等待同步方法 —— LLM 连接从未真正关闭

**位置**：`runtime.py:664` + `llm_client.py:382`、`119`

```python
# runtime.py
await self.llm.close()      # LLMRouter.close() 是 def，返回 None → await None → TypeError
```

`LLMRouter.close()`（`llm_client.py:382`）是普通同步方法且返回 `None`，`await None` 抛 `TypeError: object NoneType can't be used in 'await' expression`，被外层 `except Exception` 吞掉并打印"关闭 LLM 客户端异常"——**看似有日志，实则一行都没执行到**。

即便执行到，`LLMProvider.close()`（`119-129`）也只是：

```python
loop.create_task(self._client.aclose())   # 不等完成
self._client = None                        # 立刻置空，task 可能随循环关闭而丢失
```

**修复**：把 `LLMRouter.close()` / `LLMProvider.close()` 改为 `async def` 并 `await`（关闭期在 lifespan 内，事件循环仍然存活）。

---

### 11. 路由遮蔽：`/api/behaviors` 双注册，视觉行为列表接口永远不可达

**位置**：`behavior_routes.py:768` 与 `vision_routes.py:415`

```python
# behavior_routes.py（_MODULES 中排在前面）
Route("/api/behaviors", behaviors_current, methods=["GET"])     # 当前 canonical 行为状态
# vision_routes.py（排在后面，永不命中）
Route("/api/behaviors", behaviors_query,   methods=["GET"])     # 视觉行为事件列表
```

两者语义完全不同：`behaviors_current` 返回"谁·在哪·做什么"的推理解果；`behaviors_query` 返回 `list_behavior_events` 的原始事件列表（支持 `member/room/from/to/limit`）。

**实际影响**：`vision_routes` 的 `/api/behaviors/{event_id}/label`（人工标注闭环）依赖前端先从 `/api/behaviors` 拿到 `event_id`，而该列表接口不可达 → **标注闭环事实上断链**。目前前端 `api.js:268` 定义了 `behaviors()` 但无调用点，所以症状尚未暴露，属于"埋着的雷"。

**修复**：把 `vision_routes` 的列表接口改到独立路径（如 `/api/vision/behavior-events`），并在路由装配 `api/__init__.py::get_routes()` 中增加**重复路径检测**（启动时对 `(path, method)` 去重，发现冲突直接报错），让这类问题无法再悄悄引入。

> 附带：`app.py:765-780` 的 `_mount_slash_fix` 把 `/mcp` 重写为 `/mcp/` 之后再交给路由，导致 `_build_routes()` 中精心构造的 `Route("/mcp", _MCPRootApp())`（`682-688`）成为死代码（功能目前由 `Mount("/mcp")` 兜住，但两处意图重叠，容易在后续改动中误删其一）。

---

### 12. 阻塞式 SQLite 查询直接进入事件循环

**位置**：`api/behavior_routes.py:538-552`

```python
async def behaviors_task_records(request):
    conn = rt.store.connect()
    ...
    with rt.store._lock:                      # 取了锁，但仍是同步阻塞
        rows = conn.execute(sql, args).fetchall()
```

`async def` 里同步执行 SQL，且持有全局 `Store._lock`。当 `task_records` 表较大或锁被采集批次占住时，会**阻塞整个事件循环**（其余请求全部排队）。同文件其余接口、以及 `mcp_server` 的同类查询都正确使用了 `asyncio.to_thread`，这一处是遗漏。

**修复**：整体包进 `asyncio.to_thread`。

---

## 四、P2：中低优先级 / 技术债

13. **重复方法定义**：`store.py` 中 `add_bug_report`（`2127`、`2159`）与 `list_bug_reports`（`2145`、`2177`）各定义两遍且函数体完全相同，后者静默覆盖前者。当前无害，但一旦有人只改第一份，修改会神秘失效。建议删除重复项并加 lint（`flake8 F811`）。

14. **HA 客户端无连接池**：`ha_client.py` 每个方法都 `with httpx.Client() as client:` 新建连接（`get_states` / `get_state` / `call_service` / `get_status` / `get_history`）。`get_state` 在 VLM gate 里是"每房间每轮一次"，高频巡检下反复 TCP/TLS 握手，并带来 fd 压力。建议改为实例级 `httpx.Client`（生命周期与 `HAClient` 对齐，`reconfigure` 时重建）。

15. **MariaDB 连接非线程安全**：`ha_db.py:92-96` 的 `_get_conn()` 共享单个 `pymysql` 连接且**无锁**，而调用方经 `asyncio.to_thread` 可能多线程并发（如手动采集与周期任务重叠）。建议按线程分配连接，或加互斥锁 + 单飞。

16. **每请求两次读盘**：`app.py:286-288` 每次鉴权都 `get_config()`（读 `config.json` + JSON 解析；且 `jwt_secret` 缺失时该函数会 `raise RuntimeError`，把**每一个请求**变成 500），`auth.py:160-185` 的 `verify_token` 再读一次 `users.json`。高频 API 场景下是可观的 I/O 放大。建议配置与用户数据加 mtime 缓存或进程内缓存 + 失效通知。

17. **指标落盘非原子**：`app.py:452-475` 的 `metrics_ingest_endpoint` 对 `af_metrics.json` 做"读-改-写"且无锁，并发 `POST /api/metrics/ingest` 会丢失更新。建议加 `threading.Lock` + `fcntl.flock`，或改用 SQLite 表承接。

18. **自我日记排程问题**：`runtime.py:248` 用 `datetime.now()` 计算 23:00（应为本地时区，见第 5 条）；`260` 行 `diaries[-1]` 取"昨天日记"依赖排序后的最后一条，若当天被重复触发（如 `generate_self_diary` 工具手动调用过），会引用到自己而非昨天。

19. **LLM 重试逻辑自相矛盾**：`llm_client.py:218-259` 的 `for _ in range(2)` 重试循环里，`except (LLMError, ...)` 会把 251 行刚刚 `raise LLMError(...)` 的 4xx 错误（401 鉴权失败、429 限流）又抓回来 `sleep(1)` 后重试一遍——既浪费 1 秒 × 后端数，也让"鉴权失败"与"临时故障"在指标上无法区分。建议只对 `5xx` / 超时 / 连接错误重试，4xx 直接抛给 router 走 fallback。

20. **DB 自检恢复不加锁**：`store.py:525-573` 的 `check_and_recover()` 在未持锁的情况下 `conn.close()`、`self._conn = None`、`shutil.copy2` 覆盖主库；若与其它线程并发访问会踩空。此外 integrity 失败且无备份时仅返回 `error` 字段，`runtime.py:150-153` 只打印一行警告后**带着坏库继续运行**。建议无可用备份时 fail-fast，或以只读模式启动并明确告警。

21. **每次启动重建 FTS5 索引**：`store.py:669-700` 每次启动都 `DROP TABLE IF EXISTS agent_memories_fts` 后重建虚表与三个触发器；`trigram` 失败会再 `DROP` 一次重试 `unicode61`。两次都失败时，`search_agent_memories_fts`（`3941`）会静默返回空列表并退化为纯向量检索——关键词检索能力悄悄消失，健康页上也看不出来。建议：仅在 schema 版本变化时重建；失败时把"关键词检索不可用"明确暴露到 `/api/health`。

---

## 五、修复优先级建议

**第一批（建议立即修，改动量都很小）**

1. `mcp_server.py:1287` 删掉 `conn.close()` —— 一行改动，消除整机级故障
2. `runtime.py:193` 给 `res` 加默认值 —— 一行改动，消除启动失败
3. `runtime.py:405` 给 `publish_presence` 套 `asyncio.to_thread` —— 一行改动，消除事件循环阻塞
4. `runtime.py:664` 把 `LLMRouter.close()` 改 async —— 小改动
5. `runtime.shutdown()` 补齐漏掉的 5 个任务 + `researcher.stop()`

**第二批（机制性修复）**

6. `ha_client.get_history` 失败可见化 + `poller` 失败不推进水位（堵住数据永久丢失）
7. `asyncio.create_task` 统一走 `_bg_tasks` 集合
8. `debug_stream` 加 `wait_for` 超时与心跳；`_CONV` 加淘汰
9. 全局统一时间源为 `now_local()`，并加 CI grep 规则

**第三批（结构性治理）**

10. `Store` 对外只暴露 `transaction()`，禁止模块外直连（解决并发与事务交叉）
11. 路由装配期做 `(path, method)` 冲突检测
12. 配置/用户数据读写加缓存与锁

---

## 六、审计局限说明

- 本次为**静态审计**，未部署运行（沙盒环境为 Python 3.10，项目要求 ≥3.11，未执行测试套件），因此运行时才显现的问题（数值边界、依赖版本差异、真实 HA/chroma 交互）不在覆盖范围内。
- 未做安全专项审计（鉴权/越权/注入）。审计过程中顺带注意到若干安全相关点（如调试令牌白名单、`/api/debug/*` 的 `_tb()` 错误信息回传），如有需要可另开一轮。
- 结论中的行号基于 main 分支当前快照，代码变动后请以符号名为准。
