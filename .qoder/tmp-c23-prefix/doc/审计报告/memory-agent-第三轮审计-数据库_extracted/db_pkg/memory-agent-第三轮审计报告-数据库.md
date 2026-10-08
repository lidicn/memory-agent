# memory-agent 第三轮审计报告：数据库速度与稳定性

> 审计对象：`https://github.com/lidicn/memory-agent`（main 分支）
> 代码基线：`src/memory_agent/store.py`（4338 行）
> 审计日期：2026-10-01
> 审计方式：**沙箱实测** —— 构造 82 万行真实规模数据库，实测每条热路径耗时与锁行为

---

## 一、实测环境

在 `/tmp/big.db` 构造真实规模测试库（`/data/workspace` 仅 500M，不足以容纳）：

| 项目 | 值 |
|---|---|
| 数据库体积 | **197.96 MB** |
| 总行数 | **82 万行** |
| 构成 | events 50 万 / behavior_events 20 万 / agent_memories 2 万 / perception_events 10 万 |
| 时间跨度 | 112 天（2026-06 ~ 2026-09） |
| 模式 | WAL，已执行 `ANALYZE` |

所有结论均来自对该库的真实查询与真实并发压测，非代码推断。

---

## 二、核心结论：慢查询会转化为全系统稳定性故障

这是本轮最重要的发现，也是"速度问题"和"稳定性问题"的交汇点。

### 🔴 P0-6　单连接 + RLock 架构下，慢查询阻塞全系统写入

`store.py` 的架构是**单连接 + `threading.RLock`**（注释写在文件头第 9 行）。生产形态下 `AppRuntime` 持有**全局单例 Store**，所有组件共用一把锁。

**实测：1 个慢查询 + 1 个写线程 + 1 个快查询线程，持续 10 秒**

| 操作 | 基线（无竞争） | 有慢查询时 | 退化倍数 |
|---|---|---|---|
| 写入 `insert_events` | 0.1 ms | P50 **657 ms**，max 1311 ms | **≈ 6570×** |
| 快查询 `query_events` | 0.4 ms | P50 **658 ms**，max 1313 ms | **≈ 1645×** |
| 10 秒内写入完成次数 | ~1000（理论） | **14** | ≈ 71× |

**为什么这么严重**：慢查询持锁期间，采集线程、WebUI、MCP 全部排队。这不是"某个页面慢"，而是**后台采集停摆**——家庭行为数据的采集是持续的，写入吞吐从 4306 条/秒掉到个位数，会造成数据积压与丢失。

### 慢查询的来源（实测逐个定位）

| 慢查询 | 实测耗时 | 位置 |
|---|---|---|
| `entity_catalog` GROUP BY entity_id | **621–668 ms** | `store.py` / `insights/repository.py:276` |
| `query_unified_events()` 无过滤 | **689 ms** | `store.py:3301`（内部**两次**全量扫描） |
| `unified_events` ORDER BY server_ts DESC LIMIT 50 | **456 ms** | 视图 `store.py:297` |
| `unified_events_daily` 全量 COUNT | **1215 ms** | 视图 `store.py:336` |
| `person LIKE '%爸%'` | **348 ms** | 前置通配符，索引失效 |

**`entity_catalog` 的数据规模曲线**（线性增长，无缓存）：

```
12.5 万行 → 132 ms
25   万行 → 264 ms
50   万行 → 543 ms
```

**关键：这是每次调用都执行的，不是启动时一次。** `insights/service.py:377` 的 `_device_health()` 每次调用都查 `entity_catalog`；`get_entity_catalog` / `get_device_health` 都是 **MCP 暴露给外部 Agent 的工具**（`mcp_server.py:1031`）。Agent 频繁调用即频繁触发 600ms+ 持锁。

**`query_unified_events` 双重扫描**：先 `SELECT COUNT(*)` 全表聚合，再 `SELECT ... ORDER BY server_ts DESC LIMIT` 全表扫描+排序。两次都在锁内，合计 689ms。同样暴露为 MCP 工具（`mcp_server.py:1222`）。

---

## 三、🔴 P0-7　`purge_old` 冻结全系统 5.2 秒

保留策略清理 `store.py:3752` **单事务删除，全程持锁**：

```python
with self._lock:
    cur = conn.execute("DELETE FROM events WHERE day < ?", (cutoff,))
    ...
    conn.commit()
```

**实测（删除 50 万行）：**

```
purge_old 耗时: 5201 ms        ← 全程持有全局 RLock
清理期间其他查询: P50=0.3ms, max=5181.1ms
                                （基线 0.4ms）
```

**含义**：定期清理触发时，整个系统冻结 5 秒。数据量增长后线性恶化——家庭每天数千条事件，一年即百万级，届时清理耗时将以分钟计。

**建议**：分批删除（`DELETE ... LIMIT 5000` 循环 + 每批 commit），或把清理挪到低峰期独立执行。

---

## 四、🔴 P0-8　`purge_old` 不 VACUUM，130.6 MB 空间永不释放

同一个实验的延续：删除 50 万行后

```
DB 体积: 198.0 MB → 198.0 MB   （删除未回收空间）
```

手动 `VACUUM` 对比：

```
VACUUM 后: 67.4 MB（耗时 1.2s）
→ 可回收 130.6 MB
```

