
---

## 十八、一个能力三张表各写各的——35 个工具没进目录、3 个工具调不动、超限响应把 JSON 腰斩（2026-10-03）

### 18.1 这条从哪来

第一轮审计 P0-4/P0-2 留的余债：`mcp_server` 里有 **35 个手写工具只有实现、没有 `ToolSpec`**。
本轮按计划卡第 4 步补登记时，实测出两件比"少报"更硬的：

1. **自我日记三件套根本调不动**：`read_self_diary` / `write_self_diary` / `generate_self_diary`
   只在 wire 上注册，没进 `mcp_scopes` 的准入表——`list_tools()` 看得见，一发调用必吃
   `NOT_FOUND: 工具 '…' 未登记到 MCP 工具表`（P0-3 的 fail-closed 分支被自己踩中）。
2. **超限响应把 JSON 切在半行上**：`list_device_health` 在生产上原始输出约 650KB > 上限 512KB，
   `_apply_response_cap` 做的是**字符截断**，调用方 `json.loads` 直接报错。这条是补登记时
   顺手真跑工具才暴露的——只查目录永远看不见。

### 18.2 根因：同一个能力，三处登记

| 面 | 位置 | 谁在消费 | 漏登记的对外后果 |
|---|---|---|---|
| ① wire | `mcp_server._build_server()` 里 85 个 `@mcp.tool()` 闭包 + `register_simple_tools` 动态注册 5 个 `generated=True` ⇒ **实测 90** | 客户端 `tools/list` | — |
| ② 目录 | `tool_schema.TOOL_SPECS` / `SPEC_BY_NAME` / `TOOL_NAMES`（本轮前 `expose∋mcp` = **55**） | `build_catalog()`（覆盖 `mcp_server.py:3134` 那份手抄 `TOOL_CATALOG`）、`runtime.py:455` presence 的 `caps.tools`、`help`/`describe` | 能力**少报**、`describe` 查不到、参数说明与真签名脱节 |
| ③ 准入 | `mcp_scopes.REGISTERED_TOOLS ∪ WRITE_TOOLS` | `_tracked_call_tool` 分发层 | `scope_of()==unknown` ⇒ **NOT_FOUND**，工具"看得见却调不动" |

三张表谁也不校验谁，所以修法的重点不是补这 35 个名字，而是**把"三面必须相等"变成一条会判红的锁**。

### 18.3 修复前红证（容器内对 `de9f9bd` 源码跑本轮新锁）

`8 failed, 4 passed`，`RED_RC=1`。四条关键断言原文：

```
AssertionError: MCP 面上这些工具没有 ToolSpec 登记（caps 会少报）：
  ['advance_rule_to_live', 'assign_member_device', 'assign_member_room', 'audit_rule_recall',
   'confirm_candidate_rule', 'confirm_member_tag', 'counterfactual_query', 'create_member',
   'delete_member', 'execute_intent_actions', 'flag_rule_false_positive', 'generate_self_diary',
   'get_behavior_drift', 'get_behavior_prediction', 'infer_behavior_intent',
   'list_behavior_anomalies', 'list_behavior_drifts', 'list_bug_reports', 'list_candidate_rules',
   'list_device_health', 'list_members', 'list_rule_channel', 'list_rule_lifecycle_audit',
   'mine_behavior_process', 'promote_candidate_rule', 'query_device_usage', 'read_self_diary',
   'refresh_behavior_anomalies', 'refresh_behavior_drift', 'refresh_rule_recall_gaps',
   'report_bug', 'review_behavior_anomaly', 'revoke_active_rule', 'revoke_signal_rule',
   'write_self_diary']                       ← 35 个，与"90 − 55"逐名吻合

AssertionError: 这些 wire 工具未登记 scope，任何令牌调用都吃 NOT_FOUND：
  ['generate_self_diary', 'read_self_diary', 'write_self_diary']
AssertionError: write_self_diary 在准入表里查不到 scope
  assert 'unknown' != 'unknown'   +  where 'unknown' = scope_of('write_self_diary')
AssertionError: ToolSpec 参数与 MCP 函数签名不一致：
  query_unified_events: spec=[person, room, start, end, source, limit, order]
                        wire=[person, room, start, end, days, source, limit, offset]
  teach_signal:         spec=[…, exclusion_type, session_id]
                        wire=[…, exclusion_type, dry_run, session_id]
```

剩下 4 条在旧码上是绿的，因为它们钉的是"别多写"而不是"要写全"（幽灵 spec、手写/generated
交叉、非 generated 找不到实现、`builtin not in expose`）——把这几条算成 bug 证据就是虚报。

### 18.4 修复（`e42d898` + `feb0f6f` + `469b659`）

