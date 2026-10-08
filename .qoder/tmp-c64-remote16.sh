#!/usr/bin/env bash
# run16（20261006 第二档，任务表 #64 收尾 + #66 DCD 20261006 §九 验收落地）
# —— 本体从 tmp-c64-remote15.sh 复制后按下面四处增量改：
#   ①格 0B 加 12 个新锚点：log-odds 措辞核销（旧标签 =0 / 否定句 =1 / k 口径文档 =1）、
#     三条 DCD 验收锁的形状（零调用者锁的 helper 名 =3：def + 两处调用；raw 放大锁 =1）、
#     Q5 那族三条（未接入 / 跨午夜 / person 未读，各 =1）、心跳量具四格
#     （G_FRAC=3：常量 + floor 计算 + 自证比较；G_FN=1；G_SELFCHECK=1；G_OLDABS=0 = 绝对阈值已删净）。
#   ②TARGETED 格把改过的量具文件带上（本机 7 条全绿，容器侧同基数）。
#   ③MUT64 从 20 条涨到 24 条：M21 给 identity_fusion 接线（零调用者锁的负控，裁定 §九.1 点名要这一格）、
#     M22 把 score_raw 分母改成按记录数（重复上报不再放大 ⇒ 那条现状登记锁红）、
#     M23 补跨午夜环形距离、M24 让 person 参与判档。预飞 ast 档已重跑（MUTANTS_PARSED=24、
#     SYNTAX_BAD=0、DEADSHAPE_COUNT=0、VERIFY_BAD=0）。
#   ④T64IF_DEFS 25→29、T64BP_DEFS 19→22，wc 相应变化（本机实量：if 403 / bp 279 / ms 3359 /
#     t64if 413 / t64bp 322 / a3_p22 343）。
# 本档另有一格**不在容器里**：心跳量具的牙齿自证改在本机做（`.qoder/tmp-b64-gauge-check.py`，
# 把 /api/auth/status 的 to_thread 摘掉 ⇒ `1 failed / 6 passed`，那条锁红在
# `test_auth_status_handler_does_not_freeze_the_event_loop`，文件按字节还原 RESTORED=OK），
# 因为这一族要证明的是"阈值改成同轮对照之后仍然咬得住"，与容器 3.11 口径无关。
#
# run15（20261006 批次 c64，任务表 #64：`identity_fusion` + `behavior_predictor` 覆盖率与输入边界收口）
# 的**远端本体**。形状沿用 run14（两层引号：NAS bash -> 容器 sh；容器内是 dash，一律
# `cmd > file; echo RC=$?`）。
#
# 本批的起点是第十五轮审计 §九 优先级 1 的点名：「identity_fusion、behavior_predictor、
# api/behavior_routes（0%~10% 覆盖）」——第三项已在 #63 收口，本批收前两项。
# **前提先被量否**：同一把尺（本机 Python313 + coverage 7.16.1、全量单轮、HEAD 干净树 b241c7d）
# 实测 identity_fusion 84% / behavior_predictor 84%，不是 0%~10%（读数见
# .qoder/tmp-b64-cov-BEFORE.out 与 .qoder/tmp-b64-covreport-BEFORE.out）。所以本批按
# **未执行行 + 读码/运行时探针查到的现行缺陷**排事，不按百分比排事。
#
# 查到的缺陷（两条是真红，改前档有 git show 成对读数）：
#   A) identity_fusion 的冲突**封顶方向**是反的：R5/R6 写 `cap = min(cap, Level.MED,
#      key=lambda x: list(Level).index(x))`，而 `Level` 声明序 HIGH→MED→LOW→NEEDS_REVIEW→NONE，
#      index 越大越严重 ⇒ `min` 取的是**较轻**那一档。HEAD 实测 R5/R6 **从未封住任何档**
#      （cap 恒等于初始 HIGH），并且当 cap 已被 R3 降成 NEEDS_REVIEW 时它还会把档位
#      **放松回 MED**（严重度判定被反向改写）。R2/R3/R4 三处是直接赋值覆盖，同样不看已有 cap。
#      改后统一走 `_more_severe(a, b)`，方向只在声明序这一处定义。
#   B) `mcp_server.get_behavior_prediction` 的 weekday 边界与 HTTP 侧不对称：
#      `/api/behaviors/predict` 校 0-6，MCP 侧只有 -1 哨兵（`wd = None if weekday < 0 else weekday`），
#      传 7~99 不报错，而是每一天都被 weekday 过滤掉 ⇒ 扫完 5000 行事件回一句"没有那一天"
#      （静默空答，不是异常）。改后在取数前加 `not (-1 <= weekday <= 6)` 的入口守卫。
#   behavior_predictor 源码**一格未动**（HEAD 279 行 = 改后 279 行）：它的未执行行
#   （35 / 49 / 52-53 / 81 / 83 / 113 / 186 / 256-273）里，256-273 是 `detect_pattern_deviation`
#   整函数零覆盖 + 全仓零 src 调用者，其余是坏数据兜底分支。这一族按"锁住现行为"处理，
#   是否接入告警链属语义决定 ⇒ 呈 DCD，不在这里单方面改变行为。
#   同族登记（都不改行为、只登记）：identity_fusion 全仓**无在役调用者**（只有测试引用）、
#   `roster` 参数除 `None→[]` 外无落点、`review_floor_without_signal` 全仓无读点、
#   `fuse` 传给 `prior_boost` 的 `k` 是**该候选者的信号条数**（单信号候选在 `k <= 1` 就短路）。
#
# 与 run14 的差异（读数不能沿用）：
#   1) 换了树：本批 2 个源文件改动（identity_fusion.py、mcp_server.py）+ 2 个新测试文件
#      （44 条 def = 25 + 19）。SUITE/TARGETED/TOUCHED/GATE 四格都是整树口径。
#   2) **取消 PROBE 格**（run14 有、本档没有）：本批不需要临时库的运行时配对读数——
#      A/B 两条的改前形状由 `git show HEAD:` 侧指纹 + 变异档（M1/M2 把封顶退回 min-by-index、
#      M14 把守卫摘掉）提供成对证据，全部在容器 3.11 口径下跑。
#   3) 格 0A = run14c 同名单元**逐字复用**（前三批零件的静态自证），格 0B = 本批新指纹。
#      0A 的期望值就是 run14c 在册读数（SCAN_DAY..API_CORE_REPORTS = 3/3/7/32/0/2/1/2/0/8/1/0/1/1/4，
#      BR/CA 全档 = 1/1/1/1/1/1/1/1/1/1/0/1/0/0/1/1/2/0/12/49/1/1/1/1/2/1/1/1/0/2 + 46/2/1/1，
#      wc = 1227/695/896/382）；对不上说明改动渗到了不该渗的地方，不是"码不对"。
#   4) MUT64 = 本批 24 条（封顶方向 5 条 M1–M5、判据本身 M6、prior_boost 3 条 M7–M9、
#      消除法置信度上限 M10、房间为空档 M11、时序衰减 M12、VOTE 档 M13、MCP 守卫 M14、
#      predictor 六条 M15–M20，再加 run16 四条负控 M21–M24：接线、分母口径、跨午夜、person）。**M14 的档位差异预先登记**：本机 MCP SDK 不可用会 skip，
#      所以本机 dry-run 读 MUTATION_BAD=1（只有 M14 没咬住）；容器侧 SDK 在则应 20/20 全咬，
#      TARGETED 格的 `-rs` 会打出这两条到底跑没跑——**skip 的档位不算实跑**，必须看这一格。
#   5) MUT63 = 上一批 22 条，一并重跑作**自证**：本批一行未动 api/behavior_routes.py 与
#      change_attribution.py，咬合读数应与 run14c 一字不差（NOTHING 85 passed；失败条数
#      5/13/6/14/1/1/1/4/2/1/2/2/2/1/3/1/1/2/3/6/1/1；MUT_COUNT=22 MUTATION_BAD=0）。
#   6) MUT13（#62 批 12 条）**不重跑**：那一档测的是 insights api/service/nlquery 三支量具面，
#      本批一格未碰；0A 格上半那 15 格就是这一面的静态自证，静态对上即可。
#
# 格 0B 的期望值 = 本机 20261006 01:05 用同名锚量过（.qoder/tmp-b64-fingerprints-head.out）：
#   改后树：IF_MORE_SEVERE=7 IF_CAPMORE=5 IF_MIN_INDEX=0 IF_ASSIGN_MED=0 IF_ASSIGN_LOW=0
#           IF_ASSIGN_HIGH=1 IF_ROSTER_DOC=1 IF_FLOOR_NOTE=1 IF_K_GATE=1 IF_CLIP=1 IF_ELIM_CAP=1
#           IF_ROOM_GATE=1 IF_VOTE=1 BP_WEEKDAY/BP_DICTSHAPE/BP_LENGTH_GATE/BP_SEVERITY/
#           BP_INBAND/BP_SETDEDUP 全 =1（BP 源码未动）MS_GATE=1 MS_MSG=1 MS_SENTINEL=1
#           T64IF_DEFS=25 T64BP_DEFS=19 T64IF_MCP_SKIPGATE=0 / T64BP_MCP_SKIPGATE=2
#           （MCP 守卫的两条锁在 T2，T1 里没有 skip 门）
#           wc = if 398 / bp 279 / ms 3359 / t64if 321 / t64bp 271
#   HEAD 侧成对读数（`git show HEAD:…` 实测，同一批锚点）：IF_MORE_SEVERE=2 IF_CAPMORE=0
#           IF_MIN_INDEX=2 IF_ASSIGN_MED=1 IF_ASSIGN_LOW=1 MS_GATE=0 MS_MSG=0；
#           IF_ASSIGN_HIGH 两侧都 =1（那是 R1 短路档，本批没动它——"赋值形状"在 HEAD 是
#           MED/LOW 两处、改后只剩 HIGH 一处，所以这一格不能当本批的落点证据）；
#           BP 六条锚点 HEAD 侧同样全 =1；两份新测试在 HEAD 整档不存在（`git cat-file -e` = no）；
#           HEAD wc = if 397 / bp 279 / ms 3355。
#   本机改前/改后测试配对：HEAD 快照 .qoder/head63 上跑两份新锁 = **5 failed / 55 passed / 2 skipped**
#   （5 条红全在封顶方向那一族）；改后树 = **60 passed**。
#
# 教训照旧在册：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ TOOLCHAIN 排第一，量不到整跑作废；
# 变异串的**语法**要在出网前量（run14b 的 M21 少冒号 ⇒ RC=2/0 FAILED 被读成"没咬住"）；
# **权威门在飞的期间不许动树**（run14b 就是被出网后又改的三行注释降级成中间档的）。
set -uo pipefail
SNAP=${1:-c64snap20261006b}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN —— 先量工具链，量不到就整跑作废（run8 的教训：/tmp/pylibs 在容器可写层，
# 任何一次 recreate 都会把它清空，届时 pytest 不在 => "非零 RC + 无 FAILED 行"会被读成"咬住了"）。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0A：SOURCES-A —— run14c 同名单元**逐字复用**：前面几批的零件在本批必须一字不动。
ex "cd $L && R=src/memory_agent/insights/repository.py && echo SCAN_DAY=\$(grep -c _scan_day \$R) && echo HOUR_BUCKET=\$(grep -c hour_bucket \$R) && echo SUBSTR_WHOLE=\$(grep -c 'substr(' \$R) && echo HOUR_STMT_LINES=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | wc -l) && I=src/memory_agent/intent_inference.py && U=src/memory_agent/insights/utils.py && echo INTENT_NOW=\$(grep -c 'datetime.now()' \$I || true) && echo INTENT_EVENT_DT=\$(grep -c _event_dt \$I) && echo UTILS_KD_DEF=\$(grep -c '^KEYWORD_DOMAINS' \$U) && echo UTILS_CD_DEF=\$(grep -c '^CATEGORY_DOMAINS' \$U) && echo SRC_LEARNING=\$(ls src/memory_agent/learning_*.py 2>/dev/null | wc -l) && echo ATTIC_LEARNING=\$(ls attic/learning/learning_*.py 2>/dev/null | wc -l) && A=src/memory_agent/insights/api.py && S=src/memory_agent/insights/service.py && N=src/memory_agent/insights/nlquery.py && echo API_ANOM_DAYS=\$(grep -c 'tr = self._tr(start, end, days=days)' \$A) && echo API_ANOM_D2R=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c 'start, end = self._days_to_range(days, start, end)' || true) && echo NL_ANOM_CALL=\$(awk '/route == Intent.ANOMALY.value/,/route == Intent.RHYTHM.value/' \$N | grep -c 'entity_id=') && echo SVC_REPORTS_MOUNT=\$(grep -cF 'self.reports = ReportBuilder()' \$S) && echo API_CORE_REPORTS=\$(grep -cF 'self.core.reports.' \$A) && B=src/memory_agent/api/behavior_routes.py && C=src/memory_agent/change_attribution.py && T=tests/test_vma_task63_routes_input_boundary.py && O=tests/test_vma_r5_event_loop_offload.py && echo DWM_IMPORT=\$(grep -cF 'from ..day_bounds import DAY_WINDOW_MAX' \$B) && echo NUM_BOOL_GUARD=\$(grep -cF 'if isinstance(raw, bool):' \$B) && echo NUM_LO_MSG=\$(grep -cF '不得小于' \$B) && echo NUM_HI_MSG=\$(grep -cF '不得大于' \$B) && echo BAD_LIMIT_NUM=\$(grep -cF 'default=20, lo=1, hi=100)' \$B) && echo INTENT_LIMIT_NUM=\$(grep -cF 'default=3, lo=1, hi=5)' \$B) && echo CAUSAL_ENTRY_GATE=\$(grep -cF 'if not (1 <= value <= DAY_WINDOW_MAX):' \$B) && echo CF_GATE=\$(grep -cF 'if not (1 <= days <= DAY_WINDOW_MAX):' \$B) && echo AUDIT_LIMIT_NUM=\$(grep -cF 'default=100, lo=1, hi=500)' \$B) && echo MIN_COUNT_NUM=\$(grep -cF 'name=\"min_count\", default=3, lo=1)' \$B) && echo TRIGGER_NUM=\$(grep -cF 'name=\"trigger_id\", default=None, lo=1)' \$B) && echo AUDIT_SWALLOW_ASSIGN=\$(grep -cE '^ *limit = 100\$' \$B || true) && echo BARE_EXCEPT_VE=\$(grep -cE '^ *except ValueError:\$' \$B || true) && echo RAW_TRIGGER_INT=\$(grep -cE '^ *_lifecycle\(rt\)\.flag_false_positive, rule_id, int\(' \$B || true) && echo RAW_WMIN_PASS=\$(grep -cE '^ *body\.get\(\"window_minutes\"\),\$' \$B || true) && echo WMIN_NUM_GUARD=\$(grep -cF 'name=\"window_minutes\",' \$B || true) && echo AUDIT_TRAIL_GATE=\$(grep -cE '^ *if status in \(\"accepted\", \"rejected\"\):\$' \$B || true) && echo LEGACY_INT_BODY_PROSE=\$(grep -c 'int(body.get(' \$B || true) && echo LEGACY_INT_BODY_CODE=\$(grep -cE '^ *[a-z_]+ = int\(body\.get' \$B || true) && echo LEGACY_QUERY_INT=\$(grep -c 'int(request.query_params.get' \$B) && echo OFFLOAD_TO_THREAD_TOTAL=\$(grep -c 'await asyncio.to_thread(' \$B) && echo OFFLOAD_PREDICT=\$(grep -cF 'arrival, routine = await asyncio.to_thread(_predict)' \$B) && echo OFFLOAD_INFER=\$(grep -cF 'intents = await asyncio.to_thread(_infer)' \$B) && echo OFFLOAD_PROFILE=\$(grep -cF 'profile_text = await asyncio.to_thread(' \$B) && echo OFFLOAD_WRITE=\$(grep -cF 'await asyncio.to_thread(write_profile_atomic' \$B) && echo HEAVY_NESTED=\$(grep -cE 'def _(predict|infer)\(\)' \$B) && echo CA_WINDOW_DAYS=\$(grep -cF 'window_days = clamp_days(lookback_days)' \$C) && echo CA_DELTA_WD=\$(grep -cF 'delta = timedelta(days=window_days)' \$C) && echo CA_HALF_LIFE_WD=\$(grep -cF 'half_life = window_days / 2.0' \$C) && echo CA_HALF_LIFE_RAW=\$(grep -cF 'half_life = lookback_days / 2.0' \$C || true) && echo CA_DESC_WD=\$(grep -cF ', window_days)' \$C) && echo T63_TESTDEFS=\$(grep -c '^def test_' \$T) && echo T63_HEAVY_NAMES=\$(grep -cF 'HEAVY_SYNC_CALLEES' \$T) && echo T63_PROD_SHAPE=\$(grep -cF 'def test_production_read_shapes_are_asc_desc' \$T) && echo R5_BLINDSPOT=\$(grep -cF '盲区' \$O) && wc -l \$B \$C \$T \$O" > ${L}_sources.log 2>&1
echo SOURCES_RC=$?
cat ${L}_sources.log

