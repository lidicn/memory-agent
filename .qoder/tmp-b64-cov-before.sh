#!/usr/bin/env bash
# 任务表 #64 覆盖率「改前档」（本机口径与 #63 同一把尺：Python313 + coverage 7.16.1、全量单轮）。
# include 换成本批两模块；同一行命令、同一个 pytest tests -q -rs，保证与改后档可比。
# 改前档必须在动树之前打（run14c 的教训：树一动，那一档就作废）。
set -u
PY='C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe'
ROOT='E:/NAS/memory-agent'
INC='src/memory_agent/identity_fusion.py,src/memory_agent/behavior_predictor.py'
cd "$ROOT"
echo "HEAD_SHA=$(git log -1 --format='%h %ci')"
"$PY" -m coverage --version
"$PY" -m coverage erase
"$PY" -m coverage run -m pytest tests -q -rs > "$ROOT/.qoder/tmp-b64-cov-BEFORE.out" 2>&1
echo "BEFORE_SUITE_RC=$?"
"$PY" -m coverage report --include="$INC" > "$ROOT/.qoder/tmp-b64-covreport-BEFORE.out" 2>&1
echo "BEFORE_REPORT_RC=$?"
"$PY" -m coverage report --include="$INC" --missing > "$ROOT/.qoder/tmp-b64-covreport-BEFORE-missing.out" 2>&1
echo "BEFORE_MISSING_RC=$?"
tail -1 "$ROOT/.qoder/tmp-b64-cov-BEFORE.out"
cat "$ROOT/.qoder/tmp-b64-covreport-BEFORE.out"
