#!/usr/bin/env bash
# run26（20261008 档，任务表 #80/#81/#82：DCD 20261007 §三 承诺键门 / §四 vendored 0.3.2 + 裁 B / §五 登录限速乙+丙）
#
# ── 为什么必须整跑，不能沿用 run25 ──
#   run25 之后落了四个提交（fa384fd / 313c6cc / e09238d / fdad983），动的是**生产面四处**：
#   `auth.py`（桶键口径 + 全局预算）、`config.py`（两枚新键 + env 映射）、`api/auth_routes.py` 与
#   `app.py`（两条入口共用一个口径）、`announcer.py` + `runtime.py`（播报两条路 + 桥注入）、
#   `Dockerfile` + `vendor/`（wheel 换版）。树变了 ⇒ run25 的聚合摘要作废，快照摘要重取，
#   pyflakes 与全量回归重新走，两档变异档（mut81 22 腿、mut82 14 腿）在容器 3.11 重跑一遍。
#
# ── 这一档的格 ──
#  -1 TOOLCHAIN：/tmp/pylibs 在容器可写层，recreate 即清空 ⇒ 永远排第一。
#    0 SNAP：本机与容器跑同一份 `_hashes.py`，聚合摘要逐字对账（证明"容器里跑的就是这棵树"）。
#   0C ANCHOR81：§五 裁乙+丙的六枚形状锚点（开关、末位 XFF、预算 60、退避 60、两条入口同口径、
#              两枚 env 映射）——每条都=1，"少一条"就是实现掉了一格。
#   0C2 ANCHOR82：§四 裁 B 的七枚形状锚点（两键就绪式、回落门槛、优先级那一行、trace_id 生成、
#              _via_ha/_via_inbox/inbox_ready 三个方法）+ 运行面的 `mqtt=self.mqtt,` + 桥侧 `publish_speak` 定义。
#   0C3 LOCKS：本批四把门的用例数（def test_ 口径）与五条新用例的名字（名字不在=门没落）。
#   0D HARNESS：两份量具的特征锚点（腿数 22/14、MUT_OUT 环境变量、ast.parse 语法护栏、WT_UNTOUCHED 自证）。
#    1/2 门：pyflakes（口径 src/memory_agent，基线只准减）+ 全量回归（容器 3.11 权威口径）。
#   定向 A/B/C：三批各自的主战场（#81 鉴权三件 / #82 播报与联动码 / #80 承诺键门与门面契约）。
#   RUNTIME：裁 B 的四格就绪矩阵在真解释器下走一遍——AST 锁只证明形状，这一格证明**真按优先级分流**
#            （HA 齐→直发且收件箱静默；缺 target→回落且带 32 位 trace_id；两条都不通→如实 False）。
#   3 MUT81：22 条腿（本机 3.13 档已读全：M-0 206/207 passed，20 腿首跑即杀，M01/M17 补用例后转杀，
#            `.qoder/tmp-c81-mut81.out` + `tmp-c81-mutv2.out`）。这一档要的是容器 3.11 的同一份判定。
#   4 MUT82：14 条腿（本机档 `.qoder/tmp-c82-mut-local1.out` 杀 10 活 4、
#            `tmp-c82-mutv2.out` 补跑四条全杀，M-0 70 passed）。
#   POST：二次哈希（与格 0 逐字同）+ 原样再跑 #82 主战场 ⇒ 变异腿没碰到被测树。
#
# 教训照旧在册：变异腿只在一次性副本树里跑（两份 harness 都写死 DST 在 $L 之外）；
# 权威门在飞期间不许动工作树；失败名单 grep 用 `^_{3,} `；CR 一律按字节量；
# 远端 `ex "…"` 内层双引号必须 `\"`，且 python 单引号串里不许再出现单引号。
set -uo pipefail
SNAP=${1:-c82snap20261008}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo REMOTE_HEADLESS_OK
docker ps --format '{{.Names}}' | grep -c '^memory-agent$' | sed 's/^/CONTAINER_PRESENT=/'

ex "cd $L && PYTHONPATH=/tmp/pylibs python -c 'import pytest, pyflakes; print(\"PYTEST=\" + pytest.__version__); print(\"PYFLAKES=\" + pyflakes.__version__)' && python -V"
echo TOOLCHAIN_RC=$?

# 格 0：SNAP —— 容器树 = 本机树？逐文件按字节 md5，聚合摘要与本机档对逐字。
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_snap.log 2>&1
echo SNAP_RC=$?
cat ${L}_snap.log

