"""run32 变异自证（副本树）：UNVERIFIED 减六格所依赖的九"能判红"证据。

任务表 #86 之后的下一批：`scan_claimed_semantics.UNVERIFIED` 18 → 12。六格分两种收法，
两种都要变异腿证明（"有断言"这句话不能由用例名字证明）：

    新增直接断言（tests/test_vma_phase4_claims_direct.py）
      store.insert_behavior_event  N01 自增返回值恒写 0
      store.get_behavior_event     N02 不存在的 id 返回 {} 而不是 None / N03 persons 反序列化恒空
      store.save_arena_snapshot    N04 MAX(version) 不带 WHERE arena_id（跨分区串号）/ N05 version 恒 1

    指向既有用例（基线文字与现树不符 —— 直接断言是后续批次补进去的，行忘了移）
      api._closed_ok               N06 `return bool(filters.get("unresolved"))` → 恒 True（静默 0 条也 ok）
                                   N07 实体集那格关掉（边声称扫了设备边答 0 条还 ok）
      api.anomaly_report           N08 `if query and not ids:` → `if False:`（解析不出就回落全屋异常）
      app.metrics_ingest_endpoint  N09 dedupe_key 不再决定键位 ⇒ 同键三次落三格

N06..N09 是本档的另一半价值：这三格**没有新增一行用例**，如果只靠"用例名对得上"就把基线减掉，
那这条减记录的就是我的一次目测。四条腿分别把那三个语义退回各自行文描述过的坏那侧，
必须让 `test_vma_qb_param_landing.py` / `test_vma_phase2_batch3_shared_ruler.py` 的既有用例当场判红。

跑法与 run31 完全一致：MUT_ROOT 指快照树/工作树，注入只发生在一次性副本树 MUT_DST；
锚点命中 != 1 ⇒ INVALID；注入后先 `ast.parse`（响过 ≠ 响对，副本不合法的"红"不算杀）；
Q-0 对照腿必须绿；跑完复核哈希并出 `WT_UNTOUCHED`。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or "/tmp/c92snap32"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or "/tmp/c92mut32"
OUT = os.environ.get("MUT_OUT") or "/tmp/c92mut32.out"

M = os.path.join("src", "memory_agent")
FILES = {
    "store": os.path.join(M, "store.py"),
    "iapi": os.path.join(M, "insights", "api.py"),
    "app": os.path.join(M, "app.py"),
}
TESTS = [
    os.path.join("tests", "test_vma_phase4_claims_direct.py"),
    os.path.join("tests", "test_vma_qb_param_landing.py"),
    os.path.join("tests", "test_vma_phase2_batch3_shared_ruler.py"),
]

# 三条 store 锚点都必须**带上下文**：`return int(cur.lastrowid or 0)` 在同文件出现两次
# （`insert_perception_event` 的幂等档后面也是这一句），`if row is None: return None` 同理。
# 裸单行锚点会命中 2 次 ⇒ 整腿 INVALID（run31 之后这条已写进预检格，这里不再重犯）。
INSERT_RETURN = (
    '                    json.dumps(payload["scene_graph_json"], ensure_ascii=False) '
    'if payload.get("scene_graph_json") else None,\n'
    '                ),\n'
    '            )\n'
    '            conn.commit()\n'
    '            return int(cur.lastrowid or 0)\n')
GET_NONE_BLOCK = (
    '            row = conn.execute(\n'
    '                "SELECT * FROM behavior_events WHERE id = ?", (event_id,)\n'
    '            ).fetchone()\n'
    '        if row is None:\n'
    '            return None\n')
GET_PERSONS = (
    '            d["persons"] = Store._deserialize_persons(d.pop("persons_json"))\n'
    '        except Exception:\n'
    '            d["persons"] = []\n')
ARENA_MAX_WHERE = ('                "SELECT COALESCE(MAX(version),0) FROM arena_snapshots '
                   'WHERE arena_id=?",\n')
ARENA_VERSION = "            version = (cur.fetchone()[0] or 0) + 1\n"
CLOSED_UNRESOLVED = "        return bool(filters.get(\"unresolved\"))\n"
CLOSED_ENTITY = (
    "        if filters.get(\"entity_ids\"):\n"
    "            return False\n")
ANOMALY_GATE = "        if query and not ids:\n"
METRICS_ASSIGN = "        store[key] = payload\n"

LEGS = [
    ("N01", "store", INSERT_RETURN,
     '                    json.dumps(payload["scene_graph_json"], ensure_ascii=False) '
     'if payload.get("scene_graph_json") else None,\n'
     '                ),\n'
     '            )\n'
     '            conn.commit()\n'
     '            return 0\n',
     "自增 id 恒写 0 ⇒ 上层拿不到可回读的主键，标注闭环断链"),
    ("N02", "store", GET_NONE_BLOCK,
     '            row = conn.execute(\n'
     '                "SELECT * FROM behavior_events WHERE id = ?", (event_id,)\n'
     '            ).fetchone()\n'
     '        if row is None:\n'
     '            return {}\n',
     "取不到返回空 dict ⇒ 「没有这条事件」与「有但字段全空」共用一个形状"),
    ("N03", "store", GET_PERSONS,
     '            d["persons"] = []\n'
     '        except Exception:\n'
     '            d["persons"] = []\n',
     "persons 恒空 ⇒ 自增 id 取回来的不是当初写进去的那条"),
    ("N04", "store", ARENA_MAX_WHERE,
     '                "SELECT COALESCE(MAX(version),0) FROM arena_snapshots WHERE 1=1",\n',
     "版本号跨分区串号 ⇒ 新分区第一条就是 2/3（递增不再是「每个分区自己的」）"),
    ("N05", "store", ARENA_VERSION, "            version = 1\n",
     "版本恒 1 ⇒ 快照表里同一分区多行同版本，历史无从指认"),
    ("N06", "iapi", CLOSED_UNRESOLVED, "        return True\n",
     "fail-closed 那格改成字面量 ⇒ 静默的 0 条也答 ok（审计要消灭的形状）"),
    ("N07", "iapi", CLOSED_ENTITY,
     "        if False:\n            return False\n",
     "实体集那格关掉 ⇒ 一边声称扫了某些设备、一边答 0 条还算自洽"),
    ("N08", "iapi", ANOMALY_GATE, "        if False:\n",
     "解析不出实体也下推 ⇒ 把全屋异常当成「这台设备的异常」交出去"),
    ("N09", "app", METRICS_ASSIGN, "        store[key + str(len(store))] = payload\n",
     "dedupe_key 不再决定键位 ⇒ 同键重复回灌落多格，幂等承诺失效"),
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
        timeout=1200)
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
