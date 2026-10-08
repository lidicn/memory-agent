---

## 四十八、#74 对外文案的"承诺了但不存在"：三处出货面一起收口，并把文案锁罩到 prose 面（run22 容器权威档）

### 一、这一格是怎么露出来的

§四十七 那张 14 行嫌疑表是**运行时键 diff**（真调门面读载荷）做的，它量出的东西里有一类不在"键名对照"里：
`get_data_coverage` 的**对外文案**还在承诺载荷里根本不存在的键。这类缺陷的危险不在"少一个键"，
而在**文案是喂给模型的那句话**——模型照着描述去读格子，读不到就编一个，或者干脆放弃这一路。
`route_question` 的 `recommended_tool`（§四十五）是同一族的第一次露脸，这次是第二次，而且面更大：

| 出货面 | 位置 | 谁读它 | 改前承诺 |
|---|---|---|---|
| MCP handler docstring | `mcp_server.py:1619-1623`（改前 5 行，改后 7 行 `:1619-1625`） | MCP 客户端 / 模型（工具描述直接进 prompt） | 每天 `has_data` 标记、first/last 有数据的日期 |
| 技能包 | `skills_bundle/insight/SKILL.md:44-45` | 模型（`get_skill` 拉到的是整份技能文本） | 同上两句 |
| 用户手册页 | `static/js/pages/user_manual.js:211` | 人（"数据从哪天开始有？"那一问的答案） | 同上两句 |

新引擎的真实载荷（`insights/service.py` 的 `coverage`）里：逐日格子叫 `empty`（不是 `has_data`），
`start_day/end_day` 是**查询窗口边界**（不是"有数据的首末日"——legacy 那两个键 `insights_legacy.py:1751-1752`
在这条路上没有对应物，因为窗口边界日本身可能就是空日）。

### 二、为什么改文案而不是补键

补 `first_day_with_data/last_day_with_data` 等于把 Q-A 的命名再往新引擎里塞一次，而裁5 Q-B 已把
"以新命名为正式口径"定了方向；`has_data` 与 `empty` 是同一信息的正反两种写法，留两个键只会让下一轮
键 diff 又要问"哪个是真的"。⇒ **按已裁口径改文案**，不动载荷形状，属于自办范围。

顺带一条**不判红**的登记：`mcp_server.py` 全文 grep `has_data` 仍有 **2** 次（`:251` 的 summary、
`:1601` 的 `get_device_health` docstring）。那两处说的是**实体目录的字段**（`entity_catalog` 的
`has_data/last_seen/stale_days`），而 `device_health` 整条交回 legacy（`api.py:767-780`），
legacy 载荷里 `data_quality_issues`、`healthy/no_data/stale` 都在（`insights_legacy.py:1705-1711`）
⇒ 那不是空头承诺，故门里按 2 记账而不是 0。文案锁管的是**各自 handler 的 docstring**（AST 取），不是全文。

### 三、锁与它的正反两问

`tests/test_vma_coverage_docstring_contract.py`（本机 mcp SDK 装不上 ⇒ **AST 读源码**，import 型锁会
在最该跑它的环境里静默 skip，这条理由在文件头写死了）：

1. `…_only_promises_keys_the_payload_has`：文案点名的 6 个顶层键（`missing_days/start_day/end_day/day_coverage/hour_coverage/peak_hours`）必须真的在载荷里，且**都得被文案提到**（少提一个等于让消费方猜）。
2. `…_does_not_resurrect_dead_legacy_keys`：`has_data/first_day_with_data/last_day_with_data` 不许回到 docstring。
3. `test_per_day_marker_is_named_empty_not_has_data`：逐日格子名字锁 + 值真的是空日才 `True`。
4. `test_start_day_is_the_window_bound_not_the_first_day_with_data`：数据集故意让**窗口起点落在无数据日**
   （`2026-09-20` 空、`09-21` 有、`09-22` 缺口、`09-23` 有），断言 `start_day` 仍是边界、
   `missing_days == [2026-09-20, 2026-09-22]`、且 `start_day != 首个有数据日`。
5–8. `PROSE_SURFACES` 参数化两条 × 两个面（SKILL.md / user_manual.js）：同一把尺罩到 prose 面。

**第 5、6 条是本轮真正的收获**：首稿只修了 docstring 就准备收尾，是 prose 面的 grep 把另外两处捞出来的
——"改一处留两处"对模型来说等于没改。

### 四、变异档 MUT74：六条腿

