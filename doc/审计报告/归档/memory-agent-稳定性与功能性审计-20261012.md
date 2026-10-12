# memory-agent 稳定性/功能性审计报告（2026-10-12）

- **审计平台**：ADM-auditkit（本机 `E:/NAS/ADM-auditkit`，30 条规则 active / broken=0，selftest 21 分析器全绿）
- **审计对象**：`E:/NAS/memory-agent`（源码态，非 NAS 部署副本）
- **轮次**：`round-003`（mode=full；round-001/002 为 2026-10-12 前序产物，未覆盖）
- **方法**：R0 手猎（假设驱动，先读代码后看命中）→ R1 基线轮（924 条机器命中）→ 逐条回读确证 → triage 落盘
- **重点专题**（按委托）：数据库层稳定性、容器崩溃与 NAS 影响链
- **原始产物**：`E:/NAS/ADM-auditkit/projects/memory-agent/round-003/`、`bugs.json`、`triage.json`

---

## 〇、结论速览

| 类别 | 数量 | 说明 |
|---|---|---|
| 确证缺陷（进台账） | **4 条站点 / 2 个族** | D1 导入期 env 裸转换（3 站点）、D2 固定名 tmp 并发交错（1 站点） |
| 结构性风险（人工确认，非规则命中） | **1 项** | D3：health 无总超时 × 全局 RLock 无超时 × VACUUM 持锁 |
| 机器候选未升级 | 924 条 | 已裁 5 族假阳性，其余多为低危静默异常（见 §3） |
| 工具漏报（应写新规则） | 2 条 | health gather 无超时、导入期 env 裸转换 |
| 已验证「已修」 | 4 项 | 避免下轮重复报（见 §5） |

**一句话**：数据库核心层（WAL/FULL/RLock/transaction/checkpoint 超时跳过/分批 DELETE/VACUUM 阈值/保留策略）已被历史审计加固得相当充分；本轮真正的风险不在 SQL 本身，而在**「一个手滑的环境变量能让容器起不来」**和**「health 可被慢查询拖到数十秒」**这两条崩溃链上。

---

## 一、确证缺陷详述

### D1【medium-high】模块导入期裸 `int(os.getenv(...))` —— 启动崩溃 + 护栏可被负值打穿

**站点（6 处，同族同形）**：

| 文件:行 | 变量 | 护栏语义 |
|---|---|---|
| `src/memory_agent/store.py:52` | `MA_VOICE_CACHE_MAX` | 语音问答缓存软上限（审计 S8） |
| `src/memory_agent/store.py:55` | `MA_FEEDBACK_TEXT_MAX` | 反馈文本截断上限（DCD R1） |
| `src/memory_agent/rule_lifecycle.py:72,74` | `MA_RULE_MIN_EVIDENCE` / `MA_RULE_DRY_RUN_DAYS` | 规则晋升红线 1/2 |
| `src/memory_agent/mcp_server.py:289` | `MCP_MAX_STATS_ENTRIES` | MCP 调用统计上限（审计 M1） |

**两条独立失效路径**：

1. **启动崩溃**：这些赋值在**模块导入期**执行。环境变量写成非数字（`MA_VOICE_CACHE_MAX=2000条`、误留引号、空串）→ `int()` 抛 `ValueError` → `import memory_agent.store` 失败 → uvicorn 起不来 → **容器启动失败循环**。按开发规范 §0，容器崩溃→重建 churn 是触发飞牛OS trim 模块死锁、NAS 整机冻结的直接路径——这正是本次委托重点关注的链路。
2. **护栏静默失效**：传 `0` 或负值不报错但语义反转——`MCP_MAX_STATS_ENTRIES=0` 使 M1 的「防进程级无限增长」修复整体关闭；`MA_VOICE_CACHE_MAX=-1` 使 S8 软上限形同虚设。与 AutoForge R9-01/02「负数 ⇒ 护栏静默失效」同族。

**修复建议**：抽 `core.env_int(name, default, lo=0)`（或 `config.py` 统一收口），导入期调用带 `try/except` + 钳位；越界记 warning 并回落默认值，**不让坏配置杀死进程**。

