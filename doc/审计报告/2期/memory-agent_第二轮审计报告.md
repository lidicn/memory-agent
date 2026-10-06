# memory-agent 第二轮审计报告：已修范式铺开度 + 入口对等性

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.1「已修范式清单化 + 逐条反查」**（第一轮 v2 的迭代）
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

第一轮用的是「找一个已修范式 → 反查同形状」，找到 4 条。本轮把这个方法**清单化**：先把项目里 69 条"第N轮审计/修复"注记全部 grep 出来，提取修法关键词，逐条写判据反查铺开度。

**结果分两半，两半都重要：**

### 一半：确认已铺开（把"未审"变成"确认无问题"）

| 已修范式 | 铺开度 | 结论 |
|---|---|---|
| 裸 `asyncio.create_task` → `TaskRegistry` | 全项目仅 `task_registry.py:40` 一处 `create_task`（即正确实现本身） | ✅ 已铺开 |
| 模块级容器有界 | `_BREAKERS`、`_STORES` 均按固定键空间（`name`/`data_dir`）键控 | ✅ 通过 |
| 覆盖连接前关闭旧连接 | `reload_config` 已关旧 `ha_db`；`_CLIENT_POOL` 为有界 LRU（8）且锁外关闭淘汰连接并留痕 | ✅ 通过 |
| 失败状态永久锁定 | 7 个候选经修正判据后**真实命中 0** | ✅ 通过 |
| 重试 / 无限循环 | 28 个重试函数；9 个 `while True` 无 `break` 全为周期调度且体有异常保护 | ✅ 通过 |

### 另一半：找到 2 条缺陷（都是**收窄类**，方向相反）

| 编号 | 级别 | 方向 | 一句话 |
|---|---|---|---|
| **MA-05** | 🔴 High | **收窄过头 + 留痕说谎** | MCP 的 admin 审计通道声称"可查全量/跨成员"，实际**只返回公共记忆**；而审计日志记的是 `AUDIT: full member_id-less listing` |
| **MA-06** | 🟢 Low | **未收窄（潜在）** | `read_self_diary` / `generate_self_diary` 走全量读取，与同文件其他工具的收窄口径不一致；当前**不构成实际泄露**（写侧不带 member_id） |

---

## 二、工作流迭代：v2 → v2.1

| 版本 | 方法 | 特点 |
|---|---|---|
| v2（第一轮） | 找一个已修范式 → 反查同形状 | 有效，但**凭印象选范式**，覆盖不可控 |
| **v2.1（本轮）** | **69 条注记全部清单化 → 逐条写判据反查** | 覆盖可控；且能产出"确认已铺开"清单 |

**新增三条判据（均来自本轮实测教训）：**

- **76** 判"失败状态永久锁定"必须排除**构造时赋值**（初扫 7 条全是这类误报）
- **77** `while True` 无 `break` ≠ 无限循环，先看是否周期调度 + 体是否有异常保护
- **78** 收窄类缺陷要看**两个方向**：该收窄没收窄（fail-open）／收窄过头致通道名存实亡（fail-closed + 声明不符）
- **79** 响应字段自报与实际返回不符，是**独立的一类缺陷**

---

## 三、确认缺陷

### 🔴 MA-05　admin 审计通道名存实亡，且审计留痕与实际不符

**位置**：`mcp_server.py:2489` `list_agent_memories`、`mcp_server.py:2550` 附近 `retrieve_agent_memories`；经 `agent_memory.py:516`（恒传 `exact_member=True`）落到 `store.py:4785`

#### 声明（docstring 原文）

> `list_agent_memories`：**不传 member_id 时：仅 admin scope 令牌可查全量（审计通道，需出证）**；普通 read/write 令牌必须传 member_id，否则拒绝。

> `retrieve_agent_memories`：DCD裁定1: fail-close——member_id 缺失时只允许 admin 审计通道

并在放行时写审计：

```python
rt.store.log_mcp_audit(token_name=_tok, tool="list_agent_memories", scope="admin",
                       error="AUDIT: full member_id-less listing", ...)
# retrieve 侧对应："AUDIT: cross-member recall"
```

#### 实际（实测）

库中：1 条公共（`m0`）+ alice（`m1`）+ bob（`m2`）

```
对外路径 agent_memory.list_agent_memories（exact_member=True）
   member_id=''             → 1 条 ['m0']
   member_id='member:alice' → 1 条 ['m1']

对照：内部 sweep 路径（不传 exact_member）
   返回 3 条 ['m0','m1','m2']   ← 这才是全量
```

**调用链**：`mcp_server:2523` → `rt.agent_memory.list_agent_memories` → `agent_memory.py:516` **恒传 `exact_member=True`** → 不传 member_id 时等于 `member_id=''` 精确匹配 → **只有公共记忆**。

