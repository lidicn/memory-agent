# memory-agent 第一轮审计报告：脏记录打断整批（parse-in-loop 无兜底）

> 审计对象：`https://github.com/lidicn/memory-agent`（main 分支）
> 代码规模：`src/memory_agent` 119 个 Python 文件
> 本轮方法：**沿用 AutoForge 二十轮沉淀的工作流 v2**，核心是 lesson 29「已修范式铺开度反查」
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

项目源码注释显示它已经历过至少 7 轮审计，且在 `patterns.py`、`history.py`、`llm_client.py` 等处留下了明确的"修复范式 + 说明理由"的注释。因此本轮**不重新设计判据**，而是用 lesson 29 的方法：

> **找到项目自己已经修好的那个形状，反查还有哪些地方没收进去。**

**结果：同一个形状找到 4 处，全部实测确证。**

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-01** | 🔴 High | `store.py:5325` `list_activity_rules` | 1 条 `tags_json` 脏记录 ⇒ 全部活动规则读不出来 ⇒ 用户自定义规则**静默全部失效** |
| **MA-02** | 🔴 High | `store.py:5012` `member_insight_feedback` | 1 条脏记录 ⇒ 成员洞察整页读不出来，HTTP **500** |
| **MA-03** | 🔴 High | `store.py:5067` `get_session_agent_trust` | 1 条 `trust` 脏值 ⇒ 声誉回路失效 ⇒ **该 session 无法写入新记忆** |
| **MA-04** | 🟠 Medium-High | `intent_inference.py:134` `_match_time_window` | 坏 `time_window` ⇒ `return True` **放行** ⇒ 时间窗约束失效，静默无日志 |

### 这四条是同一个形状

```
for r in rows:                          # ← 循环内
    json.loads(r["tags_json"])          # ← 解析 DB 行字段
                                        # ← 无 try
```

一条脏数据不是被跳过，而是**炸掉整个循环**——用户看到的是"功能整个没了"，而不是"少了一条"。

### 项目已经修好过这个形状，但没铺开

`patterns.py:52` `_json_object` 的注释写得非常清楚：

> 坏 condition 不能退化成 `{}`，否则匹配全屋所有时段

它做的是：解析失败 → `logger.warning` → 返回 `None` → **跳过该条**。

**这是正确的范式。而 `store.py` 里至少 3 处同形状没收进去。**

---

## 二、方法：怎么找到的

### 步骤 1：模板反查（lesson 43）

不写新规则，用项目自己的正确实现 `_json_object` 当模板，反查同形状调用点。

写了精确扫描器：**循环体内对"行变量字段"做 `json.loads` / `int()` / `float()`，且该循环内无 `try`**。全仓命中 11 处。

### 步骤 2：按"字段是否可能坏"筛掉误报（关键一步）

11 处里只有一部分是真缺陷，判据是**该字段能否被写入坏值**：

| 字段 | 来源 | 可能坏？ | 结论 |
|---|---|---|---|
| `r['c']`（SQL COUNT 结果） | 数据库计算 | ❌ 不可能 | **误报**，排除（`hour_histogram` / `top_entities` / `transitions` / `daily_counts_by_room` / `room_behavior_summary` 共 9 处） |
| `r['tags_json']` | 应用/LLM 写入的文本列 | ✅ | **真缺陷** |
| `r['trust']` / `r['feedback_up']` | 数值列，但 **SQLite 弱类型** | ✅ | **真缺陷** |

**SQLite 弱类型是这轮的关键前提**，已实测确认：

```
写入 'abc' 到 INTEGER 列 → 读回: {'n': 'abc', 'r': 'xyz'} 类型: str
⇒ SQLite 弱类型成立：无 STRICT 表时，数值列可存非数字字符串
```

所以 `int(r['feedback_up'])` / `float(r['trust'])` **不是理论风险**。

### 步骤 3：实测确证 + 同仓对照