### D2【low-medium】更新标记用固定名 `.tmp` + `os.replace` —— 并发提交内容交错

- 位置：`src/memory_agent/api/system_routes.py:149-152`
- 形态：`tmp_path = MARKER_PATH + ".tmp"` → 写 → `os.replace(tmp_path, MARKER_PATH)`
- 竞态：两名管理员并发点「检查更新」时写同一 `.tmp`，两次 `json.dump` 交错 → marker 的 `ref`/`requested_by` 可能来自不同请求 → 宿主脚本按错误 ref 执行 `git pull + 重启`
- 同族证据：AutoForge `af_api.py:1155` 已确证完全同形的缺陷（固定名 tmp 并发交错，medium）
- 修复建议：`tmp_path = MARKER_PATH + ".tmp." + uuid4().hex[:8]`， rename 前 `os.fsync`

### D3【medium·结构性】health 无总超时 × 全局 RLock 无超时 × VACUUM 持锁

三条叠加构成「/health 被拖住 → 存活探针误判 → 容器被杀 → churn」的完整链：

1. `runtime.py:1048` `asyncio.gather(...)` **没有 `asyncio.wait_for` 总超时**，7 路探测（HA/LLM/store/chroma/NR/ha_db/embedding）最慢的一个决定 health 耗时。
2. `store.py` 的 `_db()` / `transaction()` 用 `with self._lock`（`threading.RLock`）**无获取超时**——与 checkpoint 的 `acquire(timeout=5.0)`（M9 修复）口径不一致。
3. `store.py:4766-4767` 保留期清理的 `VACUUM` **持同一把全局锁**执行（`with self._lock: conn.execute("VACUUM")`）；purge 由 `_run_retention_cleanup` 经 `asyncio.to_thread` 跑，与 health 的 `store.stats()` 争同一把锁。
4. 容器实测：`chromadb 0.5.23` 的 `chroma_query_request_timeout_seconds` 默认 **60s**（sysdb/logservice 3s）——chroma 慢时 health 的 `chroma_status` 一路可耗 60s。

**后果**：大库（events 百万行级）上 VACUUM 数秒~数十秒，或 chroma 慢查询 60s，都会让 `/health` 挂住；外部存活探针（或 watcher）超时即杀容器重启 → 正是开发规范 §0 记载的 churn→冻结链。

**修复建议**：health 的 gather 包 `asyncio.wait_for(..., timeout=5)`，慢于阈值的子系统降级为 `{"connected": False, "timeout": true}`；`VACUUM` 移出业务锁（用独立连接 + `PRAGMA busy_timeout` 后直接执行，VACUUM 本身不依赖 `_lock` 保护的正确性——它只需要独占数据库）；`_db()` 增加获取超时并记录 warning。

---

## 二、机器命中分布（round-003）

924 条 = high 65 / medium 171 / low 688。按规则：

| 规则 | 命中 | 判读 |
|---|---|---|
| `py.silent_except_no_trace` | 493 | 最大族（与三期「失败被当成没问题」族吻合）；绝大多数 low，逐条人审不现实，建议抽样式 |
| `py.except_broad_swallow` | 186 | 同上 |
| `GEN-06-log-sensitive` | 54 | 日志打印敏感字段（token/密钥掩码缺失）——**待抽样式，中危** |
| `py.sql_limit_placeholder` | 53 | 全部为参数化 `LIMIT ?` + `clamp_limit` 正确用法 → 假阳性（规则 README 已声明局限） |
| `py.async_def_sync_block` | 31（db/runtime 内 25） | **全部为 `asyncio.sleep`/`print`，非阻塞** → 假阳性 |
| `py.periodic_claim_no_loop` | 16 | 声称周期执行但调用点不在循环内——**待抽样式** |
| `GEN-01-hardcoded-secret` | 12 | 全部落在 `tests/` 与 `debug_token.py`（测试/调试令牌）→ benign |
| `py.unbounded_container` | 8 | 2 处假阳性（见 §3），其余待核 |
| `GEN-02-sql-concat` | 8（生产 4） | 生产 4 处经回读为「内部片段拼接 + 用户值全参数化」→ 假阳性 |

