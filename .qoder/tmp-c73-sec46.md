# §四十六 草稿（容器读数待填，填完整段搬进 `审计核实与修复_20261001.md`）

## 四十六、Q-A 点名却没落的那两格 `window`：#73 落码，以及预检两次拦住我自己（2026-10-07，任务表 #73/#58）

### 一、为什么这一格我自办、不等 DCD 回复

DCD `decisions/20261005-AF用户WebUI与MA四件与CVE-裁定.md:74`（裁5 追加 Q-A=甲）把 `window` 定成
**洞察类读数的通用回显键**，并点名两格：`coverage` 与 `data_quality`。当时只落了 `usage` /
`device_health` / `behavior_insights` 三格，那两格**已裁未落**。已裁的事不需要再问一次，
所以走自办（同一份呈文 `20261007-MA-route_question承诺键与window回显范围-决策申请.md` §五 里也写明了这条边界）。

缺陷形状很干净：`insights/service.py` 里这两个读法把结果直接 `return out`，异常那一路
`return self._fail(...)` 也是直出——同文件同侧的 `usage`（改后 `service.py:485-493`）走的是
`return self._with_window(out, tr)`。后果不是"少一个键"这么简单：**降级信封连口径一起丢了**，
消费方（MCP 面 `mcp_server.py:1619-1622` 的 docstring / `:2614` 把门面返回体整包给模型）
既拿不到"这批数覆盖哪一段"，也分不清"取不到"与"整段都是空"。

### 二、改了什么：两处实现 + 一把钉门面的锁

1. `src/memory_agent/insights/service.py:430-439`（`coverage`）与 `:1276-1283`（`data_quality`）：
   成功路与 `_fail` 路都收成 `out = …`，末尾统一 `return self._with_window(out, tr)`。
   `_with_window` 本身没动（`service.py:425-427`），口径仍来自 `_window_echo`（`service.py:409-424`），
   取不到就回**空 dict**——不抛、不省键、也不凭空造一个窗口。
2. `tests/test_vma_insights_window_echo.py`：**三处代码位**加上这两格——
   `:97-98` 与 `:109-110` 两条 `parametrize`（回显逐字等于请求区间 / 对偶档不许造固定窗），
   `:150-151` 降级信封那个 `for` 圈。
3. 新增 `test_outward_facade_reads_carry_the_window`（`:188`）：走**门面**读
   `get_data_coverage` / `get_data_quality`，逐键核对 `window` 的键集合
   `{start,end,start_ts,end_ts,days,label}`。门面（`api.py:886-891`、`:906-910`）今天是纯委托，
   这把锁钉的是"哪天门面重造返回体，别把口径又洗掉"。
4. 模块 docstring 更正：写清 Q-A 裁的是哪两格、这次落了哪两格、**没**动
   `compare_insights` / `plan_question`（那两格的回溯范围在呈文 Q2 里，属未裁）。
5. **渲染面确认没被牵连**：`insights/report.py:59-83` 的 `to_markdown` 只读命名字键
   （`summary` / `time_range` / `total`…），不遍历载荷 ⇒ Markdown 那份输出一个字都没变；
   只有 `to_json`（`:84-85`，整包 dump）这条路现在会把口径带出境——这正是要的效果。

### 三、三腿变异档（本机 3.13，副本树 `git archive HEAD`，工作树零改动）

| 腿 | 打的是什么 | 读数 |
|----|-----------|------|
| NOTHING | 什么都不改（控制腿） | `27 passed` |
| L1 | `coverage` 退回 `return out`（改前形状） | `4 failed, 23 passed` |
| L2 | `data_quality` 退回 `return out`（改前形状） | `4 failed, 23 passed` |
| L3 | `_window_echo` 的 `to_dict` 分支恒回 `{}`（键在、口径空壳） | `13 failed, 14 passed` |

`MUT_COUNT=3 MUTATION_BAD=0`，每腿 `restored=OK`（按字节 sha 对账）。
L3 红 13 条不是过量：`_window_echo` 是五个读法**共用**的，空壳化把两档窗口断言 + 门面锁一起打红。
"键在但值是空壳"比"整格缺席"更难发现，所以这一腿必须存在——这也是 #72 那三腿的形状复用。

### 四、这一档被自己的预检拦住两次（都是我的错，不是产品的错）

1. **量具锚点首稿写错缩进**：L2 的 anchor 我按记忆写成 16 空格缩进的 `out = self._fail(...)`，
   实际是 12 空格 ⇒ 本机 `--verify` 直接报 `L2 ANCHOR_BAD anchor=0`、`VERIFY_BAD=1`。
   改成带上前一行 `"total_events": 0, "filters": {}})` 才唯一——
   单行 `return self._with_window(out, tr)` 在本文件出现 **5 次**，拿它当锚点必然撞号。