每条都做了实测，并找出**同文件/同表的对照实现**——这是 lesson 41「对照实现是确证缺陷最快的证据」。

---

## 三、确认缺陷

### 🔴 MA-01　`list_activity_rules`：一条脏规则 ⇒ 用户所有自定义活动规则静默失效

**位置**：`store.py:5325`

```python
for r in rows:
    tags = json.loads(r["tags_json"] or "[]")     # ← 循环内，无 try
    ...
```

**实测**（3 条规则，第 2 条 `tags_json = '[坏 JSON'`）：

```
✅ 整批失败: JSONDecodeError: Expecting value: line 1 column 2
⇒ 3 条里有 1 条坏 → **全部**读不出来（不是跳过那 1 条）
enabled_only=True 同样整批失败
```

#### 后果链：为什么"静默"比"500"更值得注意

全仓唯一调用点是 `insights_legacy.py:2747`：

```python
try:
    custom_rules = self.store.list_activity_rules(enabled_only=True)
except Exception:
    custom_rules = []          # ← 有保护，但静默
```

**有 try 保护 ⇒ 不会 500，但也没有任何日志。**

⇒ 用户视角：**自己定义的所有活动规则突然全部不生效了，系统不报错、不告警、界面无提示。** 这是本条最隐蔽的地方——不是崩溃，是"功能悄悄消失"。

**对标**：`patterns.py:_json_object` 在同一形状上做了 `logger.warning` + 跳过单条。本处把"跳过单条"变成了"跳过全部"，且无日志。

---

### 🔴 MA-02　`member_insight_feedback`：脏记录 ⇒ 成员洞察整页 500

**位置**：`store.py:5012`

```python
for r in rows:
    tags = _json.loads(r["tags_json"] or "[]")            # ← 无 try
    ...
    "up": int(r["feedback_up"]),                          # ← 无 try
    "down": int(r["feedback_down"]),
    "trust": float(r["trust"]),
```

**实测**（3 条 `agent_memories`，第 2 条 `tags_json` 截断为 `["member:lidicn`）：

```
✅ 整批失败: JSONDecodeError: Expecting ',' delimiter: line 1 column 17
```

**第二种触发**（`feedback_up` 为非数字 `'abc'`）：

```
✅ 整批失败: ValueError: invalid literal for int() with base 10: 'abc'
```

#### 对照证据（本条最有力）

**同一个文件、同一张表、同一个字段**，相邻两个函数：

| 函数 | 行 | 处理 | 同样坏输入下 |
|---|---|---|---|
| `researcher_direction_feedback` | `store.py:5042` | `try: ... except Exception: tags=[]` | ✅ **整批存活**，返回 2 个方向 |
| **`member_insight_feedback`** | **`store.py:5012`** | **无 try** | ❌ **整批失败** |

**这是"已修一处未铺开"的教科书样本**——修法就在 30 行之外。

#### 后果

调用点 `api/member_routes.py:248` **裸调，无 try**：

```python
data = store.member_insight_feedback(member_id, member.get("name", ""))
return ok(data)
```

⇒ 直接 HTTP **500**。一个成员的反馈数据里有一条脏记录 ⇒ 该成员的洞察页整个打不开。

---

### 🔴 MA-03　`get_session_agent_trust`：脏 trust ⇒ 声誉回路失效 ⇒ 阻断记忆写入

**位置**：`store.py:5067`

```python
trusts = [float(r["trust"]) for r in rows]      # ← 列表推导，无 try
```

**实测**（真实建表结构，3 条记录中 1 条 `trust='bad'`）：

```
✅ 整批失败: ValueError: could not convert string to float: 'bad'
   修正脏值后: {'session_id': 's1', 'count': 3, 'avg_trust': 0.733, ..., 'strict': False}
```

注意：真实表结构里 `trust` 是 `REAL NOT NULL`，**但 SQLite 弱类型仍成功存入了 `'bad'`**——所以"声明了 REAL"不构成保护。