⇒ admin 拿到的不是"全量"，是"公共子集"。

#### 危害（两条，第二条更重）

1. **功能**：admin 审计通道查不到成员记忆 —— 声称的能力不成立。
2. **证据**：审计日志记录的是 `AUDIT: full member_id-less listing` / `cross-member recall`。**事后追责时，这条记录会让人以为"该管理员查了全量"**——而实际只返回了公共记忆。这是**假证据**，与本轮看到的项目自身标准（反对假证据的注释贯穿全仓）直接冲突。

#### 定级说明

不是 High 于"隐私泄露"——它是 **fail-closed**，数据没多给。定 High 是因为**审计留痕说谎**：一个以"可出证"为设计目标的通道，出的证与实际不符。

#### 修复建议（二选一，不要两个都做）

```python
# A) 让 admin 通道真正查全量（对齐声明）
#    agent_memory.list_agent_memories 增加 exact/allow_all 参数：
def list_agent_memories(self, state="all", source="", member_id="", *, allow_all=False):
    rows = self.store.list_agent_memories(state, source, member_id=member_id or "",
                                          exact_member=not allow_all)
#    MCP admin 分支调 allow_all=True；非 admin 仍 fail-closed

# B) 改声明与留痕（承认只查公共）
#    docstring 改成"仅返回公共记忆"，审计 error 改成 "AUDIT: public-only listing"
```

**回归验证**：库中含 1 公共 + 2 成员记忆时，admin 不传 member_id →
- 选 A：应返回 **3 条**
- 选 B：应返回 1 条，且审计文本不应出现 "full"/"cross-member"

---

### 🟢 MA-06　自我日记工具绕过成员收窄（潜在，当前不可达）

**位置**：`mcp_server.py:2391`、`mcp_server.py:2416`、`runtime.py:294`

```python
all_mem = await asyncio.to_thread(rt.store.list_agent_memories, "all", "", 500, "")
diaries = [m for m in all_mem if m.get("topic_key") == "self_diary"]
```

**不传 `exact_member`** ⇒ 无 member 过滤 ⇒ 读全量；且**无 member_id 校验、无 scope 校验**。与同文件 `list_agent_memories`/`retrieve_agent_memories` 的收窄口径不一致。

**为什么当前不构成泄露（必须诚实标注）**：写入侧 `write_self_diary`（`mcp_server.py:2370`）调 `store.add_agent_memory` **不带 member_id** ⇒ 日记恒为公共（`member_id=''`）。所以"按 topic 过滤"实际等价于"按 topic 过滤公共记忆"。

⇒ 判为 **Low / 潜在**：一旦将来给日记加成员归属，这两条通道会立刻变成跨成员读取。

**建议**：统一走 `agent_memory.list_agent_memories(..., allow_all=True)` 或直接加 `topic_key` 参数到 store 层，避免绕过收窄层。

---

## 四、入口对等性：HTTP 面的两个观察

同一操作在 MCP / HTTP 两个面的口径：

| | MCP 面 | HTTP 面 |
|---|---|---|
| member_id 缺失 | 非 admin → **403 显式拒绝** + 审计 | `api/agent_memory_routes.py:71` **不传 member_id**，且不解析该 query 参数 |
| 结果 | 拒绝 | 静默返回公共子集 |
| 响应自报 | — | `"member_id": member_id or "all"` ⇒ 自报 **`"all"`**，实际只有公共 |

**两个面的行为不等价**（一个显式拒绝、一个静默降级）。这不是权限绕过（HTTP 面也没多给数据），但：

1. HTTP 面**无法按成员筛选**——连 `member_id` query 参数都没有。
2. 响应里 `member_id: "all"` 与实际返回不一致（lesson 79）。调用方会据此认为拿到了全量。

**建议**：HTTP 面补 `member_id` query 参数；响应字段改成真实口径（如 `"member_id": member_id or "public_only"`）。

---

## 五、验证通过（确认无问题）

这一节与缺陷同样重要——它把"未审"变成"确认无问题"：

