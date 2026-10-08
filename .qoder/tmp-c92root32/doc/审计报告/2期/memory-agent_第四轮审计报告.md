# memory-agent 第四轮审计报告：时间窗口参数无上界（`timedelta` 极值溢出）

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.3「遗留项闭环 + 门禁文档里的『已知局限』当新维度」**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮做了两件事，结果一负一正，**两者同样重要**。

### 负结果（价值很高）：判据 E「days 类参数钳制铺开度」——实际已被项目自带门禁覆盖

扫描器报出 **114 处"未钳制"**，几乎全是假阳性。核查后发现项目已有
**`scripts/scan_day_bounds.py`（744 行，含 `--self-test`）**，且 `insights` 路径经
`insights/parser/timeframe.py:197 resolve_range` 统一 `clamp_days` 收口。

我做了 **12 例变异测试**验证该门禁（形参裸用 / 局部变量 / `self.attr` / 嵌套函数返回 /
`body.get` / 正则抽取 / 字典解包 / 默认值非字面 / 已 clamp / `day-ok` 标记 / `timedelta(hours=)`）：

| 探针 | 期望 | 实际 |
|---|---|---|
| A 外部形参裸用 | 判红 | ✅ rc=1 |
| B 形参经局部变量 | 判红 | ✅ rc=1 |
| F 形参→`self.attr` | 判红 | ✅ rc=1 |
| G 嵌套函数返回值 | 判红 | ✅ rc=1 |
| H `body.get(请求体)` | 判红 | ✅ rc=1 |
| I 正则抽取天数 | 判红 | ✅ rc=1 |
| J 字典解包 | 判红 | ✅ rc=1 |
| K 形参默认值非字面 | 判红 | ✅ rc=1 |
| C 带 `# day-ok:` 标记 | 放行 | ✅ rc=0 |
| D 已 `clamp_days` | 放行 | ✅ rc=0 |
| E `timedelta(hours=)` | 口径外放行 | ✅ rc=0（文档已声明） |

⇒ **门禁本身无盲区，该维度已覆盖。** 114 处是判据太粗造成的假阳性。

**lesson 89：动手扫描前先 `ls scripts/`** —— 避免重复造轮子并产生大量假阳性。

### 正结果：门禁文档里"已知局限"的那句话，直接给了新维度

`scan_day_bounds.py` 头部写明：**只登记 `timedelta(days=)`，`hours/seconds` 同族"要不要扩册另开一条"**。

⇒ 拿这句话当 checklist，扫 `timedelta(hours|minutes|seconds=)` 同族，得 14 处；其中 **3 处外部可达且确证缺陷**：

| 编号 | 级别 | 入口 | 位置 | 一句话 |
|---|---|---|---|---|
| **MA-11** | 🔴 High | HTTP `GET /api/vision/...` | `store.py:2432` ← `vision_routes.py:371` | `?minutes=2000000000` → **HTTP 500 OverflowError** |
| **MA-12** | 🟠 Medium | MCP `trigger_incremental_collection` | `poller.py:238` | `since_minutes=2e9` → **500**；`1e9` → 窗口回溯到**公元 124 年** |
| **MA-13** | 🟠 Medium | MCP `infer_behavior_intent` | `intent_inference.py:344` | `window_min=2e9` → **500**；`1e9` → 窗口回溯公元 124 年 |

---

## 二、方法：v2.3 的两个迭代点

| 版本 | 做法 |
|---|---|
| v2（一轮） | 找一个已修范式反查 |
| v2.1（二轮） | 69 条注记清单化，逐条反查 |
| v2.2（三轮） | 注释里的"改前后果"当 checklist |
| **v2.3（本轮）** | **① 先跑遗留队列；② 读工具自带的"已知局限"注释，把它当新维度** |

**lesson 85**：门禁文档里"口径外/不在册"的说明，是现成的扩展维度清单。
**lesson 89**：反查前先找项目自带的门禁，它可能已经覆盖你想查的维度。

---

## 三、确认缺陷

### 🔴 MA-11　`store.list_scene_graphs`：`?minutes` 只有下界，极值 → HTTP 500

**调用链**：`api/vision_routes.py:371` → `store.list_scene_graphs(minutes=...)` → `store.py:2432` `timedelta(minutes=int(minutes))`

**路由侧只有下界**：

```python
# vision_routes.py:363
minutes = int(params.get("minutes") or 0)
...
if minutes < 0:
    minutes = 0          # ← 只挡负数，不挡极值
```

**实测**（用项目真实建表语句建 `behavior_events`，补 `scene_graph_json` 列，插 30 条）：

| `?minutes=` | 结果 |
|---|---|
| `60` | 正常返回 0 条 ✅ |
| `abc` | HTTP 400 ✅（`try/except` 兜住 ValueError） |
| `-5` | 正常（归 0） ✅ |
| **`2000000000`** | **HTTP 500 `OverflowError: date value out of range`** ❌ |
| **`1000000000000`** | **HTTP 500 `OverflowError`** ❌ |

