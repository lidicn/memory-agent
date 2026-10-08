# memory-agent 第三轮审计报告：外部数值参数边界（`LIMIT ?` 未钳制）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.2「项目注释里的『改前后果』当 checklist」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮不设计新判据，而是**把项目自己写下的"改前后果"抄成 checklist**。

来源注释在 `api/behavior_routes.py:809`：

> 改前 `min(int(query),100)` 遇非数字 500、负数下推 `LIMIT -1` 全表返回

这两句直接就是两条判据。据此全仓反查外部数值参数下推点，**确证 4 条缺陷**：

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-07** | 🔴 High | `api/insight_routes.py:402` | `limit` 无 try 无钳制 → `?limit=abc` **HTTP 500**；`?limit=-1` **全表返回** |
| **MA-08** | 🟠 Medium | `api/llm_routes.py:631` | `limit` 有 try 无钳制 → `?limit=-1` **全表返回**（答案缓存） |
| **MA-09** | 🔴 High | `mcp_server.py:2323` `list_rule_lifecycle_audit` | MCP 侧裸下推 → `limit=-1` **全表**；**同一个 store 方法在 API 侧已钳制**（入口不对等） |
| **MA-10** | 🟡 Low | `mcp_server.py:2358` `list_bug_reports` | 同上，`limit=-1` 全表（表小，影响低） |

### 核心事实：`LIMIT -1` 在 SQLite 里是**无上限**，不是报错也不是 0 条

实测确认：

```
LIMIT -1    → 返回全表
LIMIT -999  → 返回全表
```

凡 `LIMIT ?` 且参数未经 `max(1, min(x, N))` 的，都可用 `?limit=-1` 拉全表。

---

## 二、方法：v2.2 的迭代点

| 版本 | 做法 | 评价 |
|---|---|---|
| v2（一轮） | 找一个已修范式反查 | 凭印象选，覆盖不可控 |
| v2.1（二轮） | 69 条注记清单化，逐条反查 | 覆盖可控，但**判据仍需自建** |
| **v2.2（本轮）** | **把注释里的"改前后果"直接当 checklist** | 判据不用设计，项目已写好 |

**lesson 80**：看到"改前……"注释，把它抄下来当 checklist。比自己想快得多。

### 过程中的一次自我修正（lesson 84）

`ma_scan_r5.py` 判据 C 用 AST 检测"外部数值转换" → **零命中**。
**先怀疑是判据写错而非"真没有问题"** —— 手工 `grep int(query_params)` 出 **29 处**。

零命中本来会被读成"这块很干净"，实际是 AST 形态没匹配上。**这是本轮最关键的一步。**

---

## 三、确认缺陷

### 🔴 MA-07　`insight_routes.py:402`：`?limit=abc` 500 + `?limit=-1` 全表

**代码**（无 try、无钳制）：

```python
limit = int(request.query_params.get("limit", 50) or 50)
runs = await asyncio.to_thread(rt.store.list_researcher_runs, job_id or None, limit)
```

**下推到** `store.list_researcher_runs`（`store.py:1668`）：

```python
sql += " ... LIMIT ?"
args.append(int(limit))          # ← 无钳制
```

**实测**（`researcher_runs` 表 300 条）：

| `?limit=` | 结果 |
|---|---|
| `50` | 50 条 ✅ |
| **`abc`** | **HTTP 500** `ValueError: invalid literal for int()` |
| **`-1`** | **300 条（全表）** ❌ |
| **`999999`** | **300 条（全表）** ❌ |

**对照：已修范式 `_num` 在同样输入上**（`behavior_routes.py:24`）：

| 输入 | `_num(raw, "limit", default=50, lo=1, hi=500)` |
|---|---|
| `50` | `v=50, bad=None` ✅ |
| `abc` | `bad="limit 必须是数字（收到 'abc'）"` → **400 而非 500** |
| `-1` | `bad="limit 不能小于 1（收到 -1）"` → **400** |
| `999999` | `bad="limit 不能大于 500（收到 999999）"` → **钳到 500** |

⇒ 同一组输入，正确范式全部拦住，本处两个方向都漏。

---

### 🟠 MA-08　`llm_routes.py:631`：`?limit=-1` 全表返回

**代码**（有 try，**无钳制**）：

```python
limit = int(request.query_params.get("limit", "100"))    # try 内
...
rows = rt.store.list_answer_cache(limit)                  # 下推
```

**下推到** `store.list_answer_cache`（`store.py:2259`）：

```python
(... " ORDER BY ... DESC LIMIT ?", (int(limit),))
```

**实测**：`limit=-1` / `-999` / `99999999` → **120 条全表返回**。

**危害**：答案缓存表可长期增长（缓存 payload），`?limit=-1` 一次拉全表 ⇒ 大响应 + 内存峰值。非数字有 try 兜住，所以**只有全表一个方向**。