# 格 0C：ANCHOR81 —— §五 裁乙+丙的六枚形状
ex "cd $L && echo AUTH_RESOLVE=\$(grep -cF 'def resolve_client_ip(' src/memory_agent/auth.py); echo AUTH_BUDGET=\$(grep -cF '_GLOBAL_FAIL_BUDGET = 60' src/memory_agent/auth.py); echo AUTH_BACKOFF=\$(grep -cF '_GLOBAL_BACKOFF_SECONDS = 60' src/memory_agent/auth.py); echo AUTH_XFF_LAST=\$(grep -cF 'return parts[-1] if parts else peer_ip' src/memory_agent/auth.py); echo AUTH_SWITCH=\$(grep -cF 'if not trust_proxy:' src/memory_agent/auth.py); echo AUTH_NETS_FN=\$(grep -cF 'def _trusted_proxy_networks(' src/memory_agent/auth.py); echo CFG_TRUST_DEFAULT=\$(grep -cF 'trust_proxy: bool = False' src/memory_agent/config.py); echo CFG_CIDRS_DEFAULT=\$(grep -cF 'trusted_proxy_cidrs: str = \"\"' src/memory_agent/config.py); echo CFG_ENV_TRUST=\$(grep -cF 'MA_TRUST_PROXY' src/memory_agent/config.py); echo CFG_ENV_CIDRS=\$(grep -cF 'MA_TRUSTED_PROXY_CIDRS' src/memory_agent/config.py); echo ROUTES_TRUST=\$(grep -cF 'trust_proxy=cfg.trust_proxy,' src/memory_agent/api/auth_routes.py); echo BASIC_RESOLVE=\$(grep -cF 'trusted_proxy_cidrs=config.trusted_proxy_cidrs,' src/memory_agent/app.py)"
echo ANCHOR81_RC=$?

# 格 0C2：ANCHOR82 —— §四 裁 B 的形状 + 运行面接线 + 桥侧定义
ex "cd $L && echo ANN_HA_FN=\$(grep -cF 'def _via_ha(' src/memory_agent/announcer.py); echo ANN_INBOX_FN=\$(grep -cF 'def _via_inbox(' src/memory_agent/announcer.py); echo ANN_READY_PROP=\$(grep -cF 'def inbox_ready(' src/memory_agent/announcer.py); echo ANN_PRIORITY_LINE=\$(grep -cF 'return self._via_inbox(msg) if not self.enabled else self._via_ha(msg)' src/memory_agent/announcer.py); echo ANN_GATE_LINE=\$(grep -cF 'if not self.enabled and not self.inbox_ready:' src/memory_agent/announcer.py); echo ANN_TRACEID=\$(grep -cF 'tid = uuid.uuid4().hex' src/memory_agent/announcer.py); echo ANN_ENABLED_LINE=\$(grep -cF 'self.enabled = self.switch and bool(self.tts_entity) and bool(self.target)' src/memory_agent/announcer.py); echo RT_MQTT_INJECT=\$(grep -cF 'mqtt=self.mqtt,' src/memory_agent/runtime.py); echo RT_CONSTRUCT=\$(grep -cF 'announcer = Announcer(' src/memory_agent/runtime.py); echo MB_PUBLISH_SPEAK=\$(grep -cF 'def publish_speak(' src/memory_agent/mqtt_bridge.py)"
echo ANCHOR82_RC=$?

# 格 0C3：LOCKS —— 本批四把门的用例数与五条新用例的名字
ex "cd $L && echo L_RATE_DEFS=\$(grep -c 'def test_' tests/test_vma_login_rate_limit.py); echo L_ANN_DEFS=\$(grep -c 'def test_' tests/test_announcer.py); echo L_CODE_DEFS=\$(grep -c 'def test_' tests/test_vma_dcd_20261006_linkage_codes.py); echo L_PROSE_DEFS=\$(grep -c 'def test_' tests/test_tool_prose_promised_keys.py); echo L_SWITCH_GATE=\$(grep -c 'def test_the_switch_is_the_master_gate_even_with_a_registered_peer():' tests/test_vma_login_rate_limit.py); echo L_WIRED=\$(grep -c 'def test_the_production_announcer_is_wired_to_the_bridge():' tests/test_vma_dcd_20261006_linkage_codes.py); echo L_HA_REJECT=\$(grep -c 'def test_ha_rejection_is_reported_as_not_announced():' tests/test_announcer.py); echo L_HA_GARBAGE=\$(grep -c 'def test_ha_non_dict_reply_is_not_announced_and_does_not_escape():' tests/test_announcer.py); echo L_CALLER_LOCK=\$(grep -c 'def test_publish_speak_production_caller_is_the_announcer_fallback():' tests/test_vma_dcd_20261006_linkage_codes.py)"
echo LOCKS_RC=$?

