
## 四十五、门面丢键自查 #72：`agent_memory` 那一格、作废过一次的控制腿，以及"静态量具在委托形状前是瞎的"（2026-10-07，任务表 #72/#40/#58）

这一档不是审计点名的，是我在 §四十四 那张核销表收口后**自查**出来的：把 ToolSpec 承诺的输出格
逐格对着实现读。它顺带把三件工具自身的坑也量了出来，所以读数比缺陷本身更值得留档。

### 一、缺陷本体：`get_data_quality` 把 `agent_memory` 整格弄丢了

- **承诺侧**：`get_data_quality` 的 legacy 原形（`insights_legacy.py:3393-3396`）就是
  "聚合数据质量 **+ agent 记忆镜像缺口**"，读数以 `mirror_dirty` 那格兑现 ToolSpec 的许诺。
- **实现侧**：门面切到 `insights/` 包后，`get_data_quality` 只剩
  `return self.core.data_quality(tr)` —— 新引擎里根本没有 agent 记忆这一块，
  于是那一格**整块缺席**，而不是"值为空"。
- **为什么不红**：`_degrade` 把任何异常降级成空 `Page` 信封（P0-5 的藏匿机制），
  而"少一格"既不抛异常也不改总页数 ⇒ 全量测试照绿。**"查不到"和"没有"是两种读数**，
  这一版把它们压成了同一种。

修复（commit `8b11437`，`api.py` +19/-2）：新增 `_agent_memory_health()`（`insights/api.py:892`），
门面在 `self.core.data_quality(tr)` 之后合并那一格（`insights/api.py:910`）；
取不到时**显式**回 `{"ok": False, "error": "agent_memory 不可用"}`（沿用 legacy 的失败形状），
附属块自身炸掉也不许把整页打成降级。三条判据锁在
`tests/test_insights_facade_contract.py:215` 分节注释之后：
`:230` 带出该格、`:237` 不可用时必须是可见失败（`None` 与"会抛"两种都测）、
`:246` 附属块炸掉时整页不得被打空。

### 二、三腿变异档：形状对，但**容器第一跑是作废的**

`.qoder/tmp-c72-mut72.py`（`if __name__` 护栏、只读 `--verify`、每腿按字节还原 + sha 对账、
`MUT_ROOT` 指副本树 ⇒ 工作树全程零改动）：

| 腿 | 变异 | 本机 3.13 FAILED | 容器 3.11 FAILED |
| --- | --- | --- | --- |
| NOTHING | 什么都不改（控制腿） | `12 passed` | `12 passed` |
| L1 | 合并那行整格摘掉（= 改前形状） | 3 | 3 |
| L2 | 格还在但恒 `{}`（谎从"没有"变成"镜像永远干净"） | 3 | 3 |
| L3 | 摘掉附属块的 `except` 保护 | 2 | 2 |

**run19 的门 3 首跑读成 `NOTHING -> RC=1 |`，stdout 一个字都没有 ⇒ `CONTROL_BAD`，整档作废。**
根因**不在产品，在量具**：`_run_leg` 用 `PYTHONPATH=os.path.join(root, "src")`
**覆盖**了外层已有的 `$L/src:/tmp/pylibs`，把容器里 pytest 所在目录摘掉 ⇒ `python -m pytest` 起不来；
本机 3.13 的 pytest 是系统装的，所以**同一份腿本机绿、容器死**。
这是"本机绿 ≠ 容器绿"第 N 次实测，也是 `.qoder/tmp-c63-mut63.py` 那批为什么不设 PYTHONPATH 的原因
（它继承外层）。修法：`os.pathsep.join([root/src] + 继承的条目)`，并且 stdout 为空时**必须把 stderr 报出来**
（上一版只报 stdout，"作废"两个字没有原因可查）。
控制腿在改树之前就退出，所以树没被污染——这一条不靠推断：run20 的 SNAP 格重取聚合摘要，
与 run19 逐字相同（`b750ca63…a0cdbd`，400 文件）。

第二处量具坑（我自己在 driver20 上撞的）：`echo SYNC_RC=$?` **只打印不赋值**，
后面 `if [ "$SYNC_RC" != 0 ]` 在 `set -u` 下直接把脚本打回 —— 那一次预检全绿但**没出网**。
先赋值再打印才对。