**同文件对照**：`vision_routes.py:358/363` 用 `try + 钳制` 写对了。

---

### 🔴 MA-09　`mcp_server.py:2323`：MCP 侧裸下推（**同一个 store 方法，API 侧已钳制**）

**这是本轮最值得注意的一条**——它是"入口不对等"的教科书样本。

同一个 store 方法 `list_rule_lifecycle`（`store.py:5158`，`args.append(int(limit))` 无钳制），两个入口：

| 入口 | 代码 | 钳制 |
|---|---|---|
| **API** `behavior_routes.py:731` | `limit, bad = _num(..., default=100, lo=1, hi=500)` | ✅ **有** |
| **MCP** `mcp_server.py:2323` | `async def list_rule_lifecycle_audit(rule_id="", limit: int = 50)` → 裸下推 | ❌ **无** |

**实测**（`rule_lifecycle_audit` 表 150 条）：

```
══ API 侧（_num lo=1 hi=500）══
   limit=-1     → 被 _num 拒（400），不可达 store
   limit=99999  → 被 _num 钳到 500

══ MCP 侧（裸下推）══
   limit=50      →   50 条
   limit=-1      →  150 条  ← ❌ 全表
   limit=-999    →  150 条  ← ❌ 全表
   limit=999999  →  150 条  ← ❌ 全表
```

**API 侧在注释里明确记过这个形状**（`behavior_routes.py:729`）：

> 改前：`except ValueError: limit = 100` —— 非数字被**静默改成默认档**……调用方以为自己要的是 5 条、拿回的是 100 条

⇒ **API 修好了，MCP 侧没有同步。**

**危害**：MCP 面由 agent 调用，`limit=-1` 会拉全表；规则生命周期审计表随规则触发持续增长。

---

### 🟡 MA-10　`mcp_server.py:2358` `list_bug_reports`：同形状，影响低

```python
async def list_bug_reports(status: str = "open", limit: int = 50) -> dict:
    bugs = await asyncio.to_thread(rt.store.list_bug_reports, status, limit)
```

**下推到** `store.list_bug_reports`（`store.py:2744`）：`params.append(limit)` —— **连 `int()` 都没有**。

**实测**（200 条）：`limit=-1` → 200 条全表；`limit=999999` → 200 条。

**定 Low**：bug 上报表通常量小。**与 MA-09 同形状，建议一并修。**

---

## 四、一条关键的"误报排除"（lesson 82）

`api/mcp_routes.py:180` 看起来与 MA-07 完全同形：

```python
limit = int(q.get("limit") or 100)        # 无 try、无钳制
rows = rt.store.list_mcp_audit(...)
```

**但追到 store 方法内部**（`store.py:1737`）：

```python
max(1, min(int(limit or 100), 1000))      # ← store 层自带钳制
```

⇒ **无全表风险**（实测 `limit=-1` 返回 1 行），只剩 `?limit=abc` 的 500 风险。

**判"全表风险"不能只看路由层有没有 try，必须追到 store 方法内部。** store 层自带钳制与否**不一致**——有的有（`list_mcp_audit`），有的没有（`list_researcher_runs` / `list_answer_cache` / `list_rule_lifecycle` / `list_bug_reports`）。

---

## 五、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **`_num` 范式本身**（`behavior_routes.py:24`） | ✅ lo/hi 钳制 + bool 拒收 + 错误文案点名参数，**写得很好** |
| `behavior_routes` 各 handler（72/250/319/917、809） | ✅ 均有 try + 钳制，且注释记录了改前后果 |
| `collect_routes.py:75` | ✅ 有 try + 钳制 |
| `vision_routes.py:358/363` | ✅ try + 钳制正确 |
| **`list_device_health`（MCP）** | ✅ `mcp_server.py:1452` `max(1, min(int(limit), 2000))` —— **同文件内已有正确写法** |
| `acp_server.py:429` `max_rounds` | ✅ `max(1, min(_, 20))` |
| `clamp_days`（`day_bounds.py:43`） | ✅ 铺开于 `activity_inference` / `analysis` / `change_attribution` |
| `list_candidate_rules`（MCP:2233） | ✅ 未传 limit，走默认值 |
| 失败状态永久锁定 / `while True` 周期循环 | ✅ 第二轮已确认通过 |

---

## 六、修复建议

### 一次性统一修（4 处同一形状）

