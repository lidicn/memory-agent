# memory-agent 稳定性与功能性缺陷清单

> 本文件是**用户真正要的交付物**：影响稳定性与功能性的 bug。
> 按 `security-audit` 技能纪律，这些**不构成安全边界违反**（故 `findings.json` 中记 `rejected`），
> 但它们是真实缺陷，按严重度排序如下，每条附实测证据与修复方案。

**实测环境**：Python 3.10.12（项目要求 ≥3.11）｜依赖装齐含 `mcp`｜测试基线 6 failed / 543 passed / 16 skipped

---

## 严重度总览

| # | 缺陷 | 严重度 | 类型 | 证据 |
|---|---|---|---|---|
| 1 | `shutdown()` 遗漏 4 个后台任务 cancel | **高** | 生命周期 | 实测 |
| 2 | `_CONV` 无上限 + 驱逐不同步 | 中高 | 资源累积 | 实测 |
| 3 | `ha_db._get_conn()` 无锁连接泄漏 | 中 | 资源泄漏 | 实测（合成） |
| 4 | purge 失败被静默吞掉 | 中 | 异常吞噬 | 静态 |
| 5 | `reload_config()` 未关旧连接 | 中低 | 资源泄漏 | 静态 |
| 6 | `LLMProvider.close()` task 未跟踪 | 低 | 关闭路径 | 静态 |
| 7 | `insight_routes.create_task` 无兜底 | 低 | 任务管理 | 静态 |

---

## 缺陷 1（高）· `runtime.shutdown()` 遗漏 4 个常驻后台任务的 cancel

**位置**：`src/memory_agent/runtime.py`

`runtime.py` 共创建 **11 个**常驻 `asyncio.Task`，`shutdown()` 只 cancel 了 **7 个**：

| 任务 | 创建行 | shutdown 是否 cancel |
|---|---|---|
| `_retention_task` | L175 | ❌ **遗漏** |
| `_sweep_task` | L182 | ✅ |
| `_identity_task` | L194 | ✅ |
| `_mqtt_task` | L196 | ✅ |
| `_backup_task` | L202 | ✅ |
| `_tpl_validate_task` | L205 | ✅ |
| `_activity_task` | L207 | ✅ |
| `_livingroom_ai_task` | L225 | ✅ |
| `_candidate_promotion_task` | L228 | ❌ **遗漏** |
| `_causal_scan_task` | L231 | ❌ **遗漏** |
| `_self_diary_task` | L237 | ❌ **遗漏** |

**为什么严重**：`shutdown()` 末尾 L673 执行 `await asyncio.to_thread(self.store.close)` 关闭 SQLite。未被取消的 4 个任务下一轮循环会访问已关闭的 DB：

```
实测：DB 关闭后 worker 抛出的异常: ProgrammingError: Cannot operate on a closed database.
```

且这 4 个任务的循环体**确实直接访问 store**——`_periodic_self_diary` 内有 `store.list_agent_memories`(L257)、`store.query_events`(L264)、`store.add_agent_memory`(L296)。

**无兜底**：全仓无 `asyncio.all_tasks()` / `gather` 统一取消；`app.py` 的 lifespan 只调 `shutdown()`。

**修复**（最小改动，加 4 段与现有风格一致的 cancel）：

```python
# runtime.py shutdown() 内，与已有 7 段并列
for name in ("_retention_task", "_candidate_promotion_task",
             "_causal_scan_task", "_self_diary_task"):
    t = getattr(self, name, None)
    if t is not None:
        t.cancel()
```

**更彻底的修法**（推荐）：把所有 task 收进 `self._tasks: set[asyncio.Task]`，`shutdown()` 统一遍历 cancel + `await asyncio.gather(*self._tasks, return_exceptions=True)`，并**放在 `store.close()` 之前**。这样以后新增任务不会再漏。

**回归测试**：启动 runtime → 立即 shutdown → 断言 `asyncio.all_tasks()` 中无残留任务；或断言 shutdown 后 1 秒内无 `ProgrammingError` 日志。