# 格 0B：SOURCES-B —— 本批自己的指纹（三处落点：identity_fusion / behavior_predictor / mcp_server）。
# 这一格把"源码到底动没动"分开量：BP 六条锚点两侧都 =1（源码未动、只有测试在补），
# IF/MS 的新形状必须 =1、改前形状必须 =0。期望值见文件头。
ex "cd $L && I=src/memory_agent/identity_fusion.py && B=src/memory_agent/behavior_predictor.py && M=src/memory_agent/mcp_server.py && T1=tests/test_vma_task64_identity_fusion.py && T2=tests/test_vma_task64_behavior_predictor.py && G=tests/test_vma_a3_p22_auth_loop_blocking.py && echo IF_MORE_SEVERE=\$(grep -c '_more_severe' \$I) && echo IF_CAPMORE=\$(grep -cF 'cap = _more_severe(cap, Level.' \$I) && echo IF_MIN_INDEX=\$(grep -cF 'list(Level).index' \$I || true) && echo IF_ASSIGN_MED=\$(grep -cE '^ *cap = Level\.MED\$' \$I || true) && echo IF_ASSIGN_LOW=\$(grep -cE '^ *cap = Level\.LOW\$' \$I || true) && echo IF_ASSIGN_HIGH=\$(grep -cE '^ *cap = Level\.HIGH\$' \$I) && echo IF_ROSTER_DOC=\$(grep -cF '本版不读它' \$I || true) && echo IF_FLOOR_NOTE=\$(grep -cF '全仓无读点' \$I || true) && echo IF_K_GATE=\$(grep -cF 'if not posterior or k <= 1 or samples' \$I) && echo IF_CLIP=\$(grep -cF 'max(-0.6, min(0.6, beta))' \$I) && echo IF_ELIM_CAP=\$(grep -cF 'min(inferred[\"confidence\"], 0.65)' \$I) && echo IF_ROOM_GATE=\$(grep -cF 'if not signal_room or not slot_room:' \$I) && echo IF_VOTE=\$(grep -cF 'cfg.manual_mode == \"VOTE\"' \$I) && echo BP_WEEKDAY=\$(grep -cF 'weekday is not None and dt.weekday() != weekday' \$B) && echo BP_DICTSHAPE=\$(grep -cF 'raw = [raw]' \$B) && echo BP_LENGTH_GATE=\$(grep -cF 'if len(text) < 16:' \$B) && echo BP_SEVERITY=\$(grep -cF 'severity = \"mild\" if delta < 1.0' \$B) && echo BP_INBAND=\$(grep -cF 'if lower <= actual_hour <= upper:' \$B) && echo BP_SETDEDUP=\$(grep -cF 'labels = {' \$B) && echo MS_GATE=\$(grep -cF 'not (-1 <= weekday <= 6)' \$M) && echo MS_MSG=\$(grep -cF 'weekday 必须是 -1' \$M) && echo MS_SENTINEL=\$(grep -cF 'wd = None if weekday < 0 else weekday' \$M) && echo T64IF_DEFS=\$(grep -c '^def test_' \$T1) && echo T64BP_DEFS=\$(grep -c '^def test_' \$T2) && echo T64IF_MCP_SKIPGATE=\$(grep -cF 'MCP SDK 不可用' \$T1 || true) && echo T64BP_MCP_SKIPGATE=\$(grep -cF 'MCP SDK 不可用' \$T2) && echo IF_LOGODDS_OLDLABEL=\$(grep -cF '先验修正项（log-odds）' \$I || true) && echo IF_LOGODDS_DENY=\$(grep -cF '不是 log-odds' \$I) && echo IF_KDOC_RECORDS=\$(grep -cF '证据记录数' \$I) && echo IF_REFHELPER=\$(grep -c '_src_ref_sites' \$T1) && echo IF_RAWSCALE=\$(grep -cF 'raw_scales_with_records' \$T1) && echo BP_UNWIREDLOCK=\$(grep -cF 'deviation_is_not_wired_while_daily_profile_is' \$T2) && echo BP_MIDNIGHTLOCK=\$(grep -cF 'does_not_wrap_across_midnight' \$T2) && echo BP_PERSONLOCK=\$(grep -cF 'ignores_the_person_argument' \$T2) && echo G_FRAC=\$(grep -c OFFLOAD_CONTROL_FRACTION \$G) && echo G_FN=\$(grep -c 'def assert_offloaded' \$G) && echo G_SELFCHECK=\$(grep -cF '量具本身没牙' \$G) && echo G_OLDABS=\$(grep -c MIN_TICKS_WHEN_OFFLOADED \$G || true) && wc -l \$I \$B \$M \$T1 \$T2 \$G" > ${L}_sources_b64.log 2>&1
echo SOURCES_B64_RC=$?
cat ${L}_sources_b64.log

