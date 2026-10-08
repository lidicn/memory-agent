"""任务表 #68 第四批（MA-24 路由层卸载 + 量具身体门）的变异自咬 harness。

口径与前面几批一致：
- 每条 mutant 单独应用、单独跑目标用例，**RC≠0 且必须有 FAILED** 才算咬住；
- 还原后与原字节逐字节比对（restored_identical），对不上直接算 MUTATION_BAD；
- `NOTHING` 是"什么都不改"档，必须绿，否则这批的锁是自证的假绿。

与 #64 那一支的差别只有一处工程细节：本批改的是 **api/ 下那六份路由**，而其中
`agent_memory_routes / member_routes / signal_routes / mcp_routes` 在工作树里是 **CRLF**
（`core.autocrlf=true` + `.gitattributes` 的 `* text=auto eol=lf`，HEAD 侧才是 LF）。
所以 `apply()` 先把文本按 LF 匹配锚点、写回时按该文件自己的行尾重新发一次，
还原走 `write_bytes(raw)` 的原始字节 ⇒ `restored_identical` 仍然是逐字节比对。

15 条腿分四族：
- M1–M6 卸载落点退回直调（gauge 一层/展开两档 + LANDING 落点锁 + 线程证据，三处都该红）
- M7–M8 "合批"的那两处退回（归一化/第一步取数回到协程里）
- M9–M11 `_num` 的上下界各摘一格（`hi` / `hi` / `lo`），对应三条边界用例
- M12 **反向腿**：给内存 token 校验加卸载 ⇒ `test_token_store_legs_stay_on_loop` 必须红
  （这一格是本批自己踩过的坑：照着坏读数去卸载，比不卸载更糟）
- M13–M15 量具自身：身体门摘掉 / 一层口径的 store 判定摘掉 / subsys 面退化
  ⇒ 自证档与两条副本树咬合档必须红

`--verify` 是出网前的预飞档：只量锚点命中次数 + 变异后能否 compile + 字节还原，
不跑 pytest（run14b 的教训：M21 少个冒号 ⇒ 容器侧 RC=2/0 FAILED 被读成"没咬住"）。
"""
import os
import pathlib
import subprocess
import sys

MUT_ROOT = pathlib.Path(os.environ.get("MUT_ROOT", pathlib.Path(__file__).resolve().parents[1]))
TARGET = os.environ.get(
    "PYTEST_TARGET", "tests/test_vma_phase2_batch4_offload.py")
PYTEST = sys.executable

AMR = "src/memory_agent/api/agent_memory_routes.py"
MBR = "src/memory_agent/api/member_routes.py"
SGR = "src/memory_agent/api/signal_routes.py"
VRS = "src/memory_agent/api/vision_routes.py"
LLR = "src/memory_agent/api/llm_routes.py"
MCR = "src/memory_agent/api/mcp_routes.py"
ACP = "src/memory_agent/acp_auth.py"
GAUGE = "scripts/scan_unloaded_async_io.py"

MUTANTS = [
    # ── 卸载落点退回直调 ────────────────────────────────────────────────
    ("M1 list_memories 退回协程直调（agent_memory 面）", AMR,
     "    result = await asyncio.to_thread(rt.agent_memory.list_agent_memories, state, source)",
     "    result = rt.agent_memory.list_agent_memories(state, source)"),
    ("M2 member_list 退回协程直调（store 面）", MBR,
     "    members = await asyncio.to_thread(runtime(request).store.list_members)",
     "    members = runtime(request).store.list_members()"),
    ("M3 events_face 退回协程直调（vision 面）", VRS,
     "    result = await asyncio.to_thread(\n"
     "        rt.vision.record_face_event, room, persons, trigger, device_ts,\n"
     "        camera=body.get(\"camera\"), client=client,\n"
     "    )",
     "    result = rt.vision.record_face_event(\n"
     "        room, persons, trigger, device_ts, camera=body.get(\"camera\"), client=client\n"
     "    )"),
    ("M4 signal list_rules 退回协程直调", SGR,
     "    data = await asyncio.to_thread(\n"
     "        rt.signal_learning.list_rules, include_revoked=include_revoked\n"
     "    )",
     "    data = rt.signal_learning.list_rules(include_revoked=include_revoked)"),
    ("M5 mcp list_audit 退回协程直调", MCR,
     "    rows = await asyncio.to_thread(\n"
     "        runtime(request).store.list_mcp_audit,\n"
     "        limit=limit,",
     "    rows = runtime(request).store.list_mcp_audit(\n"
     "        limit=limit,"),
    ("M6 llm_cache_clear 退回协程直调", LLR,
     "    deleted = await asyncio.to_thread(runtime(request).store.clear_answer_cache, key)",
     "    deleted = runtime(request).store.clear_answer_cache(key)"),
    # ── "合批"的两处退回 ────────────────────────────────────────────────
    ("M7 外观档案的归一化不再进线程（两跳合一被拆掉）", MBR,
     "        return store.update_member(member_id, appearance_json=store._normalize_appearance(appearance))",
     "        return store.update_member(member_id, appearance_json=appearance)"),
    ("M8 insight_feedback 的第一步取数回到协程里", MBR,
     "    data = await asyncio.to_thread(_fetch)",
     "    member = store.get_member(member_id)\n"
     "    data = await asyncio.to_thread(store.member_insight_feedback,\n"
     "                                   member_id, member.get(\"name\", \"\"))"),
    # ── _num 上下界各摘一格 ─────────────────────────────────────────────
    ("M9 list_audit 的上限摘掉（1001 会下推到 SQLite）", MCR,
     "    limit, bad = _num(q.get(\"limit\"), name=\"limit\", default=100, lo=1, hi=1000)",
     "    limit, bad = _num(q.get(\"limit\"), name=\"limit\", default=100, lo=1)"),
    ("M10 list_audit 的下界放松到 0（0 档不再被拒）", MCR,
     "    limit, bad = _num(q.get(\"limit\"), name=\"limit\", default=100, lo=1, hi=1000)",
     "    limit, bad = _num(q.get(\"limit\"), name=\"limit\", default=100, lo=0, hi=1000)"),
    ("M11 member_tag 的 confidence 上界摘掉（1.5 一路写进库）", MBR,
     "    confidence, conf_err = _num(body.get(\"confidence\"), name=\"confidence\",\n"
     "                                 cast=float, default=0.0, lo=0.0, hi=1.0)",
     "    confidence, conf_err = _num(body.get(\"confidence\"), name=\"confidence\",\n"
     "                                 cast=float, default=0.0, lo=0.0)"),
    # ── 反向腿：不该卸载的那两跳被卸载 ─────────────────────────────────
    ("M12 给内存 token 校验加线程跳转（#51 裁定被推翻）", ACP,
     "        name = store.verify(token) if token else None",
     "        name = await asyncio.to_thread(store.verify, token) if token else None"),
    # ── 量具自身 ────────────────────────────────────────────────────────
    ("M13 量具身体门摘掉：叫 store 一律判红（纯内存也响）", GAUGE,
     '        return None                    # 同步但纯内存（MCPTokenStore 那一族）⇒ 不是缺陷',
     '        return "store"                 # MUT13 身体门摘掉'),
    ("M14 量具一层口径失效（真 SQL 也要等展开才响）", GAUGE,
     "        if all(id(c) in shallow_ids for c in cand):",
     "        if False and all(id(c) in shallow_ids for c in cand):"),
    ("M15 subsys 面退化：不再按属性解析到类", GAUGE,
     '    return "subsys" if _resolve_via_attr(chain, ctx) else "transitive"',
     '    return "transitive"                 # MUT15 不再区分 subsys'),
]


