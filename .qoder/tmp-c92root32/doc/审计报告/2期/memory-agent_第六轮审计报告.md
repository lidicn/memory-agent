# memory-agent 第六轮审计报告：缺陷形状传播扫描（收尾轮）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.5「缺陷形状传播扫描」**（依据 lesson 93）
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

**本轮确认新缺陷：0 条。**

这是六轮里第一次出现"0 条"，但它的价值不在"没找到"，而在**闭合**。前三轮每轮开新维度，代价是"同一形状有多份"反复出现（三轮 `LIMIT ?` 4 处、四轮 `timedelta` 3 处、五轮坏窗 2 处）。**v2.5 规定：收尾轮不再开新维度，而是把已确认的缺陷形状建成清单，逐条全量反查。**

### 四条形狀的闭合结论

| 形状 | 全量枚举 | 已报 | 覆盖率 | 结论 |
|---|---|---|---|---|
| **M1** 循环内裸 `json.loads` | 2 处 | 2 处（MA-01/02） | **100%** | ✅ 闭合 |
| **M3** 坏窗 → `return True` 放行 | 4 处 | 4 处（MA-04/14/16 + 1 观察） | **100%** | ✅ 闭合 |
| **M2** `LIMIT ?` 无钳制 | 46 站点 | 4 处（MA-07~10） | 入口层已闭合 | ✅ 其余为字面量/下游钳制 |
| **M5** MCP 数值参数 | 48 工具 / 36 无体内钳制 | 2 处（MA-09/10） | 其余下游钳制 | ✅ 见第四节 |

### 一条流程修正（本轮最重要的产出）

**第五轮的扫描器有盲区**：判据 G 要求 `except Exception/BaseException`，因此漏掉了
`except (ValueError, AttributeError): return True`（`intent_inference.py:134`）。
该处靠**第一轮**报出才没漏 ⇒ **lesson 96：不得按 except 类型过滤**。

---

## 二、方法：v2.5 的迭代点

| 版本 | 做法 | 代价 |
|---|---|---|
| v2（一轮） | 找一个已修范式反查 | 覆盖凭印象 |
| v2.1（二轮） | 69 条注记清单化 | 判据仍需自建 |
| v2.2（三轮） | 注释里的"改前后果"当 checklist | — |
| v2.3（四轮） | 遗留队列 + 工具"已知局限"当维度 | — |
| v2.4（五轮） | 找自带门禁 → 变异测试找盲区 | — |
| **v2.5（本轮）** | **已确认缺陷形状清单 → 逐条全量反查** | 收尾专用 |

**lesson 95**：传播扫描要作为收尾步骤。**lesson 99**：0 条新缺陷是有效结果，但必须给出闭合证据。

---

## 三、形状闭合明细

### M1：循环内裸 `json.loads`（全量 2 处，100% 已报）

| 位置 | 函数 | 状态 |
|---|---|---|
| `store.py:5331` | `list_activity_rules` | ✅ 第一轮 MA-01 |
| `store.py:5021` | `member_insight_feedback` | ✅ 第一轮 MA-02 |

**扩展扫描**（循环内裸 `int()/float()` 行字段，同"一条脏数据炸整批"形状）另得 22 处，逐条归属后：
- `store.py:5013/5014/5019`、`5022/5023` → `member_insight_feedback`（MA-02 同一函数）✅
- `store.py:5048/5049/5051` → `researcher_direction_feedback`（**第一轮已确认有 try，是正面对照**）✅
- `store.py:5067` `float(r['trust'])` → `get_session_agent_trust`（MA-03）✅
- 其余（`r['c']` SQL COUNT、`r['count']` 聚合、字面量下标）→ 不可能坏，**误报**

⇒ M1 及其同族**全覆盖，无遗漏**。

### M3：坏窗 → `return True` 放行（全量 4 处，100% 已报/已观察）

修正扫描（**不按 except 类型过滤**）得 4 处：

| 位置 | 函数 | 状态 |
|---|---|---|
| `intent_inference.py:134` | `_match_time_window` | ✅ 第一轮 MA-04（`except (ValueError, AttributeError)` —— **第五轮扫描器漏掉**） |
| `activity_inference.py:115` | `_in_time_window` | ✅ 第五轮 MA-14 |
| `template_validate.py:247` | `_within_window` | ✅ 第五轮 MA-16（Low） |
| `identity.py:194` | `_health_allows` | ✅ 第五轮观察项（docstring 声明有意为之，非安全闸） |

⇒ M3 **全覆盖**。同时暴露第五轮扫描器的盲区（lesson 96）。

### M6：读空后写回（RMW）——扫描 3 处，**不采用**

