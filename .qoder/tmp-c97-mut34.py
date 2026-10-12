"""run34 变异自证（副本树）：审计 20261012 三件（D1/D2/D3）+ 契约 §7.1 status JSON 的"能判红"证据。

这三件的共同形状是**一句护栏承诺写在代码里**：坏 env 回落默认值、每趟请求一个随机临时名、
每路探测各自限时 / VACUUM 仍在业务锁内、status 载荷的键与版本号单源。
每条腿把那一句承诺退回坏的那侧，必须让 `tests/test_vma_audit_20261012_fixes.py`（15 条）
或 presence 载荷用例当场判红：

    D1（导入期裸 int(os.getenv) 崩启动）
      E01 畸形值不再被 except 兜住 ⇒ 一个手滑的 env 杀死 import 链路
      E02 越界回落整段关掉 ⇒ 0 / 负值把护栏语义反转
      D01 一个读数点退回裸 int(os.getenv) ⇒ 第六把门当场点名
    D2（固定名 .tmp 被两趟请求共写）
      S01 临时名退回 MARKER_PATH + ".tmp"（静态锁 + 两趟实测双重杀）
    D3（health 无总超时 × 锁无超时 × VACUUM 持锁）
      H01 每路 wait_for 摘掉 ⇒ 一路慢探针决定 /health 的耗时
      C01 等锁不再限时 ⇒ 超阈值的 warning 永远不会出现（"谁在抢锁"重新变回猜）
      V01 VACUUM 挪出业务锁 ⇒ 这一条是**明确不采纳**的 D3 建议，挪出去就把健康面卡顿
          换成写路径 SQLITE_BUSY，所以它必须由用例判红，而不是只写在注释里
    契约 §7.1（adm/*/status 的 JSON 形状与版本单源）
      M01 status.version 写死 "1.4" ⇒ 与 caps.version 两个号，DB 侧对账永远对不上
      M02 presence 分支只看"库在不在场" ⇒ 0.3.1 那版发裸字面量，两条路不同构
      M03 version 键改名 ⇒ §7.1 的键集合缺位
      M04 degraded 恒 False ⇒ 降级原因还在载荷里，健康位却说不降级
      M05 签名探测恒真 ⇒ 旧库被当成新库，advertise(version=) 直接 TypeError

跑法与 run33 一致：MUT_ROOT 指快照树/工作树，注入只发生在一次性副本树 MUT_DST；
锚点命中 != 1 ⇒ INVALID；注入后先 `ast.parse`（响过 ≠ 响对）；Q-0 对照腿必须绿；
跑完复核工作树哈希并出 WT_UNTOUCHED。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or "/tmp/c97snap34"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or "/tmp/c97mut34"
OUT = os.environ.get("MUT_OUT") or "/tmp/c97mut34.out"

M = os.path.join("src", "memory_agent")
FILES = {
    "env": os.path.join(M, "env_cfg.py"),
    "sr": os.path.join(M, "api", "system_routes.py"),
    "st": os.path.join(M, "store.py"),
    "mb": os.path.join(M, "mqtt_bridge.py"),
    "rt": os.path.join(M, "runtime.py"),
}
TESTS = [
    os.path.join("tests", "test_vma_audit_20261012_fixes.py"),
    os.path.join("tests", "test_vma_step1_adm_presence.py"),
    os.path.join("tests", "test_vma_dcd_20261002_payload.py"),
]

# ── 锚点片段（逐字取自现树；命中次数由预检格现量）────────────────────────────
EXCEPT_TUPLE = '    except (TypeError, ValueError, OverflowError):\n'
EXCEPT_NARROW = '    except (OverflowError,):\n'
RANGE_IF = '    if (lo is not None and value < lo) or (hi is not None and value > hi):\n'
RANGE_OFF = '    if False:\n'
VOICE_ENV_INT = '_VOICE_CACHE_MAX = env_int("MA_VOICE_CACHE_MAX", 2000, lo=1)\n'
VOICE_BARE_INT = '_VOICE_CACHE_MAX = int(os.getenv("MA_VOICE_CACHE_MAX", "2000"))\n'

TMP_LINE = '    tmp_path = "%s.tmp.%s" % (MARKER_PATH, uuid.uuid4().hex[:8])\n'
TMP_FIXED = '    tmp_path = MARKER_PATH + ".tmp"\n'

WAITFOR_LINE = '            *(asyncio.wait_for(t, HEALTH_PROBE_TIMEOUT_SECONDS) for t in\n'
PLAIN_T = '            *(t for t in\n'
ACQUIRE_WARN = '        if not self._lock.acquire(timeout=DB_LOCK_WARN_AFTER_SECONDS):\n'
ACQUIRE_BLOCK = '        if not self._lock.acquire(timeout=None):\n'
VACUUM_BLOCK = ('            with self._lock:\n'
                '                conn.execute("VACUUM")\n')
VACUUM_NOLOCK = '            conn.execute("VACUUM")\n'

RET_PLAN = '    return PLAN_VERSION\n'
RET_14 = '    return "1.4"\n'
PRESENCE_IF = '        if presence and _presence_carries_plan_version(presence):\n'
PRESENCE_IFONLY = '        if presence:\n'
VER_KEY = '        "version": _status_version(),\n'
VER_KEY_RENAMED = '        "plan_version": _status_version(),\n'
DEGRADED_LINE = '        "degraded": bool(reasons),\n'
DEGRADED_FALSE = '        "degraded": False,\n'
SIG_CHECK = '        return "version" in inspect.signature(presence.advertise).parameters\n'
SIG_TRUE = '        return True\n'

LEGS = [
    ("E01", "env", EXCEPT_TUPLE, EXCEPT_NARROW,
     "畸形值不再兜住 ⇒ MA_VOICE_CACHE_MAX=2000条 让 import memory_agent.store 直接崩，容器起不来"),
    ("E02", "env", RANGE_IF, RANGE_OFF,
     "越界回落关掉 ⇒ 0 / 负值照样生效，护栏上限变成 0 等于整条护栏关闭"),
    ("D01", "st", VOICE_ENV_INT, VOICE_BARE_INT,
     "读数点退回导入期裸 int(os.getenv) ⇒ 第六把门必须当场点名它"),
    ("S01", "sr", TMP_LINE, TMP_FIXED,
     "临时名退回固定 .tmp ⇒ 两趟并发请求共写一个文件，marker 的 ref 与 requested_by 可能不同源"),
    ("H01", "rt", WAITFOR_LINE, PLAIN_T,
     "每路限时摘掉 ⇒ VACUUM 期间 store.stats 一路慢就把 /health 拖到超时被杀"),
    ("C01", "st", ACQUIRE_WARN, ACQUIRE_BLOCK,
     "等锁不再限时 ⇒ 超阈值永远不记 warning，「谁在抢锁、抢了多久」重新变成猜"),
    ("V01", "st", VACUUM_BLOCK, VACUUM_NOLOCK,
     "VACUUM 挪出业务锁（D3 第三条明确不采纳）⇒ 独立连接与业务写在 SQLite 层相撞"),
    ("M01", "mb", RET_PLAN, RET_14,
     "status.version 写死 1.4 ⇒ 与 caps.version（PLAN_VERSION）两个号，DB 侧按 §7.1 对不上账"),
    ("M02", "mb", PRESENCE_IF, PRESENCE_IFONLY,
     "只看库在不在场 ⇒ 0.3.1 那版 advertise 只会发裸字面量 online，两条路不同构"),
    ("M03", "mb", VER_KEY, VER_KEY_RENAMED,
     "§7.1 的 version 键改名 ⇒ 消费者按契约解析时拿不到计划号"),
    ("M04", "mb", DEGRADED_LINE, DEGRADED_FALSE,
     "degraded 恒 False ⇒ reasons 里还挂着降级原因，健康位却说一切正常"),
    ("M05", "mb", SIG_CHECK, SIG_TRUE,
     "签名探测恒真 ⇒ 旧库被当成带计划号的那版，advertise(version=) 直接 TypeError"),
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
    for sub in ("src", "scripts"):
        shutil.copytree(os.path.join(WT, sub), os.path.join(DST, sub),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in TESTS:
        shutil.copy2(os.path.join(WT, rel), os.path.join(DST, rel))
    log(f"COPY_TREE_READY={DST} src_files={len(os.listdir(os.path.join(DST, M)))}")


def run_pytest():
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-rfEs", "--tb=no", "-p", "no:cacheprovider"],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800)
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

    if os.environ.get("MUT_ANCHOR_ONLY") == "1":
        texts = {tag: read(os.path.join(WT, rel)) for tag, rel in FILES.items()}
        bad = 0
        for leg, tag, anchor, new, defect in LEGS:
            text = texts[tag]
            eol = "\r\n" if "\r\n" in text else "\n"
            n = text.count(to_eol(anchor, eol))
            msg = ""
            ok_parse = True
            if n == 1:
                try:
                    ast.parse(text.replace(to_eol(anchor, eol), to_eol(new, eol), 1))
                except SyntaxError as exc:
                    ok_parse = False
                    msg = " 语法不过(%s @ line %s)" % (exc.msg, exc.lineno)
            note = "" if n == 1 else " 命中 %d 次" % n
            status = "OK" if n == 1 and ok_parse else "BAD"
            if status == "BAD":
                bad += 1
            log(f"[{leg}] {status} {FILES[tag]}{msg}{note}")
        log("ANCHOR_OK=%d ANCHOR_BAD=%d legs=%d" % (len(LEGS) - bad, bad, len(LEGS)))
        return 0 if bad == 0 else 2

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