数据库/运行时相关文件命中 169 条：`store.py` 115、`mcp_server.py` 35、`runtime.py` 30、`candidate_promotion` 22、`backup.py` 10、`agent_memory.py` 7、`history.py` 7、`poller.py` 6。

---

## 三、假阳性裁决（已落盘 triage.json，下轮不再重报）

| 规则 | 位置 | 理由 |
|---|---|---|
| `py.async_def_sync_block` | `runtime.py:161`（代表 25 条） | 命中形态是 `await asyncio.sleep(...)`，非阻塞原语；print 到 stdout 亦非实质阻塞 |
| `GEN-02-sql-concat` | `insights/repository.py:393`（代表 4 条） | 拼接的是代码内部片段（小时枚举、where 片段），用户值全走占位符；规则缺数据流判据 |
| `py.unbounded_container` | `identity.py:288` | `_needs_review` 在 `reconcile()` 开头（identity.py:428）每轮重置 |
| `py.unbounded_container` | `entity_resolution.py:163` | `self.priors` 是固定配置 dict 拷贝 |
| `py.sql_limit_placeholder` | `store.py:1776`（代表 32 条） | 参数化 + `clamp_limit(limit,50,500)` 已钳制，是正确用法 |

**回灌平台**：以上 5 族正是「经验/跨项目审计经验包-2026-10-12」收敛建议 01/04 指向的问题（async_def_sync_block 识别 await 形态、GEN-01 折叠测试路径）。建议按该包实施规则收敛。

---

## 四、工具漏报（本轮发现规则看不到的两个真缺陷类）

1. **`py.health_gather_no_timeout`（草案）**：`asyncio.gather(...)` 用于 health/存活判据却无 `asyncio.wait_for` 总超时。本项目 `runtime.py:1048` 命中；判据：gather 的结果被返回给 health/ready 探针且所在函数无 wait_for。
2. **`py.import_time_env_int`（草案）**：模块导入期裸 `int(os.getenv(...))`。本项目 6 处命中；判据：Assign 在模块级（非函数内）+ value 是 `int(os.getenv/os.environ.get)` 调用。一条规则同时覆盖「坏配置杀死进程」与「负值打穿护栏」两个后果。

两条已作为规则草案列入平台待办（本次未实现，实现需按 `RULE_AUTHORING_GUIDE` 走 dirty/clean 自检）。

---

## 五、已验证「已修」的项（避免下轮重复排查）

| 项 | 证据 |
|---|---|
| SQLite 连接参数（WAL / synchronous=FULL / foreign_keys / timeout=30 / check_same_thread=False） | `store.py:652-658` |
| 单连接 + RLock + transaction 统一入口（execute/commit 绑定） | `store.py:635,692-709` |
| checkpoint 加锁超时 5s 跳过（M9） | `store.py:669-672` |
| 周期任务全部经 `asyncio.to_thread`，不阻塞事件循环 | `runtime.py` 全部 `_periodic_*` |
| 保留期清理分批 DELETE + 删除量阈值才 VACUUM（P0-8） | `store.py:4740-4768` |
| 启动期 DB 检查挪后台，lifespan 立即返回（DCD Q3） | `runtime.py:168-190` |
| API limit 钳制口径统一（`deps._num`，MA-07/08） | `api/deps.py:171-183` |
| MCP 侧分页信封 + 上限（裁4） | `mcp_server.py:495-560,1028-1062` |
| chroma/embedding 超时（httpx 30s；chroma 默认 60s） | `history.py:78`；容器实测 chromadb 0.5.23 |
| 备份 VACUUM INTO 先写临时文件再原子替换（MA40b） | `backup.py:57-71` |

---

## 六、工作流验证结论（本次委托的一部分）