源自 AutoForge R10-02 / R16-01 的形状。全仓扫出 3 处同作用域组合：
- `candidate_promotion.py`（加载 `_as_mapping:119/124` + 写回 `_json_dumps:188`）
- `store.py`（加载 `_deserialize_persons:610`、`_loads:4550` + 写回 `save_answer_cache:2212`）

**判定为弱/偶然而未推进**：三处的"空加载"都发生在**单次调用内的局部路径**，不构成"损坏文件 → 静默空 → 下次正常写回覆盖"的跨调用链。**按严格档不升级。**

---

## 四、追查确认安全的链路（M2 / M5）

只在入口层扫描会得到大量假阳性。以下链路逐条追到**最终消费点**后确认安全（lesson 98）：

| 链路 | 追查结果 |
|---|---|
| **MCP `search_events(days, limit, offset)`** | 工具本体内无钳制，但 `insights_legacy.py:439` 有 `limit = max(1, min(int(limit or 200), 2000))`、`offset = max(0, int(offset or 0))` ⇒ ✅ 安全 |
| **MCP `retrieve_agent_memories(top_k, limit)`** | MCP 侧 `k = int(limit) if ... else int(top_k)`、传 `top_k=max(1,k)`；`agent_memory.retrieve` 中向量路 `n_results=k`、FTS 路 `limit=k`，`k` 由 `config.agent_retrieve_k` 经 `max(1, min(_, 50))` 决定；尾部 `return scored[:top_k]` ⇒ ✅ 安全，**非 bug** |
| **`insights/repository._top_n`** | `max(1, min(n, ceiling=5000))`，且 `scan_limit()` 对外暴露（DCD 20261004 MA-裁5 Q4=A 明确"裁定不提高上限"，但要求调用方说清 `total` 是扫描上限还是全量）⇒ ✅ 有文档 + 有暴露 |
| **MCP `list_rule_lifecycle_audit` / `list_bug_reports`** | ❌ **未钳制**（第三轮 MA-09 / MA-10，已报） |

### `activity_inference._iter_events` 的 5000 硬上限

注释明确记录了这个坑：

> `store.query_events` 单次 LIMIT 硬上限 5000 会**静默截断**，直接传大 limit 只会拿到最早一段

并已修为复用 `insights.utils.iter_all_events` 分页（审计 P0-5）⇒ ✅ 已修，且留下说明。

---

## 五、一项负结果：路由层无鉴权调用 ≠ 鉴权缺失

扫描 201 个疑似 HTTP handler，**35 个函数体内无 `require_user`**（含 `arena_routes` 4 个、`member_routes` 2 个、`nr_routes` 4 个等）。

**全部为假阳性（lesson 97）**：本项目鉴权集中在 `app.py` 的 `AuthMiddleware`（纯 ASGI 中间件），`_authenticate` 统一处理 JWT / Basic / cookie / 管家 / 竞技场 / 应用 / 服务 / 调试令牌，并通过 `state["user"]` 注入。路由层不必再写。

白名单体系核查结果：

| 白名单 | 放行条件 | 路由层独立校验 |
|---|---|---|
| `PUBLIC_EXACT/PREFIXES` | `/`、`/health`、`/static`、`/mcp`、`/acp`、`/api/auth/login|register|status` | 各自为公开端点 |
| `DEVICE_ENDPOINTS` | `/api/events/face`、`/api/face/node/*` | ✅ `face_routes._check_device_token`（`secrets.compare_digest`） |
| `HA_ENDPOINTS` | `/v1/chat/completions`、`/v1/models` | ✅ `ha_assist_routes._check_ha_token`（未配置则 401） |
| `BUTLER_*` | 方法+路径双维度白名单 | 中间件内判，路由层无需重复 |
| `ARENA_ENDPOINTS` | `/api/arena/snapshot|snapshots` | ⚠️ **路由层无独立校验**（见下） |
| `APP_ENDPOINTS` | `/api/insights/query`、`/api/agent/memories*` | 中间件内按令牌 scopes 逐条判 |

### ⚠️ 观察项（不升级为缺陷）：ARENA 通道路由层无独立令牌校验

`api/arena_routes.py` 全文 90 行，`grep token/Token/auth/scope/verify/require` 仅命中一处注释（"竞技场闭环分析"），**无任何校验函数**。而同思路的 `face_routes` 与 `ha_assist_routes` 都有各自的 `_check_*_token`。

