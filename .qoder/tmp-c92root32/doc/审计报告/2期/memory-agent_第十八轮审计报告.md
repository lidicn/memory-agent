# memory-agent 第十八轮审计报告：只读契约与写入开关

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.17「跨入口对表」** —— 不只验证 MCP 面的只读契约，而是枚举服务函数的**全部调用点**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

本轮判据：MCP 侧声明"只读 / 不落库 / 可缓存省 token"的工具，其实现里是否真的不写。

**结论先说：MCP 面完全干净，而且干净得很完整。** 缺陷不在 MCP 边界，在两个**非 MCP 入口**。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-34** | 🟡 Medium | `activity_inference.py:538` `persist: bool = True` | 服务层**默认可写**，"只读"靠 MCP 包装显式传 `persist=False` 实现；而 `api/behavior_routes.py:359` 默认 `persist=True`、`runtime.py:741` 每日任务**不传** ⇒ 走默认写。同一能力三个入口两种口径 |

---

## 二、方法：v2.17 的迭代点

| 版本 | 做法 |
|---|---|
| v2.16（十七轮） | 跨进程契约（真源/同步） |
| **v2.17（本轮）** | **只读契约：先扫"写调用"，再看是否被形参门控，最后跨入口对表** |

**lesson 159**：扫到写调用后必须回答两个问题——① 有没有开关形参？② 各入口传的是什么？否则全是假阳性（本轮 9 处疑似 → 1 处有效）。

**lesson 158**：判"只读契约成立"前，先把该函数的**全部调用点**列出来。只看 MCP 面会漏掉 WebUI 与内部定时任务。

---

## 三、MCP 面：验证通过，且是一个完整的正面对照

### 三对"只读 / 写入"工具一一对应，无一遗漏

| 只读工具 | 传参 | 写工具（登记 WRITE_TOOLS） |
|---|---|---|
| `audit_rule_recall` | `persist=False` | `refresh_rule_recall_gaps` ✅ |
| `mine_behavior_process` | `persist=False, emit_rules=False` | `refresh_behavior_anomalies` ✅ |
| `get_behavior_drift` | `persist=False` | `refresh_behavior_drift` ✅ |

⇒ 三个只读包装**全部**显式传 `False`，三个 `refresh_*` **全部**登记在 `WRITE_TOOLS`。

### 未登记 WRITE_TOOLS 却含写调用的工具：2 个，均非违规

| 工具 | 写调用 | 结论 |
|---|---|---|
| `query_unified_events` | `.execute(` | ❌ 假阳性：是 `conn.execute(count_sql)` 的 `COUNT(*)` 读查询 |
| `audit_rule_recall` | `upsert_candidate_rule` | ⚠️ 在 `if persist:` 分支内，MCP 恒传 `False` ⇒ **不触发** |

### scope 判定与 fail-close 实测

| 项 | 结果 |
|---|---|
| `requires('audit_rule_recall', ['read'])` | `True` ✅（未登记写工具，只读令牌可调） |
| `assert_write_tools_complete()` | 返回 `[]` ✅ |
| 变异测试：注入伪造 `create_evil_tool` | `SystemExit` 拒绝启动 ✅（门禁有效） |
| `normalize(['bogus'])` | 回落到 `['read']` ✅ fail-closed |
| 以写前缀开头但未登记的工具 | **0 个** ✅ |

⇒ **不受信边界（MCP / Agent）是干净的。** 这是本轮最重要的正面结论。

---

## 四、确认缺陷

### 🟡 MA-34　`persist` 默认 `True`：只读靠调用点自觉，两个非 MCP 入口未遵守

**位置**：`activity_inference.py:534-538`

```python
def audit_rule_recall(self, start=None, end=None, days=14, rooms=None, *,
                      persist: bool = True,        # ← 默认是"写"
                      min_near_miss=2, max_gaps=10, extra_rules=None) -> dict:
```

docstring 明确写了这个开关的语义：

> ``persist=True`` 且某缺口出现次数 ≥ ``min_near_miss`` 时，产出**放宽建议**候选规则
> （``source="recall_gap"``）供人工审核——这是"补召回"的落地形式，不自动改线上规则。

#### 三个入口，两种口径

| 入口 | 传参 | 是否写库 |
|---|---|---|
| MCP `audit_rule_recall`（`mcp_server.py:2045`） | `persist=False` | ❌ 不写 ✅ |
| **API `/api/behaviors/audit-rule-recall`（`behavior_routes.py:359`）** | `persist=bool(body.get("persist", True))` | ✅ **默认写** |
| **每日周期任务（`runtime.py:741`）** | **不传** | ✅ **默认写** |

#### 实测（构造 near_miss 场景，错序事件）

```
[默认 persist=True]  0 → 1 条   events=25 gap_count=1
[persist=False]      0 → 0 条

写入: 召回放宽[书房工作]:第5步时间约束放宽   source=recall_gap  conf=0.77
```

⇒ 默认路径**确实落库**，`persist=False` 确实不落。

#### 可观测性：周期任务的日志没提落库

`runtime.py:748-757` 只打印：

```
[Activity] 召回审计：3 条规则，缺口建议 1 条，召回最低 书房工作=0.0
```

⇒ **不含落库条数**。每日任务在后台写 `candidate_rules`，日志里看不出来（lesson 161）。

#### 定 Medium 而非 High —— 四条理由（lesson 160）

