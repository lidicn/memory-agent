"""run30 变异自证（容器内跑，副本树）：UNVERIFIED 减三格所依赖的六条"能判红"证据。

任务表 #85 收尾那一件的尺子侧：`scan_claimed_semantics.UNVERIFIED` 从 26 减到 23，
前提是这三格现在**有直接断言**——而"有断言"必须由变异腿证明，不是由用例名字证明。

跑在一次性副本树（MUT_DST，默认 `/tmp/c90mut30`）：工作树/快照树只读；
每腿注入前 `ast.parse`、锚点唯一性先判（命中 != 1 ⇒ INVALID，既不算杀也不算活），
跑完把 pristine 内容写回并复核哈希。Q-0 是对照腿（什么都不改），它必须绿。

六条腿都是把那一格**退回报告描述过的旧写法**，不是随机破坏：
R01 health 恒 True（探活方拿到"能用"）/ R02 health 只数行不看值（宽松化）/
R03 health 把异常上抛（探活被打断，不是 fail-closed）/
R04 activity_rules 的 enabled_only 失效（禁用规则混进结果）/
R05 retrieve_agent_memories 的 403 门拆掉（普通令牌不点名成员也能召回）/
R06 空 question 放行（拿空串去召回全库）。

R05/R06 只有容器（mcp>=2.0）才真跑：本机旧 SDK 取不到已注册工具 ⇒ 这两腿必须在这一档读，
否则把它们移进 REGISTRY 就是假登记（该结论已写进量具头部）。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or "/tmp/c90snap30"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or "/tmp/c90mut30"
OUT = os.environ.get("MUT_OUT") or "/tmp/c90mut30.out"

FILES = {
    "repo": os.path.join("src", "memory_agent", "insights", "repository.py"),
    "mcp": os.path.join("src", "memory_agent", "mcp_server.py"),
}
TESTS = ["tests" + os.sep + "test_vma_a8_insights_clock_and_tags.py",
         "tests" + os.sep + "test_rounds11_19_ma25_35_fixes.py"]

HEALTH_RET = '        return bool(rows) and rows[0].get("ok") == 1\n'
HEALTH_CATCH = ('        except Exception as exc:\n'
                '            self.log.warning("health 检查失败: %s", exc)\n'
                '            return False\n')
RULES_WHERE = ('               "min_events, confidence, note, enabled FROM activity_rules")\n'
               '        if enabled_only:\n')
MCP_403 = ('        # DCD裁定1: fail-close——member_id 缺失时只允许 admin 审计通道\n'
           '        if not member_id:\n'
           '            _tok, _scopes, _origin = _caller_context()\n'
           '            if "admin" not in (_scopes or []):\n')
MCP_EMPTYQ = ('        q = question or query\n'
              '        if not q:\n')

LEGS = [
    ("R01", "repo", HEALTH_RET, "        return True\n",
     "health 恒 True ⇒ 库不可达时探活照样报健康"),
    ("R02", "repo", HEALTH_RET, "        return bool(rows)\n",
     "health 只数行不看探针值 ⇒ SELECT 1 被换成别的形状也算健康"),
    ("R03", "repo", HEALTH_CATCH,
     "        except Exception:\n            raise\n",
     "health 把异常上抛 ⇒ 不是 fail-closed，而是让调用方崩"),
    ("R04", "repo", RULES_WHERE,
     '               "min_events, confidence, note, enabled FROM activity_rules")\n'
     '        if False:\n',
     "activity_rules 的 enabled_only 失效 ⇒ 禁用规则混进引擎输入"),
    ("R05", "mcp", MCP_403,
     '        # DCD裁定1: fail-close——member_id 缺失时只允许 admin 审计通道\n'
     '        if not member_id:\n'
     '            _tok, _scopes, _origin = _caller_context()\n'
     '            if False:\n',
     "retrieve_agent_memories 的 403 门拆掉 ⇒ 普通令牌不点名成员也能召回"),
    ("R06", "mcp", MCP_EMPTYQ,
     '        q = question or query\n'
     '        if False:\n',
     "空 question 放行 ⇒ 拿空串去召回全库"),
]


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def to_eol(text, eol):
    return text.replace("\n", eol) if eol != "\n" else text


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
    log(f"COPY_TREE_READY={DST} files={len(os.listdir(os.path.join(DST, 'src', 'memory_agent')))}")


def run_pytest():
    # `-r` 是单选项：写成 "-rf" "-rs" 两串会让后一个整体覆盖前一个 ⇒ FAIL 名单被吞（实测）。
    # 所以合并成一串 fEs（failed + error + skipped 三种 short summary 都要）。
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-rfEs", "--tb=no", "-p", "no:cacheprovider"],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = sorted({ln.split(" ")[1].split("::")[-1].split(" - ")[0]
                     for ln in out.splitlines() if ln.startswith("FAILED ")})
    tail = [ln.strip() for ln in out.splitlines()
            if any(w in ln for w in ("passed", "failed", "error")) and any(c.isdigit() for c in ln)]
    skipped = [ln for ln in out.splitlines() if ln.startswith("SKIPPED")]
    return proc.returncode, failed, (tail[-1] if tail else "NO SUMMARY LINE"), skipped


def main():
    lines = []

    def log(msg):
        lines.append(str(msg))
        with open(OUT, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines) + "\n")

    wt_before = {rel: sha(read(os.path.join(WT, rel))) for rel in FILES.values()}
    build_copy(log)
    pristine = {tag: read(os.path.join(DST, rel)) for tag, rel in FILES.items()}

    rc, failed, summary, skipped = run_pytest()
    log(f"[Q-0] 对照腿（什么都不改）rc={rc} summary={summary!r} failed={failed} skipped={len(skipped)}")
    for ln in skipped:
        log(f"[Q-0] SKIPPED {ln}")
    if rc != 0 or failed:
        log("Q-0 不绿：副本树基线本身有问题，本轮所有读数作废，停在这里。")
        return 1
    log("Q-0 绿：副本树与快照树同口径，后续红都是注入造成的。")

    killed = survived = invalid = 0
    for leg, tag, anchor, new, defect in LEGS:
        rel = FILES[tag]
        path = os.path.join(DST, rel)
        text = pristine[tag]
        eol = "\r\n" if "\r\n" in text else "\n"
        a = to_eol(anchor, eol)
        n = text.count(a)
        if n != 1:
            invalid += 1
            log(f"[{leg}] INVALID 锚点命中 {n} 次（要求恰好 1 次），未注入：{defect}")
            continue
        mutated = text.replace(a, to_eol(new, eol), 1)
        try:
            ast.parse(mutated)
        except SyntaxError as exc:
            invalid += 1
            log(f"[{leg}] INVALID 注入后语法不过（{exc.msg} @ line {exc.lineno}），未跑：{defect}")
            continue
        write(path, mutated)
        try:
            rc, failed, summary, skipped = run_pytest()
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
