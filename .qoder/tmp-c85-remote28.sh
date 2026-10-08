#!/usr/bin/env bash
# run28（20261008 档，任务表 #85：2期 第十一轮~第十九轮 MA-25/26/27/28/30/31/33/35 八条落码）
#
# ── 为什么必须整跑，不能沿用 run27 ──
#   run27 之后落了 `800c17b`，动的是**生产面七处 + 门禁台账一条 + 新测试一个**：
#   `llm_client.py`（MA-25 关旧 provider）、`mcp_server.py`（MA-26 版本基数 / MA-33 真源同步 /
#   MA-28 门面假回执删除）、`agent_memory.py`（MA-27 回显口径）、`signal_learning.py`（MA-28 判据并入本体）、
#   `api/nr_routes.py`（MA-30 如实 501）、`runtime.py`（MA-31 外壳重启 / MA-35 常驻清理）、
#   `config.py` + `store.py`（MA-35 间隔键与取数）、`tests/test_rounds11_19_ma25_35_fixes.py`（28 锁）、
#   `tests/test_vma_phase2_batch3_shared_ruler.py`、`.gates-baseline.txt`（−1 行）。
#   树变了 ⇒ run27 的聚合摘要作废，快照摘要重取，pyflakes 与全量回归重走。
#
# ── 三档变异的取舍（口径当场可核，不是"沿用上一轮结论"）──
#   MUT85 11 腿：本轮新档，容器 3.11 的第一份判定 ⇒ 必跑。
#   MUT81 22 腿：靶面含 `config.py`，本轮 diff 里有它 ⇒ 有交集，重跑。
#   MUT82 14 腿：靶面含 `runtime.py`，本轮 diff 里有它 ⇒ 有交集，重跑。
#   MUTR20 6 腿：靶面只有 `acp_server.py`，与本轮 diff **零交集**（driver 尺4 现读 `git diff --name-only`），
#                且本轮全量回归里 ACP 三件照常跑 ⇒ 显式跳过，读数并立不混引（MA-36/37 出自 run27）。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账。
#   0C ANCHOR85：八条修复各自的形状锚点（每条 =1；门面假回执那格要求 =0，即"改回去了就红"）。
#   0C2 LOCKS85：新锁文件用例数（28）+ 七条关键用例的名字（名字不在 = 门没落）+ 基线台账 −1 的自证。
#   0D HARNESS：c85 量具特征锚点（11 腿、MUT_ROOT/MUT_OUT、ast.parse、WT_UNTOUCHED、to_eol 行尾适配）。
#    1/2 门：pyflakes（基线只准减）+ 全量回归（容器 3.11 权威口径）。
#   定向 E：本轮主战场（新锁文件 + 门禁 + 被改动过的共享尺子）。
#   定向 F：本轮八个落点的既有面（signal_learning / agent_memory / llm 关闭 / 长跑 / purge / 反馈接线）。
#   定向 G：mcp_server 门面与承诺键（MA-26/28/33 都改在这个文件里）。
#   3/4/5 MUT85 / MUT81 / MUT82：三档 47 腿，一次性副本树。
#   POST：二次哈希（与格 0 逐字同）+ 原样再跑定向 E + ACP 三件（r20 跳过那格的旁证）。
#
# 教训照旧在册：变异腿只在一次性副本树里跑；权威门在飞期间不动工作树；失败名单 grep 用 `^_{3,} `；
# CR 一律按字节量；远端 `ex "…"` 内层双引号必须 `\"`，锚点一律挑不含引号的那一截；
# 量具取根走环境变量；容器里的读数只能从容器里 grep（MUT_OUT 是容器内写的）。
set -uo pipefail
SNAP=${1:-c85snap28}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0C：ANCHOR85 —— 八条修复的形状锚点
ex "cd $L && echo LLM_STALE_LOOP=\$(grep -cF 'for p in stale:' src/memory_agent/llm_client.py); echo LLM_KEEP_STALE=\$(grep -cF 'stale = self.providers' src/memory_agent/llm_client.py); echo LLM_CLOSE_LOG=\$(grep -cF '旧 provider 关闭失败' src/memory_agent/llm_client.py); echo MCP_PREV_MAX=\$(grep -cF 'prev_version = max(prev_version,' src/memory_agent/mcp_server.py); echo MCP_SEED_FN=\$(grep -cF 'def seed_builtin_skills(' src/memory_agent/mcp_server.py); echo MCP_SEED_CMP=\$(grep -cF 'if disk_version >= bundled_version:' src/memory_agent/mcp_server.py); echo MCP_FAKE_RETURN=\$(grep -cF '参数校验通过' src/memory_agent/mcp_server.py); echo SIG_ENUM_GATE=\$(grep -cF 'if exclusion_type not in EXCLUSION_TYPES:' src/memory_agent/signal_learning.py); echo AGENT_SCOPE_ECHO=\$(grep -cF 'member_scope' src/memory_agent/agent_memory.py); echo NR_NOTIMPL=\$(grep -cF '未实现：本服务不执行动作' src/memory_agent/api/nr_routes.py); echo NR_501=\$(grep -cF '501,' src/memory_agent/api/nr_routes.py); echo RT_DIARY_BACKOFF=\$(grep -cF 'backoff = min(backoff * 2, 3600)' src/memory_agent/runtime.py); echo RT_RETENTION_FN=\$(grep -cF 'async def _run_retention_cleanup(self)' src/memory_agent/runtime.py); echo RT_RETENTION_SHORT=\$(grep -cF 'await asyncio.sleep(min(interval, 600))' src/memory_agent/runtime.py); echo CFG_RETENTION_KEY=\$(grep -cF 'data_retention_interval_seconds: int = 86400' src/memory_agent/config.py)"
echo ANCHOR85_RC=$?