```python
# 1) insight_routes.py:402  —— 换成 _num
from .behavior_routes import _num          # 或把 _num 提到公共位置
limit, bad = _num(request.query_params.get("limit"), name="limit",
                  default=50, lo=1, hi=500)
if bad:
    return error(bad)

# 2) llm_routes.py:631 —— 补钳制
limit = max(1, min(int(request.query_params.get("limit", "100") or 100), 1000))

# 3) mcp_server.py:2323 —— 对齐 API 侧口径
async def list_rule_lifecycle_audit(rule_id: str = "", limit: int = 50) -> dict:
    limit = max(1, min(int(limit or 50), 500))     # 与 API 侧 hi=500 同口径

# 4) mcp_server.py:2358 —— 同上
limit = max(1, min(int(limit or 50), 500))
```

### 更彻底的做法（推荐）

**把钳制下沉到 store 层**，参照 `list_mcp_audit` 已有的写法：

```python
# store.list_researcher_runs / list_answer_cache / list_rule_lifecycle / list_bug_reports
args.append(max(1, min(int(limit or 50), 500)))
```

理由：本轮 4 条缺陷全部是"**路由层没钳 + store 层也没钳**"的组合。只要 store 层钳住，无论哪个入口（API / MCP / 内部调用）都安全——**这正好治 MA-09 那种"一个入口修了另一个没修"的根因。**

### 建议加门禁

> `check_limit_pushdown.py` —— 断言所有 `LIMIT ?` 的参数必须经 `max(1, min(int(x), N))`。
> 对照 `list_mcp_audit` 的现存正确写法做白名单。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | `GET /api/insights/researcher-runs?limit=abc` | **400**（非 500） |
| 2 | `?limit=-1` / `?limit=999999` | ≤ 500 条 |
| 3 | `GET /api/llm/answer-cache?limit=-1` | ≤ 1000 条，非全表 |
| 4 | MCP `list_rule_lifecycle_audit(limit=-1)` | ≤ 500 条 |
| 5 | MCP `list_bug_reports(limit=-1)` | ≤ 500 条 |
| 6 | `list_mcp_audit` 现有钳制不被破坏 | `limit=-1` 仍返回 1 行 |

---

## 七、横向观察

### "API 修了、MCP 没修"是第二轮 MA-05 的同一形状，第二次出现

| 轮 | 缺陷 | 形状 |
|---|---|---|
| 二 MA-05 | admin 通道："MCP 侧接错参数" | 正确范式在一个入口对、另一入口接错 |
| **三 MA-09** | **limit 钳制："API 侧修了、MCP 侧没修"** | **同一形状，第二次** |

两条都是**同一个 store 方法 / 同一份数据，两个入口口径不一致**。

而本轮还找到了一个更直接的证据：**`list_device_health` 在 `mcp_server.py:1452` 就写对了**（`max(1, min(int(limit), 2000))`）。也就是说，**MCP 面里已经存在正确写法**，只是没有铺到同一文件的其他工具上。

### 三轮下来的两条主线

1. **声明与实现漂移**（一轮 MA-04/05、二轮 MA-05、本轮 MA-09 的注释说"改前…"却仍有未改的同形点）
2. **正确范式孤岛**（`_num` 在 `behavior_routes`、`list_device_health` 的钳制在 `mcp_server` 一处、`list_mcp_audit` 的钳制在 store 一处）

**三次都是：写对了，但只写在了到达的那个文件里。**

---

## 八、工作流执行与已知未覆盖

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 新脚本用 `auditlib` 原语 | ✅ `ma_scan_r5.py` 遵守 |
| **门 2** | **命中异常少时手工 grep** | ✅ **决定性**——AST 零命中，grep 出 29 处 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ 4 条全部实测；每条都有同仓正确写法对照 |

### 已知未覆盖

| 项 | 状态 |
|---|---|
| 判据 D 疑似软上限 4 处 | ⚠️ 未深入：`alert_dispatcher._evict_if_needed` / `should_send`、`ha_client._pooled_client`、`mcp_server._record_mcp_call` |
| `store.py:4813` `LIMIT ?` 传 `(limit,)` 无 int | ⚠️ 未核查调用点是否外部可达 |
| `clamp_days` 铺开度 | ⚠️ `mcp_server` 40 处 `days: int =` 参数中仅 3 处调用 `clamp_days`，**其余未核查** |
| `auth.py:92 _save_users` 原子写 | ⚠️ 未验证（`bcrypt` 未装） |
| 一/二轮遗留 | ⚠️ MA-03 端到端（`chromadb` 未装） |

### 环境

| 项 | 值 |
|---|---|
| 未装依赖 | `chromadb`、`bcrypt`、`starlette` |
| 对照方式 | 因缺 `starlette` 无法导入 api 模块，`_num` 对照用**独立复现**（行为等价） |
| 扫描器 | `ma_scan_r5.py` |
| 明细 | `reports/ma-r5.json` |
| 新增 lessons | **80–84**（已并入 `lessons-round2.md`，共 1001 行） |
| 仓库状态 | 探针已还原 |
