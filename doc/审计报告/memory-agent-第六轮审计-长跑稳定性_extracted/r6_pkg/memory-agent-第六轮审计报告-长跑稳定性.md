# memory-agent 第六轮审计报告：长跑稳定性 —— 幂等、事务原子性、重试

> 审计日期：2026-10-02
> 方法论：**sota-async-concurrency skill** `rules/07-audit-bug-catalog.md`（AUDIT 模式）+ 沙箱实测
> 本轮方向（自主选定）：**长跑稳定性** —— 重试风暴、惊群、幂等与重复执行、资源增长
> 测试库：`/tmp/ma30.db`（30 天 / 103.9 MB / events 30 万行）

---

## 一、本轮最重要发现（两个高危，均已实测复现）

### 🔴 CRITICAL-1　幂等键 TOCTOU：并发下写工具重复执行

**位置**：`mcp_server.py:904–930`（`_tracked_call_tool` 分发层）

代码结构是典型的 **check-then-act，且 check 与 act 之间横跨 `await`**：

```python
cached = _idem_get(cache_key)                 # CHECK
if cached is not None:
    return _idem_result(cached)
result = await MCPServer.call_tool(...)       # ACT  ← await，窗口在此打开
_idem_save(cache_key, ...)                    # SAVE ← 太晚
```

**实测（绕过权限层，注入 mock 工具，控制工具耗时）**：

| 工具耗时 | 并发携带同一 key | 实际执行次数 | 结论 |
|---|---|---|---|
| 0 ms | 5 | **1** | ✓ 幂等生效（顺次执行，未真正交错） |
| 50 ms | 5 | **5** | ✗ 重复执行 5 次 |
| 300 ms | 5 | **5** | ✗ 重复执行 5 次 |
| 1 s | 5 | **5** | ✗ 重复执行 5 次 |

**即：只要工具耗时 > 0，所有并发调用全部穿透。** 幂等保证**仅在串行调用下成立**。

**真实触发场景**：网络重试、Agent 并发调用同一工具、客户端超时重发。受影响的都是**写工具**——`report_bug`、`save_agent_memory`、`write_self_diary` 等，重复执行意味着重复写入、重复上报。

**加剧因素**：`store.get_idempotency()`（`store.py:2441`）**未持有 `_lock`**，而 `save_idempotency()`（`:2454`）持锁——读写两侧保护不对称。

**修复**：把 CHECK+ACT+SAVE 变成原子操作——用 per-key `asyncio.Lock`（或 DB 层 `INSERT ... ON CONFLICT DO NOTHING` 占位先行，成功后再写结果）。占位方案更优，因为它跨进程也成立。

---

### 🔴 CRITICAL-2　18 处绕过 `_lock` 直连，事务原子性被破坏

**位置**：`store.py:503` 的 `connect()` 返回**单例共享连接**；但 18 个调用点拿到连接后**直接 `conn.execute()`，不持有 `_lock`**。

| 文件 | 处数 |
|---|---|
| `rule_engine.py` | 7 |
| `candidate_promotion.py` | 4 |
| `task_record.py` | 2 |
| `mcp_server.py` | 2（`:608`、`:1249`） |
| `causal_scanner.py` / `signal_learning.py` / `api/behavior_routes.py` | 各 1 |

**实测（构造线程 A 持锁跑多语句事务 + 线程 B 无锁插入并提交）**：

```
A 事务中途看到自己插的行数: 1
最终表内容: ['A_step1', 'B_rogue', 'A_step2']

🔴 确认：B 的 commit 把 A 尚未完成的事务一并提交
   A 的「要么全成功要么全失败」保证被破坏（原子性失效）
```

**为什么生产可达**：`causal_scanner.scan` 由 `runtime.py:370` 经 `asyncio.to_thread` 在**工作线程**执行，而 API 请求走持锁的 `db_query()`——两者真并发。`signal_learning.list_rules` 同样经 `to_thread`（`mcp_server.py:1867`）。

**后果**：后台任务的一次 commit 可能提前提交 API 路径尚未完成的事务；反之 API 的 rollback 也可能回滚掉后台任务已"提交"的写入。数据一致性无保证。

**修复**：这 18 处改用 `with store._db() as conn:`（已有该上下文管理器，持锁并 yield 连接），或封装 `store.execute(sql, args)` 统一入口。

---

## 二、🟠 MEDIUM 级

### M-1　幂等 TTL 用日期存储，实际有效期偏离承诺 2 倍

`expires_at` 以 `%Y-%m-%d` 存储，但 TTL 按**小时**传入。实测（复刻 `save_idempotency` 算法）：

