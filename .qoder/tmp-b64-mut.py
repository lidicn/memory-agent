"""任务表 #64 的变异自咬 harness：每条变异都必须让新锁判红，且原文件按字节还原。

口径与前面几批一致：
- 每条 mutant 单独应用、单独跑目标用例，**RC≠0 且必须有 FAILED** 才算咬住；
- 还原后与原字节逐字节比对（restored_identical），比对不上直接算 MUTATION_BAD；
- `NOTHING` 是"什么都不改"档，必须绿（否则这批的锁是自证的假绿）。

`MUT_ROOT` 指仓库快照根（容器里是 /tmp/cNNsnap…），`PYTEST_TARGET` 可给多个文件（空格分隔）。
本批三处落点：identity_fusion（封顶方向 + 未执行分支）、behavior_predictor（坏数据与 weekday 过滤）、
mcp_server（weekday 边界守卫——**本机 SDK 不可用会 skip，容器侧才算实跑档**）。

R5/R6 两条的锚点必须带上前一行的 `conflicts.append(...)`：`cap = _more_severe(cap, Level.MED)`
在文件里出现三次（R2/R5/R6），只写这一行会命中 3 次 ⇒ harness 自己判锚点异常。
"""
import os
import pathlib
import subprocess
import sys

MUT_ROOT = pathlib.Path(os.environ.get("MUT_ROOT", pathlib.Path(__file__).resolve().parents[1]))
TARGET = os.environ.get(
    "PYTEST_TARGET",
    "tests/test_vma_task64_identity_fusion.py tests/test_vma_task64_behavior_predictor.py")
PYTEST = sys.executable

IF = "src/memory_agent/identity_fusion.py"
BP = "src/memory_agent/behavior_predictor.py"
MS = "src/memory_agent/mcp_server.py"

MUTANTS = [
    ("M1 R5 封顶退回改前的 min-by-index（会松档）", IF,
     '        conflicts.append(Conflict("R5", "ROOM_MISMATCH", "房间不匹配"))\n'
     "        cap = _more_severe(cap, Level.MED)",
     '        conflicts.append(Conflict("R5", "ROOM_MISMATCH", "房间不匹配"))\n'
     "        cap = min(cap, Level.MED, key=lambda x: list(Level).index(x))"),
    ("M2 R6 封顶退回改前的 min-by-index", IF,
     '        conflicts.append(Conflict("R6", "NO_FACE_OBSERVATION", "无人脸观测"))\n'
     "        cap = _more_severe(cap, Level.MED)",
     '        conflicts.append(Conflict("R6", "NO_FACE_OBSERVATION", "无人脸观测"))\n'
     "        cap = min(cap, Level.MED, key=lambda x: list(Level).index(x))"),
    ("M3 R4 退回直接赋值覆盖", IF,
     '            conflicts.append(Conflict("R4", "STRANGER_DETECTED", "陌生人检测"))\n'
     "            cap = _more_severe(cap, Level.LOW)",
     '            conflicts.append(Conflict("R4", "STRANGER_DETECTED", "陌生人检测"))\n'
     "            cap = Level.LOW"),
    ("M4 R3 封顶失效（改回 HIGH 即不封顶）", IF,
     '            conflicts.append(Conflict("R3", "CLOSE_RACE", "分差太小"))\n'
     "            cap = _more_severe(cap, Level.NEEDS_REVIEW)",
     '            conflicts.append(Conflict("R3", "CLOSE_RACE", "分差太小"))\n'
     "            cap = Level.HIGH"),
    ("M5 R2 封顶失效", IF,
     '            conflicts.append(Conflict("R2", "SOURCE_DISAGREE", "两路人脸信号对立"))\n'
     "            cap = _more_severe(cap, Level.MED)",
     '            conflicts.append(Conflict("R2", "SOURCE_DISAGREE", "两路人脸信号对立"))\n'
     "            cap = Level.HIGH"),
    ("M6 _more_severe 取反（判据本身坏掉）", IF,
     "    return a if severity[a] >= severity[b] else b",
     "    return a if severity[a] <= severity[b] else b"),
    ("M7 prior_boost 的 k<=1 门失效", IF,
     "    if not posterior or k <= 1 or samples < cfg.prior_min_samples:",
     "    if not posterior or samples < cfg.prior_min_samples:"),
    ("M8 prior_boost 的样本量门失效", IF,
     "    if not posterior or k <= 1 or samples < cfg.prior_min_samples:",
     "    if not posterior or k <= 1:"),
    ("M9 log-odds 裁剪失效", IF,
     "    return max(-0.6, min(0.6, beta))",
     "    return beta"),
    ("M10 消除法不再限置信度上限 0.65", IF,
     '            confidence=min(inferred["confidence"], 0.65),',
     '            confidence=inferred["confidence"],'),
    ("M11 房间为空的那一档失效", IF,
     "    if not signal_room or not slot_room:",
     "    if False:"),
    ("M12 时序衰减整体失效（年龄不再降权）", IF,
     "    if age_sec <= 0:\n        return 1.0\n    return math.exp(-age_sec / tau)",
     "    if age_sec <= 0:\n        return 1.0\n    return 1.0"),
    ("M13 VOTE 档不抬升 MANUAL 权重", IF,
     '    if cfg.manual_mode == "VOTE":',
     "    if False:"),
    ("M14 MCP 侧 weekday 边界守卫失效", MS,
     "        if weekday is not None and not (-1 <= weekday <= 6):",
     "        if False:"),
    ("M15 weekday 过滤失效（改前传 99 也不报错那一族）", BP,
     "        if dt is None or (weekday is not None and dt.weekday() != weekday):",
     "        if dt is None:"),
    ("M16 persons 为 dict 的那一代形状不认", BP,
     "    if isinstance(raw, dict):\n        raw = [raw]",
     "    if isinstance(raw, dict):\n        raw = []"),
    ("M17 house_dt 的短串门放宽（残缺串被当日期）", BP,
     "    if len(text) < 16:",
     "    if len(text) < 6:"),
    ("M18 偏离严重度写死 mild", BP,
     '        severity = "mild" if delta < 1.0 else "moderate" if delta < 2.0 else "severe"',
     '        severity = "mild"'),
    ("M19 带内判据只看下界（早退也被判正常）", BP,
     "    if lower <= actual_hour <= upper:",
     "    if lower <= actual_hour:"),
    ("M20 同窗口去重失效（集合改列表 ⇒ 频次按事件计）", BP,
     "        labels = {\n"
     '            (ev.get("scene") or ev.get("action") or "unknown")\n'
     "            for dt, ev in timeline if arrival_dt <= dt <= window_end\n"
     "        }",
     "        labels = [\n"
     '            (ev.get("scene") or ev.get("action") or "unknown")\n'
     "            for dt, ev in timeline if arrival_dt <= dt <= window_end\n"
     "        ]"),
    # ── DCD 20261006 §九 验收锁的四条负控（M21–M24，本批 run16 新加）─────────
    ("M21 有人给 identity_fusion 接线（零调用者锁的负控）", BP,
     "def detect_pattern_deviation(",
     "from . import identity_fusion  # MUT21 负控：接了一线\n\n\ndef detect_pattern_deviation("),
    ("M22 score_raw 的分母改成按记录数（重复上报不再放大）", IF,
     "    total_weight = sum(W.get(src, 0.1) for src in active_weights)",
     "    total_weight = sum(W.get(s.source, 0.1) for s in sigs if s.candidate_id is not None)"),
    ("M23 偏离判据补上跨午夜环形距离（那条现状登记失效）", BP,
     "    if lower <= actual_hour <= upper:",
     "    if lower <= actual_hour <= upper or (upper < lower and (actual_hour >= lower or actual_hour <= upper)):"),
    ("M24 person 形参开始影响判档", BP,
     '    if not predicted or not predicted.get("range"):',
     '    if not predicted or not predicted.get("range") or person == "Alice":'),
]