### 三、运行时探针：静态 key-diff 在 `return self.core.x(tr)` 前面是瞎的

`.qoder/tmp-c72-keydiff.py`（AST，静态）给出 36 对方法、14 条"可疑"。
拿 `.qoder/tmp-c72-probe.py`（同库同种子真跑两侧，种子=5 类域 × 4 时段 × 6 天=120 事件）复核后，
**14 条里只有 #72 那一条是真丢键**，其余是改名或新增：

| 方法 | legacy 有、门面没有 | 判定 |
| --- | --- | --- |
| `get_data_quality` | `agent_memory`（改前） | **缺陷，本档已修**；改后 LOST 只剩 `data_quality_issues/days`（新引擎改名 `issues`/`total_events`）⇒ 非缺陷 |
| `plan_question` | `answer,device,matched_templates,ok,recommended_tool,room_scope,rooms_available,steps,suggested_args,window` | **另案**，见 §六 Q1 |
| `data_coverage` | `days_total,days_with_data,first_day_with_data,last_day_with_data,note,window` | 改名（`total_days/active_days`，`missing_days` 两代都有）；`window` 见 §六 Q2；first/last 属**文案漂移** |
| `get_user_persona` | `most_active_room,persona,summary,window_days` | 新引擎换发 `rhythm/traits/top_entities/window` ⇒ 形状重设计，无代码消费方 |
| `get_behavior_insights` | `climate_comparison,compare_days,comparison,current_window,previous_window,summary` | 同上；`window` 缺席记入 Q2 |
| `infer_activities` | `behavior_only,detector_report,signal_inventory` | `behavior_only` 降为入参，新侧改发 `excluded_entities/rule_sources` |
| `anomaly_report` | `checks` | **假号**：legacy 同名函数是 `(by_day, hourly, …)` 的纯格式化助手，签名不可比 |
| 其余 7 条（`climate_sessions`/`entity_catalog`/`search_events`/`query_behavior_events`/`define_activity`/`ask_memory`/`device_usage`） | — | 门面 `FWD_LEGACY` 转发 legacy，键由构造保持；`LOST=-` 实测证实 |

探针自己也有一格必须先钉住：**legacy 构造形参是 `InsightService(config, store)`，门面是
`InsightService(store, config)`，顺序相反**。首跑写反 ⇒ 满屏假丢键
（`AttributeError: 'Config' object has no attribute 'day_counts'`）。run20 的 ORDER 格把两行形状各钉一个 1。

### 四、容器权威档读数（run19 全档 + run20 补腿，HEAD `8b11437`）

| 格 | run19（06:06:21→06:18:02） | run20（06:23:29→06:24:33，只补门 3） |
| --- | --- | --- |
| TOOLCHAIN | `PYTEST=9.1.1 PYFLAKES=4.0.2 Python 3.11.16`、`TOOLCHAIN_RC=0` | 同值，`TOOLCHAIN_RC=0` |
| SNAP | `HASH_LISTED=400 HASH_FILES=400 HASH_MISSING=0`、`HASH_AGGREGATE=b750ca638fd1ec79f9054cce3b365c7ab05c71288ca30b3af45dda9df1a0cdbd`（本机=容器逐字同） | **同一摘要逐字复现** ⇒ 作废那腿没动过树 |
| ANCHOR | `MERGE_LINE=1 PRE_FIX_LEGS=0 FAIL_SHAPE=2 NEW_LOCKS=3`、`ANCHOR_RC=0` | `PATH_APPEND=1 OLD_CLOBBER=0 STDERR_SHOWN=1`、`FACADE_ORDER=1 LEGACY_ORDER=1` |
| 门 1 pyflakes | `GATE_RC=0`，当前 0 条 / 基线 0 条 | — |
| 门 2 SUITE | `SUITE_RC=0`，`1907 passed, 10 skipped in 485.90s` | — |
| 定向 1 / 定向 2 | `TARGETED_RC=0` 12 passed；`FACES_RC=0` 208 passed in 175.00s | — |
| 门 3 MUT72 | **`MUT72_RC=1` 控制腿空输出 ⇒ `CONTROL_BAD`，该格作废** | `MUT72_RC=0`；`MUT_COUNT=3 MUTATION_BAD=0`；restored=OK 全条 |
| POST 还原自证 | `POST_RC=0` 12 passed | `POST_RC=0` 12 passed |
| 出网前 | 四把尺全 `GUARD_OK`、`UNESCAPED=0`、`PREFLIGHT_OK=1`、`STAGE_RC=0`、`REMOTE_NAS_SYNTAX_RC=0` | 同套尺全 `GUARD_OK`、`DRIVER_RC=0` |