1. **不受信边界是干净的**：MCP 三个只读包装全部正确传 `False`，Agent 无法触发写入
2. 写入目标是 `candidate_rules`（**staging**），需人工审核，**不自动改线上规则**
3. 有 `refresh_rule_recall_gaps` 作为正经的写入口并已登记写 scope
4. **不绕过任何审批门**——只是"审计接口顺手也写了建议"

⇒ 与第八轮 MA-19（同一批配置键两个端点口径不一致）同形，但影响面更小。

#### 修复方向（最小）

把服务层默认改成 `False`，让"写"成为必须显式声明的选择：

```python
persist: bool = False,     # 默认只读；写侧入口（refresh_* / API / 周期任务）显式传 True
```

同时：
- `runtime.py:741` 显式传 `persist=True`（若确为设计意图），并把落库条数加进日志
- `behavior_routes.py:359` 若保持默认 `True`，应在 docstring 里写明（当前只写"可选产出放宽建议"）

---

## 五、其余核查（不构成缺陷，如实标注）

| 项 | 结论 |
|---|---|
| `rule_lifecycle.manual_add`（:487） | ❌ 假阳性：docstring 里的"只读面先挂上"是描述**上线顺序**（先只读后写 CRUD），不是声明自身只读；函数明确写 `candidate_rules` 且受"四条红线"约束 |
| `mcp_server.res_skill`（:3198） | ❌ 假阳性：`open(path, "r")` 只读打开；且带双重路径遍历校验（字符黑名单 + realpath 前缀） |
| `mcp_tokens.generate`（:102） | ❌ 假阳性：生成令牌必然写 config，非只读声明工具 |
| `mcp_scopes.assert_write_tools_complete`（:193） | ❌ 假阳性：启动期完整性断言自身含 `update_`/`delete_` 字符串关键词 |

⇒ 31 处"声明只读"入口中，4 处命中写调用，**全部为假阳性**。真问题不在"只读函数写了库"，而在"`persist` 默认值"。

---

## 六、修复建议

### MA-34（一处）

见上节。核心一条：**把默认从"写"改成"不写"**，让写成为显式选择。

### 建议加门禁

> `check_write_default.py`：断言签名含 `persist` / `write` / `commit` 类布尔形参的函数，
> 其默认值**不得为 True**（或登记显式豁免）。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | MCP `audit_rule_recall` | 仍不落库（现有行为不变） |
| 2 | MCP `refresh_rule_recall_gaps` | 仍落库 |
| 3 | API 不传 `persist` | 与 docstring 声明一致（改默认后应不写，或文档写明默认写） |
| 4 | 每日周期任务 | 日志含落库条数 |
| 5 | `assert_write_tools_complete()` | 仍通过；注入 `create_evil_tool` 仍拒绝启动 |

---

## 七、横向观察

### "护栏在调用点而非唯一入口"——第 18 次同形

| 层 | 本项目 | AutoForge 对照 |
|---|---|---|
| 服务层 | `persist=True` 默认可写 | `GraphStore` 原生方法无护栏 |
| 护栏位置 | MCP 包装显式传 False | 服务层函数才是护栏载体 |
| 结果 | 漏掉 API / 周期任务两个入口 | 漏掉 CLI 两条路径（R15-01/R18-01） |

⇒ **两边都把护栏放在了"到达的那个包装上"，而不是能力本身。** 差别只在本项目 MCP 面**三个包装全对**，所以后果轻得多——**这是"铺开程度"决定严重度的一个干净样本**。

### 值得明确肯定的设计

`mcp_scopes.py` 的 fail-close 三件套（未登记工具一律 `UNKNOWN` + `requires` 拒绝、写前缀启发式启动断言、变异测试证实有效）+ 三对 read/refresh 工具的一一对应，是本项目**做得最完整的一块**。十三轮已验证登记表双向一致，本轮验证只读契约，两次都干净。

⇒ 说明**被单独立项并配了门禁的族，是能做到位的**。问题仍在于哪些族被选中立项。

---

## 八、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：9 处疑似写调用 → 逐一追形参门控 → 8 处假阳性；31 处只读声明命中 → 4 处全部假阳性 |
| **门 3** | 每条缺陷有实测 + 如实标注 | ✅ MA-34 构造 near_miss 实测（默认 0→1 条 / persist=False 0→0 条）；MCP 面用变异测试验证门禁有效；定级理由逐条列出 |

### 遗留队列（十八轮累积）

| 项 | 状态 |
|---|---|
| **MCP 只读契约** | ✅ 本轮闭合（三对 read/refresh 一一对应） |
| **跨仓 outbound trace_id** | ✅ 十七轮闭合 |
| **配置面三方一致性** | ✅ 十六轮闭合 |
| **Store 共享连接 / 分页 / 重试** | ✅ 十五轮闭合 |
| **MCP 登记表 / 启动期断言** | ✅ 十三轮闭合 |
| **锁覆盖** | ✅ 十二轮闭合 |
| **logging 格式串** | ✅ 十四轮闭合 |
| 常驻周期任务 | ⚠️ 已报 MA-31；`device_feed` 观察项 |
| `candidate_promotion` NaN | ⚠️ 观察项（实测不可达） |
| `redis_host` / `redis_port` | ⚠️ 预留字段 |
| `recent_audit` 死代码 | ⚠️ 无调用方 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 30 处无钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| `fail-closed` 39 处声明 | ⚠️ 已核两支 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`pymysql`、`starlette`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`mcp` SDK（模块可导入，仅打印警告） |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **157–161**（已并入 `lessons-round2.md`，共 1697 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
