#!/usr/bin/env bash
# run14（20261006 批次 c63，任务表 #63：`api/behavior_routes.py` 输入边界与事件循环收口）的
# **远端本体**。形状沿用 run13（两层引号：NAS bash -> 容器 sh；容器内是 dash，一律
# `cmd > file; echo RC=$?`）。
#
# 本批修的是"入参收下了却不守边界 / 重活留在主线程"这一族，四条缺陷族 + 一把新量具：
#   第一族  `_num` 之前的 `int(body.get(x) or 3)`：非数字 → 未捕获 ValueError → **500**
#           （app 无 exception_handlers）；`true` 被读成 1；负数被 SQLite 的 `LIMIT -1`
#           （= 无上限）绕过夹紧；越界静默改成默认档；缺省值覆盖 config（`min_variant_support`
#           的 `None → config.process_mining_min_variant_support` 那条路被字面量 3 顶死）。
#   第二族  `causal_analyze` / `causal_counterfactual` 的 `days`/`lookback_days` 入口不设门，
#           而 `change_attribution.search_candidate_causes` 内部对同一个入参有**三个出口**：
#           窗口跨度走 `clamp_days`、衰减半衰期 `half_life` 用原值、给读者看的 description 也用原值
#           ⇒ 传 10**9 时窗口 3650 天而半衰期 5e8 天，`_temporal_proximity` 对所有事件年龄
#           一律 ≈1.0，末尾按 confidence 的排序**静默失效**（第八轮 P3-2 只收了会崩的那一半）。
#           库侧收成同一个 `window_days`（三个出口同一口径），路由侧再入口拒绝、不替调用方改数。
#   第三族  `home_profile` / `predictions` / `intent` 三条热路由把重活留在事件循环上
#           （A3 卸载族第四处；r5 的 AST 量具按"receiver 是不是 store/ha_db"判，看不见
#            `build_profile(rt.store, …)` 这种**把 store 当参数**的形状，所以它全绿而事件循环照旧被占）。
#   第四族  顺着「每个入参要么落点要么显式拒」这条口径在本文件又查到三处现行（审计没点名）：
#           `rule_channel_audit` 的 `except ValueError: limit = 100`（吞掉照样 200，
#           `swallow-and-claim-ok` 的形状）、`negative_sample_suggestions` 的
#           `int(body.get("min_count") or 3)`（0 静默改 3、`true` 读成 1）、
#           `rule_channel_false_positive` 的裸 `int(trigger_id)`（非数字 → 500）。
#           改后这三条"改前形状"指纹归零：`AUDIT_SWALLOW_ASSIGN`（`except ValueError:` 后面那句
#           `limit = 100` 赋值）1→0、`RAW_TRIGGER_INT`（裸 `int(trigger_id)` 那行调用）1→0、
#           `LEGACY_INT_BODY_CODE`（`x = int(body.get(...))` 形状的赋值行）4→0。
#           锚点一律取**整行代码**而不是子串：按名字 grep 会罩住注释和 docstring——
#           `BARE_EXCEPT_VE` 改后仍是 1（那是 weekday 的合法守卫，:1001 它返回 400，不是吞掉），
#           `LEGACY_INT_BODY_PROSE` 改后仍是 2（:28 `_num` docstring 的改前记载 + :172 本批点名
#           改前形状的那句注释），
#           所以"归零"说的是**形状**、不是关键字，两个口径都在这格出读数。
#   量具    `tests/test_vma_task63_routes_input_boundary.py` 的 `_unguarded_heavy_calls()`：
#           按**函数名**点名六个重同步 callee（补 r5 的盲区）+ 心跳真响（50ms 睡眠期间 tick>0）。
#
# 与 run13 的差异（读数不能沿用）：
#   1) 换了树：#62 出网后又落本批 2 个源文件改动 + 1 个新测试 + 1 个测试 docstring。
#      SUITE/TARGETED/TOUCHED/GATE 四格都是整树口径。
#   2) 本批 PROBE 格跑在**临时库**（`tempfile` + `Store(...)`），不是生产库；生产库这轮不动。
#      静态结论只能证明"主线程上还压着一次同步调用"，证明不了"心跳因此停 tick"，这一格出成对读数。
#   3) SOURCES 上半 = run10/run11/run13 在册零件，全部必须一字不动；下半是本批新指纹 + wc。
#   4) 变异两套：MUT63（本批 22 条 = 前 19 + 族 5 三条 M20/M21/M22）+ MUT13（上一批 12 条，
#      **自证**：本批刻意没动 insights/api|service|nlquery 与三支量具，读数应与 run13d 一字不差
#      —— 失败条数 3/2/1/1/1/1/1/1/1/3/2/2）。
#   5) SOURCES 下半的**期望值**（本机 20261006 22:01 用同名锚点量过，见 .qoder/tmp-b63-fingerprints.out
#      第三轮；容器侧这格应与之一字不差，对不上就是快照/行尾/引号层出了问题，不是"码不对"）：
#        新形状 = 1：DWM_IMPORT NUM_BOOL_GUARD NUM_LO_MSG NUM_HI_MSG BAD_LIMIT_NUM INTENT_LIMIT_NUM
#                     CAUSAL_ENTRY_GATE CF_GATE AUDIT_LIMIT_NUM MIN_COUNT_NUM TRIGGER_NUM
#                     WMIN_NUM_GUARD
#                     OFFLOAD_PREDICT OFFLOAD_INFER OFFLOAD_PROFILE OFFLOAD_WRITE
#                     CA_WINDOW_DAYS CA_DELTA_WD CA_HALF_LIFE_WD T63_PROD_SHAPE R5_BLINDSPOT
#        新形状 = 2：HEAVY_NESTED CA_DESC_WD T63_HEAVY_NAMES LEGACY_INT_BODY_PROSE
#        两侧都该是 1（本批只加测试不加代码）：AUDIT_TRAIL_GATE
#        新形状 = 0（改前形状必须消失）：AUDIT_SWALLOW_ASSIGN RAW_TRIGGER_INT RAW_WMIN_PASS
#                                        LEGACY_INT_BODY_CODE CA_HALF_LIFE_RAW
#        计数：BARE_EXCEPT_VE=1（weekday 合法守卫）LEGACY_QUERY_INT=12 OFFLOAD_TO_THREAD_TOTAL=49
#              T63_TESTDEFS=46
#        wc：behavior_routes 1227 / change_attribution 695 / task63 测试 896 / r5 测试 382
#        （三套档的差别只在测试文件那几行注释，源码一格未动：
#          run14  = 21:28 的**中间树**：BR 1210 / task63 598 / T63_TESTDEFS 32 / MUT_COUNT 19；
#          run14b = 22:09 那份：BR 1227 / task63 893——出网后我又改了测试里三处**注释与
#                   断言说明**（把「改前也回 200」两句假记载改成"HEAD 已实现、改前 0 条用例走过"，
#                   371 那句改成带口径的中间档读数），893 → 896；
#          run14c = **终树档**，本表就是它的期望值，读数以 run14c 为准。
#          教训写在这儿：**权威门在飞的期间不许动树**——树一动，那一档就自动降级成中间档。）
#   6) run14b 出网后另有一处**量具**修复（族 6，源码一格未动、本表期望值不受影响）：
#      M21 的替换串 `    if status in (),` 少冒号 ⇒ 变异文件 collection 期 SyntaxError、
#      pytest RC=2 且 0 条 FAILED，门把"自己坏了"报成"没咬住"（MUT63_RC=1 / MUTATION_BAD=1，
#      其余 21 条腿全咬住）。现改为合法恒假守卫 `    if status in ():`，并给 ast 验证脚本
#      加 `SYNTAX_BAD` 一格：出网前对全部 22 条**在内存里**替换后 compile，语法不过即红；
#      负证明 = 喂旧形状读回 `SyntaxError invalid syntax line 2`。
#      HEAD 侧成对读数（改前响，全部按 `git show HEAD:…` 实测，见 .qoder/tmp-b63-fingerprints-head.out；
#      原先这里写的是"上面 =1 的源侧指纹全 0"——那是**推论**，十来条锚点当时并没在 HEAD 上量过）：
#      DWM_IMPORT / NUM_BOOL_GUARD / NUM_LO_MSG / NUM_HI_MSG / BAD_LIMIT_NUM / INTENT_LIMIT_NUM /
#      CAUSAL_ENTRY_GATE / CF_GATE / AUDIT_LIMIT_NUM / MIN_COUNT_NUM / TRIGGER_NUM / WMIN_NUM_GUARD /
#      OFFLOAD_PREDICT / OFFLOAD_INFER / OFFLOAD_PROFILE / OFFLOAD_WRITE / HEAVY_NESTED /
#      CA_WINDOW_DAYS / CA_DELTA_WD / CA_HALF_LIFE_WD / CA_DESC_WD = **全 0**；
#      AUDIT_SWALLOW_ASSIGN 1、BARE_EXCEPT_VE 2、RAW_TRIGGER_INT 1、RAW_WMIN_PASS 1、
#      LEGACY_INT_BODY_PROSE 6、LEGACY_INT_BODY_CODE 4、LEGACY_QUERY_INT 14、
#      OFFLOAD_TO_THREAD_TOTAL 45、CA_HALF_LIFE_RAW 1、AUDIT_TRAIL_GATE 1（这条两侧都该是 1）；
#      task63 测试在 HEAD **整档不存在**（`git cat-file -e` = no，T63_* 三格 0），
#      r5 测试 374 行、R5_BLINDSPOT 0；HEAD 源文件 wc = br 1131 / ca 689。
set -uo pipefail
SNAP=${1:-c63snap20261006a}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

