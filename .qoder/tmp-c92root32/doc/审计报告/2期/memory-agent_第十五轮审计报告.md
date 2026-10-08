# memory-agent 第十五轮审计报告：常驻周期任务健壮性

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.14「常驻周期任务」** —— 不看"有没有 try"，看"**try 包住了循环推进语句吗**"
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

前十四轮审的都是**请求路径**（一次请求进来、一次响应出去）。本轮换到**常驻路径**：`while True` + `asyncio.sleep` 的后台任务。这类任务有一个请求路径没有的性质——**一次未捕获异常 = 永久停摆**，没有下一次请求来"冲掉"它。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-31** | 🔴 High | `runtime.py:277-345` `_periodic_self_diary` | 循环推进语句 `now_local(...)`（284 行）在 try 之外裸奔；外层 `except` 只捕 `CancelledError` ⇒ 一次异常即让每日日记任务**永久停摆** |

### 四个判据的核查结果

| 判据 | 结果 |
|---|---|
| **A** Store 直连共享连接不持 `_lock` | ✅ 24 处，**23 处经 `_db()`**（自带 `with self._lock`）为误报；唯一走 `connect()` 的 `check_and_recover` 在启动阶段单线程执行 ⇒ **归零** |
| **B** 分页契约一致性（`has_more`/`next_offset`） | ✅ `query_unified_events` 的 count 与 rows 在同一锁区一次读完；`device_health_page` 用"本页必须给出行"避免死循环、页级 64KB 自动收窄 ⇒ **无缺陷** |
| **C** 重试循环内副作用 | ✅ `fetch_frame` 只对传输层瞬断重试、退避带 ±50% 抖动（第六轮已修）、末次 raise；`llm_client.chat` 只对 5xx/超时重试、4xx 直接 raise ⇒ **语义正确** |
| **D** 常驻任务循环体裸奔 | ❌ 5 处真裸奔 → 1 处构成缺陷（MA-31），2 处低风险，2 处观察项 |

---

## 二、方法：v2.14 的迭代点

| 版本 | 做法 |
|---|---|
| v2.13（十四轮） | grep TODO/FIXME 优先 + 错误处理路径自身健壮性 |
| **v2.14（本轮）** | **常驻周期任务：把 while 循环体内、不在任何 try 内的可抛异常调用标出来** |

**lesson 142**：判据不是"函数有没有 try"，而是"**循环推进语句**有没有被保护"。`_periodic_self_diary` 明明有 try（279 行包住整个 `while`），但它的 `except` **只捕 `asyncio.CancelledError`**——对 `ValueError` 完全无效。

### 一次自查：初版判据误判（lesson 143）

初版按"函数体顶层是否直接有 `Try`"判断，把 `if not feed.enabled(): continue` 这种后面紧跟 try 的结构误判为裸奔 ⇒ 14 处中标错 8 处。修正为**递归标记不在 try 内的调用节点**后 → 5 处。这是 lesson 141 的第 N 次再现。

---

## 三、确认缺陷

### 🔴 MA-31　`_periodic_self_diary`：循环推进语句裸奔，一次异常即任务永久停摆

**位置**：`runtime.py:277-345`，关键在 284 行

```python
async def _periodic_self_diary(self) -> None:
    try:                                              # 279：包住整个 while
        await asyncio.sleep(10)
        while True:
            from datetime import timedelta
            now_dt = now_local(self.config.tz_offset_hours)   # ← 284 裸奔
            next_23 = now_dt.replace(hour=23, minute=0, ...)  # ← 285 裸奔
            if next_23 <= now_dt: next_23 += timedelta(days=1)
            wait_sec = (next_23 - now_dt).total_seconds()
            await asyncio.sleep(wait_sec)
            try:                                      # 291：内层只包"生成日记"
                ...
            except Exception as exc:
                print(f"[SelfDiary] 生成日记失败: {exc}")   # 内层吞掉后 continue
    except asyncio.CancelledError:                     # ← 外层只捕这一个
        pass
```

#### 三重失效

| 层 | 问题 |
|---|---|
| 内层 try（291） | 只包"生成日记"，**管不到 284/285 的循环推进语句** |
| 外层 try（279） | `except` 只捕 `asyncio.CancelledError`，`ValueError` 直接穿出 |
| TaskRegistry | 记录日志但**不重启**（实测见下） |