| 项目 | 验证方式与结论 |
|---|---|
| **TaskRegistry（防 task 被 GC）** | 强引用 dict + `done_callback` discard + `cancel_all`；全项目仅 1 处 `create_task`（即正确实现本身）⇒ 已铺开 |
| **`_BREAKERS` / `_STORES` 有界性** | 按 `name` / `data_dir` 键控，键空间固定，非按事件增长 |
| **`_CLIENT_POOL` 连接池** | 有界 LRU（`POOL_MAX_ENTRIES=8`），**锁外**关闭淘汰连接并留痕 |
| **`reload_config` 关闭旧连接** | `runtime.py` 已关闭旧 `ha_db` |
| **HAClient 是否持有连接** | 不持有自有连接，走共享池 ⇒ 缺陷5 不适用 |
| **`llm_client.close`** | 经 `TaskRegistry` 异步关闭 |
| **`history.reset_chroma`** | 重置全部连接缓存 |
| **失败状态永久锁定** | 7 候选 → 修正判据 → 真实命中 **0**（`degraded_error` 为构造时赋值） |
| **周期循环退出** | 9 个 `while True` 无 break 均为周期调度，体内有 try/except |
| **`_periodic_causal_scan` 取消** | `except asyncio.CancelledError: pass` 为正常的取消传播，非吞异常 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | MA-05 admin 通道声明/实现对齐（A 或 B） | 约 8 行 | 审计留痕说谎优先修 |
| **P2** | HTTP 面补 `member_id` + 响应自报改正 | 约 5 行 | 入口对等性 |
| **P3** | MA-06 日记工具走收窄层 | 约 4 行 | 潜在，可随下次改动带上 |

---

## 七、横向观察

### "声明与实现漂移"是两轮的共同主线

| 轮 | 缺陷 | 形状 |
|---|---|---|
| 一 MA-04 | `_match_time_window` 坏输入 → `return True` 放行 | 声明"时间窗约束"，实现放行 |
| 一 MA-01/02/03 | 脏记录打断整批 | 声明"列出规则/洞察"，实现整批失败 |
| **二 MA-05** | **admin 通道"可查全量"** | **声明全量，实现只给公共** |
| **二 响应自报** | `member_id: "all"` | **自报全量，实际只有公共** |

三轮都指向同一件事：**docstring 里的承诺没有机制保证与实现同步**。

第一轮我建议过加 `check_parse_in_loop.py` 门禁。本轮再加一条建议：

> **把"对外 API 的自报字段"纳入契约测试**——即第二轮 AutoForge 用过的方法（声明提取 + 逐条验证）。`member_id: "all"` 这种自报，只要有一条断言"返回行数必须等于该口径的真实行数"的测试就能发现。

### 一个值得肯定的点

MA-05 之所以能被这样定位，是因为项目的收窄设计**本身是严谨的**：MCP 侧显式 403、admin 出证日志、store 层 `exact_member` 双口径（`对外 fail-closed / 内部 sweep 看全部`）都写在注释里。

**问题不在"没想到"，在"两条链路接起来后语义被悄悄改了"**——`agent_memory.py:516` 恒传 `exact_member=True` 这一步，把"对外 fail-closed"正确地用在了普通调用上，却也让 admin 分支跟着变成了 public-only。

**这正是 AutoForge 二十轮里反复出现的形状：正确范式在一个入口是对的，在另一个入口接错了。**

---

## 八、工作流执行与已知未覆盖

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 新脚本用 `auditlib` 原语 | ✅ `ma_scan_r3.py` / `ma_scan_r4.py` 均遵守 |
| **门 2** | 命中异常时手工复核间接形态 | ✅ 判据 A 从 7 条降到 0 条真命中；P5 的 13 条全为 `__init__` 误报 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-05 有实测 + 内部 sweep 对照；MA-06 标注为不可达 |

### 脚本自身的一个 bug（已修，记入 lessons）

`ma_scan_r4.py` 初版有 `UnboundLocalError`（dict 序列化时变量未初始化），导致判据 A 结果不可信。**已修正 `RESET_VALS` 字面值判据与 dict 序列化后重跑。**

### 已知未覆盖

| 项 | 状态 |
|---|---|
| `auth.py:92 _save_users` 原子写声明 | ⚠️ 未验证（`bcrypt` 未装） |
| `api/behavior_routes.py:398 behaviors_home_profile` | ⚠️ 未核查 |
| `insights_legacy.py:2214 _check_activity_rule_coverage` | ⚠️ 候选：预检失败静默 `return None` 无日志 |
| 第一轮 MA-03 写入路径端到端 | ⚠️ 源码推断 + 读取层实测，未端到端（`chromadb` 未装） |
| 时区关节（第七轮）铺开度 | ⏸ 未逐条反查 |

### 环境

| 项 | 值 |
|---|---|
| 未装依赖 | `chromadb`、`bcrypt` |
| 扫描器 | `ma_scan_r3.py`（范式反查）、`ma_scan_r4.py`（失败态/重试） |
| 明细 | `reports/ma-r3.json`、`reports/ma-r4.json` |
| 新增 lessons | **75–79**（已并入 `lessons-round2.md`，共 959 行） |
| 仓库状态 | 探针已还原 |
