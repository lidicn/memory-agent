"""任务表 #85（2期 第十一轮~第十九轮 MA-25~MA-35）变异自证 harness。

跑在一次性副本树（默认 `.qoder/tmp-c85-mut/`），工作树只读：每腿注入前 `ast.parse`、
锚点唯一性先判（命中 != 1 ⇒ INVALID，不算杀也不算活），跑完把 pristine 内容写回并复核哈希。
Q-0 是对照腿（什么都不改），它必须绿。

每条腿都是把那一格的修复**逐字退回报告描述的旧写法**，不是随机破坏：
Q01 旧 provider 不关 / Q02 版本基数只看出入参 / Q03 空成员回显 "all" /
Q04 越界枚举放行 / Q05 dry_run 抢在校验前假回 / Q06 零实现端点报"已执行" /
Q07 顶层 handler 吞掉异常 / Q08 外壳接住但不重跑 / Q09 同步判据回到文件存在性 /
Q10 清理回到跑一轮就退 / Q11 失败后等满一个周期才重试。

Q02 的锁在本机会 skip（`mcp` SDK 过旧取不到已注册工具），只有容器 3.11 那档才真跑 ⇒
本机这腿必须靠控制腿之外的容器档判存活，读数与容器档并立登记。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or r"E:\NAS\memory-agent"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or os.path.join(WT, ".qoder", "tmp-c85-mut")
OUT = os.environ.get("MUT_OUT") or os.path.join(WT, ".qoder", "tmp-c85-mut.out")

FILES = {
    "llm": os.path.join("src", "memory_agent", "llm_client.py"),
    "mcp": os.path.join("src", "memory_agent", "mcp_server.py"),
    "amem": os.path.join("src", "memory_agent", "agent_memory.py"),
    "sig": os.path.join("src", "memory_agent", "signal_learning.py"),
    "nr": os.path.join("src", "memory_agent", "api", "nr_routes.py"),
    "rt": os.path.join("src", "memory_agent", "runtime.py"),
}
TESTS = ["tests" + os.sep + "test_rounds11_19_ma25_35_fixes.py"]

LEGS = [
    ("Q01", "llm",
     "        for p in stale:\n",
     "        for p in []:\n",
     "MA-25 回归：热更新整表重建后不关旧 provider ⇒ 每次 /api/config 漏一批 httpx.AsyncClient"),
    ("Q02", "mcp",
     "        for src in (existing, _read_skill_meta(path) if os.path.isfile(path) else {}):",
     "        for src in (existing,):",
     "MA-26 回归：版本基数只从入参 frontmatter 取 ⇒ 入参不带版本号时把盘上 v5 覆盖成 v1"),
    ("Q03", "amem",
     '            "member_id": member_id or "",',
     '            "member_id": member_id or "all",',
     "MA-27 回归：fail-closed 只回公共记忆，响应却把自己说成跨成员全量"),
    ("Q04", "sig",
     "        if exclusion_type not in EXCLUSION_TYPES:",
     "        if False:",
     "MA-28 回归：越界 exclusion_type 不再被拦，直接写进 signal_exclusions"),
    ("Q05", "sig",
     '        entity_id = (entity_id or "").strip()',
     '        if dry_run:\n'
     '            return {"ok": True, "dry_run": True, "message": "参数校验通过"}\n'
     '        entity_id = (entity_id or "").strip()',
     "MA-28 回归：dry_run 抢在校验之前回假成功（一行判据都没走）"),
    ("Q06", "nr",
     '    return error(\n'
     '        f"未实现：本服务不执行动作（{action_type or \'未指定\'} -> {target or \'未指定\'}），"\n'
     '        "未实际执行任何设备操作",\n'
     '        501,\n'
     '    )\n',
     '    return ok({"executed": True, "detail": f"动作已执行: {action_type} -> {target}"})\n',
     "MA-30 回归：零实现端点回到 200 + 「动作已执行」"),
    ("Q07", "rt",
     '            print(f"[SelfDiary] 循环退出: {e}")\n            raise\n',
     '            print(f"[SelfDiary] 循环退出: {e}")\n',
     "MA-31 回归：顶层 handler 吞掉异常，外壳收不到 ⇒ 无从重启"),
    ("Q08", "rt",
     '            except Exception as e:  # noqa: BLE001\n'
     '                print(f"[SelfDiary] 任务异常，{backoff}s 后重启: {e}")\n'
     '                await asyncio.sleep(backoff)\n'
     '                backoff = min(backoff * 2, 3600)\n',
     '            except Exception as e:  # noqa: BLE001\n'
     '                print(f"[SelfDiary] 任务异常: {e}")\n'
     '                return\n',
     "MA-31 回归：接住就 return（旧结构），一次异常 ⇒ 任务永久停摆"),
    ("Q09", "mcp",
     "            if disk_version >= bundled_version:",
     "            if disk_version is not None:",
     "MA-33 回归：同步判据退回\"盘上有文件就跳过\" ⇒ 旧部署永久停在旧版且无日志"),
    ("Q10", "rt",
     "        while True:\n            try:\n                removed = await asyncio.to_thread(\n",
     "        while False:\n            try:\n                removed = await asyncio.to_thread(\n",
     "MA-35 回归：保留期清理回到只在启动跑一次 ⇒ 长跑容器里 events 跨度远超 90 天"),
    ("Q11", "rt",
     "                await asyncio.sleep(min(interval, 600))",
     "                await asyncio.sleep(interval)",
     "MA-35 回归：清理失败后等满一个周期才重试（默认 86400s）而非短退避"),
]

if len(sys.argv) > 1 and sys.argv[1] != "all":
    wanted = {x.strip() for x in sys.argv[1].split(",") if x.strip()}
    LEGS = [leg for leg in LEGS if leg[0] in wanted]


def read(p):
    with open(p, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def to_eol(text, eol):
    """锚点按目标文件自己的行尾匹配：本机 `core.autocrlf=true` ⇒ 工作副本是 CRLF，
    而入库 blob 是 LF（`.gitattributes` `* text=auto eol=lf`）。跨行锚点若只写 `\\n`，
    在 CRLF 的盘上副本上命中 0 次 —— 那是量具自己瞎，不是靶面没动。"""
    if eol == "\r\n":
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text.replace("\r\n", "\n")


def write(p, text):
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_copy(log):
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(os.path.join(DST, "tests"))
    shutil.copy2(os.path.join(WT, "pytest.ini"), os.path.join(DST, "pytest.ini"))
    shutil.copytree(os.path.join(WT, "src"), os.path.join(DST, "src"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in TESTS:
        shutil.copy2(os.path.join(WT, rel), os.path.join(DST, rel))
    log(f"copy tree ready: {DST}")


def run_pytest():
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-rf", "--tb=no", "-p", "no:cacheprovider"],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = sorted({ln.split(" ")[1].split("::")[-1]
                     for ln in out.splitlines() if ln.startswith("FAILED ")})
    tail = [ln.strip() for ln in out.splitlines()
            if any(w in ln for w in ("passed", "failed", "error")) and any(c.isdigit() for c in ln)]
    return proc.returncode, failed, (tail[-1] if tail else "NO SUMMARY LINE")


def main():
    lines = []

    def log(msg):
        lines.append(str(msg))
        with open(OUT, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines) + "\n")

    wt_before = {rel: sha(read(os.path.join(WT, rel))) for rel in FILES.values()}
    build_copy(log)
    pristine = {tag: read(os.path.join(DST, rel)) for tag, rel in FILES.items()}

    rc, failed, summary = run_pytest()
    log(f"[Q-0] 对照腿（什么都不改）rc={rc} summary={summary!r} failed={failed}")
    if rc != 0 or failed:
        log("Q-0 不绿：副本树基线本身有问题，本轮所有读数作废，停在这里。")
        return 1
    log("Q-0 绿：副本树与工作树同口径，后续红都是注入造成的。")

    killed = survived = invalid = 0
    for leg, tag, anchor, new, defect in LEGS:
        rel = FILES[tag]
        path = os.path.join(DST, rel)
        text = pristine[tag]
        eol = "\r\n" if "\r\n" in text else "\n"
        anchor = to_eol(anchor, eol)
        new = to_eol(new, eol)
        n = text.count(anchor)
        if n != 1:
            invalid += 1
            log(f"[{leg}] INVALID 锚点命中 {n} 次（要求恰好 1 次），未注入：{defect}")
            continue
        mutated = text.replace(anchor, new, 1)
        try:
            ast.parse(mutated)
        except SyntaxError as exc:
            invalid += 1
            log(f"[{leg}] INVALID 注入后语法不过（{exc.msg} @ line {exc.lineno}），未跑：{defect}")
            continue
        write(path, mutated)
        try:
            rc, failed, summary = run_pytest()
        finally:
            write(path, pristine[tag])
            if sha(read(path)) != sha(pristine[tag]):
                log(f"[{leg}] !! 还原失败：{rel} 哈希不符")
        if failed:
            killed += 1
            log(f"[{leg}] KILLED {rel} :: {defect}\n        {summary} :: 红用例 {len(failed)} 条: "
                + ", ".join(failed[:6]) + (" ..." if len(failed) > 6 else ""))
        elif rc != 0:
            killed += 1
            log(f"[{leg}] KILLED(非零退出，无用名) {rel} :: {defect} :: {summary}")
        else:
            survived += 1
            log(f"[{leg}] SURVIVED !! {rel} :: {defect} :: {summary}")

    wt_after = {rel: sha(read(os.path.join(WT, rel))) for rel in FILES.values()}
    log(f"WT_UNTOUCHED={wt_before == wt_after}")
    log(f"totals: killed={killed} survived={survived} invalid={invalid} legs={len(LEGS)}")
    log(f"python: {sys.version.split()[0]} harness-exe={PY}")
    return 0 if survived == 0 and invalid == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
