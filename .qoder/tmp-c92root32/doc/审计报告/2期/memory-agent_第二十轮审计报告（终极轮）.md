# memory-agent 第二十轮审计报告（终极轮）：ACP 会话属主隔离

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v3「盲区清单化」** —— 主攻 B1（所有权 / 入口对等），辅攻 B6/B8/B9
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

终极轮按工作流 v3 计划执行。**主攻方向（B1 所有权 / 入口对等）命中两条，且是二十轮里最严重的一组**——跨主体的会话读写与劫持。辅攻的三个方向（除零 / 编码 / 导出鉴权）**全部为负结果**。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-36** | 🔴 Critical | `acp_server.py:416` `M_PROMPT` | 唯一接受 `sessionId` 却**不做属主校验**的入口 ⇒ 另一个令牌持有者 B 可用 A 的 sessionId 起 run，**读到 A 的会话历史、并写回 A 的会话** |
| **MA-37** | 🟠 High | `acp_server.py:365` `M_SESSION_NEW` | `new()` 对已存在的 sessionId 直接覆盖 ⇒ B 用同一自定义 ID 建会话即可**改写 owner_token，夺取会话属主**，A 失去自己的会话 |

### 两条缺陷的关系

MA-36 与 MA-37 是**同一资源（ACP 会话）的两个入口**，一个能直接用、一个能永久夺走。合起来构成完整攻击链：

```
知道/猜到 A 的 sessionId
   ├─ 直接用 M_PROMPT（MA-36）→ 读+写 A 的会话，无需取得属主
   └─ 用 M_SESSION_NEW（MA-37）→ 改写 owner_token → 永久夺走，A 被拒
```

---

## 二、MA-36（Critical）：`M_PROMPT` 无属主校验，跨主体读写会话

### 入口对等的直接对照（同一文件、相邻分支）

| 入口 | 行 | `check_owner` | 代码注释 |
|---|---|---|---|
| `M_SESSION_HISTORY` | 372 | ✅ | 「P0-9 修复：先查会话存储+属主校验，再查 `_CONV`」 |
| `M_SESSION_DELETE` | 389 | ✅ | 「P0-9 owner check (centralized helper)」 |
| `M_CANCEL` | 399 | ✅ | 「必须在 run_id 查询之前：避免跨 principal 运行态 oracle」 |
| **`M_PROMPT`** | **416** | ❌ **无** | — |

`check_owner` 定义于 `acp_server.py:93`，注释明确写着：

> P0-9 owner check (fail-close): unknown/empty owner_token is DENIED.
> **Centralized helper to avoid drift across history/delete/cancel.**

⇒ 它就是为了"避免各入口漂移"而集中收口的，但**漏了 `M_PROMPT`**——而 `M_PROMPT` 恰恰是**能力最强**的那个（会实际起 `DebugRun` 执行指令）。

### 缺陷代码

```python
# acp_server.py:416
if method == M_PROMPT:
    session_id = params.get("sessionId") or _STORE.new()   # ← 传入已有 sid 直接沿用，不校验属主
    ...
    run = DebugRun(..., conversation_id=session_id, ...)    # :440 sessionId 即 conversation_id
    _STORE.bind(session_id, run.run_id, owner_token=_owner) # :443
```

而 `_execute_run`（`api/debug_routes.py`）用 `conversation_id` 直接索引共享会话表 `_CONV`：

```python
# debug_routes.py:204-206  加载
messages = list(_CONV.get(run.conversation_id) or [])
...
# debug_routes.py:335     回写
_CONV[run.conversation_id] = messages
```

### 实测（两个令牌，A 先建立含隐私内容的会话）

```
B 调 M_SESSION_HISTORY 读 A 会话      → ❌ 拒绝「属主不匹配」  ✅ 护栏生效
B 调 M_PROMPT 传 A 的 sessionId       → ✅ 放行，已起 run      ❌ 无护栏

B 的 LLM 请求 messages = 4 条，其中含 A 的银行卡号 6222021234567890 共 1 条
```

**读取链**：`_execute_run` 把 `_CONV[A_sid]` 全部历史装进 LLM 上下文 ⇒ B 的 SSE 流收到基于 A 上下文生成的回答 ⇒ **B 完整读到 A 的私密内容**。