| 动作 | 落点 |
|---|---|
| 补 35 条 `ToolSpec` | `tool_schema.py`，全部 `service="static", method="", generated=False, expose=("mcp",)`——`validate_specs()` 在 import 期就校验"非 static 必须有 method / generated 必须有派发目标"，静态条目是唯一既能进目录又不伪造派发路径的形状 |
| 补准入登记 | `mcp_scopes.py`：`read_self_diary` → `REGISTERED_TOOLS`（只读）；`write_self_diary`/`generate_self_diary` → `WRITE_TOOLS`（写 `agent_memory` staging，`auto_promote_blocked=1`） |
| 修 `assign_member_device` 假成功 | `mcp_server.py:1597`：`set_member_devices` 是 DELETE+INSERT 且无外键拦着，成员不存在时照样落孤儿行并回 `ok=True`；改为与 `assign_member_room` 同口径先验存在性，回 `NOT_FOUND: 成员 … 不存在` |
| 修两处目录↔实现漂移 | `query_unified_events` 的 `order` 是照 Store 方法抄出来的**假参数**（MCP 面没有，真参数是 `days/offset`）；`teach_signal` 缺真参数 `dry_run` |
| 超限响应改结构降级 | `_apply_response_cap` 前置 `_json_shrink_to_fit`：按字节挑最大列表逐半裁，直到连 `_truncated` 摘要一起装得下；非 JSON / 无列表 / 单条即超限时退回字符截断；`is_error` 两条路径都保留 |

### 18.5 回归锁与变异红证

`tests/test_mcp_surface_parity.py` **13 条**（AST 直读源码，本机与容器同构）：

| 锁 | 判据 |
|---|---|
| 读数器自锁 | `len(WIRE)>50` + `len(WIRE_DEFS)==len(WIRE)` + 6 个已知工具名在场（读数器坏了会让下面几条"空集=通过"） |
| 少报锁 | `WIRE − SPEC_MCP` 必须为空 |
| 幽灵 spec 锁 | `SPEC_MCP − WIRE − GENERATED` 必须为空 |
| 手写/generated 交叉锁 | 交集为空（P0-4 的运行时形状）；且非 generated 的 spec 必须能在 wire 上找到实现 |
| 同名重复定义锁 | 按 **def 计数**判同名多次（见 18.8-3） |
| 准入锁（双向） | wire ⊆ `REGISTERED_TOOLS ∪ WRITE_TOOLS`，且准入表 − wire − spec 的幽灵名必须为空 |
| scope 解析锁 | 每个 wire 工具 `scope_of()!=unknown` 且对 read+write 令牌可达 |
| 参数对齐锁 | 逐工具比较 `spec.params` 与函数签名（**含顺序**） |
| 日记三件套 | 读工具 scope=read；写工具 `not requires(name,[READ])` 且 `requires(name,[READ,WRITE])` |
| 行为锁 ×2 | 真取 `_tool_manager._tools["assign_member_device"].fn`，临时库 `init_schema()` 后跑：不存在成员 → `ok=False`+`NOT_FOUND`+`member_devices` **0 行孤儿**；真实成员 → 仍写入（反例锁，防守卫过头） |

`tests/test_mcp_contract.py` 新增 **5 条**上限锁（见 18.4 末行）。

| 红证 / 变异 | 结果 |
|---|---|
| 对 `de9f9bd` 源码跑 13 条 parity | `8 failed, 4 passed`（`RED_RC=1`） |
| 对修复前源码跑 5 条 cap（同目录旧 `mcp_server`） | `3 failed, 2 passed`——绿的 2 条钉的是"无列表退回字符截断""非 JSON 走旧路径"，即**旧行为保持**，不该算 bug 证据 |
| M1 变异：在 `_build_server()` 内再补一份同名 `report_bug` 的 `@mcp.tool()` 定义 | 判红 2 条：`test_no_tool_name_is_defined_twice_on_the_wire` + 读数器自锁的 `len(WIRE_DEFS)==len(WIRE)`。⚠️ 同一跑里另外 2 条行为用例也红了，那是变异体的**放置副作用**（插入位置在 `mcp` 绑定之前，模块 import 就炸），不是判据证据，故不计入 |

### 18.6 读数