`MUT_ROOT` 用一次性副本树（`.qoder/tmp-c74-wt`，工作树全程零改动，跑完按字节还原 + sha 比，
最后与工作树逐文件 md5 对账 `FILES_COMPARED=401 DIFFS=0 MISSING=0`）。

本机 3.13 先跑一遍（`.qoder/tmp-c74-mut74-local22.out`，`MUT74_RC=0`）：
`MUTANTS_PARSED=6 VERIFY_BAD=0`、`MUT_COUNT=6 MUTATION_BAD=0`，六条腿 `restored=` 全 `OK`；
跑完与工作树逐文件按字节 md5 对账 `FILES_COMPARED=401 DIFFS=0 MISSING=0`（不是抽查）。
容器 3.11 的同一份读数在下一节。

两条量具自身的坑，都在这一档里被锚点钉住（写进了档头注释）：
- 还原腿必须写回**原文那一份**。首版把 `mutated` 又写了一遍 ⇒ 三条腿全报 `restored=BAD`，
  "没污染树"这句话当场不成立（腿确实咬住了，但自证是假的）。
- CRLF 文件里的锚点不许带 `\n`。`SKILL.md` / `user_manual.js` 整份是 CRLF，带 `\n` 的锚点 count 恒 0，
  看起来像"锚点不唯一"，其实是行尾对不上 ⇒ L4/L5 两条锚在单行内部。

### 五、容器权威档读数（run22，HEAD `fec2c2c`，快照 `/tmp/c74snap20261007`）

- 快照树身份：本机与容器跑同一份 `_hashes.py`，清单 **401** 个文件、`HASH_MISSING=0`，
  聚合摘要两侧**逐字相同**：`934f2beeceae3614e86d9231f379b833bf86dd092dc487d492cfdafb204caf70`
  （run21 那档是 `3600c38a…15922`，树变了 ⇒ 旧摘要作废，这一行才是 HEAD `fec2c2c` 的指纹）。
- 格 -1 TOOLCHAIN：`CONTAINER_PRESENT=1`、`Python 3.11.16`、`PYTEST=9.1.1`、`PYFLAKES=4.0.2`、`TOOLCHAIN_RC=0`。
- 格 0C/0C2/0C3 ANCHOR（容器读数，与本机 guard 逐字对）：`NEW_HEADLINE=1 OLD_HEADLINE=0 MCP_HAS_DATA_OTHER=2
  SVC_START_DAY=1 SVC_PEAK=1 WITH_WINDOW=5` / `SKILL_HAS_DATA=0 SKILL_FIRSTLAST=0 SKILL_DAYCOV=1
  JS_HAS_DATA=0 JS_FIRSTLAST=0` / `PROSE_SURFACES=5 PROSE_TESTS=2`；`ANCHOR_RC=ANCHOR_PROSE_RC=ANCHOR_TEST_RC=0`。
- 格 0D HARNESS：`PATH_APPEND=1 STDERR_TAIL=1 VERIFY_FLAG=2 OLD_CLOBBER=0 FAILED_GUARD=1`，`HARNESS_RC=0`。
- 门 1 pyflakes：`GATE_RC=0`，`pyflakes: 当前 0 条，基线 0 条，新增 0，已修 0`（口径 `src/memory_agent`，基线只准减）。
- 门 2 全量回归：`SUITE_RC=0` ⇒ **`1920 passed, 10 skipped in 467.20s`**；
  10 条 skip 的名单里是 `river` 缺模块 4 条 + 精简检出无法比 provenance 1 条 + 其余 5 条既有项，
  失败名单 grep 无命中（`^_{3,} ` / `^FAILED ` 两式都空）。
- 定向 1（主战场：文案锁 8 + window 回显 15 + 门面契约 12）：`TARGETED_RC=0` ⇒ **`35 passed in 23.73s`**。
- 定向 2（读这三处面的 13 个文件，含 `test_tool_schema.py` / `test_mcp_surface_parity.py` / `test_acp_server.py`）：
  `FACES_RC=0` ⇒ **`315 passed in 179.12s`**；失败名单 grep 同样无命中。
