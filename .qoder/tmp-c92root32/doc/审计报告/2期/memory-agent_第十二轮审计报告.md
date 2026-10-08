# memory-agent 第十二轮审计报告：契约声明验证

> 审计对象：`https://github.com/lidicn/memory-agent`（main）
> 范围：`src/memory_agent` 119 个 Python 文件
> 本轮工作流：**v2.11「契约声明验证」** + 锁覆盖核查（负结果）
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 日期：2026-10-06

---

## 一、执行摘要

前十一轮的判据都是"**代码长什么样**"。本轮换一个方向：**代码自称是什么** —— 把 docstring/注释里可验证的承诺提取出来逐条实测。

提取三类声明：**幂等 50 处、fail-closed 40 处、唯一真源 7 处**。

### 确认缺陷

| 编号 | 级别 | 位置 | 一句话 |
|---|---|---|---|
| **MA-26** | 🔴 High | `mcp_server.py:3058 save_skill` | 声明"自增 version"，实测**连续两次保存恒为 1**；磁盘 v5 被覆盖后版本**倒退**到 1 |
| **MA-27** | 🟠 Medium | `agent_memory.py:522` `list_agent_memories` | 响应自报 `"member_id": "all"`，实测只返回公共记忆（库里 3 条 → 返回 1 条） |

### 附带：锁覆盖核查（负结果，本维度闭合）

| 检查项 | 结果 |
|---|---|
| async 函数内持 `threading.Lock` 时 `await` | **0 处** ✅ |
| 多锁区函数（一个函数多把锁/RMW 被拆散） | **2 处，均正确**（`store.py:4579 purge_old` 为审计 P0-7 已修；`candidate_promotion.py:758`） ✅ |
| 锁覆盖不一致（同容器部分函数不持锁） | **3 个低风险观察项**，均"最坏重算/晚生效一次"，无数据损坏 ⇒ **不升级为缺陷** |

---

## 二、方法：v2.11 的迭代点

| 版本 | 做法 |
|---|---|
| v2.9（十轮） | 读项目自带量具声明的"看不见"部分 |
| v2.10（十一轮） | 资源生命周期：重建后的旧实例回收 |
| **v2.11（本轮）** | **契约声明验证：代码自称是什么 → 逐条实测** |

**lesson 126**：与形状扫描互补。形状扫描找"代码长什么样"，声明验证找"**代码自称是什么**"——后者能抓到形状完全正常、但语义与声明不符的缺陷（MA-27 的 fail-closed 实现**本身是对的**）。

---

## 三、确认缺陷

### 🔴 MA-26　`save_skill`：「自增 version」不成立，版本可倒退

**声明**（三处一致）：

```
mcp_server.py:3064  「网关保存后会把 version +1 并刷新 updated_at，
                      Agent 下次 get_skill 即拿到最新版本」
mcp_server.py:19    「把分析经验写回网关（唯一真源）：自增 version…」
tool_schema.py:713  （工具描述同口径）
```

**根因**：`prev_version` 只解析**传入 `content` 的 frontmatter**，从不读磁盘现有 `SKILL.md`：

```python
prev_version = 0
if existing.get("version"):          # existing ← 来自 content，不是磁盘
    try: prev_version = int(existing["version"])
    except ValueError: prev_version = 0
new_version = prev_version + 1       # 无 frontmatter ⇒ 恒为 1
```

#### 实测（四个场景）

| # | 场景 | 结果 |
|---|---|---|
| 1 | 连续两次保存不带 frontmatter 的正文 | version **均 1** ⇒ "自增"不成立 ❌ |
| 2 | 磁盘已有 v5，保存无 frontmatter 新正文 | version **倒退到 1** ❌ |
| 3 | Agent B 缓存 v1，网关 v1 | 消费方判定"无更新" ⇒ **不拉取**，继续用旧内容 ❌ |
| 4 | Agent 回传 `get_skill` 拿到的完整内容（含 frontmatter） | version 正常 1 → 2 ✅ |

⇒ **只有场景 4 契约成立**，即要求调用方严格回传完整内容。而工具描述并未声明这个前提。

#### 消费端影响

`list_skills` 的口径是"比对版本号决定是否需要拉取"。场景 3 下版本号不涨 ⇒ **Agent 永远认为没有更新**，写回的经验不会被其他 Agent 看到。

**方向是安全的**（不会把旧的当新的），但**功能实质失效**——写回网关的"唯一真源"承诺落空。

#### 修复方向

`save_skill` 应先读磁盘现有 `SKILL.md` 的 `version` 作为 `prev_version` 基数：

```python
disk = _read_frontmatter(path) if os.path.isfile(path) else {}
prev_version = int(existing.get("version") or disk.get("version") or 0)
```

