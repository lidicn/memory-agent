#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异自证：把三个「静默归零/静默放行」的写法逐个注回 `scripts/dep_audit_cve.py`，
每个都必须被 `tests/test_vma_dep_audit_cve.py` 判红；末态字节必须与基线一致。
基线档（什么都不改）必须先绿，否则这套门本身没有信息量。"""
import hashlib
import subprocess
import sys

PY = sys.executable
SRC = "scripts/dep_audit_cve.py"
TEST = "tests/test_vma_dep_audit_cve.py"

MUTANTS = [
    ("M1 只读 matches 键（本机踩过的原形：已知有洞也报 0 命中）",
     b'    rows = (result.get("vulns") or []) + (result.get("matches") or [])',
     b'    rows = (result.get("matches") or [])'),
    ("M2 反例不咬也照样出报告（假绿的门）",
     b'        if not canary_ok:',
     b'        if False:'),
    ("M3 出网失败回 0（把「没扫成」读成「没有洞」）",
     b'        print(f"[DEP-AUDIT] \xe2\x9d\x8c \xe6\x89\xab\xe6\x8f\x8f\xe6\x9c\xaa\xe5\xae\x8c\xe6\x88\x90\xef\xbc\x9a{type(exc).__name__}: {exc}")\n        print("[DEP-AUDIT]    \xe8\xbf\x99\xe6\x9d\xa1\xe8\xaf\xbb\xe6\x95\xb0\xe6\x98\xaf\xe3\x80\x8c\xe6\xb2\xa1\xe6\x89\xab\xe6\x88\x90\xe3\x80\x8d\xef\xbc\x8c\xe4\xb8\x8d\xe6\x98\xaf\xe3\x80\x8c\xe6\xb2\xa1\xe6\x9c\x89\xe6\xb4\x9e\xe3\x80\x8d\xe2\x80\x94\xe2\x80\x94\xe6\x8c\x89 RC=2 \xe7\x99\xbb\xe8\xae\xb0\xe3\x80\x82")\n        return 2',
     b'        print(f"[DEP-AUDIT] \xe2\x9d\x8c \xe6\x89\xab\xe6\x8f\x8f\xe6\x9c\xaa\xe5\xae\x8c\xe6\x88\x90\xef\xbc\x9a{type(exc).__name__}: {exc}")\n        return 0'),
    ("M4 忽略 database_specific 档位（严重度全落 UNKNOWN）",
     b'    db_sev = str((vuln.get("database_specific") or {}).get("severity") or "").upper()',
     b'    db_sev = ""'),
]

with open(SRC, "rb") as fh:
    base = fh.read()
base_sha = hashlib.sha256(base).hexdigest()


def run_suite():
    p = subprocess.run([PY, "-X", "utf8", "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip().splitlines()[-1:]


rc, tail = run_suite()
print(f"BASELINE（什么都不改）: RC={rc} {' '.join(tail)}  <- 必须是 0，否则这套门零信息量")
if rc != 0:
    sys.exit(1)

bitten = 0
for name, old, new in MUTANTS:
    if old not in base:
        print(f"{name}: ?? 锚点没找到（变异没落上，读数无效）")
        continue
    payload = base.replace(old, new, 1)
    with open(SRC, "wb") as fh:
        fh.write(payload)
    rc, tail = run_suite()
    with open(SRC, "wb") as fh:          # 字节级还原
        fh.write(base)
    ok = hashlib.sha256(open(SRC, "rb").read()).hexdigest() == base_sha
    print(f"{name}: RC={rc} {' '.join(tail)} RESTORED={ok}")
    bitten += 1 if rc != 0 else 0

print(f"BITTEN={bitten}/{len(MUTANTS)} 基线 sha8={base_sha[:8]} 末态一致="
      f"{hashlib.sha256(open(SRC, 'rb').read()).hexdigest() == base_sha}")
sys.exit(0 if bitten == len(MUTANTS) else 1)
