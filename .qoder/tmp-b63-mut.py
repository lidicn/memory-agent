"""任务表 #63 的变异自咬 harness：每条变异都必须让新锁判红，且原文件按字节还原。

口径与前面几批一致：
- 每条 mutant 单独应用、单独跑目标用例，**RC≠0 且必须有 FAILED** 才算咬住；
- 还原后与原字节逐字节比对（restored_identical），比对不上直接算 MUTATION_BAD；
- 最后一条 `NOTHING` 是"什么都不改"档，必须绿（否则这批的锁是自证的假绿）。

`MUT_ROOT` 指仓库快照根（容器里是 /tmp/cNNsnap…），`PYTEST_TARGET` 指目标用例文件。
"""
import os
import pathlib
import subprocess
import sys

MUT_ROOT = pathlib.Path(os.environ.get("MUT_ROOT", pathlib.Path(__file__).resolve().parents[1]))
TARGET = os.environ.get("PYTEST_TARGET", "tests/test_vma_task63_routes_input_boundary.py")
PYTEST = sys.executable

BR = "src/memory_agent/api/behavior_routes.py"
CA = "src/memory_agent/change_attribution.py"
# `_num` 原住在 behavior_routes，#68 第三批（2a14651）为"同一个口径两个入口都钳"提到 api/deps.py。
# 锚点跟着函数走：M1~M4 改指 deps.py，M4 的 except 行同时带上后来补的 OverflowError。
DP = "src/memory_agent/api/deps.py"

MUTANTS = [
    ("M1 丢掉 bool 判定", DP,
     '    if isinstance(raw, bool):        # JSON 的 true 不该被读成 1\n'
     '        return None, f"{name} 必须是数字"\n', ""),
    ("M2 下界门失效", DP, "    if lo is not None and val < lo:", "    if False:"),
    ("M3 上界门失效", DP, "    if hi is not None and val > hi:", "    if False:"),
    ("M4 转换失败退回默认档（改前的 int(x or 3)）", DP,
     '    except (TypeError, ValueError, OverflowError):\n'
     '        return None, f"{name} 必须是{\'整数\' if cast is int else \'数字\'}"\n',
     '    except (TypeError, ValueError, OverflowError):\n        return default, None\n'),
    ("M5 min_variant_support 顶死字面量 3", BR,
     "        min_variant_support=mvs,", "        min_variant_support=mvs or 3,"),
    ("M6 mine_process 的合法 bucket_sec=0 被吞", BR,
     "        min_variant_support=mvs,\n        bucket_sec=bsec,",
     "        min_variant_support=mvs,\n        bucket_sec=bsec or 60,"),
    ("M7 min_score 的合法 0.0 被吞", BR, "min_score=mscore,", "min_score=mscore or 0.5,"),
    ("M8 causal_analyze 窗口门失效", BR,
     "        if not (1 <= value <= DAY_WINDOW_MAX):", "        if False:"),
    ("M9 causal_counterfactual 窗口门失效", BR,
     "    if not (1 <= days <= DAY_WINDOW_MAX):", "    if False:"),
    ("M10 bad-cases 的 limit 拒绝分支摘掉", BR,
     '    limit, bad = _num(request.query_params.get("limit"), name="limit",\n'
     '                      default=20, lo=1, hi=100)\n'
     "    if bad:\n        return error(bad)\n",
     '    limit, bad = _num(request.query_params.get("limit"), name="limit",\n'
     '                      default=20, lo=1, hi=100)\n'),
    ("M11 predictions 回到主线程", BR,
     "    arrival, routine = await asyncio.to_thread(_predict)",
     "    arrival, routine = _predict()"),
    ("M12 intent 回到主线程", BR,
     "    intents = await asyncio.to_thread(_infer)", "    intents = _infer()"),
    ("M13 home_profile 回到主线程", BR,
     "    profile_text = await asyncio.to_thread(\n"
     "        build_profile, rt.store, max_chars=max(100, min(max_chars, 20000)))",
     "    profile_text = build_profile(rt.store, max_chars=max(100, min(max_chars, 20000)))"),
    ("M14 半衰期回到原值（两个口径）", CA,
     "    half_life = window_days / 2.0", "    half_life = lookback_days / 2.0"),
    ("M15 intent 的 limit 上下界失效", BR,
     '    limit, bad = _num(request.query_params.get("limit"), name="limit",\n'
     '                      default=3, lo=1, hi=5)',
     '    limit, bad = _num(request.query_params.get("limit"), name="limit",\n'
     '                      default=3)'),
    ("M16 mine_drift 的合法 bucket_sec=0 被吞", BR,
     "        bucket_sec=bsec, window_size=wsize, min_score=mscore,",
     "        bucket_sec=bsec or 60, window_size=wsize, min_score=mscore,"),
    ("M17 rule_channel_audit 回到吞异常改 100", BR,
     '    limit, bad = _num(request.query_params.get("limit"), name="limit",\n'
     "                      default=100, lo=1, hi=500)\n"
     "    if bad:\n        return error(bad)\n",
     '    try:\n'
     '        limit = max(1, min(500, int(request.query_params.get("limit") or "100")))\n'
     "    except ValueError:\n        limit = 100\n"),
    ("M18 min_count 回到 `or 3` 静默改值", BR,
     '    min_count, bad = _num(body.get("min_count"), name="min_count", default=3, lo=1)\n'
     "    if bad:\n        return error(bad)\n",
     '    try:\n        min_count = int(body.get("min_count") or 3)\n'
     '    except (TypeError, ValueError):\n        return error("min_count 必须是整数")\n'),
    ("M19 trigger_id 回到裸 int（无守卫）", BR,
     '    trigger_id, bad = _num(body.get("trigger_id"), name="trigger_id", default=None, lo=1)\n'
     "    if bad:\n        return error(bad)\n"
     "    if not rule_id or trigger_id is None:\n"
     '        return error("缺少 rule_id / trigger_id")\n'
     "    res = await asyncio.to_thread(\n"
     '        _lifecycle(rt).flag_false_positive, rule_id, trigger_id, "user",',
     '    trigger_id = body.get("trigger_id")\n'
     '    if not rule_id or trigger_id in (None, ""):\n'
     '        return error("缺少 rule_id / trigger_id")\n'
     "    res = await asyncio.to_thread(\n"
     '        _lifecycle(rt).flag_false_positive, rule_id, int(trigger_id), "user",'),
    ("M20 window_minutes 回到原样透传（改前的 500 路）", BR,
     '    wmin, bad = _num(body.get("window_minutes"), name="window_minutes",\n'
     "                     default=None, lo=1, hi=DAY_WINDOW_MAX * 1440)\n"
     "    if bad:\n        return error(bad)\n",
     '    wmin = body.get("window_minutes")\n'),
    ("M21 人工审核不再留痕（R3 红线的留痕步）", BR,
     '    if status in ("accepted", "rejected"):',
     # 替换串必须是**合法 Python**。run14b 这里写的是 `if status in (),`（少冒号），
     # 变异文件在 collection 阶段就 SyntaxError ⇒ pytest RC=2、0 条 FAILED 行，
     # 量具把"自己坏了"报成"没咬住"（与 run13 的 N11 空变异同族）。空元组才是"守卫恒假"。
     "    if status in ():"),
    ("M22 规则视图的冷却位写死 False", BR,
     "        in_cd = elapsed is not None and elapsed < cooldown",
     "        in_cd = False"),
]