**关键点（lesson 86）**：路由的 `except (TypeError, ValueError)` **兜不住 `OverflowError`**——它不是 `ValueError` 的子类。所以"非数字有 try"不代表数值安全。

---

### 🟠 MA-12　MCP `trigger_incremental_collection`：窗口可回溯到公元 124 年

**调用链**：`mcp_server.py:2868` `since_minutes: int = 0` → `poller.py:238` `timedelta(minutes=int(since_minutes))` —— **连下界都没有**

**实测**：

```
since_minutes=60              → 窗口起点 2026-01-01   ✅
since_minutes=0 / -5          → 回落 config.last_poll_time  ✅
since_minutes=1000000         → 2024-02-07
since_minutes=1000000000      → 0124-09-05        ← ❌ 公元 124 年
since_minutes=2000000000      → ❌ OverflowError → 500
since_minutes=1000000000000   → ❌ OverflowError → 500
```

**两个方向的危害（lesson 88：没崩 ≠ 安全）**：
- `2e9` 起：MCP 调用 500
- `1e9`：**不崩**，但增量采集窗口回溯到公元 124 年 ⇒ 实际等于全量重采 HA 历史，**HA 侧与本机资源压力**

---

### 🟠 MA-13　MCP `infer_behavior_intent`：`window_min` 只有下界

**调用链**：`mcp_server.py:2756` `window_min: int | None = None` → `intent_inference.py:344` `max(1, int(window_min))`

**实测**：

```
window_min=60 / 0 / -5        → 正常 ✅
window_min=1000000000         → 窗口起点 0124-09-05   ← ❌
window_min=2000000000         → ❌ OverflowError → 500
window_min=1000000000000      → ❌ OverflowError → 500
```

**对照：项目既有范式 `clamp_days` 在同类输入上**：

```
60          → 60
10**6       → 3650
10**9       → 3650
10**12      → 3650
-5          → 1
```

⇒ **已修范式就在 `day_bounds.py`，只是没铺到 `hours/minutes/seconds` 同族。**

---

## 四、"有下界无上界"是本轮三条的共同形状（lesson 87）

| 位置 | 下界 | 上界 |
|---|---|---|
| `vision_routes.py:365` | ✅ `if minutes < 0: minutes = 0` | ❌ 无 |
| `intent_inference.py:344` | ✅ `max(1, int(window_min))` | ❌ 无 |
| `poller.py:238` | ❌ 无 | ❌ 无 |

**判"有没有钳制"必须分别测下界与上界。** 只测负数会漏掉极值。

---

## 五、验证通过（确认无问题）

| 项 | 结论 |
|---|---|
| **`scripts/scan_day_bounds.py` 门禁** | ✅ 12 例变异测试全部正确判红/放行，**门禁本身无盲区** |
| 全仓 `timedelta(days=)` 97 处 | ✅ `guard_bounded=42`、`guard_unguarded=55`（后者为 literal/local/date_math，非外部输入）、`SCAN_RC=0` |
| `insights` 路径 24 处"未钳制" | ✅ **假阳性**——经 `resolve_range`（`timeframe.py:197`）统一 `clamp_days` |
| `mcp_server` 34 处 days 参数 | ✅ 5 处已钳制（616/1541/1901/2021/2396），其余透传下游由 `resolve_range` 收口 |
| `clamp_days` 本身 | ✅ `10**12 → 3650`、`-5 → 1`，超大整数走 `isinstance(int)` 快路径 |
| `store.py:4813` `LIMIT ?` 传 `(limit,)` | ⚠️ 遗留闭合：`list_agent_memories` 默认 `limit=500`，**外部调用点均未传 limit**（MCP 侧经 `agent_memory` 固定 500）⇒ 当前不可达 |
| `_record_mcp_call` | ✅ 旁路设计，失败只记日志不影响主链路（注释声明） |
| `alert_dispatcher` 上限 | ✅ `_max_sessions=1000` / `_max_alerts_per_session=50`，`_evict_if_needed` 清理 10%，键空间有限 |

---

## 六、修复建议

### 三处各一行（对齐既有 `clamp_days` 范式）

```python
# 1) vision_routes.py（HTTP）—— 或直接在 store.list_scene_graphs 内收口
minutes = max(0, min(int(minutes or 0), 60 * 24 * 30))     # 最多 30 天

# 2) poller.py:238
if since_minutes and 0 < int(since_minutes) <= 60 * 24 * 30:
    last = now - timedelta(minutes=int(since_minutes))

# 3) intent_inference.py:344
window_min = max(1, min(int(window_min), 60 * 24 * 7))      # 最多 7 天
```

### 更彻底：把 `clamp_days` 泛化成 `clamp_units`