#### 实测（复刻 277-345 结构）

```
第 3 次迭代 tz_offset_hours 变非法 → 循环到第 3 次：**穿出外层 try**（ValueError）
   日记实际生成 2 次（应 8 次），此后**永不生成**
```

#### 可达性：`tz_offset_hours` 是 HTTP 可写键

```
api/config_routes.py:31    "tz_offset_hours",      ← 在 WRITABLE_FIELDS 里
```

**这正是第八轮 MA-18「24 个数值键无范围校验」报的键之一**，且第八轮已实测 `now_local(-999.0)` 抛 `ValueError: offset must be a timedelta strictly between -24h and 24h`。

⇒ **本轮不重复报 MA-18，而是报它的下游后果**（lesson 145）：非法值不是让 API 报错，而是让**常驻任务永久停摆**。两者根因相同、修复面不同：
- MA-18 修在入口（加范围校验）
- **MA-31 修在任务结构**（把循环推进语句纳入可捕非 CancelledError 的 try，或加自愈重启）

⇒ 只修 MA-18 并不能消除本条（历史上已写入的非法配置、手工改 config.json 仍会触发）。

#### 可观测性：必须如实修正（lesson 144）

我一度以为"无任何告警"。**实测 `TaskRegistry._on_done` 会打日志**：

```
[WARNING] [Tasks] 后台任务异常退出: periodic_self_diary:
          ValueError('offset must be a timedelta strictly between -24h and 24h')
```

但 `/api/debug/tasks`（即 `TaskRegistry.status()`）：

```
任务启动后：{'pending': 2, 'tasks': ['periodic_causal_scan', 'periodic_self_diary']}
任务死亡后：{'pending': 1, 'tasks': ['periodic_causal_scan']}
```

⇒ **死掉的任务只是从列表消失**，看不出"它死了"；无健康面告警、无重启。对长期运行的家庭系统，自我日记会**从此再也不生成**，且只有翻日志才能发现。

---

## 四、其余 4 处裸奔（不构成缺陷，如实标注）

| 位置 | 裸奔语句 | 结论 |
|---|---|---|
| `runtime.py:394` `_periodic_candidate_promotion` | `getattr(self.config, ...)` | 低风险：不抛异常 |
| `runtime.py:611` `_periodic_device_feed` | `feed.interval_seconds()` → `int(getattr(...))` | ⚠️ **观察项**：`device_feed_interval_seconds` ∉ `WRITABLE_FIELDS`，只能改 config.json；配成 `"abc"` 时实测同样让任务停摆（lesson 146：可达性不同则不升级为缺陷） |
| `acp_server.py:468` `gen` | SSE 生成器 | 低风险：已有 `CancelledError` 处理 |
| `api/debug_routes.py:435` `gen` | SSE 生成器 | 低风险：同上 |

---

## 五、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **判据 A** Store 共享连接 | ✅ 24 处中 23 处走 `_db()`（自带 `with self._lock`）；唯一 `connect()` 直连的 `check_and_recover`（697 行）在启动阶段单线程执行，`connect()`/`close()`/置 `self._conn=None` 全程无并发 ⇒ **可接受** |
| **判据 B** 分页契约 | ✅ `query_unified_events._query`：`COUNT(*)` 与行在同一锁区一次读完，`has_more=(offset+len(events))<total`，`next_offset` 自洽；`device_health_page` 用"本页必须给出行"避免 `has_more` 死循环，页级 64KB 自动收窄 |
| **判据 C** 重试语义 | ✅ `vision_service.fetch_frame`（223-285）：只对传输层瞬断重试、退避带 ±50% 抖动（第六轮 M-3 已修）、末次 raise；`llm_client.chat`：5xx/超时/传输错误才重试，4xx 直接 raise `LLMError` |
| `TaskRegistry` | ✅ 有 `add_done_callback` + 异常记录 + 统一取消（`cancel_all`），设计完整——**缺的是自愈重启，不是记录** |
| `poller.py:408/641`、`runtime.py:553` 的 CancelledError | ✅ 正确 re-raise |

---

## 六、修复建议

### MA-31（一处）

**最小修复**——把循环推进语句纳入可捕 `Exception` 的范围：

