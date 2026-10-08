# memory-agent 第七轮审计报告：广度探测 —— 关节与连接处

> 审计日期：2026-10-02
> 方法论：**sota-async-concurrency skill**（`rules/07-audit-bug-catalog.md` AUDIT 模式）+ 沙箱实测
> 本轮方向：**广度探测，重点「关节处 / 连接处」** —— 组件之间的接缝、配置传递、客户端连接、数据边界
> 测试库：`/tmp/ma30.db`（30 天 / 103.9 MB / events 30 万行）

---

## 一、本轮视角

前六轮分别看了功能完整性、数据库性能、启动路径、并发模型、长跑稳定性。这一轮换一个切面：**不看单个模块内部对不对，看模块与模块之间的接缝对不对**。

接缝处最容易出问题，因为它们往往没人负责：写 A 的人假设 B 会更新，写 B 的人假设 A 不会变。

---

## 二、🔴 CRITICAL-1　配置热更新覆盖不全，5 个组件永久持有旧配置

**位置**：`runtime.py` `reload_config()`

`reload_config()` 逐一更新了 auth / ha / ha_db / history / tokens / llm / arena / identity / identity_reconciler / mqtt / insights / analysis / agent_memory / collector / vision / tv / store——**看起来很全面**。

但组件清单里还有 5 个从未被更新：

| 组件 | 是否在 reload 中更新 | 实测结果 |
|---|---|---|
| `insights`（含 templates） | ❌ | 仍持旧 config |
| `ha_assist` | ❌ | 仍持旧 config |
| `semantic_dedup` | ❌ | 仍持旧 config |
| `backup` | ❌ | 仍持旧 config |
| `activity`（ActivityInferenceService） | ❌ | 仍持旧 config |
| `researcher` | ❌ | 仍持旧 config |

**实测（AppRuntime + reload_config）**：

```
reload 前: config=8.0  activity=8.0  researcher=8.0
reload 后: config=0.0  activity=8.0  researcher=8.0   ← 未跟随
```

7 个抽检组件中 **5 个**热更新后仍持有旧配置对象。

**根因**：`activity_inference.py` 与 `researcher.py` 用的是 `self.config = runtime.config` —— 拿到的是**对象引用快照**。`reload_config()` 替换了 `runtime.config` 指向的新对象，这两个组件仍指向旧对象。

**实际后果**：用户在 WebUI 设置页改 HA 地址 / 令牌 / LLM 配置后保存，热更新提示"已生效"，但：
- 行为推断仍用旧时区
- 备份任务仍写旧目录、旧开关状态（`backup_enabled` 关了也可能还在跑）
- HA 辅助仍用旧的 `ha_assist_memory_top_k`

**修复**：`reload_config()` 补上这 5 个组件的 config 重指向；更彻底的做法是让组件统一用 `get_config()` 惰性取值，而不是构造时快照。

---

## 三、🔴 CRITICAL-2　Chroma 首次连接失败被永久缓存，服务恢复后不再重试

**位置**：`history.py` 的 `_chroma_tried` / `_chroma_error` 失败缓存机制

**实测**：首次连接失败后，错误被缓存进 `_chroma_error`，后续所有调用**直接返回 None，不再尝试连接**。即使 Chroma 随后启动完成、网络恢复，MA 也不会自愈。

**唯一的重置点**：`reload_config()` 中当 chroma 地址 / embedding 配置**发生变化**时调 `reset_chroma()`；否则只能重启进程。

**为什么这是高风险关节**：`docker-compose.yml` 里 MA 与 chroma 是并列服务，**启动顺序不保证**。首次 `docker compose up` 时 MA 常常先起来、chroma 还没就绪——这一次失败会被永久记住，之后 MA 一直处于"无向量记忆"的降级状态，而日志里除了启动期那一行外再无提示。

这正是典型的"连接处"缺陷：**初始化竞争的后果被固化成了永久状态**。

**修复**：失败缓存加 TTL（如 30 秒后允许重试），或改指数退避重试；不要让一次启动竞争决定整个进程的生命周期行为。

---

## 四、🔴 HIGH　时区关节：跨天窗口下 day 错位（两个缺陷的交叉）

这一条是 CRITICAL-1 与独立时区问题的**交叉放大**。

**独立问题**：部分模块用 `store.now_local(tz_offset_hours)`（正确，应用时区偏移），但 `candidate_promotion.py:1089/1434`、`causal_scanner.py:44`、`backup.py:33` 等直接用 `datetime.now()` —— 容器内通常为 UTC，无偏移。

**交叉放大**：`reload_config()` **显式同步了** store / identity / identity_reconciler 的 `tz_offset_hours`（说明作者知道这里要同步），却漏了 activity 与 researcher。

**实测（初始 tz=+8，用户改为 0，模拟容器 UTC 时钟）**：

| 容器时钟（UTC） | store / identity 归入 | activity / researcher 归入 | |
|---|---|---|---|
| 16:30 | 2026-10-02 | **2026-10-03** | 🔴 错位 |
| 20:30 | 2026-10-02 | **2026-10-03** | 🔴 错位 |
| 23:30 | 2026-10-02 | **2026-10-03** | 🔴 错位 |

**即：UTC 16:00–24:00 这 8 小时窗口内（对应北京时间凌晨 0–8 点，恰是家庭行为采集的高峰时段），行为记录与事件记录会被归到不同的「天」。**

后果：
- 按天聚合的行为统计对不上
- 保留策略 `purge_old` 按 `day < cutoff` 清理，可能清掉当天数据或留下过期数据