# 格 1：SCANS —— 前三批的量具在本批必须仍然是绿（改动没渗到那些面）。
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_qb_param_landing.py --strict; echo QBLAND_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights_callsites.py --strict; echo CALLSITE_RC=\$?; PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights_engine_attrs.py --strict; echo ATTRS_RC=\$?" > ${L}_scans.log 2>&1
echo SCANS_RC=$?
grep -E '^(QBLAND_RC|CALLSITE_RC|ATTRS_RC|FINDINGS|扫描 |门面|空指向|未登记|共 )' ${L}_scans.log | tail -8

# 门 1：pyflakes（口径 src/memory_agent；attic 不参与，用计数自证）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -3 ${L}_gate.log
echo GATE_MENTIONS_ATTIC=$(grep -c attic ${L}_gate.log)

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_____ ' ${L}_suite.log | head -5; echo SUITE_FAILNAMES_RC=$?

# 定向 1：本批的判据链（两份新锁）。`-rs` 是**必须的**：MCP 守卫那两条在本机会 skip，
# 只有容器侧真跑上，M14 的"咬住了"才算实跑档；skip 原因要打出来看。
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_task64_identity_fusion.py tests/test_vma_task64_behavior_predictor.py tests/test_vma_a3_p22_auth_loop_blocking.py -q -rs -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -8 ${L}_targeted.log
grep -E 'SKIPPED|MCP' ${L}_targeted.log | head -6; echo TARGETED_SKIPNAMES_RC=$?

