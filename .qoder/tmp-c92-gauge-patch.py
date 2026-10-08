"""run32 台账落册器：把六格从 UNVERIFIED 移进 REGISTRY，并按**现读**下调 BASELINE_CAP。

用法：`python tmp-c92-gauge-patch.py <树根>`（先跑在 `git archive HEAD` 的副本树上验证，
再把同一条命令跑在工作树 —— 两边改的是同一份文本，避免"副本绿了、真树忘了改"）。

三件事按顺序做，且**期望值一律当场量**（run31 就把 35 记成过 36，这次不靠记忆）：
1. 结构改动：REGISTRY 加六格、UNVERIFIED 删六行、锁文件加 `REDUCED_20261008_C` 并把六键并进循环；
2. 现读：在该树跑 `scripts/scan_claimed_semantics.py`，取 DECLARED/REGISTERED/CASES/UNVERIFIED/PROBLEM；
3. 回写：把现读串写进两处读数行（gauge 头部 + 锁文件注释），并把 `BASELINE_CAP` 写成现读的
   UNVERIFIED 数（= `len(UNVERIFIED)`，不是"我以为的数"）。

行尾按目标文件自身走（本机 `core.autocrlf=true` ⇒ 工作副本可能是 CRLF、`git archive` 出来的是 LF；
锚点只写 `\n` 在 CRLF 文件上就是数 0 次 ⇒ 量具自己瞎）。
"""
import os
import re
import subprocess
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
GAUGE = os.path.join(ROOT, "scripts", "scan_claimed_semantics.py")
LOCK = os.path.join(ROOT, "tests", "test_vma_phase2_claims_gauge.py")

PH4 = "test_vma_phase4_claims_direct.py"
QB = "test_vma_qb_param_landing.py"
B3 = "test_vma_phase2_batch3_shared_ruler.py"

DROP_KEYS = [
    ("api.py", "_closed_ok"),
    ("api.py", "anomaly_report"),
    ("app.py", "metrics_ingest_endpoint"),
    ("store.py", "insert_behavior_event"),
    ("store.py", "get_behavior_event"),
    ("store.py", "save_arena_snapshot"),
]

REGISTRY_TAIL_ANCHOR = "    },\n}\n\n#: 首批基线"

NEW_ENTRIES = '''    # 2026-10-08 第三批减基线六格（run32）。两种收法都要变异腿证明，不靠用例名字对上：
    #   前三格是本批新增的直接断言（正例 + 对偶档成对）；
    #   后三格指向**后续批次补用例时早已写进去的直接断言** —— 基线只检查"函数还在不在、
    #   声明词还成不成立"，不检查"缺的那一半后来有没有被补上"，所以这三行的文字已经与现树不符。
    #   把它们留在基线里 = 台账在谎报"没断"；移出去又不证明 = 台账在谎报"断了"。两头的谎都由
    #   run32 的 N06..N09 兜住：把那三格的语义退回各自行文描述过的坏那侧，既有用例必须当场判红。
    ("store.py", "insert_behavior_event"): {
        "claims": ["自增"],
        "cases": [(PH4, "test_insert_behavior_event_ids_strictly_increase_inside_one_store"),
                  (PH4, "test_insert_behavior_event_keeps_increasing_after_the_store_is_reopened")],
    },
    ("store.py", "get_behavior_event"): {
        "claims": ["自增"],
        "cases": [(PH4, "test_get_behavior_event_returns_the_row_that_insert_wrote"),
                  (PH4, "test_get_behavior_event_returns_none_for_an_id_that_was_never_written")],
    },
    ("store.py", "save_arena_snapshot"): {
        "claims": ["递增"],
        "cases": [(PH4, "test_save_arena_snapshot_increments_the_version_for_the_same_arena"),
                  (PH4, "test_save_arena_snapshot_versions_are_independent_per_arena")],
    },
    ("api.py", "_closed_ok"): {
        "claims": ["fail-closed"],
        "cases": [(QB, "test_fail_closed_ok_is_derived_not_literal"),
                  (PH4, "test_closed_ok_requires_both_self_consistency_and_a_stated_reason")],
    },
    ("api.py", "anomaly_report"): {
        "claims": ["fail-closed"],
        "cases": [(QB, "test_anomaly_report_query_lands_on_entities_or_fails_closed"),
                  (QB, "test_fail_closed_ok_is_derived_not_literal")],
    },
    ("app.py", "metrics_ingest_endpoint"): {
        "claims": ["幂等"],
        "cases": [(B3, "test_ingest_replaces_the_same_dedupe_key_instead_of_doubling"),
                  (B3, "test_twenty_concurrent_ingests_all_land")],
    },
'''