| 验证点 | 结果 |
|---|---|
| selftest 门禁 | 通过（21 分析器 ok，0 命中无，clean 无回归）——无静默失效 |
| 规则加载 | 30 条 active / broken=0 |
| 扫描范围 | 409 py / 111,225 行，`.qoder/` 副本已被 `DEFAULT_SKIP_DIRS` 排除（对比：未修前 memory-agent 93% 命中来自 `.qoder/tmp-*`） |
| 定位精度 | 命中路径全部指向 `src/` 真源码 |
| R0→R1→triage 闭环 | 成立：手猎假设 H1（周期任务阻塞循环）被证伪、H-A（chroma 无超时）部分证伪（有 60s 默认）、D1/D2/D3 为手猎+回读产出，其中 D3 完全在规则射程外（工具漏报） |
| 已知平台缺陷仍在 | P3（`static_ast_mode.py:111` BOM 未修，9 项目 93 文件零覆盖——**本轮 memory-agent 亦受其影响**）、P4（targeted 不过滤 generic_text） |

**方法论收获**：本轮 3 条确证缺陷里，2 条（D1 部分形态、D3）**没有任何规则能报**——分别是「导入期副作用」和「无超时的 gather」这类跨语句/结构性形态。这再次验证平台哲学：规则库只能复印已知模式，崩溃链类缺陷必须靠手猎假设驱动。

---

## 七、修复优先级建议

| 优先级 | 项 | 工作量 |
|---|---|---|
| P0 | D1：抽 `env_int()` 收口 6 处导入期裸转换 | 小（<1h），消除「一个 env 手滑 → 容器崩溃循环 → NAS 冻结风险」 |
| P1 | D3：health 加 `wait_for(5s)`；VACUUM 移出业务锁 | 中（需回归 health 行为） |
| P2 | D2：marker tmp 改随机后缀 | 极小 |
| P2 | 按经验包收敛 `async_def_sync_block` / `GEN-01` / `sql_limit_placeholder` 假阳性（本次 5 族、数百条噪音的来源） | 中 |
| P3 | 实现 §4 两条新规则草案 | 中 |

---

## 八、局限性声明

1. 本轮为静态分析 + 代码回读，**未做运行时 PoC**（未在容器内构造坏 env 实测启动失败、未构造并发 marker 写入）。D1 的崩溃路径由代码结构直接推证（导入期 `int()` 语义确定），D2/D3 为竞态/时序推理，标注【候选·已回读】。
2. `silent_except` 493 条未逐条确证，仅抽样；`GEN-06-log-sensitive` 54 条未展开。
3. 平台 P3 缺陷意味着本轮扫描可能漏掉带 BOM 的文件；memory-agent 主源码未发现 BOM 文件（画像与命中路径正常），影响估计有限。
4. 部署副本（NAS 容器内 `/app`）与源码态的差异未核对；本报告结论仅针对 `E:/NAS/memory-agent` 源码态。

---

## 九、修复状态更新（2026-10-12 当日闭环）

审计进行期间，D1 与 D3 已在源码侧落地修复（本轮复读确认）：

| 缺陷 | 修复证据 | 判定 |
|---|---|---|
| **D1** 导入期裸 `int(os.getenv)` | 新增 `src/memory_agent/env_cfg.py`：`env_int(name, default, lo, hi)` / `env_float`，畸形与越界值**回落默认值而不崩进程**；`store.py:54/57/61`、`rule_lifecycle.py`、`mcp_server.py` 共 6 处全部改接 | ✅ 已修（修法与本报告建议一致） |
| **D3** health 无总超时 | `runtime.py:health()` 已出现 `asyncio.wait_for`（本轮 AST 复核确认 gather×1 + wait_for×1） | ✅ 已修（VACUUM 持锁与 RLock 无超时两项待确认） |
| **D2** 固定名 tmp | 未修（仍为 `MARKER_PATH + ".tmp"`） | ⬜ 待修 |

同时新增两条**回归护栏规则**（0 命中=修复生效，见平台提案包）：
`py.import_time_env_int`（本轮在 MA 上 0 命中——D1 修复的直接证据）、
`py.health_gather_no_timeout`（命中 `vision_service.py:1344` 一处**同类遗漏**，
即 vision_service 里另一个 health 类函数仍无 wait_for——建议顺带修）。

### 规则收敛实证（round-003 → round-005 同目标对比）

