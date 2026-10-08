#!/usr/bin/env bash
# 20261005 Qoder 批次（#40 交付件 A：Q3-1 六个过滤/排序位 + Q3-4 分页/窗口键 + Q3-3 口径更正）
# 工作区快照 → 容器权威回归（pyflakes 门 + 全量 pytest）+ 生产只读探针（改前/改后成对读数）
set -uo pipefail
cd /e/NAS/memory-agent
SSH="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/ssh.exe"
SCP="C:/Users/lidicn/.ssh/openssh/OpenSSH-Win64/scp.exe"
KEY="C:/Users/lidicn/.ssh/id_ed25519"
NAS="lidicn@192.168.2.200"
OPTS=(-i "$KEY" -o StrictHostKeyChecking=no)
POST=c17snap20261005a             # 改后：工作区快照
PRE=c17pre20261005a               # 改前：HEAD 快照（同一支探针跑两遍，成对读数）
TGZ_POST=.qoder/tmp-c17-snap.tgz
TGZ_PRE=.qoder/tmp-c17-pre.tgz

echo HEAD=$(git rev-parse --short HEAD) ORIGIN=$(git rev-parse --short origin/main)

# ── 改后快照：全部已跟踪代码 + 新增测试/脚本 ───────────────────────────────
{ git ls-files -- src tests scripts benchmarks .gates-baseline.txt .gates.toml .gates pytest.ini pyproject.toml gates.sh; \
  git ls-files --others --exclude-standard -- tests scripts; } | sort -u > .qoder/tmp-c17-filelist.txt
echo FILES=$(wc -l < .qoder/tmp-c17-filelist.txt)
tar -czf "$TGZ_POST" -T .qoder/tmp-c17-filelist.txt
echo TAR_POST_RC=$? SIZE=$(stat -c %s "$TGZ_POST")

# ── 改前快照：HEAD 的 src+scripts，再把这支新探针放进去 ────────────────────
rm -rf .qoder/tmp-c17-pre
mkdir -p .qoder/tmp-c17-pre
git archive --format=tar HEAD src scripts | tar -x -C .qoder/tmp-c17-pre
cp scripts/probe_insights_q3_acceptance.py .qoder/tmp-c17-pre/scripts/
tar -czf "$TGZ_PRE" -C .qoder/tmp-c17-pre .
echo TAR_PRE_RC=$? SIZE=$(stat -c %s "$TGZ_PRE")

for pair in "$TGZ_POST:$POST" "$TGZ_PRE:$PRE"; do
  tgz=${pair%%:*}; snap=${pair##*:}
  "$SCP" "${OPTS[@]}" -q "$tgz" "$NAS:/tmp/ma_$snap.tgz"; echo "SCP_${snap}_RC=$?"
  "$SSH" "${OPTS[@]}" "$NAS" "rm -rf /tmp/${snap}_stage && mkdir -p /tmp/${snap}_stage && tar -xzf /tmp/ma_$snap.tgz -C /tmp/${snap}_stage && rm -f /tmp/ma_$snap.tgz && docker exec memory-agent sh -c 'rm -rf /tmp/$snap' && docker cp /tmp/${snap}_stage memory-agent:/tmp/$snap && docker exec -u root memory-agent chown -R 10001:10001 /tmp/$snap && rm -rf /tmp/${snap}_stage; echo STAGE_RC=\$?"
  echo "SYNC_${snap}_RC=$?"
done

# ── 权威门：pyflakes（改后快照）──────────────────────────────────────────
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$POST && PYTHONPATH=/tmp/pylibs GATES_REQUIRE=1 bash scripts/pyflakes_gate.sh' > /tmp/${POST}_gate.log 2>&1; echo GATE_RC=\$?; tail -4 /tmp/${POST}_gate.log"

# ── 权威回归：全量 pytest（改后快照）──────────────────────────────────────
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$POST && PYTHONPATH=/tmp/$POST:/tmp/$POST/src:/tmp/pylibs GATES_REQUIRE=1 JWT_SECRET=ci-test python -m pytest tests -q -rs' > /tmp/${POST}_suite.log 2>&1; echo SUITE_RC=\$?; tail -14 /tmp/${POST}_suite.log"

# ── 生产只读探针：改前 / 改后 成对（Q3 验收单六条）────────────────────────
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$PRE && PYTHONPATH=/tmp/$PRE/src:/tmp/pylibs python -u scripts/probe_insights_q3_acceptance.py' > /tmp/${PRE}_q3.log 2>&1; echo PRE_PROBE_RC=\$?; wc -l /tmp/${PRE}_q3.log"
"$SSH" "${OPTS[@]}" "$NAS" "docker exec memory-agent sh -c 'cd /tmp/$POST && PYTHONPATH=/tmp/$POST/src:/tmp/pylibs python -u scripts/probe_insights_q3_acceptance.py' > /tmp/${POST}_q3.log 2>&1; echo POST_PROBE_RC=\$?; wc -l /tmp/${POST}_q3.log"
"$SCP" "${OPTS[@]}" -q "$NAS:/tmp/${PRE}_q3.log" .qoder/tmp-c17-pre-q3.log; echo FETCH_PRE_RC=$?
"$SCP" "${OPTS[@]}" -q "$NAS:/tmp/${POST}_q3.log" .qoder/tmp-c17-post-q3.log; echo FETCH_POST_RC=$?
rm -rf "$TGZ_POST" "$TGZ_PRE" .qoder/tmp-c17-pre