# 格 0D：HARNESS —— 两份量具的特征锚点必须在
ex "cd $L && echo M81_LEGS=\$(grep -cE '^[[:space:]]+\\(\"M[0-9]+\", ' ${L}_mut81.py); echo M82_LEGS=\$(grep -cE '^[[:space:]]+\\(\"N[0-9]+\", ' ${L}_mut82.py); echo M81_OUTENV=\$(grep -cF 'MUT_OUT' ${L}_mut81.py); echo M82_OUTENV=\$(grep -cF 'MUT_OUT' ${L}_mut82.py); echo M81_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut81.py); echo M82_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut82.py); echo M81_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut81.py); echo M82_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut82.py)"
echo HARNESS_RC=$?

# 门 1：pyflakes（口径 src/memory_agent；基线只准减）
ex "cd $L && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh" > ${L}_gate.log 2>&1
echo GATE_RC=$?
tail -4 ${L}_gate.log

# 门 2：全量回归（容器 Python 3.11 是本仓权威口径）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs" > ${L}_suite.log 2>&1
echo SUITE_RC=$?
tail -6 ${L}_suite.log
grep -E '^_{3,} |^FAILED ' ${L}_suite.log | head -10; echo SUITE_FAILNAMES_RC=$?

# 定向 A：#81 主战场（限速三件 + 共享尺子的鉴权件）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_login_rate_limit.py tests/test_vma_a3_p22_auth_loop_blocking.py tests/test_vma_phase2_batch3_shared_ruler.py tests/test_wo_ma_012_g1_security.py -q -rf" > ${L}_authA.log 2>&1
echo AUTH_A_RC=$?
tail -4 ${L}_authA.log

# 定向 B：#82 主战场（播报两条路 + 联动码 + 载荷 provenance）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_announcer.py tests/test_vma_dcd_20261006_linkage_codes.py tests/test_vma_dcd_20261002_payload.py tests/test_perception_ingest.py tests/test_livingroom_ai.py -q -rf" > ${L}_annB.log 2>&1
echo ANN_B_RC=$?
tail -4 ${L}_annB.log

# 定向 C：#80 主战场（承诺键门 + 门面契约）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_tool_prose_promised_keys.py tests/test_insights_facade_contract.py tests/test_vma_promised_keys_runtime_contract.py -q -rf" > ${L}_proseC.log 2>&1
echo PROSE_C_RC=$?
tail -4 ${L}_proseC.log

# RUNTIME：裁 B 四格矩阵，真解释器真 import
ex "cd $L && PROBE_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs python ${L}_probe.py" > ${L}_probe.log 2>&1
echo PROBE_RC=$?
cat ${L}_probe.log

# 格 3 MUT81：22 条腿（控制腿 M-0 必须先绿，否则整表读数无信息量）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/${SNAP}_m81 MUT_OUT=/tmp/${SNAP}_m81.log PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut81.py all" > ${L}_m81_run.log 2>&1
echo M81_RC=$?
grep -E 'KILLED|SURVIVED|INVALID|WT_UNTOUCHED|^M-0|^totals' ${L}_m81.log | tail -28
grep -c 'SURVIVED' ${L}_m81.log | sed 's/^/M81_SURVIVED_COUNT=/'
grep -c 'INVALID' ${L}_m81.log | sed 's/^/M81_INVALID_COUNT=/'

# 格 4 MUT82：14 条腿
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/${SNAP}_m82 MUT_OUT=/tmp/${SNAP}_m82.log PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mut82.py all" > ${L}_m82_run.log 2>&1
echo M82_RC=$?
grep -E 'KILLED|SURVIVED|INVALID|WT_UNTOUCHED|^M-0|^totals' ${L}_m82.log | tail -20
grep -c 'SURVIVED' ${L}_m82.log | sed 's/^/M82_SURVIVED_COUNT=/'
grep -c 'INVALID' ${L}_m82.log | sed 's/^/M82_INVALID_COUNT=/'

# 还原自证：二次哈希（与格 0 逐字同）+ 原样再跑 #82 主战场
ex "cd $L && python ${L}_hashes.py ${L}_filelist.txt" > ${L}_post_hashes.log 2>&1
echo POST_HASHES_RC=$?
cat ${L}_post_hashes.log

ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_announcer.py tests/test_vma_dcd_20261006_linkage_codes.py -q" > ${L}_post_ann.log 2>&1
echo POST_ANN_RC=$?
tail -3 ${L}_post_ann.log

ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_vma_login_rate_limit.py -q" > ${L}_post_auth.log 2>&1
echo POST_AUTH_RC=$?
tail -3 ${L}_post_auth.log

date -Iseconds
echo REMOTE_DONE