**影响**：项目面向 NAS / 树莓派部署（仓库含 `deploy_nas.sh`），磁盘空间有限。删除事件后磁盘占用不下降，长期运行会持续膨胀。SQLite 会复用这些页给后续 INSERT，所以不是"泄漏"，但**已占用的磁盘永远不会归还文件系统**。

**建议**：`purge_old` 删除量超过阈值（如 1 万行）后执行 `VACUUM` 或 `PRAGMA incremental_vacuum`。

---

## 五、🟠 P1-9　`_lock` 无超时保护，持锁卡住则全系统永久挂起

```python
self._lock = threading.RLock()   # 无 timeout 参数
```

**实测**：一个线程持锁 6 秒，另一个线程的请求等待 **5501 ms** 后才执行——没有超时、没有报错、没有降级。

**风险点**：`sqlite3.connect(timeout=30.0)` 只保护 **SQLite 层**的锁竞争，不覆盖这个**进程内 Python 锁**。若持锁线程因外部 IO（如锁内发起 HTTP 调用）或异常卡死，其余线程将**无限排队**，且无法通过任何超时机制恢复。

**建议**：改用带超时的锁（`threading.Lock` + `acquire(timeout=...)`），或严格保证锁内不做任何 IO。

---

## 六、🟡 P2 级问题

| # | 问题 | 实测证据 |
|---|---|---|
| 1 | `perception_events` 重复索引 | `idx_pe_event_id`（非唯一）与 `idx_perception_events_event_id`（唯一）**同时覆盖 `event_id` 列** → 每次写入维护两份索引，写放大且浪费空间 |
| 2 | 写入每条独立事务 | 实测 **4306 条/秒**；批量 `insert_events(1000条)` 仅 7ms。采集历史回填若走逐条路径，吞吐差一个数量级 |
| 3 | `LIKE '%x%'` 前置通配符 | `store.py:2678/4068/4098` 用 `tags_json LIKE '%...%'`，索引必然失效，348ms 全表扫描 |
| 4 | 视图谓词不下推 | `unified_events` 的 `ORDER BY + LIMIT` 未下推到 UNION 分支，三表全扫再排序（456ms）；`unified_events_daily` 无 `server_ts` 列，按该列过滤直接报 `no such column` |

---

## 七、✅ 已验证健康的部分（避免误判）

审计同样验证了以下内容**没有问题**，供维护者排除疑虑：

| 审计项 | 方法与结果 |
|---|---|
| 索引覆盖完整性 | 用真实库 `init_schema()` 后查 `sqlite_master`（非正则扫描）：**36 张表**中仅 `arena_results`、`arena_snapshots` 与 FTS 内部表无索引，其余齐全 |
| 跨实例并发 | 2 个 Store 实例共享同一 DB：WAL 模式下读写不互斥，写延迟保持 0.1ms，**0 异常** |
| 高并发压测 | 8 秒内 2 写线程 42708 次 + 7 读线程 33623 次，**0 异常**（`timeout=30.0` + WAL 生效） |
| FTS5 | `agent_memories_fts` 2 万行，检索亚毫秒级 |
| WAL 增长 | 写入 2 万条后 WAL 3.96 MB，自动 checkpoint，无失控膨胀 |

---

## 八、修复优先级

| 优先级 | 缺陷 | 建议动作 | 预期收益 |
|---|---|---|---|
| 🔴 P0-6 | 慢查询阻塞全系统 | ① 给 `entity_catalog` 加 TTL 缓存（同类缓存见 `insights/utils.py:570`）；② `query_unified_events` 去掉冗余 COUNT 或改为估算；③ 视图查询下推 | 写入吞吐恢复 2 个数量级 |
| 🔴 P0-7 | `purge_old` 冻结 5.2s | 分批删除 + 每批提交 | 系统不再冻结 |
| 🔴 P0-8 | 130 MB 空间不释放 | 删除量超阈值后 `VACUUM` | NAS 部署磁盘可控 |
| 🟠 P1-9 | `_lock` 无超时 | 改用可超时锁，或严禁锁内 IO | 消除永久挂起风险 |
| 🟡 P2 | 重复索引 / 逐条事务 / LIKE 通配符 | 删重复索引、批量提交、改写前置通配符查询 | 降低写放大 |

**一句话总结**：这个库的存储层设计（WAL + RLock + 超时）本身是**正确的**，并发压测也证明了它。真正的问题是**几个 600ms 级的慢查询被放在全局锁内执行**——它们把"查询慢"放大成了"整个系统停摆"。优先修 P0-6，收益最大。

---

## 附：复现脚本（沙箱内保留）

| 脚本 | 用途 |
|---|---|
| `perf4.py` | 真实 Store 方法耗时（unified_events / entity_catalog 等） |
| `perf5.py` | 跨实例并发验证（WAL 读写不互斥） |
| `perf6.py` | **同实例锁竞争**——P0-6 核心证据 |
| `perf7.py` | `purge_old` 持锁 5.2s + 空间不释放 |
| `perf8.py` | FTS 检索 + 写入吞吐 + WAL 增长 |
| `perf9.py` | RLock 无超时验证 + VACUUM 空间回收对比 |
| `conc.py` | 8 秒高并发压测 |

测试库位于 `/tmp/big.db`（197.96 MB / 82 万行），重跑脚本前需确认其存在。
