#!/usr/bin/env bash
# run28b 驱动：只读补格（任务表 #85 量具口径错锚）。
# 出网前三把尺：① 语法 ② 两层引号 lint ③ 本机同口径读数当场量（期望值取自**当场**的文件量，不写上轮的数）。
# 远端读数回来后逐格与本机对账：本机=工作树(=HEAD，run28 的 CODE_CLEAN 已证)，远端=容器快照树。
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
SNAP=c85snap28
PY=C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe
OUT=.qoder/tmp-c85b-run28b.out
T=tests/test_rounds11_19_ma25_35_fixes.py
S=src/memory_agent/mcp_server.py
BAD=0

bash -n .qoder/tmp-c85b-remote28b.sh; REM_SYNTAX_RC=$?
echo REMOTE_SYNTAX_RC=$REM_SYNTAX_RC
if [ "$REM_SYNTAX_RC" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c85b-remote28b.sh
LINT_RC=$?
echo LINT_RC=$LINT_RC
if [ "$LINT_RC" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi

# ── 尺3：本机同口径读数（这就是"期望"，全部当场量） ──
{
echo B_DEFS=$(grep -c 'def test_' $T)
echo B_MA25=$(grep -c 'def test_reconfigure_closes_the_providers_it_is_replacing(' $T)
echo B_MA26=$(grep -c 'def test_save_skill_version_never_regresses(' $T)
echo B_MA27=$(grep -c 'public档不谎称_all()' $T)
echo B_MA28=$(grep -c 'def test_mcp_facade_no_longer_short_circuits_dry_run(' $T)
echo B_MA30=$(grep -c 'def test_execute_action_returns_not_implemented(' $T)
echo B_MA31=$(grep -c 'def test_self_diary_round_reraises_to_the_wrapper(' $T)
echo B_MA33=$(grep -c 'def test_seed_keeps_a_higher_agent_iterated_version(' $T)
echo B_MA35=$(grep -c 'def test_retention_task_is_registered_at_startup(' $T)
echo FAKE_ALL=$(grep -cF '参数校验通过' $S)
echo FAKE_CODE=$(grep -F '参数校验通过' $S | grep -vcE '^[[:space:]]*#')
echo FAKE_COMMENT=$(grep -F '参数校验通过' $S | grep -cE '^[[:space:]]*#')
echo OLD_SHORT_ALL=$(grep -cF 'if dry_run: return' $S)
echo OLD_SHORT_CODE=$(grep -F 'if dry_run: return' $S | grep -vcE '^[[:space:]]*#')
echo NR_NOTIMPL=$(grep -cF '未实现：本服务不执行动作' src/memory_agent/api/nr_routes.py)
echo NR_NOTIMPL_CODE=$(grep -F '未实现：本服务不执行动作' src/memory_agent/api/nr_routes.py | grep -vcE '^[[:space:]]*#')
echo NR_501=$(grep -cF '501,' src/memory_agent/api/nr_routes.py)
echo NR_501_CODE=$(grep -F '501,' src/memory_agent/api/nr_routes.py | grep -vcE '^[[:space:]]*#')
} > .qoder/tmp-c85b-host28b.txt 2>&1
echo HOST_MEASURED=$(grep -c . .qoder/tmp-c85b-host28b.txt)
cat .qoder/tmp-c85b-host28b.txt
# 本机自洽：代码口径必须 0、注释口径必须 1、七条锁名各 1（不是"应该"，是当场读到的）
for k in B_MA25 B_MA26 B_MA27 B_MA28 B_MA30 B_MA31 B_MA33 B_MA35 FAKE_CODE OLD_SHORT_CODE; do
  v=$(grep "^$k=" .qoder/tmp-c85b-host28b.txt | cut -d= -f2)
  if [ "$k" = FAKE_CODE ] || [ "$k" = OLD_SHORT_CODE ]; then exp=0; else exp=1; fi
  if [ "$v" != "$exp" ]; then echo "GUARD_FAIL HOST_$k 期望=$exp 实际=$v"; BAD=1; else echo "GUARD_OK HOST_$k=$v"; fi
done
for k in FAKE_ALL OLD_SHORT_ALL NR_NOTIMPL NR_NOTIMPL_CODE NR_501 NR_501_CODE; do
  v=$(grep "^$k=" .qoder/tmp-c85b-host28b.txt | cut -d= -f2)
  if [ "$v" = 0 ]; then echo "GUARD_FAIL HOST_$k 期望>0 实际=0（锚点本身没落到树里）"; BAD=1
  else echo "GUARD_OK HOST_$k=$v（>0，含义由两口径对读）"; fi
done
if [ "$(grep '^B_DEFS=' .qoder/tmp-c85b-host28b.txt | cut -d= -f2)" != "25" ]; then
  echo "GUARD_FAIL HOST_B_DEFS 期望=25 实际=$(grep '^B_DEFS=' .qoder/tmp-c85b-host28b.txt | cut -d= -f2)"; BAD=1
else echo GUARD_OK HOST_B_DEFS=25; fi
if [ "$(grep '^FAKE_COMMENT=' .qoder/tmp-c85b-host28b.txt | cut -d= -f2)" != "1" ]; then
  echo GUARD_FAIL HOST_FAKE_COMMENT 期望=1 实际=0; BAD=1
else echo GUARD_OK HOST_FAKE_COMMENT=1; fi
# 工作树代码面必须仍是 HEAD（run28 在飞 ⇒ 只读，不许动）
CODE_DIFF=$(git status --porcelain --untracked-files=no -- src tests scripts)
echo CODE_DIFF_LINES=$(printf '%s' "$CODE_DIFF" | grep -c .)
if [ "$(printf '%s' "$CODE_DIFF" | grep -c .)" != 0 ]; then echo GUARD_FAIL CODE_CLEAN 期望=0 非 0; BAD=1; else echo GUARD_OK CODE_CLEAN=0; fi
if [ "$BAD" != 0 ]; then echo PREFLIGHT_FAILED=1 未出网; exit 2; fi
echo PREFLIGHT_OK=1

"$SCP" "${OPTS[@]}" -q .qoder/tmp-c85b-remote28b.sh "$NAS:/tmp/ma_c85b_remote28b.sh"; echo SCP_RC=$?
"$SSH" "${OPTS[@]}" "$NAS" "tr -d '\r' < /tmp/ma_c85b_remote28b.sh > /tmp/ma_c85b_remote28b_unix.sh && bash -n /tmp/ma_c85b_remote28b_unix.sh; echo NAS_SYNTAX_RC=\$?"

{ "$SSH" "${OPTS[@]}" "$NAS" "bash /tmp/ma_c85b_remote28b_unix.sh $SNAP; echo REMOTE_DRIVER_RC=\$?"; } > "$OUT" 2>&1
echo REMOTE_RC=$?
cat "$OUT"

# ── 逐格对账：本机(=HEAD 工作树) vs 容器快照树 ──
echo COMPARISON:
while IFS='=' read -r k v; do
  case "$k" in B_*|FAKE_*|OLD_SHORT_*|NR_NOTIMPL*|NR_501*) ;;
    *) continue ;;
  esac
  rv=$(grep -E "^(exports )?$k=" "$OUT" | tail -1 | cut -d= -f2 | tr -d '\r')
  if [ "$v" = "$rv" ]; then echo "MATCH $k=$v"; else echo "MISMATCH $k 本机=$v 容器=$rv"; fi
done < .qoder/tmp-c85b-host28b.txt

date -Iseconds
echo DRIVER_DONE