```python
while True:
    try:
        now_dt = now_local(self.config.tz_offset_hours)
        next_23 = now_dt.replace(hour=23, minute=0, second=0, microsecond=0)
        if next_23 <= now_dt: next_23 += timedelta(days=1)
        wait_sec = (next_23 - now_dt).total_seconds()
    except Exception as exc:
        print(f"[SelfDiary] 计算下次触发时间失败，回退 1 小时: {exc}")
        wait_sec = 3600                      # ← 关键：给一个安全的默认间隔
    await asyncio.sleep(wait_sec)
    ...
```

**要点**：`wait_sec` 的兜底值不能是 0，否则会变成忙循环。

**更彻底**：给 `TaskRegistry` 加 `restart_on_fail=True` 选项，任务异常退出后按退避重启。这对所有常驻任务一次性生效（本轮 5 处裸奔全部受益）。

### 建议加门禁

> `check_periodic_loop_guard.py`：断言 `while True` 循环体内**不存在**未被 `try/except Exception`（或显式重启机制）覆盖的可抛异常调用。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | `tz_offset_hours = -999` 后运行一天 | 日记任务**不停摆**（打日志 + 回退间隔重试） |
| 2 | 同上后查 `/api/debug/tasks` | `periodic_self_diary` **仍在列表内** |
| 3 | 恢复合法配置 | 日记自动恢复生成，无需重启进程 |
| 4 | `device_feed_interval_seconds = "abc"` | 同样不停摆（若采纳 TaskRegistry 重启方案） |
| 5 | `cancel_all()` 关停 | 仍正常（CancelledError 不被误捕） |

---

## 七、横向观察

### "包住了但不是为了这个目的" —— try 语义错配

`_periodic_self_diary` 的 try **不是没写**，是写来捕 `CancelledError`（关停语义）的。它被"顺便"当成了异常保护，但**关停语义 ≠ 容错语义**。

这与 AutoForge 侧反复出现的形状一致：**代码有防护、但防护对象的口径对不上**。

### 又一次"正确范式孤岛"

| 正确 | 错误 |
|---|---|
| `poller.py:408/641` 的 CancelledError 正确 re-raise | `runtime.py:277` 的外层只捕 CancelledError 却当容错用 |
| 内层 try 包住了"生成日记" | 内层 try 没包住"算下次触发时间" |
| `TaskRegistry` 有异常记录 | 无自愈重启 |

⇒ 三层各自都"做了一半"，叠加起来就是"有日志、无恢复、无健康面告警"。

### 与 MA-18 的叠加关系值得单独强调

第八轮报 MA-18 时，我判的是"API 层无范围校验"。本轮说明：**同一个非法值，在常驻任务里的后果比在请求路径里严重一个量级**——请求路径错了下一次还能对，常驻任务错了就**再也不跑**。

⇒ 建议把"周期任务的输入健壮性"和"配置入口校验"作为**同一条修复线**推进，而不是分开修。

---

## 八、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：判据 A 24 处 → 查 `_db()` 定义 → 23 处误报；判据 D 初版 14 处 → 修正判据 → 5 处 → 按"裸奔语句是否真会抛"降到 1 处缺陷 |
| **门 3** | 每条缺陷有实测 + 如实标注 | ✅ MA-31 复刻真实结构实测；**可观测性经实测修正**（有 warning 日志、无自愈、tasks 列表只显示"少了"）；与 MA-18 的叠加关系已说明而非重复计 |

### 遗留队列（十五轮累积）

| 项 | 状态 |
|---|---|
| **Store 共享连接** | ✅ 本轮闭合（判据 A 归零） |
| **分页契约** | ✅ 本轮闭合 |
| **重试语义** | ✅ 本轮闭合 |
| **MCP 登记表 / 启动期断言** | ✅ 十三轮闭合 |
| **锁覆盖** | ✅ 十二轮闭合 |
| **logging 格式串** | ✅ 十四轮闭合 |
| `runtime.py:611` device_feed | ⚠️ 观察项（键不可 HTTP 写） |
| `nr_routes.py` 其余路由 | ⚠️ 未逐一核查 |
| 判据 B 的 229 处 except 内调用 | ⚠️ 与第五轮静默降级族重叠 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| `fail-closed` 39 处声明 | ⚠️ 已核两支 |
| 其余 3 处 `dry_run` | ⚠️ 静态确认正确 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette`、`pymysql` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **142–146**（已并入 `lessons-round2.md`，共 1562 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
