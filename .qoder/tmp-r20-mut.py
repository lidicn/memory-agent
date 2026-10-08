"""第二十轮 MA-36 / MA-37 变异自证 harness。

跑在一次性副本树（默认 `.qoder/tmp-r20-mut/`），不碰工作树：每腿注入前 `ast.parse`、
锚点唯一性先判、跑完把 pristine 内容写回并复核哈希。P-0 是对照腿（什么都不改），
它必须绿——否则这一轮的"红"是环境问题而不是判据。

六条腿各自对应一种"护栏退回去"的写法：P01/P05 是 MA-36 的两种松法（整条删掉 /
只对存在的会话校验），P02/P03 是 MA-37 的两种松法（覆盖属主 / 冲突炸成 500），
P04 是"新建会话不带属主"这半条（A 读不到自己的历史）。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or r"E:\NAS\memory-agent"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or os.path.join(WT, ".qoder", "tmp-r20-mut")
OUT = os.environ.get("MUT_OUT") or os.path.join(WT, ".qoder", "tmp-r20-mut.out")

FILES = {
    "acp": os.path.join("src", "memory_agent", "acp_server.py"),
}
TESTS = ["tests" + os.sep + t for t in (
    "test_acp_round20_owner_isolation.py",
    "test_acp_session_cross_owner_denied.py",
    "test_acp_server.py",
)]

LEGS = [
    ("P01", "acp",
     '        if requested and not _STORE.check_owner(requested, _owner):\n'
     '            return make_error(req_id, ERR_INVALID_PARAMS, "无权访问该会话（属主不匹配）"), None\n',
     '        if False:\n'
     '            return make_error(req_id, ERR_INVALID_PARAMS, "无权访问该会话（属主不匹配）"), None\n',
     "MA-36 回归：prompt 完全不校验属主（B 可读可写 A 的会话）"),
    ("P02", "acp",
     "        if prev is not None and prev.get(\"owner_token\") != owner_token:\n"
     "            raise SessionOwnerConflict(sid)\n",
     "        if False:\n"
     "            raise SessionOwnerConflict(sid)\n",
     "MA-37 回归：new() 回到无条件覆盖 ⇒ 后来者改写属主"),
    ("P03", "acp",
     '        except SessionOwnerConflict:\n'
     '            return make_error(req_id, ERR_INVALID_PARAMS, "sessionId 已被占用（属主不匹配）"), None\n',
     '        except KeyError:\n'
     '            return make_error(req_id, ERR_INVALID_PARAMS, "sessionId 已被占用（属主不匹配）"), None\n',
     "接错异常类型：属主冲突不再回错误信封，而是炸穿成 500"),
    ("P04", "acp",
     "        session_id = requested or _STORE.new(owner_token=_owner)\n",
     "        session_id = requested or _STORE.new()\n",
     "新建会话不带属主：A 随后读自己的历史被 fail-close 拒掉"),
    ("P05", "acp",
     "        if requested and not _STORE.check_owner(requested, _owner):\n",
     "        if requested and _STORE.get(requested) is not None \\\n"
     "                and not _STORE.check_owner(requested, _owner):\n",
     "只对会话表里存在的会话校验：被 _trim 裁掉、历史仍在的会话成了越权入口"),
    ("P06", "acp",
     "        if requested and not _STORE.check_owner(requested, _owner):\n",
     "        if requested and not _STORE.check_owner(requested, _owner) and _owner:\n",
     "空主体放行：未带 scope 的入口直接绕过属主校验"),
]

if len(sys.argv) > 1 and sys.argv[1] != "all":
    wanted = {x.strip() for x in sys.argv[1].split(",") if x.strip()}
    LEGS = [leg for leg in LEGS if leg[0] in wanted]


def read(p):
    with open(p, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


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
    log(f"[P-0] 对照腿（什么都不改）rc={rc} summary={summary!r} failed={failed}")
    if rc != 0 or failed:
        log("P-0 不绿：副本树基线本身有问题，本轮所有读数作废，停在这里。")
        return 1
    log("P-0 绿：副本树与工作树同口径，后续红都是注入造成的。")

    killed = survived = invalid = 0
    for leg, tag, anchor, new, defect in LEGS:
        rel = FILES[tag]
        path = os.path.join(DST, rel)
        text = pristine[tag]
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