---

## 缺陷 2（中高）· `debug_routes._CONV` 无上限，且 run 驱逐不同步

**位置**：`src/memory_agent/api/debug_routes.py:51`

```python
_RUNS: dict[str, "DebugRun"] = {}
_CONV: dict[str, list] = {}   # conversation_id -> 完整 messages 上下文
_MAX_RUNS = 200               # ← 只有 _RUNS 有上限
```

`_register()`（L115-130）在 `len(_RUNS) > _MAX_RUNS` 时驱逐旧 run，**只 `pop(_RUNS)`，从不清理 `_CONV`**。

**实测**（5000 个会话依次进入）：

```
_RUNS =    200   ← 上限 200 生效 ✅
_CONV =   5000   ← 无任何上限 ❌
净增长 = 5.37 MB
被驱逐出 _RUNS 的 run 数        = 4800
其 conversation 仍驻留 _CONV 的 = 4800  (100%)
```

`_CONV` 的 value 是**完整 messages 上下文**（system prompt + 全部轮次 user/assistant/tool 消息），真实场景单条远大于测试用的 8KB。长期运行的 debug/chat 接口会单调增长 → OOM。

**单 conversation 内部也单调增长**：10 轮后 messages 从 1 条涨到 21 条，无上下文裁剪。

**范围修正（独立验证者纠正，已采纳）**：
- `acp_server.py:89` 的 `SessionStore.delete()` 会 `_CONV.pop(sid)`，**ACP 路径有清理入口**
- 因此泄漏面限定为 **HTTP debug 入口**（`debug_routes.py:272` 写入路径）
- 该入口需通过 `require_user` 认证，属已认证用户的 self-impact，非未授权攻击面

**修复**：

```python
# 1. 加容量上限与淘汰（与 _RUNS 同构）
_MAX_CONV = 200
_CONV_ORDER: list[str] = []   # 简易 FIFO，或改用 OrderedDict

# 2. _register() 驱逐 run 时级联清理
for r in finished[: len(_RUNS) - _MAX_RUNS]:
    _RUNS.pop(r.run_id, None)
    if r.conversation_id:
        _CONV.pop(r.conversation_id, None)      # ← 新增：级联清理
        _CONV_ORDER.remove(r.conversation_id) if r.conversation_id in _CONV_ORDER else None

# 3. 写入时按 _MAX_CONV 淘汰最旧
if len(_CONV) > _MAX_CONV:
    oldest = _CONV_ORDER.pop(0)
    _CONV.pop(oldest, None)
```

**回归测试**：模拟 N 个 conversation → 断言 `len(_CONV) <= _MAX_CONV`；模拟 run 被驱逐 → 断言其 conversation 同步被清理。

---

## 缺陷 3（中）· `ha_db._get_conn()` 无锁，并发首调泄漏连接

**位置**：`src/memory_agent/ha_db.py:97-100`

```python
def _get_conn(self) -> pymysql.connections.Connection:
    if self._conn is None or not self._conn.open:
        self._conn = self._connect()      # ← 无锁
    return self._conn
```

`close()`（L102-107）**只关当前 `self._conn`**——被覆盖掉的先前连接无人持有、无人关闭。

**实测**（16 线程并发首调，放大连接耗时以暴露竞态窗口）：

```
实际创建的连接数         = 16
实际保留在 self._conn 的 = 1
泄漏（创建但丢失引用）   = 15
泄漏连接是否被 close()   = 否
加锁对照组：创建连接数   = 1   ✅
```

**可触发性修正（独立验证者纠正，已采纳）**：源码中 `ha_db` 的实际调用点是**单点**（`poller.py` 附近一处 `asyncio.to_thread`），**不存在实体级扇出并发**。上述 16 线程是**合成 PoC**，真实部署下的可触发性低于初始声明——除非未来新增并发调用点，或首次调用恰逢多请求竞争。

**建议**：仍应修（成本极低，且是明确的正确性缺陷），但**优先级排在缺陷 1、2 之后**。

**修复**：