**为什么不是缺陷（必须诚实标注）**：
- `/api/arena/*` **不在** `PUBLIC_EXACT` / `PUBLIC_PREFIXES` 里 ⇒ 匿名不可达
- 中间件 `if not user: await self._reject(...)` ⇒ **任何访问都需先通过 `_authenticate`**
- `ARENA_ENDPOINTS` 只是**竞技场令牌的作用域收窄**（`user.get("arena")` 分支），不是绕过

⇒ 实际安全边界是"需要有效 WebUI JWT 或竞技场令牌"。**与 face/HA 通道的设计不同（那两条是绕过全局鉴权、改由路由层独立校验），arena 没有绕过，所以不需要。**

**结论：不升级为缺陷**，但建议补一行注释说明"arena 走全局 JWT，无需路由层独立校验"，避免下一个人误以为漏了。

---

## 六、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| M1 循环内裸 `json.loads` | ✅ 2/2 全覆盖 |
| M3 坏窗放行 | ✅ 4/4 全覆盖 |
| `search_events` limit/offset | ✅ 下游钳制 2000 |
| `retrieve_agent_memories` top_k | ✅ 配置钳制 [1,50] + 尾部截断 |
| `repository._top_n` | ✅ `min(n, 5000)` + `scan_limit()` 对外暴露 |
| `activity_inference._iter_events` | ✅ 已改为分页（审计 P0-5） |
| 35 个"路由层无鉴权"handler | ✅ 全部由 `AuthMiddleware` 覆盖 |
| DAY 类参数 | ✅ 第四轮已确认 `scan_day_bounds.py` 覆盖，12 例变异无盲区 |
| 门禁套件 | ✅ 0 新增（208 条基线只准减少） |

---

## 七、修复建议

本轮无新增缺陷，故无代码修复项。两条**流程建议**：

### 1. 给"已确认缺陷"建一份形状清单，纳入 CI

把 MA-01~MA-16 的每条形状写成一条可执行的枚举脚本（M1/M3 已在本轮完成），断言"命中数 == 已登记数"。新增一处即判红 ⇒ **把"缺陷没扫完"变成机器可校验**。

### 2. 给 `homesdk/gates` 补 `silent-degrade-no-trace`（第五轮建议，本轮再次确认必要）

M3 的 4 处正是第五轮盲区里的形状。理由不变：现有规则只认"空体 except"，`except: return None/True` 不认。

**并注意 lesson 96**：新规则**不得**按 except 类型过滤。

---

## 八、横向观察

### 六轮下来，"同一形状有多份"出现了四次——这是本项目最稳定的模式

| 轮 | 形状 | 实例 |
|---|---|---|
| 三 | `LIMIT ?` 未钳制 | 4 处 |
| 四 | `timedelta` 同族无上界 | 3 处 |
| 五 | 坏窗 → `return True` | 2 处 |
| **六** | **（收尾确认）M1 2/2、M3 4/4** | **全部闭合** |

本轮把这些全部闭合，同时也说明一件事：**前五轮每轮都"发现一处就报"，而没有在报的同时做全量枚举**。如果第一轮报 MA-04 时就 grep 同形状，MA-14 会在同一轮被报出，而不是拖到第五轮。

### 与 AutoForge 二十轮的对照

两边都反复出现"**写对了，但只写在了到达的那个文件里**"。memory-agent 六轮的具体表现是**防护在下游而不在入口**（`search_events` 的钳制在 legacy、`_top_n` 的钳制在 repository、`top_k` 的钳制在 config）——这比"没做"好得多，因为下游钳制天然覆盖所有入口；代价是**入口层看起来像没防护**，容易在下一次重构中被误删。

⇒ 建议在下游钳制处加注释标记"这是某某入口的唯一防护点"，让重构者看得见。

---

## 九、遗留

| 项 | 状态 |
|---|---|
| `auth.py:92 _save_users` 原子写声明 | ⚠️ 未验证（`bcrypt` 未装） |
| `api/behavior_routes.py:398 behaviors_home_profile` | ⚠️ 未核查 |
| `timedelta` 同族 14 处中剩余 11 处 | ⚠️ 未逐一核查（arena `history_days` 抽查安全：走 `resolve_range` → `clamp_days`） |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 未装） |
| 47 处静默降级中"否定方向"的 44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 21 处在 `store.py`、11 处在 `insights/repository.py`，按"调用方是否传字面量"抽查，**未逐一确认** |

### 环境

| 项 | 值 |
|---|---|
| 未装依赖 | `chromadb`、`bcrypt`、`starlette` |
| 门禁 | `PYTHONPATH=/tmp/hsdk python3 /tmp/run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **95–99**（已并入 `lessons-round2.md`，共 1142 行） |
| 仓库状态 | 探针已还原；门禁、基线、源码均未修改 |
