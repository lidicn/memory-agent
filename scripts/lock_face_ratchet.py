#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量具：`scripts/` 里被**出境面**引用过的量具，每一枚有没有一枚 `tests/` 的锁。

为什么要有这把尺（2026-10-14 第十三面现读出来的结构性事实）：
- `.gates.toml` 的 `extra_skip_globs` 含 `"scripts/*"` ⇒ 一次性脚本不参与门禁；
- `scripts/pyflakes_gate.sh` 的 `TARGET_DIR` 只有 `src/memory_agent`；
- ⇒ **仓内没有任何一道权威门罩得住 `scripts/`**（容器面也罩不住，根上是配置面 skip）。
所以**被对端抄走当证据的那把尺，写坏了门⛔ 会红**，它的正确性只能由 `tests/` 自锁承担。
本尺不补锁，只**量还欠几枚**，并把"欠锁"这件事变成一道会红的闸（锁＝`tests/test_lock_face_ratchet_gauge.py`）。

取材面（三面，各自可注入，⛔ 靠 cwd）：
① `SCRIPTS`＝被量对象（默认 `<repo>/scripts`，只取 `*.py` 的名字）；
② `TESTS`＝锁面（默认 `<repo>/tests`）。两种形状都算"有锁"：
   该名的**词边界**命中出现在锁面文本里，或锁面里有 `test_<名>.py`／`<名>_test.py` 这枚**同名约定文件**
   （⛔ 只认内容：本仓多数锁确实靠内容里的 `scripts/<名>.py` 命中，但"按约定文件名建了锁、内容⛔ 复述名"
   的那一类若判成欠锁，就是**假红**——判例 2「闸必须先保证它有绿的可能」）。
③ `DOC`＝仓内引用面（默认 `<repo>/doc`）——本仓审计文书引用过＝**只登记为 `DOC_ONLY`，⛔ 计欠**。
   ★ 口径钉死：**欠锁义务只随"对端出境面"（`--face` ＋ `PEER_ROSTER`）算**。把 `doc/` 也计进来会把
   49 枚 `probe_*`/`hc_check` 全判成欠锁（本席 16:1x 实测：一改成三面合算，NAKED 从 3 涨到 18）——
   那不是"锁的债务"，是"自家文档引用过自家脚本"，⛔ 同一种东西。
   另加 `PEER_ROSTER`＝**转抄**的对端引用名单（对端⛔ 在本尺射程里，见下）。
★ `--face DIR` 可再挂出境面（比如 DCD／链主的目录），盘上没有就**跳过并在头部声明跳过几面**，⛔ 静默把范围读窄。

**射程边界（必须读，⛔ 把它当全仓结论）**：
- `PEER_ROSTER` 是**手抄**的对端名单——对端改了脚本名或新引一枚，本尺**看不见**（同族判例：
  转抄对端名单的锁只锁得住自己这一边）。所以它只保证"**抄过来的那一枚欠不欠**"，
  ⛔ 保证"对端引用的全集"。全集读数得由对端那把尺出（链主的逐仓闸面尺）。
- 豁免（`EXEMPT`）＝"被引用但本席位判**⛔ 欠锁**"的逐枚理由，**必须非空**；
  豁免名单里的名字若已⛔ 在 `scripts/` 面消失 ⇒ 判红（过期豁免＝假绿的一种）。

用法：
    python scripts/lock_face_ratchet.py            # 真仓三面
    python scripts/lock_face_ratchet.py --tests <dir> --scripts <dir> --doc <dir>   # 合成树
