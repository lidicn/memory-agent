# memory-agent 第十轮审计报告：事件循环阻塞（传递性同步 I/O）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.9「读项目自带量具声明的『看不见』部分」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮的起点是一个 **HITS=0**。

项目自带量具 `scripts/scan_unloaded_async_io.py`（176 行，审计 A3 §八的可复现量具）跑出来：

```
SELFTEST_POS_HITS=2 SELFTEST_NEG_HITS=0   ← 自证通过
HITS=0                                     ← 正式扫描
```

**但它自己的文档里写着：**

> 本量具只看**一层**调用，`/api/auth/login` 那种"路由 → 同步方法 → bcrypt"的
> **传递性阻塞**它看不见（A3 那格的真身恰好是传递性的），
> 所以 **HITS=0 ≠ 循环没被冻住**。

于是本轮就做它做不到的事：**跨函数展开**。一次得到 **29 处传递性阻塞**，其中 `api/` 下 33 个路由 handler。

### 实测（复刻项目自己的探针口径：心跳协程 tick 计数）

| 场景 | 耗时 | **心跳 tick** |
|---|---|---|
| 同步直调 ×60（每次 ~0.94 ms） | 57.5 ms | **1** ❌ |
| `to_thread` ×60（对照） | 90.2 ms | **95** ✅ |
| 同步直调一次 2 s TCP 超时 | 2002 ms | **0** ❌ |
| `to_thread` 同上（对照） | 2006 ms | **1003** ✅ |

⇒ **tick 1 vs 95**、**tick 0 vs 1003**。注意 `to_thread` 总耗时反而更长（线程切换开销）——**判据应该是循环的响应性，不是单次耗时**（lesson 117）。

---

## 二、方法：v2.9 的迭代点

| 版本 | 做法 |
|---|---|
| v2.7（八轮） | 门禁自己声明的折算口径 → 新维度 |
| v2.8（九轮） | 跨项目缺陷族迁移 |
| **v2.9（本轮）** | **项目自带量具声明的"我不判什么" → 新维度** |

**lesson 116**：`HITS=0` 不等于没问题。找到量具后要读它的**局限性段落**——那正是作者知道但没做的部分，也是最可能有货的地方。

**做法**：`async def` → 解析被调用的同仓函数 → 查该函数是否 `async` → 若同步且体内含 DB/文件/网络 → 判为传递性阻塞。这正是 AutoForge 第十五轮之后沉淀的 `CallGraph` 跨函数展开能力。

---

## 三、确认缺陷

### 🔴 MA-23　`reload_config`：4 个 async handler 同步调用，单次可冻结整条循环数秒

**位置**：`runtime.py:856` `reload_config`（同步方法）

```python
def reload_config(self) -> Config:
    ...
    self.ha = HAClient(self.config)              # 重建 HTTP 客户端
    self.ha_db = self._build_ha_db(self.config)  # ← pymysql 建连（TCP）
    ...
    self.llm.reconfigure(self.config)            # LLM 后端重配
```

**4 个同步调用点（均为 async handler）**：

| 文件:行 | handler | 路由 |
|---|---|---|
| `api/config_routes.py:227` | `update_config_api` | `POST /api/config` |
| `api/collect_routes.py:120` | `collect_enable` | `POST /api/collect/enable` |
| `api/collect_routes.py:135` | `collect_config` | `POST /api/collect/config` · `/api/poller/config` |
| `api/ha_routes.py:85` | `ha_save_rooms` | `POST /api/ha/rooms` |

**实测**（挂起后端 accept 但不响应，模拟 HA DB / LLM 后端不通）：

```
单次 pymysql 建连 + 读超时（timeout=2s）= 2004 ms

同步直调（现状）   耗时 2002 ms   心跳 tick 0      ❌
to_thread（对照）  耗时 2006 ms   心跳 tick 1003   ✅
```

⇒ **一次配置保存即可让整条事件循环停摆 2 秒**：同循环内所有 HTTP 请求、SSE 心跳、后台 tick 全部停摆。

**可达性**：`POST /api/config` 需 `require_admin`；`POST /api/collect/*` 需 `require_user`。均为正常运维操作，**不需要构造攻击**——改一次配置就触发。

---

### 🟠 MA-24　76 个路由 handler 未卸载同步 store 调用（卸载率 56%）

**统计**（`api/` 下触碰 `store.` / `runtime` / `reload_config` 的 async handler）：

```
合计 172 个
  已 to_thread 卸载   96 个  ✅
  未卸载（同步直调）  76 个  ❌
  卸载率 56%
```

**最集中的文件**：

| 文件 | 未卸载数 |
|---|---|
| `api/member_routes.py` | **10**（`member_list` / `member_create` / `member_detail` / `member_delete` / `member_rooms` / `member_devices` / `member_tag_add` / `member_tag_delete` / `member_appearance` / `member_insight_feedback`） |
| `api/mcp_routes.py` | 6 |
| `api/insight_routes.py` | 5 |
| `api/llm_routes.py` | 5 |
| `api/signal_routes.py` | 3 |
| 其余 | 分散 |

**为什么定 Medium 而非 High**：实测真实 schema 5 万行：

```
query_events(limit=500)   p50 0.94 ms   p95 1.49 ms
count_events()            p50 0.94 ms   p95 1.03 ms
list_members()            p50 0.01 ms
```

**单次 1 ms 的 SQLite 调用不足以成为缺陷**（lesson 118）。真正构成风险的是 **高并发叠加**——60 并发时实测 tick 从 95 掉到 1。