**写入链**：`_execute_run` 回写 `_CONV[A_sid]` ⇒

```
_CONV[A_sid]: 2 条 → 3 条，新增的 1 条是 B 注入的指令
A 再读自己的历史：3 条，其中 1 条是 B 的内容   ← 写回污染确认
```

⇒ **双向**：B 能读 A 的会话，也能往 A 的会话里写东西，且 A 会在不知情的情况下看到。

### 为什么定 Critical

1. **读写双向**——不只是泄露，还能污染受害者会话（B 可注入指令影响 A 后续对话）
2. 泄露内容是**对话全文**（本仓库是家庭行为记忆库，隐私密度极高）
3. 护栏在**同一文件相邻分支**都做了，唯独这个入口没有 ⇒ 是遗漏不是设计
4. 无日志、无告警，A 完全无感知

---

## 三、MA-37（High）：`M_SESSION_NEW` 覆盖已存在会话 ⇒ 属主劫持

### 缺陷代码

```python
# acp_server.py:365
if method == M_SESSION_NEW:
    sid = _STORE.new(params.get("sessionId"), owner_token=_owner)
```

```python
# acp_server.py:76  _STORE.new()
def new(self, session_id=None, owner_token=""):
    sid = session_id or f"acp_{uuid.uuid4().hex}"
    self._sessions[sid] = {"run_id": None, "created_at": time.time(),
                           "owner_token": owner_token}   # ← 直接赋值，无存在性检查
    self._trim()
    return sid
```

⇒ **`sessionId` 由调用方指定，且已存在时直接覆盖 `owner_token`**。

### 实测（A 用自定义稳定 ID 建会话）

```
A: session/new  sessionId="alice-laptop"  → owner_token=tokenA
B: session/new  sessionId="alice-laptop"  → owner_token 被改写为 tokenB

之后：
   B 读历史   → ✅ 通过（B 已成为属主）
   A 读历史   → ❌ 被拒「属主不匹配」   ← A 失去自己的会话
```

⇒ 一步夺取，且**受害者会被自己创建的会话拒之门外**。

### 为什么定 High 而非 Critical

- 需要 **sessionId 已知或可预测**
- 默认 ID 是 `acp_{uuid4().hex}`（128 位随机，**不可猜**）
- 但 `sessionId` 可由调用方指定，而客户端常用**稳定 ID**（设备名、用户名）作为会话标识——这正是 `sessionId` 参数存在的意义
- 与 MA-36 相比：MA-36 **不需要**夺取属主就能读写，MA-37 需要多一步

### 一个加重因素

`bind()`（:82）用 `setdefault` 不覆盖属主，但它会**把 B 的 run_id 写进 A 的会话记录**。配合 MA-36，A 的会话里会残留 B 的运行态。

---

## 四、利用前提与缓解（如实标注）

### 前提（两条缺陷共同）

| 条件 | 是否满足 |
|---|---|
| 攻击者需有有效 ACP 令牌 | ✅ `/acp` 经 `ACPTokenMiddleware` 鉴权，**匿名不可达** |
| 攻击者需知道受害者的 sessionId | ⚠️ **这是关键限制** |
| 能否枚举 sessionId | ❌ `M_SESSION_LIST` 按属主过滤，不可枚举 |

⇒ **不是匿名远程可利用**。定位是**多主体隔离失效**（持有效令牌的一个主体可以读取另一主体的会话），类似多租户越权。

### 现有缓解（值得肯定）

- `check_owner` 对空/未知 `owner_token` **fail-closed**（:98-99）
- 默认 sessionId 为 `uuid4`（不可猜）
- `M_SESSION_LIST` 按属主过滤
- `M_CANCEL` 的注释显示团队**专门想过**跨主体的信息泄露（"运行态 oracle"）

⇒ 团队对这类问题有清晰认识并写了集中 helper，**问题只在于漏了两个入口**。

---

## 五、修复建议

### MA-36（一行 + 一个判据）

```python
if method == M_PROMPT:
    session_id = params.get("sessionId") or _STORE.new()
    # P0-9 owner check：传入已有 sessionId 时必须校验属主（与 history/delete/cancel 同口径）
    if params.get("sessionId") and not _STORE.check_owner(session_id, _owner):
        return make_error(req_id, ERR_INVALID_PARAMS, "无权访问该会话（属主不匹配）"), None
```