# 格 -1：TOOLCHAIN —— 先量工具链，量不到就整跑作废（run8 的教训：/tmp/pylibs 在容器可写层，
# 任何一次 recreate 都会把它清空，届时 pytest 不在 => "非零 RC + 无 FAILED 行"会被读成"咬住了"）。
ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SOURCES —— 上半是"前三批的零件没被动"，下半是本批自己的指纹。
ex "cd $L && R=src/memory_agent/insights/repository.py && echo SCAN_DAY=\$(grep -c _scan_day \$R) && echo HOUR_BUCKET=\$(grep -c hour_bucket \$R) && echo SUBSTR_WHOLE=\$(grep -c 'substr(' \$R) && echo HOUR_STMT_LINES=\$(awk '/def _hour_statement/,/return .*UNION/' \$R | wc -l) && I=src/memory_agent/intent_inference.py && U=src/memory_agent/insights/utils.py && echo INTENT_NOW=\$(grep -c 'datetime.now()' \$I || true) && echo INTENT_EVENT_DT=\$(grep -c _event_dt \$I) && echo UTILS_KD_DEF=\$(grep -c '^KEYWORD_DOMAINS' \$U) && echo UTILS_CD_DEF=\$(grep -c '^CATEGORY_DOMAINS' \$U) && echo SRC_LEARNING=\$(ls src/memory_agent/learning_*.py 2>/dev/null | wc -l) && echo ATTIC_LEARNING=\$(ls attic/learning/learning_*.py 2>/dev/null | wc -l) && A=src/memory_agent/insights/api.py && S=src/memory_agent/insights/service.py && N=src/memory_agent/insights/nlquery.py && echo API_ANOM_DAYS=\$(grep -c 'tr = self._tr(start, end, days=days)' \$A) && echo API_ANOM_D2R=\$(awk '/def anomaly_report/,/def device_health/' \$A | grep -c 'start, end = self._days_to_range(days, start, end)' || true) && echo NL_ANOM_CALL=\$(awk '/route == Intent.ANOMALY.value/,/route == Intent.RHYTHM.value/' \$N | grep -c 'entity_id=') && echo SVC_REPORTS_MOUNT=\$(grep -cF 'self.reports = ReportBuilder()' \$S) && echo API_CORE_REPORTS=\$(grep -cF 'self.core.reports.' \$A) && B=src/memory_agent/api/behavior_routes.py && C=src/memory_agent/change_attribution.py && T=tests/test_vma_task63_routes_input_boundary.py && O=tests/test_vma_r5_event_loop_offload.py && echo DWM_IMPORT=\$(grep -cF 'from ..day_bounds import DAY_WINDOW_MAX' \$B) && echo NUM_BOOL_GUARD=\$(grep -cF 'if isinstance(raw, bool):' \$B) && echo NUM_LO_MSG=\$(grep -cF '不得小于' \$B) && echo NUM_HI_MSG=\$(grep -cF '不得大于' \$B) && echo BAD_LIMIT_NUM=\$(grep -cF 'default=20, lo=1, hi=100)' \$B) && echo INTENT_LIMIT_NUM=\$(grep -cF 'default=3, lo=1, hi=5)' \$B) && echo CAUSAL_ENTRY_GATE=\$(grep -cF 'if not (1 <= value <= DAY_WINDOW_MAX):' \$B) && echo CF_GATE=\$(grep -cF 'if not (1 <= days <= DAY_WINDOW_MAX):' \$B) && echo AUDIT_LIMIT_NUM=\$(grep -cF 'default=100, lo=1, hi=500)' \$B) && echo MIN_COUNT_NUM=\$(grep -cF 'name=\"min_count\", default=3, lo=1)' \$B) && echo TRIGGER_NUM=\$(grep -cF 'name=\"trigger_id\", default=None, lo=1)' \$B) && echo AUDIT_SWALLOW_ASSIGN=\$(grep -cE '^ *limit = 100\$' \$B || true) && echo BARE_EXCEPT_VE=\$(grep -cE '^ *except ValueError:\$' \$B || true) && echo RAW_TRIGGER_INT=\$(grep -cE '^ *_lifecycle\(rt\)\.flag_false_positive, rule_id, int\(' \$B || true) && echo RAW_WMIN_PASS=\$(grep -cE '^ *body\.get\(\"window_minutes\"\),\$' \$B || true) && echo WMIN_NUM_GUARD=\$(grep -cF 'name=\"window_minutes\",' \$B || true) && echo AUDIT_TRAIL_GATE=\$(grep -cE '^ *if status in \(\"accepted\", \"rejected\"\):\$' \$B || true) && echo LEGACY_INT_BODY_PROSE=\$(grep -c 'int(body.get(' \$B || true) && echo LEGACY_INT_BODY_CODE=\$(grep -cE '^ *[a-z_]+ = int\(body\.get' \$B || true) && echo LEGACY_QUERY_INT=\$(grep -c 'int(request.query_params.get' \$B) && echo OFFLOAD_TO_THREAD_TOTAL=\$(grep -c 'await asyncio.to_thread(' \$B) && echo OFFLOAD_PREDICT=\$(grep -cF 'arrival, routine = await asyncio.to_thread(_predict)' \$B) && echo OFFLOAD_INFER=\$(grep -cF 'intents = await asyncio.to_thread(_infer)' \$B) && echo OFFLOAD_PROFILE=\$(grep -cF 'profile_text = await asyncio.to_thread(' \$B) && echo OFFLOAD_WRITE=\$(grep -cF 'await asyncio.to_thread(write_profile_atomic' \$B) && echo HEAVY_NESTED=\$(grep -cE 'def _(predict|infer)\(\)' \$B) && echo CA_WINDOW_DAYS=\$(grep -cF 'window_days = clamp_days(lookback_days)' \$C) && echo CA_DELTA_WD=\$(grep -cF 'delta = timedelta(days=window_days)' \$C) && echo CA_HALF_LIFE_WD=\$(grep -cF 'half_life = window_days / 2.0' \$C) && echo CA_HALF_LIFE_RAW=\$(grep -cF 'half_life = lookback_days / 2.0' \$C || true) && echo CA_DESC_WD=\$(grep -cF ', window_days)' \$C) && echo T63_TESTDEFS=\$(grep -c '^def test_' \$T) && echo T63_HEAVY_NAMES=\$(grep -cF 'HEAVY_SYNC_CALLEES' \$T) && echo T63_PROD_SHAPE=\$(grep -cF 'def test_production_read_shapes_are_asc_desc' \$T) && echo R5_BLINDSPOT=\$(grep -cF '盲区' \$O) && wc -l \$B \$C \$T \$O" > ${L}_sources.log 2>&1
echo SOURCES_RC=$?
cat ${L}_sources.log