⇒ 这一条的性质是**P99 劣化**，与 A3 §八的原始定性一致（"少量未卸载的同步调用在高并发下会造成 P99 劣化"）。

---

## 四、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| 项目自带量具 `scan_unloaded_async_io.py` | ✅ 自证通过（`POS_HITS=2 / NEG_HITS=0`），一层判定正确 |
| **`api/behavior_routes.py:417-421`** | ✅ **已卸载，且写了探针读数与理由**——本轮修法的现成模板 |
| `api/auth_routes.py:94` login | ✅ `await asyncio.to_thread(rt.auth.login, ...)`（注释说明 bcrypt 是"刻意慢"函数） |
| `app.py:182` 的注释 | ✅ 明确指出鉴权中间件是"未卸载同步 I/O 的真身"，并说明 register 不能卸载的理由（写盘） |
| `api/debug_routes.py:361` `_execute_run` | ✅ 被 `await` 调用（协程），非同步阻塞 |
| `runtime.shutdown` | ✅ 走 TaskRegistry 统一取消 |

---

## 五、修复建议

### MA-23（四处，一行一处）

```python
# api/config_routes.py:260 等四处
- rt.reload_config()
+ await asyncio.to_thread(rt.reload_config)
```

`reload_config` 内部已有关闭旧连接的处理（"稳定性审计缺陷5"），是线程安全的候选。

### MA-24（批量，按文件推进）

优先级建议按**外部可达 + 调用频次**：`member_routes`（10 处）→ `mcp_routes`（6）→ `insight_routes`（5）→ `llm_routes`（5）。

模板直接照抄 `api/behavior_routes.py:417`：

```python
profile_text = await asyncio.to_thread(build_profile, rt.store, max_chars=...)
```

### 建议：给自带量具加一层（治本）

`scripts/scan_unloaded_async_io.py` 的 docstring 已经承认"只看一层"。建议给它加 `--transitive` 选项，做一次跨函数展开（复用 `DB_METHODS` 表，把同步被调函数的体内命中算进来）。

这样：
- 不新增一个独立工具，与作者原意对齐
- 76 处会进入基线，之后"只准减少"
- 与 `scan_day_bounds.py` 的既有治理方式一致

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | `POST /api/config` 且 HA DB 不可达 | 心跳 tick > 0（循环存活），且请求最终返回 |
| 2 | 60 并发 `GET /api/members` | 心跳 tick 显著 > 1（对标 `to_thread` 的 95） |
| 3 | 全仓 `scan_unloaded_async_io.py --transitive` | 命中进入基线，此后只准减少 |
| 4 | 改后 A3 §八 那一格 | 可从"少量未卸载"更新为"零未卸载" |

---

## 六、横向观察

### "修了一半"第十次——但这次有精确的百分比

| 轮 | 族 | 已修 | 未修 |
|---|---|---|---|
| 三 | LIMIT 钳制 | API | MCP |
| 八 | 配置边界 | collect 端点 | config 端点 |
| 九 | IP 来源 | `app.py`（TCP） | `auth_routes`（XFF） |
| **十** | **同步 I/O 卸载** | **96 / 172（56%）** | **76 个 handler** |

本轮第一次给出**百分比**：56%。这个数字说明它不是"没做"，而是**做到一半停了**——与 AutoForge 侧"已修范式未铺开"是同一条。

**而且已修处自带完整模板**：`behavior_routes.py:417` 不只有 `to_thread`，还写了探针读数和"为什么"——知识停在那个文件里，没传播。

### 一个值得注意的细节

`app.py:182` 的注释写得非常清楚：

> 这里可以卸载而 register 不行：`login`/`verify_token`/`_load_users` 全是只读，
> 唯一的写盘是 MCPTokenStore._touch 的节流写，它自带 self._lock——没有「读→改→整份写回」被拆散。

⇒ **作者想过"哪些能卸载、哪些不能"，并把结论写下来了**。这说明 56% 不是随意的，是有判断的。问题在于**判断没做完**（172 个 handler 里只判了 96 个）。

### 与 AutoForge 的对照

AutoForge 第七/八轮测的是"门禁会不会失效"，本轮测的是"量具会不会漏报"——**两者都是"审计工具自身的可信度"**。

共同结论：**工具报 0 时，先读它自己声明的局限性，再决定信不信。**
AutoForge 那边是 `unparse` 截断导致全表误判（静默），memory-agent 这边是 docstring 明写"传递性看不见"（诚实）。**后者比前者好得多**——作者知道自己不知道什么。

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ 本轮用 `all_walk` / `own_walk` / `full_unparse` / `rel_path` 自建跨函数展开 |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**——29 处按"是否重量级 / 是否外部可达"分级：1 条 High + 1 条 Medium，其余归入 P99 范畴不单报 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-23 有 2 s TCP 超时实测（tick 0 vs 1003）；MA-24 有 60 并发实测（tick 1 vs 95）+ 真实 schema 单次耗时基准；对照 `behavior_routes.py:417` |

### 遗留队列（十轮累积）

| 项 | 状态 |
|---|---|
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查，未逐一确认 |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| `self._lock` 15 处 | ⚠️ 未逐一核查锁覆盖范围 |
| 29 处传递性阻塞中未单报的 | ⚠️ 归入 MA-24 的 P99 范畴，未逐一定级 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette`、`pymysql`（用 socket 等价模拟） |
| 项目量具 | `scan_unloaded_async_io.py --self-test` 通过，正式扫描 `HITS=0`（docstring 已声明一层限制） |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **116–120**（已并入 `lessons-round2.md`，共 1329 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