注意保留 `or _STORE.new()`——未传 sessionId 时新建是合法语义（`bind` 会带上 owner_token），**只校验"显式传入已有 sid"这一种情况**。

### MA-37（拒绝或校验）

```python
def new(self, session_id=None, owner_token=""):
    if session_id and session_id in self._sessions:
        # 已存在：不得改写属主
        if self._sessions[session_id].get("owner_token") != owner_token:
            raise ValueError("sessionId 已被占用")
        return session_id
    ...
```

或在 `M_SESSION_NEW` 分支先 `check_owner` 再决定。

### 建议加门禁（第 3 个）

> `check_entry_parity.py`：断言同一模块内所有接受同一资源 ID 的入口，
> 要么都调用校验 helper，要么都登记显式豁免。

配合前两轮建议的 `check_archive_gate.py`（闸唯一性）与 `check_cli_writes.py`（入口对等性），构成三条"入口一致性"门禁。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | A 用自己 sid 调 M_PROMPT | 通过（现有行为不变） |
| 2 | B 用 A 的 sid 调 M_PROMPT | **DENY**（当前放行 ❌） |
| 3 | B 用 A 的 sid 调 M_SESSION_HISTORY/DELETE/CANCEL | DENY（现有行为不变） |
| 4 | 未传 sessionId 调 M_PROMPT | 新建会话并通过 |
| 5 | A 建 "alice-laptop"，B 用同 ID 建 | **拒绝或保持属主为 A**（当前被夺 ❌） |
| 6 | A 随后读自己历史 | 通过（当前被拒 ❌） |

---

## 六、辅攻方向：三个负结果（全部闭合）

| 判据 | 候选 | 结论 |
|---|---|---|
| **B6 除零 / 空序列聚合** | 8 | ✅ **全部有守卫**：`sum(d.values()) + 1e-9`、`if not a and not b: return 0.0`、`round(ok / n, 3) if n else 0.0`、`sum(sp)/len(sp) if sp else None` |
| **B8 编码未显式指定** | 3 | ✅ **全部假阳性**：`subprocess.Popen`（且该端点已按审计 P0-10 停用）、`tarfile.open(out_path,'w:gz')`（二进制无需 encoding）、`urlopen(r, timeout=90)`（非文件 open） |
| **B9 导出/备份通道鉴权** | 9 | ✅ **均为内部函数或 MCP 工具**（MCP 工具经令牌 scope 收口），无对外裸入口 |
| **B4 TTL 方向** | 6 | ✅ 方向全部正确（`expires_at <= now`、`ttl > 0`、`monotonic() < deadline`；空 `expires_at` 判过期属 fail-closed） |
| **B5 锁跨 I/O** | 1 | ✅ `purge_old` 锁间 commit 是审计 P0-7 的**有意优化**（避免持锁 143 秒） |
| **B7 优雅退出** | 0 | ✅ `TaskRegistry.cancel_all` 已统一收口 |

⇒ 工作流 v3 计划里的 B4–B9 六个辅攻方向**全部闭合**，无一新增缺陷。**B10（去重键）23 条候选噪音偏高，未分诊，留作遗留。**

---

## 七、横向观察

### "已设计过但漏了入口" —— 第 20 次，也是最严重的一次

二十轮下来这个模式出现 20 次，但以往都是**功能性**后果（配置不生效、日志说谎、清理不跑）。本轮第一次是**安全隔离**后果。

而且证据链最完整：

- `check_owner` 的 docstring 亲口写着「Centralized helper to avoid drift across **history/delete/cancel**」
- 三个入口都调了，`M_PROMPT` 没调
- `M_CANCEL` 的注释甚至考虑了**跨主体的信息泄露**（避免用错误码探测对方是否有 in-flight run）

⇒ **团队对这类威胁建模得相当细致**，只是那份细致没有覆盖到 `M_PROMPT` 与 `M_SESSION_NEW` 两个分支。

### 与 AutoForge 的同形对照（第四、五次）