# 定向 2：上一批（#63）的判据链，本批一行未动 behavior_routes / change_attribution ⇒ 应全绿
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_task63_routes_input_boundary.py tests/test_vma_r5_event_loop_offload.py tests/test_change_attribution.py tests/test_p5c_counterfactual.py tests/test_audit_arrival_time.py tests/test_p5b_conditional_probability.py tests/test_vma_p32_day_bounds.py tests/test_feedback_pack.py -q -p no:cacheprovider" > ${L}_prevbatch.log 2>&1
echo PREVBATCH_RC=$?
tail -3 ${L}_prevbatch.log

# 定向 3：凡引用 identity_fusion / behavior_predictor / mcp_server 的测试（18 个，本机 grep -l 实测）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_audit_arrival_time.py tests/test_audit_identity_fusion.py tests/test_insights_facade_contract.py tests/test_mcp_contract.py tests/test_mcp_surface_parity.py tests/test_p1_7_indexes_audit_async.py tests/test_run_template.py tests/test_tool_schema.py tests/test_vma121_intent_and_pii.py tests/test_vma_audit20261002_fixes.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_insights_callsite_binding.py tests/test_vma_p32_day_bounds.py tests/test_vma_q2_device_health_64kb.py tests/test_vma_r7_seam_fixes.py tests/test_vma_task63_routes_input_boundary.py tests/test_vma_task64_behavior_predictor.py tests/test_vma_task64_identity_fusion.py -q -rs -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -3 ${L}_touched.log