- 门 3 自证：`VERIFY_RC=0` ⇒ `MUTANTS_PARSED=6 VERIFY_BAD=0`；六条腿容器读数（`MUT74_RC=0`）——

      M-0 基线          -> RC=0  23 passed in 9.60s
      L1 docstring 回滚  -> RC=1  failed=2  restored=OK | 2 failed, 21 passed in 8.56s
      L2 载荷丢 peak_hours -> RC=1  failed=1  restored=OK | 1 failed, 22 passed in 9.29s
      L3 start_day 越界   -> RC=1  failed=1  restored=OK | 1 failed, 22 passed in 8.27s
      L4 SKILL.md 回滚    -> RC=1  failed=1  restored=OK | 1 failed, 22 passed in 9.26s
      L5 手册回滚         -> RC=1  failed=2  restored=OK | 2 failed, 21 passed in 9.75s
      MUT_COUNT=6 MUTATION_BAD=0

  与本机 3.13 那档（`M-0 23 passed / L1 2 / L2 1 / L3 1 / L4 1 / L5 2`）**逐腿同形**。
- 还原自证 POST：`POST_RC=0` ⇒ `23 passed in 8.90s`（变异跑完原样再跑同一份用例）。
- 收尾：`REMOTE_BATCH_RC=0 REMOTE_DRIVER_RC=0 CONTAINER_BATCH_RC=0 DRIVER_RC=0`；
  整档留档 `.qoder/tmp-c74-run22b.out`（首跑那档被预检拦下，留档 `.qoder/tmp-c74-run22.out`）。

### 六、本机侧其余读数与两处预检自捉

- 全量回归（本机 3.13，副本树 `.qoder/tmp-c74-wt`）：`1905 passed, 25 skipped in 201.61s`，`LOCAL_SUITE_RC=0`
  （留档 `.qoder/tmp-c74-suite-local22.out`；那 25 条 skip 全是 `MCP SDK 不可用或版本过旧` 一类，本机口径）。
- 定向锁（本机）：`8 passed in 1.41s`（4 条原有 + 4 条 prose 参数化）。
- **自捉 1（本轮唯一一次出网前拦截）**：driver 的本机 guard 写成 `grep -cF '\"--verify\"'`——
  单引号里的 `\"` 是**字面反斜杠**，pattern 找的是 `\"--verify\"` ⇒ 三格恒 0（`VERIFY_FLAG`、
  `SVC_START_DAY`、`SVC_PEAK`）→ `PREFLIGHT_FAILED=1 未出网`，代价约七秒。
  远端格子恰好相反：模式在 `ex "…"` 的双引号内，**必须**写 `\"` 才能把引号送进容器 shell。
  两侧口径已写进 driver 注释，别再凭"上轮能跑"改回去。
  留档：`.qoder/tmp-c74-run22.out`（失败那档原样保留）。
- **自捉 2**：`echo RC=$?` 接在管道尾巴后面量的是 `tail` 的退出码，不是量具的。本轮所有退出码
  一律"命令 > 文件 2>&1; echo X=$?"，不再出现管道后取码。

### 七、这一格关掉之后仍然留着的

- `climate_comparison` 的温控环比维度在新引擎无落点 ⇒ 已呈 DCD
  （`关键决策部/inbox/20261007-MA-温控环比维度在新引擎无落点-决策申请.md`），MA 侧倾向乙（compare 读 legacy 的
  climate 读数），裁定前不动。
- `mcp_server.py` 里那份手写的死 `TOOL_CATALOG` 有重复条目（`get_behavior_insights` 两行）——
  该目录早被 `build_catalog()` 取代、不参与对外口径，属清理项，不是缺陷，不按本轮口径登记为红。
- 同一族缺陷的**通用扫描**还没做：`mcp_server.py` 里 `@mcp.tool()` 出现 **86** 次（本轮当场量，不是记忆里的数），
  这些 handler 的 docstring 各点了哪些键、载荷里是否真的有，只锁了被运行时 diff 抓到的这一格 + 它的 prose 面。
  扫描量具要能同时读"文案点名的键"和"载荷实际给的键"，前者可 AST 取，后者要么 import（本机 SDK 残缺）、
  要么再造一份真库夹具 ⇒ 这是下一格的活，不是一句话的事。
- 另一处同族**但当前不流血**的：`mcp_server.py:265` 手工 `TOOL_CATALOG` 里 `get_data_coverage` 的 summary
  还写着"明确窗口内实际有数据的日期"。该字面量在 `:3344` 被 `TOOL_CATALOG = build_catalog()` 覆盖、
  `:604` 的 `TOOL_NAMES` 也被 `:3345` 的 spec 版覆盖 ⇒ 对外不生效（对外真源是 `tool_schema.py:292-306`，
  它没承诺死键）。留下它是给下一个读代码的人挖坑，随 TOOL_CATALOG 字面量一起清理。