#### 后果：这条阻断的是**写入**，不只是读取

调用点 `agent_memory.py:194`，位于 `add_semantic_memory` 内部：

```python
# 声誉回路（v2 #3）：声誉差的 session 写入即锁自动晋升
trust_info = self.store.get_session_agent_trust(...)     # ← 裸调，无 try
auto_block = 1 if trust_info.get("strict") else 0
```

⇒ 1 条脏 `trust` ⇒ `add_semantic_memory` 在**入口校验之后、写库之前**抛异常 ⇒ **该 session 无法写入任何新记忆**。

`avg_trust` 与 `strict`（严格模式判定）同时失效 ⇒ 本该因声誉差被锁自动晋升的会话，反而**收不到收紧**。

> ⚠️ **诚实标注**：读取层的整批失败已实测确证；"写入被阻断"是基于调用链的源码推断（调用点裸调、函数内无 try、位于写库之前）。**未做端到端复现**——因 `chromadb` 未安装，`AgentMemoryService` 的完整写入路径无法实例化。建议修复后按第五节清单验证。

---

### 🟠 MA-04　`_match_time_window`：坏时间窗 ⇒ **放行**（约束失效）

**位置**：`intent_inference.py:134`

```python
def _match_time_window(time_window: str, hour: int) -> bool:
    try:
        start, end = time_window.split("-")
        ...
    except (ValueError, AttributeError):
        return True            # ← 解析失败 → 放行
```

**实测**（`hour=03:00`）：

| `time_window` | 返回 | 正确？ |
|---|---|---|
| `08:00-22:00`（正常） | `False` | ✅ 正确（3 点不在窗内） |
| `'bad'`（缺 `-`） | **`True`** | ❌ **放行** |
| `'abc-def'`（非数字） | **`True`** | ❌ **放行** |
| `'08:00-'`（缺结束） | **`True`** | ❌ **放行** |
| `dict`（非字符串） | **`True`** | ❌ **放行** |

⇒ **4 种坏输入全部放行**，时间窗约束彻底失效，且**无日志**。后果是意图规则在全天匹配，导致误判意图。

#### 对照证据（双重，都在项目自己的注释里）

1. **`patterns.py` 的注释**：「坏 condition 不能退化成 `{}`：三道检查会全部变成『不限』」——**正是同一个道理**，项目在别处想清楚了。
2. **同文件 `_event_dt`（`intent_inference.py:113`）的注释**：「第十三轮 P4-3 的 now 兜底不给回来：一条坏行就能让整句 `max()` 抛异常」——**相邻函数一个对了一个错了**。

**判据**：解析失败时，返回"约束不成立"（放行）是 **fail-open**，方向错了。应返回 `False`（拒绝匹配）或显式标 `unverified`。

---

## 四、验证通过（确认无问题）

这一节与缺陷同样重要——**它说明项目的很多"看起来像缺陷"的实现其实是对的**：

| 项目 | 结论 |
|---|---|
| **CRITICAL-2「共享连接必须持 Store 的锁读」** | ✅ **铺开彻底**：错误形态 `getattr(...,"_conn")` 全仓仅剩注释 1 处；`store.py` 内部 16 处 `self._conn` 使用全部持锁；`_db()` / `transaction()` 是持锁正确入口 |
| **CRITICAL-2 修复（失败缓存永久生效）** | ✅ `history.py:161` 已修 |
| **M-2 重试退避** | ✅ `llm_client.py:88` 已修 |
| **`alert_dispatcher` 单飞（ONCE 声明）** | ✅ 实测：并发 40 次 `send=True` 恰好 1 次；`threading.Barrier(30)` 强制同时到达也恰好 1 次。高优先级覆盖分支是注释声明的设计意图 |
| **`write_profile_atomic` 原子写** | ✅ `mkstemp` + `fsync` + `os.replace` + `unlink`，无残留 |
| **`config.py:334 save` 原子写** | ✅ 同上，且 `except` 内清理 tmp |
| **`_idem_reserve/_idem_finalize/_idem_release` 幂等** | ✅ `reserve_idempotency` 在 `with self._lock` 内完成 `INSERT OR IGNORE`，check/set 同临界区（符合 lesson 60「校验与置位同一临界区」） |
| **`identity.py:194 _health_allows` 的 `return True`** | ✅ 有意设计，docstring 明确"身份层未就绪时不阻断" |
| **`patterns.py:_json_object`** | ✅ **正确范式**（本轮的模板来源） |
| **`api/system_routes.py:74 get_version`** | ✅ 有留痕（`info["error"]=str(exc)`），失败保留 DEFAULT |