ALIAS_LINES = ('PH4 = "%s"\nQB = "%s"\nB3 = "%s"\n' % (PH4, QB, B3))

LOCK_TAIL_ANCHOR = '                      ("store.py", "make_event_id"))\n'
NEW_LOCK_TUPLE = '''
#: 2026-10-08 第三批减掉的六格（run32）。同一条规矩：不许静默退回基线。
#: 后三格没有新增用例，它们锁的是"指向的既有用例真能判红"——由 run32 的 N06..N09 四条腿证明。
REDUCED_20261008_C = (("store.py", "insert_behavior_event"),
                      ("store.py", "get_behavior_event"),
                      ("store.py", "save_arena_snapshot"),
                      ("api.py", "_closed_ok"),
                      ("api.py", "anomaly_report"),
                      ("app.py", "metrics_ingest_endpoint"))
'''


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def to_eol(text, eol):
    return text.replace("\n", eol) if eol != "\n" else text


def cr_count(path):
    raw = open(path, "rb").read()
    return raw.count(b"\r")


def main():
    for p in (GAUGE, LOCK):
        if not os.path.isfile(p):
            print("PATCH_ABORT 缺文件 %s" % p)
            return 1

    g = read(GAUGE)
    eol_g = "\r\n" if "\r\n" in g else "\n"
    l = read(LOCK)
    eol_l = "\r\n" if "\r\n" in l else "\n"

    # 1a. 常量别名（新格引用三个测试文件名，写死三遍容易漂）
    if "PH4 = " not in g:
        # 必须带行首换行：裸 `REGISTRY = {` / `UNVERIFIED = {` 也是 `FIXTURE_REGISTRY = {` /
        # `FIXTURE_UNVERIFIED = {` 的子串（实测命中 2 次 ⇒ 量具自己瞎，不是树里没有）。
        anchor = to_eol("\nREGISTRY = {\n", eol_g)
        if g.count(anchor) != 1:
            print("PATCH_ABORT gauge REGISTRY 锚点命中 %d" % g.count(anchor))
            return 1
        g = g.replace(anchor, to_eol("\n" + ALIAS_LINES, eol_g) + anchor, 1)

    # 1b. REGISTRY 追加六格
    a = to_eol(REGISTRY_TAIL_ANCHOR, eol_g)
    if g.count(a) != 1:
        print("PATCH_ABORT gauge REGISTRY 尾部锚点命中 %d" % g.count(a))
        return 1
    g = g.replace(a, to_eol("    },\n" + NEW_ENTRIES + "}\n\n#: 首批基线", eol_g), 1)

    # 1c. UNVERIFIED 删六行 —— 必须**只在 UNVERIFIED 那一格里删**：
    # `FIXTURE_UNVERIFIED`（量具 self-test 的假台账）里有同名键，全文正则会一次删到 12 行，
    # 把 self-test 的正例也吃掉 ⇒ 门自己瞎。实测命中 12/6 就是这个形状。
    m_start = re.search(r"(?m)^UNVERIFIED = \{\n", g)
    if not m_start:
        print("PATCH_ABORT 找不到 UNVERIFIED 块")
        return 1
    m_end = g.index("\n}\n", m_start.end())
    block = g[m_start.end():m_end]
    dropped = 0
    for fn, name in DROP_KEYS:
        pat = re.compile(r'^[ \t]*\("%s", "%s"\):.*\r?\n' % (re.escape(fn), re.escape(name)),
                         re.M)
        block, n = pat.subn("", block)
        dropped += n
    if dropped != len(DROP_KEYS):
        print("PATCH_ABORT 只删掉 %d/%d 行基线" % (dropped, len(DROP_KEYS)))
        return 1
    g = g[:m_start.end()] + block + g[m_end:]
    write(GAUGE, g)

    # 2. 现读（在这棵树上跑量具本身，不猜数）
    proc = subprocess.run([sys.executable, os.path.join("scripts", "scan_claimed_semantics.py")],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"DECLARED=(\d+) REGISTERED=(\d+) CASES=(\d+) UNVERIFIED=(\d+) PROBLEM=(\d+)",
                  out)
    if not m or proc.returncode != 0:
        print("PATCH_ABORT 现读失败 rc=%d\n%s" % (proc.returncode, out[-1500:]))
        return 1
    decl, reg, cases, unv, prob = (int(x) for x in m.groups())
    print("MEASURED DECLARED=%d REGISTERED=%d CASES=%d UNVERIFIED=%d PROBLEM=%d SCAN_RC=%d"
          % (decl, reg, cases, unv, prob, proc.returncode))

    # 3a. gauge 头部读数行（紧跟"减五格后"那行插一条"减六格后"）
    g = read(GAUGE)
    eol_g = "\r\n" if "\r\n" in g else "\n"
    a = to_eol("    减五格后  SRC=src/memory_agent DECLARED=%d REGISTERED=23 CASES=36 "
               "UNVERIFIED=18 PROBLEM=0 / SCAN_RC=0\n" % decl, eol_g)
    if g.count(a) != 1:
        print("PATCH_ABORT gauge 读数行锚点命中 %d" % g.count(a))
        return 1
    new_line = to_eol("    减六格后  SRC=src/memory_agent DECLARED=%d REGISTERED=%d CASES=%d "
                      "UNVERIFIED=%d PROBLEM=%d / SCAN_RC=0\n"
                      % (decl, reg, cases, unv, prob), eol_g)
    write(GAUGE, g.replace(a, a + new_line, 1))

    # 3b. 锁文件：读数行 + CAP 现读下调 + 六格锁
    l = read(LOCK)
    eol_l = "\r\n" if "\r\n" in l else "\n"
    a = to_eol("#:   减五格后 DECLARED=%d REGISTERED=23 CASES=36 UNVERIFIED=18 PROBLEM=0\n" % decl,
               eol_l)
    if l.count(a) != 1:
        print("PATCH_ABORT 锁文件读数行锚点命中 %d" % l.count(a))
        return 1
    l = l.replace(a, a + to_eol("#:   减六格后 DECLARED=%d REGISTERED=%d CASES=%d UNVERIFIED=%d "
                                "PROBLEM=0\n" % (decl, reg, cases, unv), eol_l), 1)
    l, n = re.subn(r"BASELINE_CAP = \d+", "BASELINE_CAP = %d" % unv, l, count=1)
    if n != 1:
        print("PATCH_ABORT 没找到 BASELINE_CAP")
        return 1
    a = to_eol(LOCK_TAIL_ANCHOR, eol_l)
    if l.count(a) != 1:
        print("PATCH_ABORT 锁文件 REDUCED_B 尾部锚点命中 %d" % l.count(a))
        return 1
    l = l.replace(a, a + to_eol(NEW_LOCK_TUPLE, eol_l), 1)
    a = to_eol("    for key in tuple(REDUCED_20261008) + tuple(REDUCED_20261008_B):\n", eol_l)
    if l.count(a) != 1:
        print("PATCH_ABORT 锁文件循环锚点命中 %d" % l.count(a))
        return 1
    l = l.replace(a, to_eol("    for key in (tuple(REDUCED_20261008) + tuple(REDUCED_20261008_B)\n"
                            "                  + tuple(REDUCED_20261008_C)):\n", eol_l), 1)
    write(LOCK, l)

    print("PATCH_OK root=%s gauge_eol=%s lock_eol=%s CR_gauge=%d CR_lock=%d"
          % (ROOT, repr(eol_g), repr(eol_l), cr_count(GAUGE), cr_count(LOCK)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