# 门 3（自证）：两档变异各咬各的锁，容器 Python 3.11 口径
#   MUT64 = 本批 24 条（封顶方向 M1–M5 / 判据本身 M6 / prior_boost M7–M9 / 消除法上限 M10 /
#           房间为空 M11 / 时序衰减 M12 / VOTE 档 M13 / MCP 守卫 M14 / run16 四条负控（下格） / predictor M15–M20）
#   MUT63 = 上一批 22 条，本批没碰 behavior_routes / change_attribution ⇒ 读数应与 run14c
#           一字不差（NOTHING 85 passed；失败条数 5/13/6/14/1/1/1/4/2/1/2/2/2/1/3/1/1/2/3/6/1/1；
#           MUT_COUNT=22 MUTATION_BAD=0）。对不上就是改动渗到了不该渗的地方。
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut64.py" > ${L}_mut64.log 2>&1
echo MUT64_RC=$?
grep -E '^(NOTHING|M[0-9]+ |MUT_COUNT|MUTATION_BAD|CONTROL_BAD|.*锚点异常|.*量具自伤)' ${L}_mut64.log
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut63.py" > ${L}_mut63.log 2>&1
echo MUT63_RC=$?
grep -E '^(NOTHING|M[0-9]+ |MUT_COUNT|MUTATION_BAD|restored_identical|.*锚点)' ${L}_mut63.log | tail -25

echo REMOTE_BATCH_RC=0