**lesson 128**：凡声明"自增"的，基数必须来自**持久态**（磁盘/DB），不能来自入参。这与 AutoForge 第十七轮 R17-01（`AuthCodeStore` 不重载盘导致一次性消耗失效）**是同一族**。

---

### 🟠 MA-27　`list_agent_memories`：响应自报 `"member_id": "all"`，实际只返回公共记忆

**位置**：`agent_memory.py:522`

```python
rows = self.store.list_agent_memories(state, source,
                                      member_id=member_id or "",
                                      exact_member=True)
return {
    ...
    "member_id": member_id or "all",    # ← 自报 "all"
    "count": len(rows),                 # ← 实际只含公共记忆
}
```

#### 实测（真实代码，库里 3 条）

```
══ 库里 3 条：公共 / m1 / m2 ══

list_agent_memories()（不传 member_id）
   响应自报 member_id = 'all'
   实际返回 count     = 1   （库里共 3 条）
   实际 member_id 集合 = ['']          ← 只有公共记忆

list_agent_memories(member_id='m1')
   自报 'm1'   count=1
```

#### 必须如实说明方向

**fail-closed 实现本身是正确的**——注释（514-515 行）明确写着：

> vMA-1.2.2: fail-closed —— 空 member_id 只返回公共记忆（member_id=''），
> 不返回任何特定成员的记忆，与 retrieve 路口径一致

⇒ **没有多给数据，不是泄露**。缺陷在于**响应体自称"all"而实际不是**。

**后果**：调用方/前端按响应体判断"我拿到了全部" ⇒ **静默少数据**，且 `count: 1` 与 `member_id: "all"` 自相矛盾，排查时会被响应体误导。

**lesson 129**：不要因为"fail-closed 所以安全"就放过。**声明与实现不符本身就是缺陷**，只是严重度由方向决定（多给 = High，少给 = Medium/Low）。

**修法**：自报值改为与实际一致，例如 `"member_id": member_id or ""` 并增加 `"scope": "public_only"` 之类的显式字段；或让路由层要求显式传 `member_id`。

---

## 四、验证通过（确认无问题）

### 契约类

| 声明 | 结论 |
|---|---|
| **MCP 写工具幂等（"防重复执行"）** | ✅ **做对了**：`store.reserve_idempotency` 用 `idem_key` 主键 + `INSERT OR IGNORE`，判定全在 `self._lock` 与同一写连接内完成，跨线程成立；docstring 标注"第六轮审计 CRITICAL-1"，且区分 `reserved` / `done` / `in_flight` 三态 |
| `save_skill` 版本自增（回传 frontmatter 场景） | ✅ 场景 4 正常 1 → 2 |
| `get_skill` 返回内容 | ✅ 返回含 frontmatter 的完整内容 ⇒ 回传链路自洽 |
| `add_agent_memory` 成员归属 | ✅ `member_id` 参数存在（WO-MA-005） |
| `retrieve` 的 member_id fail-closed | ✅ 与 `list_agent_memories` 口径一致（空 → 只公共） |

### 锁覆盖（本轮附带维度，负结果）

| 检查 | 结果 |
|---|---|
| 持锁 `await` | **0 处** |
| 多锁区 | 2 处均正确：`store.py:4579 purge_old`（审计 P0-7 已修，3 锁）、`candidate_promotion.py:758 get_json`（5 锁，各锁区仅单计数器自增） |
| `store.py` 129 处持锁 I/O | ✅ 单连接 + 全局 RLock 的设计使然，**非缺陷** |
| `_FAULT_INJECT` 无锁读 | 低风险：`get()` 单操作 GIL 原子，最坏"注入晚生效一次" |
| `_PROMPT_CACHE` 无锁 | 低风险：上限 8 条，`clear` 后最坏重算 |
| `ha_client._session` / `debug_routes._RUNS/_CONV` | 低风险：asyncio 单线程场景 |

⇒ **本维度可标记为已闭合**（lesson 130：负结果也要写清"扫了什么、覆盖到什么程度"）。

---

## 五、修复建议

### MA-26（一处，约 3 行）

```python
# save_skill 内，path 已知
disk_fm = {}
if os.path.isfile(path):
    try:
        disk_fm = _parse_frontmatter(Path(path).read_text(encoding="utf-8"))
    except Exception:
        disk_fm = {}
prev_version = 0
for src in (existing, disk_fm):
    if src.get("version"):
        try: prev_version = max(prev_version, int(src["version"]))
        except ValueError: pass
new_version = prev_version + 1
```

### MA-27（一处，1 行 + 建议加字段）

