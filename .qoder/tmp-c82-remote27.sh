#!/usr/bin/env bash
# run27（20261008 档，任务表 #84：第二十轮 MA-36/MA-37 —— ACP 会话属主护栏第四个入口）
#
# ── 为什么必须整跑，不能沿用 run26 ──
#   run26 之后落了 `af3e0fe`，动的是**生产面一处 + 新测试一个**：
#   `acp_server.py`（`SessionStore.new` 的属主改写、`M_SESSION_NEW` 的冲突出口、`M_PROMPT` 的第四道
#   属主校验与新建带属主）+ `tests/test_acp_round20_owner_isolation.py`（8 条回归锁 + 1 条 AST 入口对等门）。
#   这是安全隔离缺陷，不是功能性缺陷 ⇒ run26 的聚合摘要作废（树变了），且 run26 只跑到 PROBE 格就断在
#   量具自己的路径缺陷上（`__file__` 算出的根在容器里指向 `/src`），POST 二次哈希那格根本没跑。
#   ⇒ 这一档既是新代码的权威门，也是 run26 未走完那段（RUNTIME + 变异腿 + POST）的补跑。
#
# ── 这一档新增的格 ──
#   0C4 ANCHOR20：MA-36/37 的七枚形状锚点（异常类、冲突抛出、`new()` 判据、prompt 校验行、
#              新建带属主行、路由层 except 出口、session.new 传参）+ check_owner 的 def/调用点计数（1/4）。
#   0C5 LOCKS20：新测试文件的用例数（8）与入口对等门的名字（名字不在 = 门没落）。
#   定向 D：ACP 主战场三件（本轮新锁 + 跨属主拒绝 + 会话面原有）。
#   格 5 MUTR20：6 条腿（本机 3.13 档 `.qoder/tmp-r20-mut-local1.out` 全杀：killed=6 survived=0
#              invalid=0，P-0 28 passed，WT_UNTOUCHED=True）。这一档要容器 3.11 的同一份判定。
#   POST：二次哈希（与格 0 逐字同）+ 原样再跑 ACP 三件 ⇒ 变异腿没碰到被测树。
#
# ── run27 沿用的格 ──
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
# 教训照旧在册：变异腿只在一次性副本树里跑（三份 harness 都写死 DST 在 $L 之外）；
# 权威门在飞期间不许动工作树；失败名单 grep 用 `^_{3,} `；CR 一律按字节量；
# 远端 `ex "…"` 内层双引号必须 `\"`，且 python 单引号串里不许再出现单引号；
# 量具自己取根一律走环境变量（run26 的 PROBE 格就是栽在 `__file__` 算根上，容器里指到 /src）。
set -uo pipefail
SNAP=${1:-c82snap27}
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

# 格 0C4：ANCHOR20 —— MA-36/MA-37 的七枚形状 + check_owner 的 def/调用点计数
ex "cd $L && echo ACP_CONFLICT_CLS=\$(grep -cF 'class SessionOwnerConflict(Exception):' src/memory_agent/acp_server.py); echo ACP_RAISE=\$(grep -cF 'raise SessionOwnerConflict(sid)' src/memory_agent/acp_server.py); echo ACP_ROUTE_EXCEPT=\$(grep -cF 'except SessionOwnerConflict:' src/memory_agent/acp_server.py); echo ACP_NEW_PREV=\$(grep -cF 'if prev is not None and prev.get(\"owner_token\") != owner_token:' src/memory_agent/acp_server.py); echo ACP_PROMPT_CHECK=\$(grep -cF 'if requested and not _STORE.check_owner(requested, _owner):' src/memory_agent/acp_server.py); echo ACP_NEW_OWNER=\$(grep -cF 'session_id = requested or _STORE.new(owner_token=_owner)' src/memory_agent/acp_server.py); echo ACP_NEWARG=\$(grep -cF 'sid = _STORE.new(params.get(\"sessionId\"), owner_token=_owner)' src/memory_agent/acp_server.py); echo ACP_CHECK_DEF=\$(grep -cF 'def check_owner(self' src/memory_agent/acp_server.py); echo ACP_CHECK_CALLS=\$(grep -cF '_STORE.check_owner(' src/memory_agent/acp_server.py)"
echo ANCHOR20_RC=$?

# 格 0C5：LOCKS20 —— 本轮新测试的用例数与入口对等门的名字
ex "cd $L && echo L20_DEFS=\$(grep -c 'def test_' tests/test_acp_round20_owner_isolation.py); echo L20_PARITY=\$(grep -c 'def test_every_entry_that_reads_session_id_enforces_ownership():' tests/test_acp_round20_owner_isolation.py); echo L20_CROSS=\$(grep -c 'def test_prompt_cross_owner_is_denied_and_writes_nothing():' tests/test_acp_round20_owner_isolation.py); echo L20_TRIM=\$(grep -c 'def test_prompt_denies_a_session_whose_store_entry_is_gone_but_history_remains():' tests/test_acp_round20_owner_isolation.py); echo L20_UNAUTH=\$(grep -c 'def test_prompt_from_an_unauthenticated_principal_is_denied_for_existing_session():' tests/test_acp_round20_owner_isolation.py)"
echo LOCKS20_RC=$?