# 格 P：PROBE —— 四族缺陷的运行时成对读数（临时库，不是生产库）。
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_probe.py" > ${L}_probe.log 2>&1
echo PROBE_RC=$?
cat ${L}_probe.log

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

# 定向 1：本批的判据链（task63 落点锁 + r5 卸载量具）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_task63_routes_input_boundary.py tests/test_vma_r5_event_loop_offload.py -q -p no:cacheprovider" > ${L}_targeted.log 2>&1
echo TARGETED_RC=$?
tail -3 ${L}_targeted.log

# 定向 2：本批改了 change_attribution 的两个出口，那条面上的既有判据链必须仍绿
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_change_attribution.py tests/test_p5c_counterfactual.py tests/test_audit_arrival_time.py tests/test_p5b_conditional_probability.py tests/test_vma_p32_day_bounds.py tests/test_feedback_pack.py -q -p no:cacheprovider" > ${L}_prevbatch.log 2>&1
echo PREVBATCH_RC=$?
tail -3 ${L}_prevbatch.log

# 定向 3：凡引用 behavior_routes / change_attribution 的测试（14 个）+ 上一批的落点锁（3 个）
ex "cd $L && PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_audit_arrival_time.py tests/test_change_attribution.py tests/test_feedback_pack.py tests/test_p5b_conditional_probability.py tests/test_p5c_counterfactual.py tests/test_vma121_intent_and_pii.py tests/test_vma_behavior_predictor_shapes.py tests/test_vma_dcd_20261005_rules.py tests/test_vma_gate_scanners_read_every_py.py tests/test_vma_p32_day_bounds.py tests/test_vma_p37_degrade_list_trace.py tests/test_vma_r5_event_loop_offload.py tests/test_vma_r7_seam_fixes.py tests/test_vma_task63_routes_input_boundary.py tests/test_vma_qb_param_landing.py tests/test_vma_insights_callsite_binding.py tests/test_vma_insights_facade_dead_tools.py tests/test_quality_gates.py -q -p no:cacheprovider" > ${L}_touched.log 2>&1
echo TOUCHED_RC=$?
tail -3 ${L}_touched.log