2. **预检的期望值凭记忆写死**：`PARAM_LIST` 我写"预期 2"（以为只有两条 parametrize），
   实测 3（还有降级信封那个 `for` 圈）⇒ `GUARD_FAIL` → `PREFLIGHT_FAILED=1 未出网`，
   整档连容器都没碰到，代价七秒。留档：`.qoder/tmp-c73-run21-preflightfail.out`。
   **规矩**：`guard 名字 期望 实际` 里的"期望"必须当场从文件量出来，不许从上轮记忆抄。

3. **顺带一条台账更正**：§四十五 六-2 写"两格空载在 `service.py:430-437` / `:1274-1281`，
   同侧 `usage` 在 `:483-490`"——那是 HEAD `8b11437` 的行号。这次 `coverage` 上方多了两行注释、
   方法体本身重写，`usage` 被推到 **`:485-493`**、`data_quality` 推到 `:1276-1283`。
   引用旧行号的人请按"改前树"理解；本节的行号一律对 HEAD `84f2fca` 重新量过。

### 五、容器权威档读数（run21，HEAD `84f2fca`，快照 `/tmp/c73snap20261007`）

- 树身份：本机与容器**逐字同一**的 400 文件哈希表，聚合 sha256
  `3600c38a43e5c7eabe7b3f66a2009e4af55613ad063faa8520240909b0415922`
  （`HASH_LISTED=400 HASH_FILES=400 HASH_MISSING=0`，快照 `SNAP_RC=0`）。
- 预检 13 个落点全对：`WITH_WINDOW=5 / PRE_FIX_COV=0 / PRE_FIX_DQ=0 / NEW_LOCK=1 / PARAM_LIST=3`，
  量具四闸 `PATH_APPEND=1 OLD_CLOBBER=0 STDERR_SHOWN=1 FAILED_GUARD=1`，`ANCHOR_RC=0 HARNESS_RC=0`。
- 工具链先跑（TOOLCHAIN 第一）：`Python 3.11.16 / pytest 9.1.1 / pyflakes 4.0.2`，`TOOLCHAIN_RC=0`。
- pyflakes 门禁：`当前 0 条，基线 0 条，新增 0，已修 0` ⇒ `GATE_RC=0`。
- 全量回归：`1912 passed, 10 skipped in 426.72s (0:07:06)`，`SUITE_RC=0`；
  `SUITE_FAILNAMES_RC=1`（无失败用例名可列，这条"该为空"的读法本身也被量了）。
  10 条 skip = 4 条 `river` 缺模块 + 5 条 `test_drift`/`test_algo_kernel` 同因 + 1 条 provenance 精简检出。
- 定向 1（窗口回显锁）：`27 passed in 10.20s`，`TARGETED_RC=0`。
- 定向 2（10 个门面/落点/共用尺/NL 路由/卸载/配对/脏行文件）：`279 passed in 175.95s (0:02:55)`，`FACES_RC=0`。
- 变异档（容器实跑）：`MUT73_RC=0` —
  NOTHING `27 passed`（门自己没坏）、L1 `failed=4 restored=OK`、L2 `failed=4 restored=OK`、
  L3 `failed=13 restored=OK` ⇒ `MUT_COUNT=3 MUTATION_BAD=0`；AST 侧 `VERIFY_RC=0 MUTANTS_PARSED=3 VERIFY_BAD=0`。
- 收尾 POST：`POST_RC=0`、窗口锁重跑 `27 passed in 11.05s`，`REMOTE_BATCH_RC=0 REMOTE_DRIVER_RC=0
  CONTAINER_BATCH_RC=0 DRIVER_RC=0`。

### 六、本机侧其余读数

- 全量回归（本机 3.13，副本树）：`1898 passed, 24 skipped in 207.99s`，`SUITE_RC=0`
  （与容器 1912/10 的差 = 本机无 MCP SDK 而 skip 的那批注册工具用例，口径已在 #64 登记过）。
- 定向（insights 门面 + 窗口 + 落点 + 第三批共用尺 + NL 路由 + 第四批卸载）：`225 passed in 56.21s`。

### 七、下一个人该记住的三件事

1. **口径这类"共用的键"要一把门管住全部读法**，别按格子逐个补：这次两格之所以空载，
   正是因为裁定点名补的三格落了、没点名的两格没人管。Q2 问的就是要不要回溯。
2. **门面纯委托不等于门面有锁**。`window` 在 core 里带上，门面若哪天重造返回体就会再洗掉；
   锁要钉在"对外那条读法"上，不是钉在 core 上。
3. **期望值来自量，不来自记忆**。这一档两次拦都拦在我自己身上，且都拦在出网之前——
   预检多花七秒，比容器跑废一趟便宜两个数量级。