# 格 0C2：LOCKS85 —— 用例数、七条门的名字、基线台账 -1 的自证
ex "cd $L && echo L85_DEFS=\$(grep -c 'def test_' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA25=\$(grep -c 'def test_reconfigure_closes_the_providers_it_is_replacing():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA26=\$(grep -c 'def test_save_skill_version_never_regresses():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA27=\$(grep -c 'public档不谎称_all():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA28=\$(grep -c 'def test_mcp_facade_no_longer_short_circuits_dry_run():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA30=\$(grep -c 'def test_execute_action_returns_not_implemented():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA31=\$(grep -c 'def test_self_diary_round_reraises_to_the_wrapper():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA33=\$(grep -c 'def test_seed_keeps_a_higher_agent_iterated_version():' tests/test_rounds11_19_ma25_35_fixes.py); echo L85_MA35=\$(grep -c 'def test_retention_task_is_registered_at_startup():' tests/test_rounds11_19_ma25_35_fixes.py); echo BASE_LINES=\$(wc -l < .gates-baseline.txt); echo BASE_STALE_TEACH=\$(grep -cF 'mcp_server.py#fake-ok-const#_build_server.teach_signal' .gates-baseline.txt)"
echo LOCKS85_RC=$?

# 格 0D：HARNESS —— 三份量具的特征锚点
ex "cd $L && echo M85_LEGS=\$(grep -cE '^[[:space:]]+\\(\"Q[0-9]+\", ' ${L}_mut85.py); echo M81_LEGS=\$(grep -cE '^[[:space:]]+\\(\"M[0-9]+\", ' ${L}_mut81.py); echo M82_LEGS=\$(grep -cE '^[[:space:]]+\\(\"N[0-9]+\", ' ${L}_mut82.py); echo M85_OUTENV=\$(grep -cF 'MUT_OUT' ${L}_mut85.py); echo M85_ROOTENV=\$(grep -cF 'MUT_ROOT' ${L}_mut85.py); echo M85_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut85.py); echo M85_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut85.py); echo M85_TOEOL=\$(grep -cF 'def to_eol(' ${L}_mut85.py); echo M81_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut81.py); echo M82_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut82.py); echo M81_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut81.py); echo M82_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut82.py)"
echo HARNESS_RC=$?

# 门 1：pyflakes
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 门 2：全量回归（容器 Python 3.11 权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?