# 门 3（自证）：两档变异各咬各的锁，容器 Python 3.11 口径
#   MUT63 = 本批 22 条（_num 四处 / 挖矿族四处 / causal 两处 / bad-cases 一处 /
#           卸载三处 / half_life 一处 / intent 上下界两处 / 第四族三处 /
#           第五族三处 M20 原样透传 + M21 审核不留痕 + M22 冷却位写死）
#   MUT13 = 上一批 12 条，本批没碰 insights 与三支量具 ⇒ 读数应与 run13d 一字不差。
ex "cd $L && MUT_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut63.py" > ${L}_mut63.log 2>&1
echo MUT63_RC=$?
grep -E '^(NOTHING|M[0-9]+ |    RC=|    异常|    无汇总行|MUT_COUNT|MUTATION_BAD|restored_identical|.*锚点)' ${L}_mut63.log
ex "cd $L && MUT_ROOT=$L PYLIBS=/tmp/pylibs PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut13.py" > ${L}_mut13.log 2>&1
echo MUT13_RC=$?
grep -E '^(N[0-9]+ |    RC=|    异常|    无汇总行|    [0-9]+ (passed|failed)|MUTATION_BAD|restored_identical|.*锚点命中)' ${L}_mut13.log

echo REMOTE_BATCH_RC=0