"""
from __future__ import annotations

import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTS = (".py", ".md", ".sh", ".toml", ".txt")

# 词边界口径（⛔ 子串：子串会把 `_diag` 这类同前缀的名字吃进来，2026-10-14 实测差一枚）。
def _pattern(name: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")

# ★ 转抄的对端引用名单：值＝出处（件号/小节），⛔ 空字符串。对端改名本尺看不见（见 docstring 射程）。
PEER_ROSTER = {
    "pii_backfill_r2": "decisions/20261014-MA-R2退路快照明文残留面-裁定.md §二（三件硬要求点名该脚本）",
    "bool_egress_sites": "inbox/20261012-MA-print布尔出口残余五处载荷语义-决策申请.md §七（#92 那把尺）",
    "prod_sanitize_zero_residue_check": "探针证据/（链主抄走的零残留读数）＋投递/ADM-06.md §六",
    "config_pin_drift": "roadmaps/MA-联动路线图.md §巡检发现（sidecar 窗口单点名）",
    # ★ 曾经抄进来过 `verify_adm_linkage`——那是**链主自己仓**（cwd＝homesdk）的尺，⛔ 本仓 scripts/ 的一枚。
    #   本尺的 `ROSTER_ABSENT` 腿当场把它咬出来了（名单里的名字⛔ 在面上＝转抄错/对端改名）。留字当负控。
}

# ★ 判"⛔ 欠锁"的逐枚理由（只准减，⛔ 加枚不带理由）。
EXEMPT = {
    "debug_token": "写死 open(\"/data/config.json\")＝容器路径的一次性调试件；本机跑必失败，"
                   "给它写锁只能写一枚恒 skip 的假锁。被引用的身份是载体（名册里'这枚曾有令牌'）而⛔'读数被抄走'",
    "check_config": "同 debug_token：容器路径＋只读体检，⛔ 本机可跑的结论面",
    "probe_activity_semantic_readings": "自述'生产库，只读'⇒ 需现网 DB 面＝等窗那类；"
                                        "本席位⛔ 碰现网⛔ 代跑，欠锁登记为余项而⛔ 造假日锁",
}


def blob(d: str) -> str:
    out = []
    for root, _dd, files in os.walk(d):
        if "__pycache__" in root or os.sep + ".git" in root:
            continue
        for f in files:
            if f.endswith(EXTS):
                try:
                    with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                        out.append(fh.read())
                except OSError:
                    pass
    return "\n".join(out)


def filenames(d: str) -> set:
    return {f for root, _dd, files in os.walk(d)
            if not ("__pycache__" in root or os.sep + ".git" in root) for f in files}


def hits(text: str, name: str) -> int:
    return len(_pattern(name).findall(text))


def has_lock(name: str, tests_blob: str, test_files: set) -> bool:
    """两种形状任一即算有锁：锁面文本的词边界命中，或锁面的同名约定件（见 docstring 取材面②）。"""
    return hits(tests_blob, name) > 0 or f"test_{name}.py" in test_files \
        or f"{name}_test.py" in test_files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts", default=os.path.join(REPO, "scripts"))
    ap.add_argument("--tests", default=os.path.join(REPO, "tests"))
    ap.add_argument("--doc", default=os.path.join(REPO, "doc"))
    ap.add_argument("--face", action="append", default=[],
                    help="追加的出境面目录（DCD／链主等），可多次；⛔ 在场则跳过并在头部登记")
    args = ap.parse_args()

    names = sorted(os.path.splitext(f)[0] for f in os.listdir(args.scripts)
                   if f.endswith(".py"))
    # ★ fail-closed：取材面塌了⛔ 交一份"范围变窄"的读数，自己先拒跑（两枚拒跑同带「量具口径失效」，判红类别才可断言）。
    assert names, f"量具口径失效：scripts 面为空（{args.scripts}）⇒ ⛔ 交 0 欠锁的假绿"
    tests_blob = blob(args.tests)
    test_files = filenames(args.tests)
    assert tests_blob.strip() or test_files, \
        f"量具口径失效：锁面读不到东西（{args.tests}）⇒ 任何'欠锁'都是假红"

    doc_blob = blob(args.doc) if os.path.isdir(args.doc) else ""
    extra, extra_missing = [], []
    for d in args.face:
        (extra if os.path.isdir(d) else extra_missing).append(d)
    extra_blob = "\n".join(blob(d) for d in extra)

    rows = []
    for n in names:
        rows.append((n, has_lock(n, tests_blob, test_files),
                     hits(extra_blob, n) + (1 if n in PEER_ROSTER else 0),
                     hits(doc_blob, n)))

    naked = [n for n, locked, peer, _d in rows if not locked and peer > 0]
    doc_only = [n for n, locked, peer, d in rows if not locked and peer == 0 and d > 0]
    orphan = [n for n, locked, peer, d in rows if not locked and peer == 0 and d == 0]
    bad_exempt = [n for n in naked if n not in EXEMPT]
    empty_reason = [n for n, r in EXEMPT.items() if not str(r).strip()]
    stale = [n for n in EXEMPT if n not in names]
    roster_absent = [n for n in PEER_ROSTER if n not in names]

    print(f"scripts={len(names)} 面={args.scripts}")
    print(f"锁面={args.tests}｜仓内引用面={args.doc}（只登记⛔ 计欠）｜对端出境面={len(extra)} 枚"
          f"（跳过⛔ 在场 {len(extra_missing)} 枚：{extra_missing or '无'}）")
    if extra_missing:
        print("★ 头部声明：本跑少扫了上面这几面 ⇒ 欠锁数是**下界**，⛔ 当全集读。")
    print(f"PEER_ROSTER（转抄对端名单，射程见 docstring）={len(PEER_ROSTER)} 枚")
    print(f"LOCKED={sum(1 for r in rows if r[1])}｜NAKED(对端引用且无锁)={len(naked)}｜"
          f"DOC_ONLY(仅本仓引用且无锁)={len(doc_only)}｜ORPHAN(三面皆无引用且无锁)={len(orphan)}")
    for n in naked:
        tag = "豁免" if n in EXEMPT else "★欠锁"
        print(f"  {tag}｜{n}")
    print(f"EXEMPT={len(EXEMPT)}｜BAD_EXEMPT(欠锁且未列豁免)={len(bad_exempt)}｜"
          f"EMPTY_REASON={len(empty_reason)}｜STALE(豁免名已⛔ 在面)={len(stale)}｜"
          f"ROSTER_ABSENT(名单里名字⛔ 在 scripts 面)={len(roster_absent)}")
    for n in (bad_exempt + empty_reason + stale + roster_absent):
        print(f"  ✗ {n}")

    ok = not (bad_exempt or empty_reason or stale or roster_absent)
    print(f"LOCK_FACE_RATCHET={'PASS' if ok else 'FAIL'}")
    print("本尺只数欠锁面，⛔ 替任何一把尺保证它对——补锁的手和判豁免的手得是同一只，读数才认。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