def _read_text(path: pathlib.Path) -> tuple:
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    return raw, text_of(raw, crlf), crlf


def text_of(raw: bytes, crlf: int) -> str:
    text = raw.decode("utf-8")
    # 只在"整份统一 CRLF"时按 LF 匹配锚点；混行尾的文件不在这里替它决定
    return text.replace("\r\n", "\n") if crlf and text.count("\n") == crlf else text


def emit(text: str, crlf: int) -> bytes:
    return text.replace("\n", "\r\n").encode("utf-8") if crlf else text.encode("utf-8")


def run_pytest():
    proc = subprocess.run([PYTEST, "-m", "pytest", "-q", *TARGET.split()],
                          cwd=str(MUT_ROOT), capture_output=True, text=True, errors="replace")
    out = proc.stdout + proc.stderr
    failed = sum(1 for line in out.splitlines() if line.startswith("FAILED"))
    syntax = "SyntaxError" in out or "INTERNALERROR" in out
    tail = [line for line in out.splitlines()
            if " passed" in line or " failed" in line or "error" in line.lower()]
    return proc.returncode, failed, (tail[-1] if tail else ""), syntax


def verify_only() -> int:
    """出网前预飞：锚点必须恰好命中 1 次、变异后必须能 compile、原字节必须能整份还原。"""
    bad = 0
    for label, rel, old, new in MUTANTS:
        path = MUT_ROOT / rel
        raw, text, crlf = _read_text(path)
        n = text.count(old)
        if n != 1:
            print("BAD %s 锚点命中 %d 次（需要恰好 1 次）" % (label, n))
            bad += 1
            continue
        mutated = text.replace(old, new, 1)
        try:
            compile(mutated, rel, "exec")
        except SyntaxError as exc:
            print("SYNTAX %s -> %s" % (label, exc))
            bad += 1
            continue
        # 写一遍再按原字节还原：验证"能进也能出"，容器侧那条腿就靠这个保字节
        try:
            path.write_bytes(emit(mutated, crlf))
        finally:
            path.write_bytes(raw)
        if path.read_bytes() != raw:
            print("RESTORE %s -> 字节对不上" % label)
            bad += 1
        if path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            print("RESTORE %s -> 内容对不上" % label)
            bad += 1
    print("MUTANTS_PARSED=%d SYNTAX_BAD=0 ANCHOR_OR_RESTORE_BAD=%d" % (len(MUTANTS), bad))
    return 0 if bad == 0 else 1


def main():
    if "--verify" in sys.argv:
        return verify_only()
    rc, failed, tail, syntax = run_pytest()
    print(f"NOTHING 什么都不改档 -> RC={rc} FAILED={failed} | {tail}")
    if rc != 0 or syntax:
        print("CONTROL_BAD=1  # 基线就不绿，后面的咬合读数没有意义")
        return 1

    bad = 0
    for label, rel, old, new in MUTANTS:
        path = MUT_ROOT / rel
        raw, text, crlf = _read_text(path)
        n = text.count(old)
        if n != 1:
            print(f"{label} -> 锚点异常：命中 {n} 次")
            bad += 1
            continue
        path.write_bytes(emit(text.replace(old, new, 1), crlf))
        rc, failed, tail, syntax = run_pytest()
        path.write_bytes(raw)
        restored = "OK" if path.read_bytes() == raw else "MISMATCH"
        if syntax:
            bites = "量具自伤(SyntaxError)"
        else:
            bites = "咬住了" if rc != 0 and failed > 0 else "没咬住"
        if bites != "咬住了" or restored != "OK":
            bad += 1
        print(f"{label} -> RC={rc} FAILED={failed} {bites} restored={restored} | {tail}")
    print(f"MUT_COUNT={len(MUTANTS)} MUTATION_BAD={bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