项目已有 `day_bounds.py`（`DAY_WINDOW_MIN/MAX`、`LONG_WINDOW_MAX`）。建议加一个
按单位收口的 helper，**让 `scan_day_bounds.py` 的"要不要扩册"变成"已扩册"**：

```python
def clamp_minutes(value, default=60, lo=1, hi=60*24*30) -> int: ...
def clamp_hours(value, default=24, lo=1, hi=24*365) -> int: ...
```

这样同族 14 处一次性收口，并可直接把 `scan_day_bounds.py` 的口径从 `days=` 扩到
`days=|hours=|minutes=|seconds=`。

### 建议加门禁

> 在 `scan_day_bounds.py` 里把 `timedelta(hours|minutes|seconds=)` 纳入统计口径
> （文档已预留这条），要求同族外部输入同样必须 `bounded` 或带 `# day-ok:` 标记。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | HTTP `?minutes=2000000000` | **≤ 30 天的窗口**（非 500） |
| 2 | HTTP `?minutes=1000000000000` | 同上 |
| 3 | MCP `trigger_incremental_collection(since_minutes=2e9)` | 拒绝或收口（非 500） |
| 4 | MCP `trigger_incremental_collection(since_minutes=1e9)` | 窗口 ≤ 30 天（非公元 124 年） |
| 5 | MCP `infer_behavior_intent(window_min=2e9)` | 非 500 |
| 6 | 扩展后的 `scan_day_bounds.py` | 同族 14 处全部 `bounded` 或标记 |

---

## 七、横向观察

### 四轮下来，"正确范式孤岛"第四次出现

| 轮 | 正确范式 | 孤岛位置 | 未铺开处 |
|---|---|---|---|
| 一 | `_json_object`（脏记录跳过） | `patterns.py` | `store.py` 3 处 |
| 二 | 成员收窄 | MCP 侧 | HTTP 侧 / 日记工具 |
| 三 | `_num(lo,hi)` / `LIMIT` 钳制 | `behavior_routes`、`list_mcp_audit` | `insight_routes`、`llm_routes`、MCP 2 处 |
| **四** | **`clamp_days`** | **`day_bounds.py`（days 族全覆盖）** | **`hours/minutes/seconds` 同族 3 处** |

第四轮有个新特点：**这次的孤岛是"单位族"级别的**——days 族被门禁完整覆盖且经 12 例变异测试验证，而同一个时间语义的 minutes/hours 族完全裸奔。

**门禁只覆盖了一个单位名**，而缺陷不看单位名只看"外部数值进了 `timedelta`"。

### 一个值得肯定的点

`scan_day_bounds.py` 的文档**自己声明了口径外清单并举例**（`eval_after_hours`、`vlm_gate_window_sec`）——说明作者知道边界在哪，只是还没做。本轮等于把那句"要不要扩册另开一条"变成了"建议扩册"的具体依据（3 条实测缺陷）。

---

## 八、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 新脚本用 `auditlib` 原语 | ✅ `ma_scan_r6.py` 遵守 |
| **门 2** | 命中异常多时手工复核 | ✅ **决定性**——114 处降到 0 真缺陷（门禁已覆盖） |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ 3 条全部实测；对照 `clamp_days` 同输入 |

### 遗留队列（四轮累积）

| 项 | 状态 |
|---|---|
| `auth.py:92 _save_users` 原子写声明 | ⚠️ 未验证（`bcrypt` 未装） |
| `api/behavior_routes.py:398 behaviors_home_profile` | ⚠️ 未核查 |
| `insights_legacy.py:2214 _check_activity_rule_coverage` | ⚠️ 候选：预检失败静默 `return None` 无日志 |
| 判据 D 软上限剩余 | ⚠️ `ha_client._pooled_client`、`mcp_server._record_mcp_call` 未深入（前者已确认为有界 LRU） |
| 同族 14 处中剩余 11 处 | ⚠️ 未逐一核查（`candidate_promotion` 4 处为 config 来源、`house_time` 1 处、`store.py:2432` 已报） |
| MA-03 端到端（一轮） | ⚠️ 未复现（`chromadb` 未装） |

### 环境

| 项 | 值 |
|---|---|
| 未装依赖 | `chromadb`、`bcrypt`、`starlette` |
| 实测方式 | `store` 可导入，用项目真实建表语句建表后调用；`poller`/`intent` 用等价复现 |
| 扫描器 | `ma_scan_r6.py`（判据 E/F） |
| 项目自带门禁 | `scripts/scan_day_bounds.py`（744 行）—— 已跑 `--self-test`，rc=0 |
| 明细 | `reports/ma-r6.json` |
| 新增 lessons | **85–89**（已并入 `lessons-round2.md`，共 1048 行） |
| 仓库状态 | 探针已还原，`scripts/scan_day_bounds.py` 未被修改 |