def run_pytest():
    proc = subprocess.run([PYTEST, "-m", "pytest", "-q", TARGET], cwd=str(MUT_ROOT),
                          capture_output=True, text=True, errors="replace")
    out = proc.stdout + proc.stderr
    failed = sum(1 for line in out.splitlines() if line.startswith("FAILED"))
    tail = [line for line in out.splitlines()
            if " passed" in line or " failed" in line or "error" in line.lower()]
    return proc.returncode, failed, (tail[-1] if tail else "")


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
    rc, failed, tail = run_pytest()
    print(f"NOTHING 什么都不改档 -> RC={rc} FAILED={failed} | {tail}")
    if rc != 0:
        print("CONTROL_BAD=1  # 基线就不绿，后面的咬合读数没有意义")
        return 1

    bad = 0
    for label, rel, old, new in MUTANTS:
        raw, err = apply(rel, old, new)
        if err:
            print(f"{label} -> 锚点异常：{err}")
            bad += 1
            continue
        rc, failed, tail = run_pytest()
        path = MUT_ROOT / rel
        path.write_bytes(raw)
        restored = "OK" if path.read_bytes() == raw else "MISMATCH"
        bites = "咬住了" if rc != 0 and failed > 0 else "没咬住"
        if bites != "咬住了" or restored != "OK":
            bad += 1
        print(f"{label} -> RC={rc} FAILED={failed} {bites} restored={restored} | {tail}")
    print(f"MUT_COUNT={len(MUTANTS)} MUTATION_BAD={bad}")
    return 0 if bad == 0 else 1


sys.exit(main())
