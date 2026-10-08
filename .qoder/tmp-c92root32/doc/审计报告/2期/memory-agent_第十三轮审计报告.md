# memory-agent 第十三轮审计报告：`dry_run` 契约与工具登记表一致性

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.12「契约声明验证 · 第二轮」** + 登记表双向核对 + 启动期断言反向变异
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

延续第十二轮的方法（把代码自称的东西拿去实测），本轮收敛到三个可判定的面。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-28** | 🔴 High | `mcp_server.py:2155-2156` `teach_signal` | 声明「dry_run=true：**只校验参数**，不写入」，实测**校验代码一行都没有**——真实路径会拒的三类输入全部返回"校验通过" |

### 三个面全部核查完毕，其中两个是负结果

| 面 | 结果 |
|---|---|
| **MCP 工具登记表**（TOOL_NAMES vs 实际实现） | ✅ 双向一致（[B] 方向 **0 处**），[A] 方向 5 处为动态注册，**非缺陷** |
| **启动期写工具完整性断言** | ✅ **反向变异验证有效**：注入 `create_evil_tool` → 正确 `SystemExit` 拒绝启动 |
| **`dry_run` 契约** | ❌ 5 处中 1 处假绿（MA-28） |

---

## 二、方法：v2.12 的迭代点

| 版本 | 做法 |
|---|---|
| v2.10（十一轮） | 资源生命周期：重建后的旧实例回收 |
| v2.11（十二轮） | 契约声明验证（第一版） |
| **v2.12（本轮）** | **契约验证收敛到三个可判定面 + 反向变异验证「启动期断言」** |

**新增lesson 133**：对"启动期 fail-closed 断言"类机制，**必须注入一个已知违规验证它真的会拒绝**——只跑一遍看到"通过"就下结论是常见误判。

---

## 三、确认缺陷

### 🔴 MA-28　`teach_signal` 的 `dry_run` 是假绿：声明「只校验参数」但一行校验都没有

**声明**（`mcp_server.py:2151`）：

```
dry_run=true：只校验参数，不写入。
```

**实现**（`mcp_server.py:2155-2156`）：

```python
if dry_run:
    return {"ok": True, "dry_run": True, "message": "dry_run：参数校验通过，未写入"}
```

⇒ **在调用 `signal_learning.teach_signal` 之前就返回了**，而真实校验全在后者里。

#### 实测（对照真实校验）

| 入参 | `dry_run=true` 返回 | 真实校验（`signal_learning.py:110-127`） |
|---|---|---|
| `entity_id=''`（空） | `ok=True` 校验通过 | ❌ `entity_id 不能为空` |
| `kind='bogus'`（非法） | `ok=True` 校验通过 | ❌ `kind 仅支持 'hard' \| 'soft'` |
| `kind='soft'` 但 `text=''` | `ok=True` 校验通过 | ❌ `kind='soft' 必须提供 text` |
| 合法入参 | `ok=True` 校验通过 | ✅ 通过 |

⇒ **dry_run 对全部输入都返回"参数校验通过"，包括真实路径会拒的三类。它既不写入，也不校验。**

#### 为什么这是 High

- 工具描述把 `dry_run` 定位成"安全试跑"，Agent/运维会**依赖它判断参数是否合法**，然后才敢用 `dry_run=false` 真跑
- 返回 `ok=True` + "校验通过" 是**主动断言**，不是沉默——比静默更有欺骗性
- 这与本项目反复强调的反假证据标准直接冲突（`device_feed` 注释：通道"结构性停在 dry_run"是**刻意的安全设计**；`config.py:293` 同）

#### 同仓正面对照（lesson 132）

`agent_memory.add_semantic_memory(dry_run=True)`（155-208 行）**做对了**：

```python
if not text or not text.strip():  return {"ok": False, "error": "text 不能为空", "code": 400}
if not source_refs:               return {"ok": False, "error": "source_refs 不能为空...", "code": 422}
ok, invalid = self._validate_source_refs(source_refs)
if not ok: ...过滤非法引用，全非法则 422...
if dry_run:
    scan = self.conflict_scan(text, tk)        # ← 真实预览
    return {"ok": True, "dry_run": True, "will_embed": ..., "conflict_scan": scan,
            "auto_promote_blocked": auto_block, "session_trust": trust_info,
            "message": "dry_run 未落库；preview 仅供参考"}
```