# 定向 E：本轮主战场（新锁 28 条在容器里应当真跑，本机 skip 的那条 MA-26 在这里必须有读数）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_rounds11_19_ma25_35_fixes.py tests/test_quality_gates.py tests/test_vma_phase2_batch3_shared_ruler.py -q -rf" > ${L}_batchE.log 2>&1
echo BATCH_E_RC=$?
tail -5 ${L}_batchE.log
grep -E '^FAILED ' ${L}_batchE.log | head -5

# 定向 F：八个落点的既有面（改这些函数时别把老契约碰坏）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_signal_learning.py tests/test_agent_memory.py tests/test_bug1_shutdown_llm_close.py tests/test_vma_r6_longrun_fixes.py tests/test_db_purge_and_indexes.py tests/test_ma016_member_id_filter.py tests/test_vma121_feedback_pii_wiring.py tests/test_vma_p32_day_bounds.py -q -rf" > ${L}_facesF.log 2>&1
echo FACES_F_RC=$?
tail -4 ${L}_facesF.log

# 定向 G：mcp_server 门面与承诺键（MA-26/28/33 三条都改在这个文件里）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_tool_prose_promised_keys.py tests/test_insights_facade_contract.py tests/test_vma_promised_keys_runtime_contract.py -q -rf" > ${L}_proseG.log 2>&1
echo PROSE_G_RC=$?
tail -4 ${L}_proseG.log

# 格 3 MUT85：11 腿（控制腿 Q-0 必须先绿）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/${SNAP}_m85 MUT_OUT=/tmp/${SNAP}_m85.log PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut85.py all" > ${L}_m85_run.log 2>&1
echo M85_RC=$?
ex "grep -E 'KILLED|SURVIVED|INVALID|WT_UNTOUCHED|^\[Q-0\]|^totals' ${L}_m85.log | tail -14"
ex "grep -c SURVIVED ${L}_m85.log" | sed 's/^/M85_SURVIVED_COUNT=/'
ex "grep -c INVALID ${L}_m85.log" | sed 's/^/M85_INVALID_COUNT=/'
ex "tail -3 ${L}_m85_run.log"

# 格 4 MUT81：22 腿（靶面 config.py 与本轮 diff 有交集 ⇒ 重跑）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/${SNAP}_m81 MUT_OUT=/tmp/${SNAP}_m81.log PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut81.py all" > ${L}_m81_run.log 2>&1
echo M81_RC=$?
ex "grep -E 'SURVIVED|INVALID|WT_UNTOUCHED|^totals' ${L}_m81.log | tail -8"
ex "grep -c SURVIVED ${L}_m81.log" | sed 's/^/M81_SURVIVED_COUNT=/'
ex "grep -c INVALID ${L}_m81.log" | sed 's/^/M81_INVALID_COUNT=/'
ex "grep -c KILLED ${L}_m81.log" | sed 's/^/M81_KILLED_COUNT=/'

# 格 5 MUT82：14 腿（靶面 runtime.py 与本轮 diff 有交集 ⇒ 重跑）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/${SNAP}_m82 MUT_OUT=/tmp/${SNAP}_m82.log PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut82.py all" > ${L}_m82_run.log 2>&1
echo M82_RC=$?
ex "grep -E 'SURVIVED|INVALID|WT_UNTOUCHED|^totals' ${L}_m82.log | tail -8"
ex "grep -c SURVIVED ${L}_m82.log" | sed 's/^/M82_SURVIVED_COUNT=/'
ex "grep -c INVALID ${L}_m82.log" | sed 's/^/M82_INVALID_COUNT=/'
ex "grep -c KILLED ${L}_m82.log" | sed 's/^/M82_KILLED_COUNT=/'

# r20 那格显式跳过，理由在 driver 尺4 现读的文件交集里；这里只留登记位
echo MR20_SKIPPED_IN_RUN28=1

# 还原自证：二次哈希 + 原样再跑定向 E + ACP 三件
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log

ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_rounds11_19_ma25_35_fixes.py -q" > ${L}_post_batch.log 2>&1
echo POST_BATCH_RC=$?
tail -3 ${L}_post_batch.log

ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_acp_round20_owner_isolation.py tests/test_acp_session_cross_owner_denied.py tests/test_acp_server.py -q" > ${L}_post_acp.log 2>&1
echo POST_ACP_RC=$?
tail -3 ${L}_post_acp.log

date -Iseconds
echo REMOTE_DONE