| 项 | AutoForge | memory-agent |
|---|---|---|
| 所有权隔离被旁路 | R20-01（导入通道绕过 owner） | **MA-36/37（M_PROMPT / M_SESSION_NEW）** |
| 服务层有护栏、旁路没有 | R15-01 / R18-01 | MA-34（persist 默认） |

⇒ 这条缺陷族现在是**跨两个代码库的五次确认**，可以稳定地作为一类判据使用。

### 一个值得注意的设计观察

`M_SESSION_NEW` 允许调用方指定 sessionId —— 这个设计**本身就是风险源**。它让 sessionId 从"服务器生成的不可猜标识"退化成"客户端可控的可预测标识"。

⇒ 建议：要么禁止调用方指定（只接受服务器生成的 uuid），要么在指定时强制校验属主（即 MA-37 的修法）。**前者更安全，后者兼容性更好。**

---

## 八、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 脚本过 `scanner_selfcheck` | ✅ `scan_round21.py` 用 `code_unparse` / `own_walk` / `CallGraph` / `rel_path`；SC-11 已加 |
| **门 2** | 命中异常少/多时手工分诊 | ✅ **决定性**：B1 由全仓扫 21 条 → 按模块边界收窄到 3 条 → 人工逐个追分支发现 M_PROMPT；B6/B8 共 11 条候选全部追到守卫/假阳性 |
| **门 3** | 每条缺陷有完整链路 PoC | ✅ MA-36 读链（B 的 LLM 请求含 A 卡号）+ 写链（A 会话 2→3 条、A 读到 B 内容）双实证；MA-37 "alice-laptop" 用例实证属主改写与 A 被拒；**利用前提与缓解逐条列出** |

### 遗留队列（二十轮累积）

| 项 | 状态 |
|---|---|
| **B1 所有权 / 入口对等** | ✅ 本轮闭合（MA-36/37） |
| **B4 TTL 方向 / B5 锁 / B6 除零 / B7 退出 / B8 编码 / B9 导出** | ✅ 本轮闭合（全负结果） |
| 清理 / 保留路径 | ✅ 十九轮闭合 |
| MCP 只读契约 | ✅ 十八轮闭合 |
| 跨仓 outbound trace_id | ✅ 十七轮闭合 |
| 配置面三方一致性 | ✅ 十六轮闭合 |
| **B10 去重键稳定性** | ⚠️ **23 条候选噪音偏高，未分诊** |
| 常驻周期任务 | ⚠️ 已报 MA-31；`device_feed` 观察项 |
| `candidate_promotion` NaN | ⚠️ 观察项（实测不可达） |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| `fail-closed` 39 处声明 | ⚠️ 已核两支 |

### 环境

| 项 | 值 |
|---|---|
| 本轮新增安装 | `starlette`、`bcrypt`、`python-jose`、`pymysql`（PoC 需要） |
| 仍未装 | `chromadb`、`mcp` SDK（模块可导入） |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **173–176** |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |

---

## 九、二十轮总结

| 指标 | 值 |
|---|---|
| 轮次 | 20 |
| 确认缺陷 | **37 条**（MA-01 – MA-37） |
| 最高严重度 | 🔴 **Critical（MA-36 跨主体会话读写）** |
| 本轮新增维度 | 6 个（B1、B4–B9 中的 B1 为主攻） |
| 闭合的负结果 | 本轮 6 个方向全部确认无问题 |

**最稳定的模式**：二十轮里"**已设计过但只做在到达的地方**"出现 **20 次**。本轮是最严重的一次，因为第一次落到安全隔离上。

**最有效的判据**（按命中率排序）：
1. 声明 vs 实现对照（贡献 MA-26/27/28/29/30/32/33/35 共 8 条）
2. 已修范式铺开度反查（MA-01/02/03/04 等）
3. 跨项目缺陷族迁移（MA-20/21/22 + MA-35）
4. **入口对等性**（本轮 MA-36/37，也是 AutoForge R15/18/20 的同一判据）

**给工程团队的一句话**：`check_owner` 的注释写着「centralized helper to avoid drift」——这个意图是对的，但"集中"只解决了重复实现，**没解决"每个入口都要调"**。真正能封住这类缺陷的是**门禁**：断言同一模块内所有接受同一资源 ID 的入口，要么都调校验，要么登记豁免。你们已经有 15 个 `check_*.py`，加第 16 个成本很低。