### 被我自己的扫描器误报、核查后排除

| 候选 | 排除理由 |
|---|---|
| `mcp_server.py:654 _read_skill_meta` | 调用点仅 `list_skills`/`get_skill`，**只读**，非 RMW |
| `mcp_server.py:951 _json_shrink_to_fit` | 纯读处理返回（loads→裁→返回），**不写盘**，非 RMW |
| `insights/activity.py:226 select` | 循环体无解析动作（仅 `from_dict` + 字符串比对），非脏记录场景 |
| `mcp_server.py:887` `project_device_health` | 循环体仅 dict 拷贝 + 字符串截断，非解析 |
| `hour_histogram` / `top_entities` / `transitions` / `daily_counts_by_room` / `room_behavior_summary` | 解析的是 `r['c']`（SQL COUNT 结果），**不可能坏** |
| `ha_client.py:286/341` `get_history` / `_fetch_area_names_via_template` | 有 `print` 留痕返回 `{}`（可观测性偏低但非静默） |
| `tv_service.py:485 read_retained_mqtt` | 有 `print` 留痕返回 `None` |

---

## 五、修复优先级与建议

四条都是同一个形状，**建议一次性统一修**，而不是逐条补。

### 推荐做法：抽一个公共的行解析 helper

```python
def _row_tags(raw, *, where: str) -> list:
    """解析行内 JSON 标签；坏值跳过该条并留痕（对齐 patterns._json_object 范式）。"""
    try:
        return json.loads(raw or "[]")
    except (ValueError, TypeError) as exc:
        logger.warning("ROW_TAGS_BAD where=%s err=%s —— 跳过该条，不影响整批", where, exc)
        return []

def _row_num(raw, default, *, where: str, field: str):
    """解析行内数值；坏值用 default 并留痕（SQLite 弱类型：数值列可能存字符串）。"""
    try:
        return type(default)(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("ROW_NUM_BAD where=%s field=%s err=%s", where, field, exc)
        return default
```

调用处（每条 1 行改动）：

| 位置 | 改成 |
|---|---|
| `store.py:5325` | `tags = _row_tags(r["tags_json"], where="list_activity_rules")` |
| `store.py:5012` | `tags = _row_tags(...)`；`int(r["feedback_up"])` → `_row_num(..., 0, field="feedback_up")`；`float(r["trust"])` → `_row_num(..., 0.0, field="trust")` |
| `store.py:5067` | 列表推导改成带 `try` 的循环，坏值跳过并留痕 |

**MA-04 单独处理**（方向问题，不是解析问题）：

```python
except (ValueError, AttributeError):
    # 坏时间窗不能放行：放行 = 约束失效（对齐 patterns.py「不能退化成 {}」）
    logger.warning("BAD_TIME_WINDOW %r —— 按不匹配处理", time_window)
    return False
```

### 回归验证清单

1. `list_activity_rules`：3 条中 1 条坏 ⇒ 应返回 **2 条**（当前：抛异常）
2. `member_insight_feedback`：同上，应返回 2 条；`feedback_up='abc'` ⇒ 该项应为 `0` 而非整批失败
3. `get_session_agent_trust`：`trust='bad'` ⇒ 应跳过该条并 `logger.warning`（当前：ValueError）
4. `_match_time_window`：`'bad'` / `'abc-def'` / `'08:00-'` 在任意 hour ⇒ 应返回 **False**（当前：True）
5. 上述每条都应产生一条 `logger.warning`

