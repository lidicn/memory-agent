#!/usr/bin/env bash
# 任务表 #64 覆盖率「改后档」——与改前档**同一把尺**：本机 Python313 + coverage 7.16.1、
# `pytest tests -q -rs` 全量单轮、include 只列本批两模块（identity_fusion / behavior_predictor）。
# 顺序不能颠倒：改后档必须等**容器权威门跑完**再打（run14b 的教训——门在飞期间动树，
# 那一档自动降级成中间档；本机 coverage 会在仓库根落 `.coverage` 数据文件，也算动树）。
# 改前档读数（.qoder/tmp-b64-covreport-BEFORE.out，HEAD b241c7d）：
#   behavior_predictor 111 语句 / 18 未执行 / 84%   未执行行 35, 49, 52-53, 81, 83, 113, 186, 256-273
#   identity_fusion    183 语句 / 29 未执行 / 84%
#   全套 1532 passed / 22 skipped，429.86s
# mcp_server 只在改后档**另报一行**：本批在 :2732 加了 weekday 上界守卫，那一格的落点证据
# 在变异档 M14（容器侧实跑，本机 skip），不在百分比里；这里带上它只为说明"改动面确实被执行到过"。
set -u
PY='C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe'
ROOT='E:/NAS/memory-agent'
INC='src/memory_agent/identity_fusion.py,src/memory_agent/behavior_predictor.py,src/memory_agent/mcp_server.py'
cd "$ROOT"
echo "HEAD_SHA=$(git log -1 --format='%h %ci')"
"$PY" -m coverage --version
"$PY" -m coverage erase
"$PY" -m coverage run -m pytest tests -q -rs > "$ROOT/.qoder/tmp-b64-cov-AFTER.out" 2>&1
echo "AFTER_SUITE_RC=$?"
"$PY" -m coverage report --include="$INC" --show-missing > "$ROOT/.qoder/tmp-b64-covreport-AFTER.out" 2>&1
echo "AFTER_REPORT_RC=$?"
"$PY" -m coverage report --include='src/memory_agent/identity_fusion.py,src/memory_agent/behavior_predictor.py' > "$ROOT/.qoder/tmp-b64-covreport-AFTER-pair.out" 2>&1
echo "AFTER_PAIR_RC=$?"
tail -1 "$ROOT/.qoder/tmp-b64-cov-AFTER.out"
cat "$ROOT/.qoder/tmp-b64-covreport-AFTER-pair.out"
grep -E 'identity_fusion|behavior_predictor|mcp_server' "$ROOT/.qoder/tmp-b64-covreport-AFTER.out"