```python
- "member_id": member_id or "all",
+ "member_id": member_id or "",
+ "member_scope": "member" if member_id else "public_only",
```

### 建议加门禁

> `check_claimed_semantics.py`：把 docstring 里出现"自增/递增/唯一真源/fail-closed/幂等"
> 的函数登记为**待验证契约**，配套一个断言脚本逐条跑；新增声明必须带验证用例。

### 回归验证清单

| # | 用例 | 期望 |
|---|---|---|
| 1 | 连续两次 `save_skill` 不带 frontmatter | version **1 → 2** |
| 2 | 磁盘 v5，保存无 frontmatter 新正文 | version **6**，不倒退 |
| 3 | 保存后 `list_skills` 版本号 | 与磁盘一致，消费方能判定"有更新" |
| 4 | `list_agent_memories()` 不传 member_id | 响应体不出现 `"all"`；或加显式 scope 字段 |
| 5 | `reserve_idempotency` 并发同 key | 仍只有一次执行（保持现状） |

---

## 六、横向观察

### 两条缺陷的共同形状：声明与实现之间的"最后一公里"

MA-26 是"**基数取错来源**"（入参 vs 磁盘），MA-27 是"**自报口径取错**"（参数 vs 实际返回集）。两者代码形状都完全正常，扫描器一条都报不出来——**只有把声明拿来对照才看得见**。

这与 AutoForge 第十七轮（提取 557 条声明逐条验证）的方法一致，且两边都命中了"**一次性/自增语义依赖持久态**"这一族（AutoForge R17-01、memory-agent MA-26）。

### "正确范式孤岛"第十二次

| 轮 | 族 | 已修 | 未修 |
|---|---|---|---|
| 十 | 同步 I/O 卸载 | 96/172 | 76 |
| 十一 | 旧实例回收 | `reload_config` 的 ha_db | 同函数的 llm |
| **十二** | **成员归属 fail-closed** | **`retrieve` 路 + `list_agent_memories` 的过滤** | **同一函数的响应体自报** |

MA-27 又一次是"**同一个函数里，过滤做对了、自报没跟上**"——与第十一轮 MA-25（同一函数 ha_db 对、llm 错）是同一个形态：**修复动作逐点发生，没有形成"凡是与口径有关的输出都要同步"的约定。**

### 值得肯定：幂等那一族修得很彻底

`reserve_idempotency` 不仅有 `INSERT OR IGNORE` 主键占位、锁内完成、三态区分，还标注了审计编号（第六轮 CRITICAL-1）和"崩溃残留 pending 行到期可重新占位"的边界处理。

⇒ 与第十一轮 `runtime.shutdown()` 类似：**被单独立项的族修得很干净**。差别在于哪些族被选中立项。

---

## 七、工作流执行与遗留

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 用 `auditlib` 原语 | ✅ `all_walk` / `own_walk` / `full_unparse` / `rel_path` |
| **门 2** | 命中多时手工分诊 | ✅ 97 条声明 → 按"是否可实测"降到 5 条候选 → 确认 2 条 |
| **门 3** | 每条缺陷有实测 PoC + 同仓对照 | ✅ MA-26 四场景实测（含正例场景 4）；MA-27 真实代码 3 条数据实测 + fail-closed 方向如实标注 |

### 遗留队列（十二轮累积）

| 项 | 状态 |
|---|---|
| **锁覆盖维度** | ✅ **本轮闭合**（负结果） |
| `save_skill` 并发 | ⚠️ 观察项，未复现交叠 |
| MA-03 端到端 | ⚠️ 未复现（`chromadb` 太重） |
| 47 处静默降级中"否定方向"44 处 | ⚠️ 仅抽查 |
| M2 的 34 处未钳制 `LIMIT` 站点 | ⚠️ 抽查 |
| 24 个数值配置键中其余 16 个 | ⚠️ 未逐一找消费点 |
| `self._lock` 15 处 | ⚠️ 未逐一核查覆盖范围（本轮已扫锁覆盖一致性） |
| 29 处传递性阻塞中未单报的 | ⚠️ 归入 MA-24 的 P99 范畴 |
| **`fail-closed` 40 处声明** | ⚠️ 本轮只验证了成员归属一支，其余 39 处未逐一验证 |

### 环境

| 项 | 值 |
|---|---|
| 已装 | `bcrypt`、`python-jose`、`httpx`、`tomli` |
| 仍未装 | `chromadb`（体积过大）、`starlette`、`pymysql` |
| 门禁 | `run_gates.py . --no-smoke` → rc=0（208 条基线） |
| 新增 lessons | **126–130**（已并入 `lessons-round2.md`，共 1417 行） |
| 仓库状态 | 探针已还原；源码、量具、门禁、基线均未修改 |