# 格 0D：HARNESS —— 两份量具的特征锚点必须在
ex "cd $L && echo M81_LEGS=\$(grep -cE '^[[:space:]]+\\(\"M[0-9]+\", ' ${L}_mut81.py); echo M82_LEGS=\$(grep -cE '^[[:space:]]+\\(\"N[0-9]+\", ' ${L}_mut82.py); echo MR20_LEGS=\$(grep -cE '^[[:space:]]+\\(\"P[0-9]+\", ' ${L}_mutr20.py); echo M81_OUTENV=\$(grep -cF 'MUT_OUT' ${L}_mut81.py); echo M82_OUTENV=\$(grep -cF 'MUT_OUT' ${L}_mut82.py); echo MR20_OUTENV=\$(grep -cF 'MUT_OUT' ${L}_mutr20.py); echo M81_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut81.py); echo M82_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mut82.py); echo MR20_AST=\$(grep -cF 'ast.parse(mutated' ${L}_mutr20.py); echo M81_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut81.py); echo M82_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mut82.py); echo MR20_UNCHANGED=\$(grep -cF 'WT_UNTOUCHED=' ${L}_mutr20.py); echo MR20_ROOTENV=\$(grep -cF 'MUT_ROOT' ${L}_mutr20.py)"
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

# 定向 D：#84 主战场（ACP 会话面：本轮 8 条新锁 + 跨属主拒绝 + 原有会话协议件）
ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_acp_round20_owner_isolation.py tests/test_acp_session_cross_owner_denied.py tests/test_acp_server.py -q -rf" > ${L}_acpD.log 2>&1
echo ACP_D_RC=$?
tail -4 ${L}_acpD.log

# RUNTIME：裁 B 四格矩阵，真解释器真 import
ex "cd $L && PROBE_ROOT=$L PYTHONPATH=$L/src:/tmp/pylibs python ${L}_probe.py" > ${L}_probe.log 2>&1
echo PROBE_RC=$?
cat ${L}_probe.log

# 格 3/4 MUT81+MUT82：**这一档不重跑**，理由当场可核（不是"沿用上一轮结论"那种含糊）：
#   run26 正在同一容器里跑这两档（22 + 14 腿），它的快照树 = commit `fdad983`，
#   而 `git diff --name-only fdad983 HEAD` 实测只有两行：
#     src/memory_agent/acp_server.py、tests/test_acp_round20_owner_isolation.py
#   ⇒ 这两档的 36 条腿打的都是 auth/config/auth_routes/app/announcer/runtime/mqtt_bridge/insights 面，
#     与本轮改动**无文件交集**；run27 的全量回归 + POST 二次哈希负责证明"没串味"。
#   登记口径：#81/#82 的容器变异读数出自 **run26**，MA-36/37 的出自 **run27**，两份并立不混引。
echo M81_SKIPPED_IN_RUN27=1
echo M82_SKIPPED_IN_RUN27=1

# 格 5 MUTR20：6 条腿（本机 3.13 档全杀；这一档取容器 3.11 的同一份判定。控制腿 P-0 必须先绿）
ex "cd $L && MUT_ROOT=$L MUT_DST=/tmp/${SNAP}_mr20 MUT_OUT=/tmp/${SNAP}_mr20.log PYTHONPATH=$L/src:/tmp/pylibs JWT_SECRET=ci-test python ${L}_mutr20.py all" > ${L}_mr20_run.log 2>&1
echo MR20_RC=$?
# 注意：MUT_OUT 是 python 在**容器内**写的，主机侧 grep 找不到文件（run26 就是栽在这里，
# 三条 grep 全部 "No such file or directory"，读数只能事后从容器里取）。读数一律走 ex。
ex "grep -E 'KILLED|SURVIVED|INVALID|WT_UNTOUCHED|^\[P-0\]|^totals' ${L}_mr20.log | tail -12"
ex "grep -c SURVIVED ${L}_mr20.log" | sed 's/^/MR20_SURVIVED_COUNT=/'
ex "grep -c INVALID ${L}_mr20.log" | sed 's/^/MR20_INVALID_COUNT=/'
ex "tail -3 ${L}_mr20_run.log"

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

ex "cd $L && PYTHONPATH=$L:$L/src:/tmp/pylibs JWT_SECRET=ci-test python -m pytest tests/test_acp_round20_owner_isolation.py tests/test_acp_session_cross_owner_denied.py tests/test_acp_server.py -q" > ${L}_post_acp.log 2>&1
echo POST_ACP_RC=$?
tail -3 ${L}_post_acp.log

date -Iseconds
echo REMOTE_DONE