**合档口径**：`8b11437` 的容器权威证明 = run19（除门 3 的每一格）+ run20（门 3 重跑）。
run19 那档的门 3 **不许**被引用成"三腿在容器咬住了"，它当时的读数是作废。

### 五、本机那三条腿之外的补充自证

`.qoder/tmp-c72-mut72-local20.out`（修好 PYTHONPATH 后重跑）：`NOTHING 12 passed`、
L1 3 failed、L2 3 failed、L3 2 failed、`MUT_COUNT=3 MUTATION_BAD=0` —— 与修前
`.qoder/tmp-c72-mut72-local.out` 逐字相同 ⇒ 那次改动没把绿的腿改坏。

### 六、这一档新登记的待办（已分流：哪些我自办、哪些呈 DCD）

1. **`route_question` 的承诺键已不存在**（严重度高于 #72，因为它在**规划主路径**）：
   `tool_schema.py:67-86` 的 summary 说"返回执行计划——推荐工具、参数建议与步骤"、
   pitfall 说"调用后必须接着按计划调用 `recommended_tool`"，而实现
   （`insights/api.py:839-844` → `models.py:622-637` `QuestionPlan.to_dict()`）
   产出的是 `route/params/hints/time_range`，`recommended_tool/steps/suggested_args/answer/ok`
   **一个都不产出**。全仓（除 legacy）对这些键的引用只有那条 pitfall ⇒ 不会 KeyError，
   **坏的是注入进 prompt 的指令**。呈文：`关键决策部/inbox/20261007-MA-route_question承诺键与window回显范围-决策申请.md`。
2. **`window` 通用回显键：裁定点名的两格仍未落**——DCD
   `decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md:74`（Q-A=甲，点名 `coverage`/`data_quality`）
   之后，`insights/service.py:430-437` 与 `service.py:1274-1281` 仍是 `return out`，
   没走同侧 `usage`（`service.py:483-490`）那条 `_with_window(out, tr)`。
   **这属已裁未落，我自办**（任务表 #73），不等回复。
3. **回溯范围之问**：`compare_insights`（`get_behavior_insights`）与 `plan_question` 也没有 `window`，
   但它们不在 Q-A 点名的两格里 ⇒ 是否回溯，写进同一呈文的 Q2。
4. **`get_data_coverage` 的文档漂移**：`mcp_server.py:1619-1622` 仍写"返回每天的 events 量与 **has_data** 标记、first/last 有数据的日期"，
   而新引擎的日行是 `{day, events, active_hours, hours, empty}`（`service.py:461-462`）——
   `has_data`、`first/last` 三格都不存在，只有语义相反的 `empty`。
   随 Q1 的门裁结果一并处置。

### 七、下一个人该记住的三件事

1. **控制腿空输出 ≠ 腿红**。`RC=1` 且 stdout 一个字都没有，八成是那条腿**根本没跑起来**
   （解释器、PATH、依赖目录）。所以变异档必须把 stderr 也报出来，否则"作废"两个字不带原因，
   下一个人只能重跑猜。
2. **`echo X=$?` 不赋值**；在 `set -u` 的预检脚本里后面再读 `$X` 会直接把脚本打死——
   这次它救了我（没出网），下次未必这么客气。要么 `X=$?; echo X=$X`，要么别读。
3. **静态 diff 只能出候选清单**。委托形状（`return self.core.x(tr)`）对静态量具是黑的，
   14 条可疑里 13 条是改名/新增/签名不可比；判定要换成"同库同种子把两侧都真调用一遍"。
   对照档两侧构造形参顺序**相反**，写反了会满屏假号。