```python
import threading

class HADBClient:
    def __init__(self, ...):
        ...
        self._lock = threading.Lock()

    def _get_conn(self):
        with self._lock:
            if self._conn is None or not self._conn.open:
                self._conn = self._connect()
            return self._conn
```

---

## 缺陷 4（中）· purge 失败被 `except Exception: pass` 静默吞掉

**位置**：`src/memory_agent/runtime.py:531-533`、`L555-557`

```python
await asyncio.to_thread(self.store.purge_behavior_anomalies, before)
except Exception:      # noqa: BLE001
    pass               # ← 静默
```

`purge_behavior_drifts` 同样写法。

**后果**：purge 失败（SQLite 锁、磁盘满）时**无任何可观测信号**，`behavior_anomalies` / `behavior_drifts` 两表持续膨胀；而**每日过程挖掘会对这两表全量扫描**，最终拖慢采集主链路。

**修复**：至少把 `pass` 改为 `logger.warning(...)`，并对连续失败计数（连续 N 次失败时告警或降级跳过挖掘）。

---

## 缺陷 5（中低）· `reload_config()` 覆盖 `ha_db` 前未关闭旧连接

**位置**：`src/memory_agent/runtime.py`

```python
self.ha_db = self._build_ha_db(self.config)   # ← 直接覆盖，旧实例连接泄漏
```

频繁在 WebUI 改配置（如切换 HA MariaDB 凭据）会累积僵尸 pymysql 连接。

**修复**：

```python
old = getattr(self, "ha_db", None)
if old is not None:
    try:
        old.close()
    except Exception as exc:
        print(f"[Runtime] 关闭旧 HA MariaDB 客户端异常: {exc}")
self.ha_db = self._build_ha_db(self.config)
```

---

## 缺陷 6（低）· `LLMProvider.close()` 创建未跟踪的 task

**位置**：`src/memory_agent/llm_client.py:123-128`

```python
loop.create_task(self._client.aclose())   # ← 不保存引用、不 await
```

事件循环先关闭时触发 `Task was destroyed but it is pending!`，且 HTTP 连接可能未正确关闭。

**修复**：保存引用并 `await`（若 `close()` 已是 async）或改用 `run_until_complete` 前的显式等待。

---

## 缺陷 7（低）· `insight_routes` 的 `create_task` 无引用无兜底

**位置**：`src/memory_agent/api/insight_routes.py:385`、`390`

```python
asyncio.create_task(rt.researcher.run_job(...))    # ← 返回值丢弃
asyncio.create_task(rt.researcher.run_all(...))
```

任务内未捕获异常 → `Task exception was never retrieved`；且 job 状态可能长期停留在 `running`，阻塞后续触发。

**修复**：保存引用到集合（防止被 GC），并 `add_done_callback` 记录异常。

---

## 附：一条被证伪的候选

`except asyncio.CancelledError: pass` 不 break 导致 `while True` 继续 —— **不成立**。

- `except asyncio.CancelledError` 缩进 8，`while True` 缩进 12，except 在 while **外层**
- 实测：cancel 后新增循环轮数 **0**，`task.done()` = **True**

任务正常结束，已剔除。

---

## 建议修复顺序

1. **缺陷 1**（shutdown 漏 cancel）—— 4 行改动，消除关闭期 `ProgrammingError` 噪音与状态不一致
2. **缺陷 2**（`_CONV` 无上限）—— 唯一会随运行时间**单调增长到 OOM** 的缺陷
3. **缺陷 4**（purge 静默）—— 改 `pass` 为日志，成本几乎为零，换取可观测性
4. **缺陷 5、6、7** —— 资源/任务生命周期收尾
5. **缺陷 3**（ha_db 无锁）—— 正确性缺陷但实际可触发性低，可随下个版本带上去

**一条横切建议**：缺陷 1、6、7 本质是同一类问题——**异步任务的生命周期没有统一管理**。建议引入一个 `TaskRegistry`（创建即登记、shutdown 统一 cancel + gather），一次解决三处，并防止未来新增任务再漏。