⇒ **同一个仓里两种 dry_run 语义：一种是真预览，一种是空返回。**

其余 3 处 dry_run 也均正确：`behavior_routes:1101`（真实预览动作）、`debug_routes:267`（`"(dry_run 未执行)"`）、`device_feed`（匹配但不派发）。**5 处中仅 1 处假绿**。

#### 修复方向

```python
if dry_run:
    # 跑真实校验但不落库：让 dry_run 兑现「只校验参数」的承诺
    from .signal_learning import SignalLearning  # 或复用现有实例
    chk = self.signal_learning.teach_signal(..., dry_run=True)   # 让后端支持 dry_run
    # 最小改动：至少复刻 entity_id/kind/text 三项校验
```

最小改动版本：把 `entity_id` / `kind` / `soft 需 text` 三项校验提到 `if dry_run` 之前（与后端同判据），这样 dry_run 至少兑现声明。

---

## 四、验证通过（确认无问题）

### 4.1 MCP 工具登记表：双向核对（lesson 134）

```
TOOL_NAMES（唯一真源）  90 个
实际 @mcp.tool() 装饰    85 个
```

**[A] 登记但无 `@mcp.tool()` 实现：5 个**

`route_question`、`list_vision_cameras`、`get_vision_status`、`analyze_camera`、`query_behavior_events`

⇒ **全部为 `generated=True` 的 `ToolSpec`，经 `register_simple_tools` 动态注册**，且带 `service`/`method` 与完整 `params`。**非缺陷。**

**[B] 有实现未登记：0 个** ✅

另有导入期自检 `validate_specs()` 覆盖登记表内部一致性（重名 / 缺 method / generated 无 method / 内置无派发目标）。

### 4.2 启动期写工具完整性断言：反向变异验证（lesson 133）

`mcp_scopes.assert_write_tools_complete()`：

```
正常跑：  返回 []（通过）
注入 create_evil_tool（伪造 spec，命中 create_ 前缀）：
  → 【安全告警·启动拒绝】…将被默认为只读对所有 scope 开放: create_evil_tool
  → SystemExit  ✅ 正确拒绝
```

⇒ **机制真实有效**，且已升级到"启动拒绝"而非只记 log（WO-MA-012 R-23）。`WRITE_TOOLS` 31 个、`REGISTERED_TOOLS` 59 个。

### 4.3 成员隔离 fail-closed（第十二轮遗留复核）

| 检查 | 结论 |
|---|---|
| `get_member_daily_pattern` 空 `member_id` | ✅ 返回 `INVALID_PARAM` / `DENIED` |
| `member_daily_pattern` 空 `member_id` | ✅ 拒绝 |
| `list_agent_memories` 非 admin 缺 `member_id` | ✅ 403 |
| `retrieve` 空 `member_id` | ✅ 只返回公共记忆（`member_id=""`），与 `list` 口径一致 |
| **admin 审计通道的日志措辞** | ⚠️ **第二轮 MA-05 仍存在**：日志写 `cross-member recall` / `full member_id-less listing`，实际只给公共记忆 |

### 4.4 参数一致性（判据 C）

全量 49 个带 `method` 的工具，报"schema 参数后端不接受" **4 个**：

| 工具 | 多余参数 | 结论 |
|---|---|---|
| `query_unified_events` | `days` / `offset` | ✅ 包装层自行转换（1516 行）→ **误报** |
| `teach_signal` | `dry_run` | ✅ 包装层处理 → **误报**（但处理方式是缺陷，见 MA-28） |
| `retrieve_agent_memories` | `limit` / `query` | ✅ 包装层处理 → **误报** |
| `save_analysis_template` | 12 个 | ✅ 包装层转为 `BehaviorInsight` → **误报** |

⇒ **判据 C 要追到 `@mcp.tool()` 的包装函数体**，不能拿 spec 直接对后端方法签名（lesson 135）。

---

## 五、修复建议

### MA-28（一处）

把三项校验提到 `dry_run` 分支之前，最小改动：

```python
entity_id = (entity_id or "").strip()
if not entity_id:
    return {"ok": False, "error": "entity_id 不能为空", "code": 400}
kind = (kind or "hard").strip().lower()
if kind not in ("hard", "soft"):
    return {"ok": False, "error": "kind 仅支持 'hard' | 'soft'", "code": 400}
if kind == "soft" and not (text or "").strip():
    return {"ok": False, "error": "kind='soft' 必须提供 text", "code": 400}
if dry_run:
    return {"ok": True, "dry_run": True, "message": "dry_run：参数校验通过，未写入"}
```

