import io

p = "doc/审计报告/进度与交接/Qoder接手进度_20261004.md"
t = io.open(p, encoding="utf-8", newline="").read()
tail = "\n---\n\n—— MA 侧执行台账 · 2026-10-04（基准 HEAD `f02eefd`）\n"
assert t.endswith(tail), repr(t[-80:])

SEC = """## 二十、2026-10-08 追加（任务表 #79~#84：DCD 20261007 四件 + 第二十轮 Critical，容器 run26 → run27）

### 落了什么（六个 commit，全部在推之前先过容器门）

| 任务表 | commit | 一句话 | 判据（当场量，非计划） |
| --- | --- | --- | --- |
| #79 | `a05f95c` | 温控环比回新引擎：门面注入 `climate_provider` 回调、顶层挂 `climate_comparison`，**键名与 legacy 逐字同** | `test_insights_facade_contract.py` 八条锁（三真库对账 + 五形状） |
| #80 | `fa384fd` | 承诺键门（`tests/test_tool_prose_promised_keys.py` 9 条）+ `window` 回溯两格 | 建门当场抓到 `query_behavior_events` 文案承诺 `server_ts` 而载荷键叫 `time` ⇒ 切文案不加键 |
| #81 | `313c6cc` | 登录限速 裁乙+丙：`resolve_client_ip` 三层默认拒绝 + 全局失败预算 60 次/分，**两条入口共用同一个桶键** | 22 条用例 + 22 条变异腿（本机档：首跑杀 20 活 2，补用例后全杀） |
| #82 | `e09238d` + `fdad983` | `vendor/` 换 homesdk 0.3.2 + `Dockerfile:26` 安装行；`announcer.py` 两条路按优先级分流 + `runtime.py` 注入桥 | 14 条变异腿（本机档：首跑杀 10 活 4，补跑四条全杀）+ RUNTIME 四格 `PROBE_BAD=0` |
| #83 | 本次落册 | DCD 20261007 §二/§三/§四/§五 裁定吸收 + 计划 §七 状态回填 + 合并窗五件原文登记 | 台账 §五十三/§五十四、计划 §六/§七 |
| #84 | `af3e0fe` | **MA-36（Critical）/ MA-37**：ACP 会话属主护栏补齐第四个入口 | 8 条回归锁 + AST 入口对等门 + 六腿变异全杀 |

### #84 这一条为什么要单独说

审计数到第 20 次的「已设计过但漏了入口」，**第一次是安全隔离后果**：
`check_owner` 的 docstring 亲口写着「avoid drift across history/delete/cancel」，三个入口都调了，
第四个入口 `M_PROMPT` 没调 ⇒ 另一个令牌持有者 B 拿 A 的 `sessionId` 就能**读到 A 的会话历史**，
并把本轮**写回 A 的会话**（读链 + 写链双实证）。`M_SESSION_NEW` 更直接：允许自报 `sessionId` 且
再 `new` 一次就改写属主 ⇒ 知道会话名就能接管它。

本轮除了照报告修，另外抓到两条报告没有的缺口（都上了锁）：

1. 会话被 `_trim()` 逐出、`_CONV` 历史还在 ⇒ 若按"表里有才校验"的写法，这条 sid 就成了新的越权入口（变异腿 P05）；
2. 判据若写成 `... and _owner`，未带 scope 的派发直接绕过校验 ⇒ 四种 scope 形状全部实测拒绝（变异腿 P06）。

**本机实测（`.qoder/tmp-r20-*`，落盘可查）**：

- 六腿变异档 `tmp-r20-mut-local1.out`：控制腿 `[P-0] rc=0 '28 passed in 4.56s'`，
  P01~P06 **全部 KILLED**，`totals: killed=6 survived=0 invalid=0 legs=6`，`WT_UNTOUCHED=True`
  （变异只打在一次性副本树 `.qoder/tmp-r20-mut/`）；
- 全量回归 `tmp-r20-suite-local.out`：**1996 passed, 24 skipped in 272.98s**、`LOCAL_SUITE_RC=0`。

### run26 已完成格（快照树 = `fdad983`，容器 3.11.16）

`TOOLCHAIN_RC=0`（`PYTEST=9.1.1` / `PYFLAKES=4.0.2`）；本机与容器**聚合摘要逐字同**
`HASH_LISTED=406 HASH_FILES=406 HASH_MISSING=0`、
`HASH_AGGREGATE=998d4aa8cbd06041c086a1598d290b11fda2e9377d2cfdd78d3f4fa664afd34a`；
`GATE_RC=0`（pyflakes 0/0/0/0）、`SUITE_RC=0`（**2002 passed, 10 skipped in 549.90s**）；
定向三格 `AUTH_A_RC=0`（157 passed）/ `ANN_B_RC=0`（84 passed, 1 skipped）/ `PROSE_C_RC=0`（39 passed）。

**`PROBE_RC=1` 是量具自己的缺陷，不是产品缺陷**：探针脚本放在 `/tmp/<SNAP>_probe.py`，
它用 `__file__` 往上算仓库根 ⇒ 在容器里算到 `/src`，`ModuleNotFoundError: No module named 'memory_agent'`。
改成判据走环境变量（`PROBE_ROOT`）+ 显式 `PYTHONPATH`，本机重跑四格 `PROBE_BAD=0`。
这是「量具的取数根必须可指认」那条教训的**第二次发作**，登记在册。
run26 因此没跑到 RUNTIME 之后的变异腿与 POST 二次哈希 ⇒ 由 run27 补。

### run27 的口径（新代码批的权威门）

树 = `af3e0fe`。新增格：ACP 形状锚点 9 枚（含 `check_owner` 的 def=1 / 调用点=4）、
新测试锁 5 枚按名字核、定向 D（ACP 三件 28 条）、**六腿 mutr20 在容器 3.11 重跑**、POST 二次哈希 + ACP 原样复跑。
**#81/#82 的 36 条变异腿不在 run27 重跑**，理由当场可核不是含糊话：
`git diff --name-only fdad983 HEAD` 实测只有 `src/memory_agent/acp_server.py` 与
`tests/test_acp_round20_owner_isolation.py` 两行，与那两档的靶文件**无交集**；
run27 的全量回归 + POST 哈希负责证明没串味。⇒ 读数并立：#81/#82 出自 run26，MA-36/37 出自 run27，不混引。

### 下一位的交接点

- #85：第十一轮~第二十轮剩下的十一条（MA-25~MA-35）已逐条**按符号重锚**并登记在台账 §五十四的表里
  （`mcp_server.py` 的行号一律以重锚后的为准，#78 删过 439 行）。
  其中 **MA-29 / MA-32 / MA-34 是产品口径选择，已随 20261008 回执 §四 呈 DCD，不要自办**；
  MA-25/26/27/28/30/33/35 可直接修。
- **MA-31 有一条机制更正**：报告说"推进语句在 try 之外、外层只捕 `CancelledError`"，
  当前树实测推进语句在 try 内（`runtime.py:287`）、外层是 `except Exception`（:349）。
  结论（一次异常 ⇒ 永久停摆、无自愈）不变，但**修点不同**：要加的是退避重启，不是补 try。照报告字面改会改不到点上。
- #83 剩回执已发（`关键决策部/inbox/20261008-MA-四件落码回执与两处字面偏离与ACP批次三问-决策申请.md`），等追认。
- 合并窗五件（装 wheel → 翻 JSON → 断连记 degraded + `ADM_ERR_BROKER_UNREACHABLE` → 探针三组，
  R2 PII 回填与 service_token 生效不拆开）**运行面未动**：重烤 / recreate 不在自主范围。

"""

io.open(p, "w", encoding="utf-8", newline="").write(t.replace(tail, "\n" + SEC + tail))
out = io.open(p, encoding="utf-8", newline="").read()
print("INSERTED lines=%d CR=%d" % (out.count(chr(10)), out.count(chr(13))))