| 项 | 读数 |
|---|---|
| 本机全量 | `1051 passed, 25 skipped in 158.28s` |
| **容器权威全量**（HEAD `469b659`，`git archive` 快照 `/tmp/vs28`，`GATES_REQUIRE=1`） | `1064 passed, 12 skipped in 186.53s`，**`SUITE_RC=0`** |
| 12 条 skip 归因 | 9 条可选算法依赖（hmmlearn/pm4py/river，按路线图不装）+ 3 条 `test_signal_learning.py:173/216/257` P2-5 待适配（等 DCD Q1/Q3，不借改名降级断言） |
| pyflakes | `[gates] pyflakes: 当前 0 条，基线 0 条，新增 0，已修 0` / `GATE_EXIT=0`；本批改动的 4 个文件单跑 `PYFLAKES_RC=0`（退出码从 `docker exec` 侧取，不经管道） |
| 契约门禁（部署前硬闸） | `42 passed in 6.14s` → `[deploy] 契约门禁绿（tests/contract 对刚同步的 /app/src）` → 才 `docker restart`（两次部署各一次） |
| 部署 | `bash scripts/deploy_nas.sh --full` ×2；第二次先被 `工作区有未提交的可部署改动` 拦下（`deploy` 只看 HEAD，不带上未提交改动）→ 提交后放行 |
| 中途一次读数作废 | 首跑 `/tmp/vs27` 权威全量曾报 `2 failed`——是我新写的行为用例缺 `Store.init_schema()`（`sqlite3.OperationalError: no such table: members`）。补 init 后 `13 passed`，作废该读数、按新快照重跑 |

### 18.7 生产验收（同一探针、同一容器、只读）

| 判据 | 修复前 | 修复后（HEAD `469b659`） |
|---|---|---|
| `list_tools()` 实际面 | 90 | **90** |
| `TOOL_NAMES(expose∋mcp)` = presence `caps.tools` | 55（少报 35） | **90** |
| `scope_of('read_self_diary')` | `unknown` → 调用吃 NOT_FOUND | **`read`** |
| `scope_of('write/generate_self_diary')` | `unknown` | **`write`** |
| wire 上 scope=unknown 的工具 | 3 | **`[]`** |
| 准入表里的幽灵名 | — | **`[]`** |
| `TOOL_ALREADY_EXISTS`（import stderr） | 曾 5 条 | **0 条** |
| 真跑只读工具 | `read_self_diary` 不可达 | `read_self_diary isError=False {'ok': True, 'count': 1}` / `list_members ok total=3` / `list_behavior_anomalies ok count=0` / `query_unified_events total=163181 count=200 limit=200` / `list_device_health` 合法 JSON `health_rows=888 logical_devices=1267` |
| 上限触发实验（`cap=524288`，4,000 行合成载荷） | 输出不可解析 | `1,026,970 → 513,389` 字节，`json.loads` 通过，留 2,000 丢 2,000，`_truncated.fields={'health':{'kept':2000,'dropped':2000}}`，标量字段 `ok/state/total/logical_devices` 全在，行字段形状不变 |

### 18.8 顺带查出来的三件（登记，不静默修）

1. **`list_device_health` 的体积主因**：`health` 888 行 = 278,712 字节，按字段合计
   `stable_id` 51,062 / `entity_id` 43,897 / `note` 36,741 字节，最大单行 554 字节。
   `stable_id` 把 HA 的整段中文设备说明直接带进载荷（样例含"…按照bit从低到高位1水草灯时间过长
   2水泵故障…"），1 汉字 = 3 字节。当前 888 行距 512KB 上限只有约 1.9 倍余量，
   而它**既无 limit/offset 也无时间窗**——本批保证"必可解析"，但"默认投影该给什么字段、
   要不要分页"是对外形状，已提 DCD（18.9）。
2. **`git archive` 快照会让 5 条基准用例崩在 setup**：`benchmarks/data/openshs_sample.csv`
   是 gitignore 的生成物，快照里没有；fixture 自称"全新 clone 也能直接 pytest"，
   但在容器内该目录对运行用户不可写时报 `PermissionError`（`5 errors`），
   不是可读的 skip。**这是取证配方问题，不是产品缺陷**，但 fixture 的自述与实际不符，登记待修。
3. **P0-4 的重复注册确实已经不在了，但我原来的锁看不见它**：实测 `_wire_defs_raw()`
   读到 **85 条 def / 85 个唯一名**，import stderr 里 `Tool already exists` **0 条**
   （该重复在 `dffdb64`「fix(audit): 审计报告 P0/P1 修复」那批清掉）。
   原 `_wire_functions()` 用"名字→参数"的 dict 读数，两份同名 def 会塌成一个，
   正好对 P0-4 的形状免疫——已在 `469b659` 换成逐条 `(名字, 参数, 行号)` 并补同名重复判红。

### 18.9 交 DCD

`inbox/20261003-MA超限响应投影与设备健康分页-决策申请.md`（**新**）：Q1 `list_device_health`
分页/上限参数（与 `query_unified_events` 的 `limit/offset` 同口径）；Q2 默认投影是否精简
`stable_id`/`note`；Q3 超限时"裁行 + `_truncated` 明示"还是"整条改计数摘要"。
MA 本批不自裁的边界：三条都会改变 DB 经 MCP 看得见的返回。
可解析性保证（超限永远返回合法 JSON）**不随裁定改变**。