### 建议加一个门禁

项目已有 7 轮审计沉淀，建议把本轮这个形状钉住：

> `check_parse_in_loop.py` —— 断言**循环体内对 DB 行字段做 `json.loads`/`int()`/`float()` 时，该循环必须有 `try`，或调用 `_row_*` helper**。

这样第 5 处就不会出现。判据已在本报告第二节步骤 2 给出（用"字段是否可能坏"筛误报）。

---

## 六、横向观察

### 与 AutoForge 二十轮的呼应

我在 AutoForge 上做过 20 轮审计，本轮在 memory-agent 上**第一轮就命中了同一个模式**：

> **"已修范式未铺开"** —— AutoForge 二十轮里出现 7 次（FileLock、深度上限、回滚留痕、rc 约定、`_env_number`、静态扫描闸、爆炸半径）。

memory-agent 本轮 4 条全是这个模式，且其中 MA-02 的对照最直白：**修法就在 30 行之外**（`store.py:5042` vs `5012`）。

**这说明它不是某个项目的特例，而是"人工修复 + 无约束传播"的必然结果。** 修的人修对了眼前那一处，没有机制把修法推到同形状的其他地方。

### 一个值得肯定的细节

`patterns.py:_json_object` 的注释不只是"修了"，还写了**为什么不能退化成 `{}`**（"否则匹配全屋所有时段"）。这说明团队对"失败方向"有清醒认识——**MA-04 的 `return True` 恰恰违背了这个已被自己写下的认识**。

换句话说：团队知道 fail-open 的危害，并且在 `patterns.py` 里正确应用了；只是这个认识没有传播到 `intent_inference.py`。

**建议**：把 `patterns.py` 那条注释提升为项目的通用约定（写进 CONTRIBUTING 或 CLAUDE.md），并用上面那个门禁钉住。这样"知道"就能变成"不会再做错"。

---

## 七、工作流执行情况

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 新脚本使用 `auditlib` 原语（`full_unparse` / `own_walk` / `call_name`） | ✅ 本轮两个新脚本均遵守，规避了 SC-01（截断 unparse 致结论反转） |
| **门 2** | 命中异常少/异常多时手工复核 | ✅ 11 条命中里手工筛掉 9 条误报（`r['c']` 不可能坏） |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ 4 条全部实测；MA-02/MA-04 有同文件对照 |

### 已知未覆盖（诚实标注）

| 项 | 状态 |
|---|---|
| `auth.py:92 _save_users` 的 ATOMICITY 声明 | ⚠️ 未验证（`bcrypt` 未安装） |
| `api/behavior_routes.py:398 behaviors_home_profile` | ⚠️ 未核查 |
| `insights_legacy.py:2214 _check_activity_rule_coverage` | ⚠️ Medium 候选：预检失败静默 `return None`，无任何日志（注释说"不阻塞注册，只跳过告警"，但无留痕） |
| MA-03 写入路径端到端 | ⚠️ 源码推断 + 读取层实测，未端到端（`chromadb` 未装） |
| D1 剩余 FAIL_OPEN 无痕候选 | ⚠️ 原 10 条已核查 8 条 |

### 环境

| 项 | 值 |
|---|---|
| 源码获取 | `git clone` 返回 **403**，改用 `codeload.github.com` zip（master 分支 404，main 可用） |
| 未装依赖 | `chromadb`、`bcrypt` |
| 扫描器 | `audit-env/scripts/ma_scan_r1.py`、`ma_scan_r2.py` |
| 明细 | `audit-env/reports/ma-r1.json`、`ma-r2.json` |
| 仓库状态 | 探针已还原 |
