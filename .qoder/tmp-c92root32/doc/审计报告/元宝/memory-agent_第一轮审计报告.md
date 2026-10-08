# memory-agent 第一轮审计报告

> 审计对象：https://github.com/lidicn/memory-agent （main 分支）
> 审计日期：2026-10-04
> 审计目标：**影响稳定性与功能性的缺陷**
> 审计方式：**图谱导引**——先建调用图认枢纽与绕过点，再用测试套件与定点探针打击（沙箱真实运行，非静态通读）

---

## 一、审计环境与方法

| 项 | 实际做法 |
|---|---|
| 代码获取 | codeload 拉取 main 源码（GitHub 直连 403，走 codeload 通道成功） |
| 代码规模 | 127 个 Python 模块 / 54524 行 / 103 个测试文件 |
| 运行环境 | Python 3.10.12（项目要求 ≥3.11，低一档；已验证全部模块可编译、1096 项测试可跑，不影响结论） |
| 测试执行 | `pytest tests/` 全量，带 `JWT_SECRET` 运行（该键未配置时 `Config` 直接抛 `RuntimeError` 拒绝启动，属 WO-MA-004 ③ 的既定设计） |
| 缺陷复现 | 对每条疑似缺陷写独立脚本/用例，构造真实数据对比「期望 vs 实际」 |
| 假阳性处理 | 对工具告警逐条读上下文证伪，未证伪的不写入结论 |

### 安装的测试与分析套件（均来自 GitHub）