**修复**：① 统一用 `now_local()`，禁用裸 `datetime.now()`；② `reload_config()` 补上 activity / researcher 的 tz 同步。

---

## 五、🟠 HIGH　HA 客户端每次请求新建连接

**位置**：`ha_client` —— 每个方法内 `with httpx.Client() as client:` 即用即建

**实测对照**（同一请求，两种写法）：

| 写法 | 单次耗时 |
|---|---|
| 每次新建 client + 请求 | **5.10 ms** |
| 复用 client | **0.54 ms** |

**差 10×，其中建连固定成本 3.78 ms 占 74%。**

（首次测试曾出现 77× 的异常值，拆解后确认是冷启动干扰，以拆解测试为准。）

**影响**：采集是高频路径（HA 历史拉取、状态查询），这 74% 是纯浪费；跨主机或弱网环境下 TLS 握手成本更高；频繁建连还会产生大量 TIME_WAIT 套接字。

**修复**：模块级持有一个 `httpx.Client`（注意线程安全，或按需加锁/用连接池）。

---

## 六、🟡 MEDIUM　14 处无保护的 `json.loads`，一条脏记录打断整批

全项目 `json.loads` 共 89 处，其中 **66 处在 try 保护内**（说明作者有这个意识），但仍有 **14 处裸调用**：

| 文件 | 行号 |
|---|---|
| `rule_engine.py` | 312, 313, 328, 329 |
| `api/llm_routes.py` | 530, 582, 639 |
| `learning_store.py` | 111, 143 |
| `patterns.py` | 305, 337 |
| `acp_server.py` | 278 |
| `arena.py` | 131 |
| `insights_legacy.py` | 2115 |

**实测**：在规则引擎加载路径注入一条脏 JSON，**3 条规则只加载成功 1 条**，其余被丢弃——且因为外层有 `except Exception: pass`，**系统照常运行，规则静默消失**。

这与第二轮发现的"62 处 `except: pass` 让缺陷无法自曝"是同一个模式在数据边界上的体现。

**修复**：这 14 处加 try/except 并跳过单条坏记录（而不是中断整批）；保留坏数据的行号日志。

---

## 七、⚪ LOW（假警报，但掩盖了真问题）

**`runtime.py:664` `await self.llm.close()`**

实测确认：`LLMRouter.close()` 是**同步** `def` 且返回 `None`，`await None` 必抛 `TypeError`，走进 except 分支打印"关闭 LLM 客户端异常"。

**但 close() 本身已执行完毕**——所以这不是功能缺陷，是**假警报**。

**真正值得记一笔的是它掩盖的东西**：`llm_client.py:124` 的 `close()` 内部用 `loop.create_task(self._client.aclose())` **发后不管、不存句柄**。理论上该 task 可能被 GC 回收而未执行（CPython 对运行中 task 只持弱引用）。本次简单实测未能复现回收失败，故定为 LOW 理论隐患——但既然 shutdown 路径已经在报错，这个真实隐患就被那行误导性的异常日志盖住了。

**建议**：把 `await self.llm.close()` 改为 `await asyncio.to_thread(self.llm.close)`（或让 close 变 async），消除假警报，让真实问题可见。

---

## 八、✅ 已验证健康（避免误判）

| 审计项 | 结果 |
|---|---|
| 超时链完整性 | `ha_client` 每个调用都显式传 `timeout`（10s / 30s）；`history` embedding 用 `httpx.Client(timeout=30)`。**无裸 HTTP 调用** |
| 数据库关闭 | `store.close()` 经 `asyncio.to_thread` 正确调用 |
| json 保护覆盖率 | 89 处中 66 处有保护（74%）——主体意识到位，是遗漏而非不知 |

---

## 九、修复优先级

| 优先级 | 缺陷 | 动作 |
|---|---|---|
| 🔴 CRITICAL-1 | 热更新漏 5 组件 | 补 config 重指向，或改惰性取值 |
| 🔴 CRITICAL-2 | chroma 失败永久缓存 | 失败缓存加 TTL / 退避重试 |
| 🔴 HIGH | 时区跨天错位 | 统一 `now_local()`；补 activity/researcher 同步 |
| 🟠 HIGH | HA 连接不复用 | 模块级复用 `httpx.Client` |
| 🟡 MEDIUM | 14 处裸 `json.loads` | 加保护，单条坏数据只跳过该条 |
| ⚪ LOW | `await` 同步 close | 改 `to_thread`，消除假警报 |

**一句话总结**：这轮六个发现全部落在接缝上——**配置从 runtime 流向组件的接缝漏了 5 个，MA 与 Chroma 的启动接缝把一次竞争固化成了永久降级，本地时间与容器时间的接缝让跨天窗口的 day 错位，HTTP 连接的接缝每次重建付出 74% 固定开销，JSON 解析的接缝让一条脏数据吃掉整批规则**。单个模块看都没错，错在交界处。

---

## 附：复现脚本

| 脚本 | 用途 |
|---|---|
| `tz_cross3.py` | ★时区 × 热更新交叉验证（三种 UTC 时刻对照） |
| `reload_verify.py` | CRITICAL-1：7 组件热更新前后 config 对比 |
| `chroma_stuck.py` | CRITICAL-2：失败缓存永久卡死 |
| `tz_boundary.py` | 时区 day 错位边界 |
| `ha_conn_reuse.py` | HA 连接复用开销对照 |
| `json_dirty.py` | 脏 JSON 打断整批加载 |
| `close_await.py` | 同步 close 被 await 的假警报验证 |
| `mk30d.py` | 构造 30 天测试库 |

> ⚠️ 沙箱重建会清空 `/tmp`，重跑前先执行 `mk30d.py`。