def run_pytest():
    proc = subprocess.run([PYTEST, "-m", "pytest", "-q", *TARGET.split()], cwd=str(MUT_ROOT),
                          capture_output=True, text=True, errors="replace")
    out = proc.stdout + proc.stderr
    failed = sum(1 for line in out.splitlines() if line.startswith("FAILED"))
    syntax = "SyntaxError" in out or "INTERNALERROR" in out
    tail = [line for line in out.splitlines()
            if " passed" in line or " failed" in line or "error" in line.lower()]
    return proc.returncode, failed, (tail[-1] if tail else ""), syntax


def apply(rel, old, new):
    path = MUT_ROOT / rel
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    n = text.count(old)
    if n != 1:
        return None, f"锚点命中 {n} 次（需要恰好 1 次）"
    path.write_bytes(text.replace(old, new).encode("utf-8"))
    return raw, None


def main():
    rc, failed, tail, syntax = run_pytest()
    print(f"NOTHING 什么都不改档 -> RC={rc} FAILED={failed} | {tail}")
    if rc != 0 or syntax:
        print("CONTROL_BAD=1  # 基线就不绿，后面的咬合读数没有意义")
        return 1

    bad = 0
    for label, rel, old, new in MUTANTS:
        raw, err = apply(rel, old, new)
        if err:
            print(f"{label} -> 锚点异常：{err}")
            bad += 1
            continue
        rc, failed, tail, syntax = run_pytest()
        path = MUT_ROOT / rel
        path.write_bytes(raw)
        restored = "OK" if path.read_bytes() == raw else "MISMATCH"
        if syntax:
            # run14b 的教训：变异串语法不过 ⇒ pytest 在 collection 期就崩，
            # 0 条 FAILED 会被读成"没咬住"。这里把它单独标出来，别让量具背锅成缺陷。
            bites = "量具自伤(SyntaxError)"
        else:
            bites = "咬住了" if rc != 0 and failed > 0 else "没咬住"
        if bites != "咬住了" or restored != "OK":
            bad += 1
        print(f"{label} -> RC={rc} FAILED={failed} {bites} restored={restored} | {tail}")
    print(f"MUT_COUNT={len(MUTANTS)} MUTATION_BAD={bad}")
    return 0 if bad == 0 else 1


sys.exit(main())