| 规则 | 收敛前 | 收敛后 | 说明 |
|---|---|---|---|
| `py.sql_limit_placeholder` | 53 | **12** | v1 正则只认形状（100% 假阳性）；v2 函数级判据（绑定裸形参且未经 clamp）消 52 条，保留 11 条真候选 |
| `py.unbounded_container` | 8 | **6** | 新增「非 `__init__` 方法里的 `self.X = []` 赋值重置」识别，消 identity.py 等 2 条误报 |
| `GEN-02-sql-concat` | 8 | **4** | 行首 `(?!.*\?)` 负向预查 + 内部片段 join 白名单，消掉 4 条「拼接内部条件片段、用户值全参数化」的误报 |

> 注：三条收敛各踩了一个坑并已修——`^(?!.*\?)` 把 select 锚到行首致 dirty 0 命中；
> `\{\}` 撞 `str.format` 占位符语法抛 KeyError；`self.X = []` 在 `__init__` 是初始化、
> 在业务方法才是重置（正则分不开，改用 AST 按所在函数判定）。三例均已记入规则注释。

---

## 十、被审计方核实补记（2026-10-12 收口，HEAD `fdbadda`）

三件的处置与两条**与本报告不符的现读**，完整证据见 `修复与核实/审计核实与修复_20261001.md` §五十九：

| 条目 | 本报告口径 | 现读 | 处置 |
|---|---|---|---|
| D1 | §九:171「共 6 处」 | **9 处**（`store.py:54/57/61`、`runtime.py:61`、`rule_lifecycle.py:75/77`、`mcp_server.py:285/286/290`） | 已修；第六把门 `scripts/scan_import_time_env_cast.py` 真树 `SITES=0 PROBLEM=0`，变异腿 D01 判红 |
| D2 | §九:173 ⬜ 待修 | **已修**（`5fc11d0`：`api/system_routes.py:158` 随机后缀 + `fsync` 先于 `os.replace` + 异常清理） | 腿 S01 判红；另有一条两趟并发实测用例 |
| D3 health 无总超时 | 已修 | 已修并加固（`runtime.py:1057` 每路 `asyncio.wait_for`） | 腿 H01 判红 |
| D3 RLock 无超时 | 待确认 | **已修**为「超阈值记 warning、不改等待语义」（`store.py:718/725`） | 腿 C01 判红；不改判失败的理由同下一条 |
| D3 VACUUM 持锁 | 待确认 | **不采纳"移出业务锁"**：`connect()` 复用单连接，独立连接 VACUUM 会把 health 卡顿换成写路径 `SQLITE_BUSY` | 由 `test_vacuum_still_runs_under_the_business_lock` 守住（AST 认 VACUUM 站点必须落在 `with ..._lock` 子树内）；腿 V01 判红 |
| §九:177 `vision_service.py:1344` | 「另一个 health 类函数」，建议顺带修 | **不是 health 链**：全仓 `asyncio.gather` 3 处（`runtime.py:1054` 已限时 / `task_registry.py:59` 关停语义 / `vision_service.py:1344`）；后者只挂 `/api/vision/status`（`api/vision_routes.py:49`、`:411`），`_room_status` 走 `to_thread` + `ha_client.py:96-101` 自带 `timeout=10` | 不采纳：套总闸只会把巡检状态读成假超时 |

方法论回执（对本仓规则沉淀有用的一条）：本轮 12 条变异腿首轮 `killed=9 survived=3`，三条存活腿暴露的不是实现缺陷，
而是**替身形状单一**——既有 presence 替身一律带 `version` 形参，于是契约 §7.1 的"判旧分支"从头到尾空跑。
补了两条用例后三条全改判 KILLED。⇒ 建议平台侧增一条通用护栏方向：凡"按对端/依赖版本分支"的实现，
用例面必须同时给出**两种形状的替身**，否则该分支的覆盖声明应记 `UNVERIFIED`。

---

审计署名：ADM 审计 Agent（CodeBuddy 审计席）
轮次产物：`projects/memory-agent/round-003/`（findings 924）、`round-004/005`（收敛对比）
裁决落盘：`projects/memory-agent/triage.json`（accept 4 / reject 5）
规则沉淀：平台提案包 `经验/提案包_MA-round003_规则收敛与回归护栏_20261012/`