| 创建时刻 | ttl_hours=24 的实际失效时刻 | 实际有效时长 |
|---|---|---|
| 00:30 | 次日次日 00:00 | **47.5 小时** |
| 08:00 | 同上 | **40.0 小时** |
| 14:00 | 同上 | **34.0 小时** |
| 23:30 | 同上 | **24.5 小时** |

文档承诺「24h 内再次调用直接返回首次结果」（`mcp_server.py:127`），实际 **24.5–48 小时**。

更糟的是精度塌陷：`ttl_hours=1` / `2` / `12` 全部得到**同一个日期串**，短 TTL 完全失效。

**影响**：写工具（如 `report_bug`）在窗口内被静默吞掉，调用方以为执行了实际没有。

### M-2　LLM 重试无退避、无 jitter

`llm_client.py:218`：`for _ in range(2)` **两次立即重试，中间无 sleep**。若端点超时（默认 timeout 可能达数十秒），单次业务请求会放大为 2 倍负载——恰好在 Provider 已经不健康时加倍施压，是典型的 retry-storm 放大器。

**对比：项目其他地方做对了** —— MQTT 用 `reconnect_delay_set(5, 60)`（paho 指数退避，5s→60s 封顶），视觉取帧用 `time.sleep(0.5*(attempt+1))` 线性退避。LLM 路径是遗漏。

**建议**：加指数退避 + 全抖动（`random.uniform(0, min(cap, base*2**n))`），并接重试预算。

### M-3　视觉取帧退避缺 jitter

`vision_service.py:241`：`time.sleep(0.5 * (attempt + 1))` 为固定线性退避。多摄像头同时失败时会**同步重试**（惊群）。仅 2 次重试，影响有限，但加 jitter 成本极低。

---

## 三、🟢 LOW（诚实说明：可忽略）

| 项 | 实测 | 判定 |
|---|---|---|
| `vision_service._hour_calls` 无界增长 | key 为 `(room, 'MMDDHH')`，无清理；模拟 365 天 × 24 小时 × 8 房间 → 5,376 条目 / **0.14 MB** | 年增长 0.1 MB，**可忽略**，不建议优先修 |

其余 6 个运行态 dict（`_last_call` / `_backoff_until` / `_fail_streak` / `_light_cache` / `_skip_counts` / `_last_result`）均以 room / entity_id 为 key，**有界**，无问题。

**为避免误判，本轮也确认了以下不是问题**：
- `tv_service.py:168`、`vision_service.py:241` 的 `time.sleep` 均在同步函数内且调用链已 `to_thread` 卸载（不阻塞事件循环）
- `mqtt_bridge.py:79` 的 `reconnect_delay_set(5, 60)` 是合格的指数退避

---

## 四、修复优先级

| 优先级 | 缺陷 | 动作 |
|---|---|---|
| 🔴 CRITICAL-1 | 幂等 TOCTOU | CHECK/ACT/SAVE 原子化：per-key `asyncio.Lock`，或 DB 占位行（`INSERT OR IGNORE` 先占位） |
| 🔴 CRITICAL-2 | 18 处无锁直连 | 改用 `with store._db() as conn:`，或统一 `store.execute()` 入口 |
| 🟠 M-1 | TTL 日期粒度 | `expires_at` 改存完整时间戳（`%Y-%m-%d %H:%M:%S` 或 epoch） |
| 🟠 M-2 | LLM 重试无退避 | 加指数退避 + jitter + 重试预算 |
| 🟢 M-3 | 取帧退避无 jitter | 加随机抖动 |

**一句话**：这一轮的两个 CRITICAL 有同一个根因——**保护机制写在了错误的层级**。幂等把"检查"和"写入"分成了两步却没加锁；Store 设计了 `RLock` 单例保护，却留了 18 个绕过它的后门。两者都不需要改架构，只需要把保护下沉到正确的位置。

---

## 附：复现脚本

| 脚本 | 用途 |
|---|---|
| `idem_toctou2.py` | ★CRITICAL-1：并发幂等穿透（0/50/300/1000ms 四档对照） |
| `lock_bypass.py` | ★CRITICAL-2：无锁直连破坏事务原子性（A_step1 被 B 提交） |
| `idem_ttl.py` | M-1：TTL 日期粒度偏差表 |
| `leak_growth.py` | 运行态 dict 是否有界（含 7 个 dict 逐一定界） |
| `mk30d.py` | 构造 30 天测试库 |

> ⚠️ 沙箱重建会清空 `/tmp`，重跑前先执行 `mk30d.py`。
> `idem_toctou2.py` 需绕过权限层（`mcp.requires = lambda *a, **k: True`）并替换 `mcp.MCPServer` 为 mock，脚本内已实现。