**更彻底**：让 `signal_learning.teach_signal` 原生支持 `dry_run` 并在校验后返回预览（对齐 `add_semantic_memory` 的形状）。

### 建议加门禁

> `check_dryrun_semantics.py`：断言声明"dry_run 只校验/预览"的函数，
> 其 dry_run 分支**之前**必须存在参数校验，或分支内调用真实校验。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | `teach_signal(entity_id="", dry_run=true)` | `ok=False` + `entity_id 不能为空` |
| 2 | `teach_signal(entity_id="e1", kind="bogus", dry_run=true)` | `ok=False` |
| 3 | `teach_signal(entity_id="e1", kind="soft", text="", dry_run=true)` | `ok=False` + `kind='soft' 必须提供 text` |
| 4 | 合法参数 `dry_run=true` | `ok=True` 且**不写入**（不产生 exclusion / memory） |
| 5 | 注入 `create_evil_tool` 后启动 | 仍 `SystemExit`（保持） |

---

## 六、横向观察

### MA-28 的形状：声明"做某事"但没做——第十二轮 MA-26 的姊妹

| 轮 | 缺陷 | 声明 | 实际 |
|---|---|---|---|
| 十二 MA-26 | `save_skill` | "自增 version" | 基数取自入参 ⇒ 恒 1 / 可倒退 |
| **十三 MA-28** | `teach_signal` | "dry_run 只校验参数" | **校验代码不存在** |

两者都是**功能路径本身没写错**（`save_skill` 会写、`teach_signal` 真跑时会校验），错的是**声明的那一步没兑现**。

⇒ 这也解释了为什么扫描器一条都报不出来：代码形状完全正常，**只有拿声明对照才看得见**。

### 又一次"同仓两种写法"

| 正确 | 错误 |
|---|---|
| `add_semantic_memory(dry_run)` 真校验 + 真预览 | `teach_signal(dry_run)` 空返回 |
| `assert_write_tools_complete` 启动拒绝 | — |
| `get_member_daily_pattern` fail-closed | admin 审计通道日志措辞 |

⇒ "正确范式孤岛"第十三次。而且这次**对照更近**：两个 `dry_run` 都在写回/教学这条链路上（`teach_signal` 的 soft 分支内部正是调用 `add_semantic_memory`）。

### 值得肯定：本轮两个负结果含金量很高

- **登记表 [B] 方向 0 处**：`TOOL_NAMES` 作为"唯一真源"名副其实，且有导入期 `validate_specs()` 自检
- **启动期断言经反向变异验证有效**：这在两个仓里都不多见（AutoForge 第七轮测过 15 个门禁，其中 2 个在依赖缺失时崩溃报 rc=1）

⇒ 说明**被单独立项的机制是真做透了的**。问题仍然只在：哪些族被选中立项。

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `rel_path` / `full_unparse` |
| **门 2** | 命中多时手工分诊 | ✅ **决定性**：[A] 5 处 → 查 `register_simple_tools` → 全部非缺陷；判据 C 4 处 → 逐查包装层 → 全部误报；39 处 fail-closed → 收敛到成员隔离一支 |
| **门 3** | 每条缺陷有实测 + 同仓对照 | ✅ MA-28 四类入参实测（含真实校验对照）；正面对照 `add_semantic_memory`；启动断言用**反向变异**验证 |

### 遗留队列（十三轮累积）

| 项 | 状态 |
|---|---|
| **MCP 工具登记表** | ✅ 本轮闭合（双向一致） |
| **启动期写工具断言** | ✅ 本轮闭合（反向变异验证有效） |
| **锁覆盖** | ✅ 十二轮闭合（负结果） |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| 29 处传递性阻塞中未单报的 | ⚠️ 归入 MA-24 的 P99 范畴 |
| **`fail-closed` 39 处声明** | ⚠️ 已核成员隔离一支 + 写工具断言一支；其余未逐一验证 |
| **其余 3 处 `dry_run`** | ⚠️ 静态确认正确，未逐一实测 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette`、`pymysql` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **131–135**（已并入 `lessons-round2.md`，共 1465 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
