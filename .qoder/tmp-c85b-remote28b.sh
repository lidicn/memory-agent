#!/usr/bin/env bash
# run28b（20261008 档，任务表 #85 的量具补格）——
# **只读**重测 run28 里两格"错锚"的读数：不跑测试、不改任何文件、不产生产物。
#
# ── 为什么要单独一档，而不是"沿用 run28 的数然后解释一下" ──
#   run28 的两格是我自己写的 grep 口径错了，不是产品少了一条：
#   1) LOCKS85 用 `def test_xxx():` 去数锁名。八条里有四条的签名带参数（`monkeypatch`），
#      真实写法是 `def test_xxx(monkeypatch):` ⇒ `():` 口径命中 0。
#      L85_MA25/26/30/33 = 0 会被读成"四条锁没落"，而本机 `grep -c 'def test_xxx('` 各 =1。
#   2) ANCHOR85 的 MCP_FAKE_RETURN 用 `grep -cF '参数校验通过'`，把 `mcp_server.py:1749`
#      那行**解释性注释**也算进命中（=1）。代码口径（剔掉整行注释）= 0，那才是"假 ok 短路已摘"的读数。
#      这是"名字锚点罩住 docstring/注释"那一族的第三种形态：注释里引用被删掉的旧代码。
#   ⇒ 两格用**新口径**当场重测，run28 的旧口径读数原样保留、两档并立，不混引、不改写。
#
# ── 这一档只允许读 ──
#   全程只有 grep / wc / ls。run28 仍在飞（全量回归 + 47 条变异腿）：它的被测树是
#   /tmp/c85snap28 快照，变异腿写在 $L 之外的 MUT_DST ⇒ 这一档的读不会串味。
#   ex 串内的路径一律**写字面全路径**（lint tmp-c37 的规则：只许 $L/$SNAP 裸展开）。
set -uo pipefail
SNAP=${1:-c85snap28}
L=/tmp/${SNAP}

ex() { docker exec memory-agent sh -c "$1"; }

date -Iseconds
echo RUN28B_READ_ONLY=1
[ -s /tmp/ma_c85_run28.log ] && echo RUN28_LOG_LINES=$(wc -l < /tmp/ma_c85_run28.log)
ex "ls -d $L"
LS_RC=$?
echo LS_RC=$LS_RC
if [ "$LS_RC" != 0 ]; then echo SNAP_DIR_MISSING=1 这一档什么都别读; exit 2; fi

# 格 B1：八条锁名 —— `def name(` 口径（run28 用的是 `def name():`，四条带 monkeypatch 的签名被漏掉）
ex "cd $L && echo B_DEFS=\$(grep -c 'def test_' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA25=\$(grep -c 'def test_reconfigure_closes_the_providers_it_is_replacing(' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA26=\$(grep -c 'def test_save_skill_version_never_regresses(' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA27=\$(grep -c 'public档不谎称_all()' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA28=\$(grep -c 'def test_mcp_facade_no_longer_short_circuits_dry_run(' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA30=\$(grep -c 'def test_execute_action_returns_not_implemented(' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA31=\$(grep -c 'def test_self_diary_round_reraises_to_the_wrapper(' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA33=\$(grep -c 'def test_seed_keeps_a_higher_agent_iterated_version(' tests/test_rounds11_19_ma25_35_fixes.py); echo B_MA35=\$(grep -c 'def test_retention_task_is_registered_at_startup(' tests/test_rounds11_19_ma25_35_fixes.py)"
echo LOCKS85B_RC=$?

# 格 B2：MCP 假 ok 短路 —— 两口径并立（代码 / 整行注释），并取出行号自证那一行确是注释
ex "cd $L && echo FAKE_ALL=\$(grep -cF '参数校验通过' src/memory_agent/mcp_server.py); echo FAKE_CODE=\$(grep -F '参数校验通过' src/memory_agent/mcp_server.py | grep -vcE '^[[:space:]]*#'); echo FAKE_COMMENT=\$(grep -F '参数校验通过' src/memory_agent/mcp_server.py | grep -cE '^[[:space:]]*#'); grep -nF '参数校验通过' src/memory_agent/mcp_server.py"
echo FAKEB_RC=$?

# 格 B3：`if dry_run: return` 与 `参数校验通过` 同族——两处命中都出自 mcp_server.py:1749 那行注释
#        （注释里引用被删掉的旧代码）。代码口径必须 0、注释口径必须 1，两口径并立报。
#        NR 两枚沿用 run28 的原锚点（`未实现：本服务不执行动作` / `501,`），只是复核它们落在代码行而非注释行。
ex "cd $L && echo OLD_SHORT_ALL=\$(grep -cF 'if dry_run: return' src/memory_agent/mcp_server.py); echo OLD_SHORT_CODE=\$(grep -F 'if dry_run: return' src/memory_agent/mcp_server.py | grep -vcE '^[[:space:]]*#'); grep -nF 'if dry_run: return' src/memory_agent/mcp_server.py; echo NR_NOTIMPL=\$(grep -cF '未实现：本服务不执行动作' src/memory_agent/api/nr_routes.py); echo NR_NOTIMPL_CODE=\$(grep -F '未实现：本服务不执行动作' src/memory_agent/api/nr_routes.py | grep -vcE '^[[:space:]]*#'); echo NR_501=\$(grep -cF '501,' src/memory_agent/api/nr_routes.py); echo NR_501_CODE=\$(grep -F '501,' src/memory_agent/api/nr_routes.py | grep -vcE '^[[:space:]]*#')"
echo OLDSHORT_RC=$?

date -Iseconds
echo REMOTE_DONE