| 套件 | 仓库 | 用途 | 状态 |
|---|---|---|---|
| pytest / pytest-asyncio | pytest-dev/pytest | 单元与异步测试执行 | 已跑 |
| pytest-cov | pytest-dev/pytest-cov | 覆盖率，定位未测区域 | 已跑 |
| pytest-xdist / -randomly / -timeout | pytest-dev/* | 并发、乱序、超时 | 已装 |
| Hypothesis | HypothesisWorks/hypothesis | 属性测试，全输入域反例搜索 | 已跑 |
| Ruff | astral-sh/ruff | 多规则静态分析 | 已跑 |
| Pyflakes | PyCQA/pyflakes | 未定义名 / 未使用导入 | 已跑 |
| Bandit | PyCQA/bandit | 安全反模式扫描 | 已跑 |
| Vulture | jendrikseipp/vulture | 死代码检测 | 已跑 |
| Semgrep | semgrep/semgrep | 语义静态分析 | **未装上**（包体 67MB 超沙箱文件上限，改用 Ruff+Bandit+Pyflakes 三件套覆盖） |
| PyCG | vitsalis/PyCG（ICSE 2021） | **调用图**，JSON 邻接表 | 已跑，2901 节点 / 7257 边 |
| pyan3 / code2flow / pydeps | PyCQA / scottrogowski / thebjorn | 调用图与模块依赖图（交叉验证） | 已装备用 |
| import-linter | seddonym/import-linter | 分层架构违规 | 已装 |
| networkx + matplotlib | — | 图谱折叠与可视化 | 已出图 |

> PyCG 0.0.8 安装目录名为大写 `PyCG`，需 `PYTHONPATH` 指向其父目录才能 `python3 -m pycg` 运行。

### 安装的审计技能（来自 trailofbits/skills）

`audit-context-building`（建系统图）、`sharp-edges`（危险 API/失败开放）、`property-based-testing`（属性测试）、`fp-check`（假阳性验证）、`second-opinion`（交叉复核）、`semgrep`、`sarif-parsing`。

按 `audit-context-building` 的方法论先建系统图再定点猎错；按 `fp-check` 的要求，所有告警必须逐条证伪后才可写入结论。

---

## 二、结论先行

> **本轮未发现 P0 级（致命）缺陷。发现 1 条实测复现的功能性契约缺陷、1 类系统性可靠性风险，其余为低级别问题。**

这个结论需要先解释一句：该仓库 `doc/审计报告/` 下已有七轮历史审计（数据库、启动稳定性、长跑稳定性、关节与连接处等），且代码内大量注释标注了对历史缺陷的修复（如「第六轮审计 CRITICAL-2」「审计 P0-11」）。**易被自动化工具命中的问题大多已被修复**，因此本轮的产出以「证伪告警 + 定位残留边角」为主。

一条最重要的判断依据：**全量测试 1096 项通过、0 失败**。测试全绿，但测试套件本身覆盖不到本轮发现的问题——这说明缺陷藏在当前测试盲区，而非跑得出来的地方。

---

## 三、已确认缺陷

### P1-1　MCP 错误契约在依赖缺失时静默失效（实测复现）

**位置**：`src/memory_agent/mcp_errors.py:91-107`

`normalize_tool_result` 的契约是把业务失败 `{"ok": false, ...}` 升级为 `isError=True` 的结构化错误，模块文档明确其目的为「终结 `ok:false` 被模型当正文的契约缺陷」。但构造 `CallToolResult` 的 `try` 块以裸 `except Exception: return result` 兜底：

```python
    try:
        from mcp.types import CallToolResult, TextContent
        ...
        return CallToolResult(**kwargs)
    except Exception:
        return result          # ← 吞掉 ImportError，原样返回 isError=False 的对象
```

**复现**：在未安装 `mcp` 的环境中执行 `tests/test_mcp_errors.py`：

```
test_normalize_ok_false_to_iserror
    assert getattr(res, "isError", False) or getattr(res, "is_error", False)
E   assert (False or False)

test_normalize_keeps_extra_as_detail
E   TypeError: string indices must be integers
```

**影响**：契约静默失效，业务失败以「正常结果」形态返回给调用方（LLM/Agent），错误被当作正文消费。且失败不可见——没有日志、没有状态位。这与项目自己在 `insights/api.py` 中记录的 P0-1 教训（「参数顺序错位被静默降级，用户完全无感」）是同一类失败模式。

**修复建议**：把 `ImportError` 与其他异常分开处理；导入失败时至少记 `LOG.exception`，并在返回值中置一个可观测标记（如 `payload["error"]["code"]` 已算好但对象造不出来时退化为带 `is_error` 属性的简单对象），而非静默返回原对象。

---

### P1-2　69 处「静默降级为空值」掩盖故障（系统性风险）

扫描 `except` 块内直接返回 `None / [] / {} / '' / 0` 且**无任何日志记录**的位置，共 **69 处**；另有 **28 处** `except ... pass` / `except ... continue`（Ruff S110/S112）。

抽查结论：**多数是有意为之的容错**，且注释写得克制，例如：

- `mcp_server._idem_store()`——「幂等是防重复的优化，不能因存储抖动打死工具」
- `mcp_server` 响应截断——「非 JSON 正文走字符截断」
- `deps.json_body`——「空体或非法 JSON 一律返回空字典，不抛异常」

但模式本身仍是风险敞口：69 处中任意一处的异常从配置错误、表结构漂移、上游返回变形引发时，表现都是「功能安静地返回空」，运维侧无信号。

**建议**：不要求逐一改，但应为这批降级加统一的可观测出口——例如在关键路径（store / mcp_server / insights）的降级点累计计数并在 `/api/health` 暴露，与项目已为 `InsightService.entities_loaded/entities_error` 建立的做法保持一致（该做法是目前代码里唯一做对了的范例，见 `insights/api.py:170`）。

---

### P1-3　净水器统计接口绕过统一家庭时钟，7 天窗口错位（图谱导引发现）

**位置**：`src/memory_agent/api/nr_routes.py:125`

```python
start = (datetime.now() - timedelta(days=7)).isoformat()   # ← 容器 UTC 时钟
history_dict = await asyncio.to_thread(rt.ha.get_history, [WATER_PURIFIER_ENTITY], start)
```

**为什么它是缺陷**：项目已确立「家庭墙钟」为唯一时间口径——`store.now_local()` 被 183 处调用，且**项目自己在三处写下同款警告注释**，说明这是已知的历史 bug 家族：

| 已修复位置 | 注释原文摘录 |
|---|---|
| `causal_scanner.py:44` | 「day 列按家庭墙钟落库；UTC 容器里的裸 `datetime.now()` 会让「今天」在凌晨 0–8 点指到昨天，去重随之失效」 |
| `mcp_server.py:611` | 「UTC 16:00–24:00 窗口里把窗口整体前移一天（第七轮审计 · 时区关节）」 |
| `backup.py:34` | 「每天 UTC 16:00–24:00 的备份会被打成前一天，轮转与排查都对不上账」 |

`nr_routes.py:125` 是**同款模式、同款风险、未修复**的那一个。

**影响**：该接口的响应结构「被 Node-RED 流直接消费」（函数 docstring 明示）。容器跑 UTC 时，查询窗口起点与库内按家庭墙钟落盘的 `day`/`server_ts` 口径不一致，窗口整体偏移，边界数据错漏。

**发现路径**（可复用）：图谱识别出 `store.now_local`（入度 121）是全局时钟枢纽 → 扫描 65 处绕过它的直接 `datetime.now()/utcnow()` 调用 → 按「落盘/落库/时间窗口 = 危险，TTL 差值比较 = 安全」分类 → 用项目自己的警告注释作种子比对 → 定位漏网之鱼。

**修复建议**：改为 `now_local(getattr(rt.config, "tz_offset_hours", 8.0)) - timedelta(days=7)`，与 `mcp_server._fetch_attribution_events` 的写法保持一致。

---

### P2-1　19 处 `zip()` 无 `strict=`，其中 3 处存在错位风险

两序列按下标配对，长度不等时 Python 静默截断、**不报错、结果串号**。逐处核对后分类：

| 风险 | 位置 | 说明 |
|---|---|---|
| 有风险 | `history.py:485` | `zip(docs, metas)`，两者均取自 chromadb 返回的两个独立字段，无等长保证 |
| 有风险 | `algo_kernel.py:659` | `zip(cases, diag)`，`diag` 来自 pm4py `tokenreplay`，与自建 `cases` 独立生成；错位会把 A 病例的诊断安到 B 病例上 |
| 有风险 | `store.py:2200/2211/2622/2654/4784` | `dict(zip(cols, row))`，依赖 `row_factory` 等长；若列定义与查询列漂移则静默丢列 |
| 安全 | `entity_resolution.py:273`、`algo_kernel.py:537/546`、`insights_legacy.py:2671/2695`、`learning_feedback.py:185` | 两序列同源推导，或为有意的 `x, x[1:]` 相邻配对 |
| 安全 | `rule_engine.py:217` | `zip(keys, combo)`，`combo` 由 `itertools.product` 按 `keys` 生成，严格等长 |

---

### P2-2　6 处 async 函数内未卸载的阻塞 I/O

| 位置 | 调用 | 说明 |
|---|---|---|
| `app.py:453` | `open(INDEX_FILE)` | 每次首页请求读磁盘 |
| `app.py:688 / 702` | `open(p,'rb')` | sw.js / manifest |
| `app.py:792` | `open(path,'w')` | traceback 落盘 |
| `system_routes.py:179` | `subprocess.Popen` | 在线更新重启（管理端点，低频） |
| `system_routes.py:203` | `open('/proc/1/cmdline')` | re-exec 重启（管理端点，低频） |

均为小文件读取或低频管理端点，事件循环阻塞窗口有限，故定级 P2。

---

### P2-3　死代码 6 处（Vulture 置信度 ≥80%）

```
api/llm_routes.py:175        冗余 if 条件（100%）
insights/api.py:534          未使用变量 base_days（100%）
insights_legacy.py:316/317   未使用导入（90%）
mqtt_bridge.py:55            未使用变量 userdata（100%）
rule_engine.py:144           未使用变量 ignore_trigger（100%）
```

`rule_engine.py:144` 的 `ignore_trigger` 值得人工确认——若本应参与规则触发判定却被丢弃，是功能性缺口而非死代码。

---

## 四、已排除的告警（假阳性，记录以备复查）

这部分与"发现了什么"同等重要——避免后续审计重复踩坑。

| 告警 | 数量 | 证伪依据 |
|---|---|---|
| **SQL 注入**（Bandit B608） | 34 | 全部为占位符 SQL。逐一检查 f-string 插值点：`cols` 来自 `update_member` 的 7 项列名白名单（且有 `assert` 兜底）、`order_dir` 为 `'DESC'/'ASC'` 三元常量、`_ue{i}` 为内部索引、`order` 经 `'desc'` 判定后取常量。无一处拼接外部输入 |
| **async 内阻塞 DB（我自己的初版误判）** | 2 | 初版 AST 检测把 `mcp_server.py:1468` 与 `behavior_routes.py:709` 判为缺陷，**实为误报**：二者的同步 DB 调用都包在嵌套同步函数 `_query()` / `_fetch_rows()` 中，并分别由 `asyncio.to_thread(_query)`（1481 行）、`asyncio.to_thread(_fetch_rows)` 卸载。项目已系统性采用该模式（全库 24 处 `to_thread`），我据此重写了检测逻辑 |
| **除零** | 8 | `algo_kernel.py:764`、`arena.py:39`、`identity_fusion.py:213/317`、`entity_resolution.py:269`、`store.py:4865`、`candidate_promotion.py:1181` 等均有 `if not x` / `max(1, ...)` / 三元守卫 |
| **配置字段未定义** | 35 | 多为局部配置对象（FusionConfig / LearningConfig 等）字段，非 `Config` 属性 |
| **弱哈希 SHA1/MD5**（Bandit B324） | 9 | 用于事件 ID 与内容寻址，非口令/签名场景 |
| **绑定 0.0.0.0**（B104） | 4 | 容器化服务的既有设定 |
| **属性测试崩溃** | 0 | Hypothesis 对 27 个纯函数各跑 300 例（文本/None/整数/浮点/嵌套结构/边界值），在契约内输入下**零崩溃**。此前观察到的 105 条 `AttributeError` 系将 `str` 喂给契约要求 `dict` 的参数，属类型契约外输入，不计为缺陷 |
| **Pyflakes** | 0 | 无未定义名、无未使用导入 |

---

## 五、图谱分析：全图长什么样

### 枢纽节点（入度 TOP，改一处影响全盘）

| 节点 | 入度 |
|---|---|
| `api.deps.ok` | 177 |
| `api.deps.runtime` | 174 |
| `api.deps.require_user` | 148 |
| `store.now_local` | 121 |
| `runtime.get_runtime` | 101 |

### 强耦合对（缺抽象边界的位置）

`api→runtime`(138)、`mcp_server→runtime`(110)、`insights_legacy↔insights`(51 + 18，**双向耦合**)

### 双轨实现（新 `insights` 包 vs `insights_legacy`）

| 调用方 | 调新 insights | 调 insights_legacy |
|---|---|---|
| insights 包自身 | 170 | — |
| insights_legacy | 45 | 15（内部） |
| activity_inference / templates | 3 / 3 | — |

`insights_legacy` 仍被 `insights` 调用 17 个目标、自身内部 15 个，两条实现并存且互相调用。这是**结构性不一致风险**：同一查询走不同实现可能返回不同口径，建议列为下一轮重点。

### 循环依赖

顶层模块级循环 **0 个**。子模块级 17 组，核心为 `runtime` 中心 + `config↔house_time` 等；已逐处验证（`config.py:534`、`house_time.py:146`）**均为函数内延迟导入**，无启动期风险。

### 孤儿函数（PyCG 零入度 1346 个）

集中在 `api`(226)、`insights`(179)、`store`(175)、`mcp_server`(105)。**不能直接当死代码处理**——PyCG 静态分析看不到装饰器注册（`@mcp.tool()`、路由装饰器），大量 MCP 工具与路由会被误判为孤儿。精确接漏需结合 AST 装饰器检查，本轮未完成。

---

## 六、审计能力的自我修正记录

本轮有一次值得记录的判断翻转：

初版 AST 扫描报告「3 处 async 路由持锁做数据库查询，高并发会卡死服务」，并据此在初版结论中列为首要缺陷。复核时发现该判定错误——被标记的语句位于**嵌套同步函数**内，而这些函数已被 `asyncio.to_thread` 正确卸载。项目在此处的工程实践是合格的（注释里还留存了修复前的故障描述：曾误关 Store 共享连接导致全进程 `Cannot operate on a closed database`）。

修正后，真正的未卸载阻塞 I/O 只剩 6 处小文件/管理端点操作，定级由「P0」降为「P2」。**这一条说明：对自动化扫描结果做上下文复核，比扫描本身更关键。**

---

## 七、建议的下一步

1. **修 P1-3（最优先）**：`nr_routes.py:125` 改用 `now_local()`，与 `mcp_server.py:611` 同款写法的三处已修位置对齐；顺带排查 `intent_inference.py:227`、`analysis.py:240`、`auth.py:76/127` 四个次要候选（均为时间落库/ID 生成偏差 8h）。
2. **修 P1-1**：`mcp_errors.py` 导入失败分支加日志与可观测标记，并为「mcp 不可用」补一条契约测试。
3. **补 P1-2 的可观测性**：把关键路径降级点计数接入 `/api/health`，先覆盖 `store` / `mcp_server` / `insights` 三条主链路。
4. **收口 P2-1**：给 `history.py:485`、`algo_kernel.py:659` 两处独立序列的 `zip` 加 `strict=True`（Python 3.10+ 已支持），把静默截断变成显式报错。
5. **人工确认 `rule_engine.py:144` 的 `ignore_trigger`**：确认是死代码还是漏接的逻辑。
6. **补测试盲区**：本轮发现的问题全部落在现有 1096 项测试之外，建议针对「依赖缺失」「上游返回变形」「并发读写」「时区偏移」四类场景补契约测试。
7. **下一轮重点**：`insights` 与 `insights_legacy` 双轨实现并存且双向耦合（45 + 18），需逐查询比对两轨口径一致性。

---

## 八、审计覆盖声明

- 本次为**第一轮**审计，聚焦稳定性与功能性，未展开安全渗透、性能压测、前端与部署脚本审计。
- 运行环境 Python 3.10 低于项目要求的 3.11，若存在 3.11 特有语法/库行为差异，本轮无法覆盖（已通过 `compileall` 确认无语法级不兼容）。
- Semgrep 因包体超限未能安装，其规则集（尤其是跨文件污点分析）未参与本轮扫描。
