r"""#80 的变异档：新建的"文案承诺键 ↔ 运行时探针"门 + window 回溯两格，必须真会红。

纪律（台账 §四十八/§五十 那几条坑的正面执行）：
* 只在一次性副本树里改，绝不碰权威树；每条腿用完即删；
* 出网前先 `--verify`：锚点在當前树唯一、文件在、腿名不重复；
* M-0 是"什么都不改"的对照腿，必须全绿，否则整表读数为零信息量；
* 判据是"这条腿让 FAILED>0"，不是"输出里出现了某个词"；
* 每条腿打完补丁先 `ast.parse`：注入把源码改坏时测试也会"响"，那响不是断言造成的。

腿分四类：**门的主判据**（P1 往文案里塞一条载荷不存在的键 / R1 让登记过期）、
**window 两格**（W1 plan_question / W2 compare / W3 降级信封）、
**量具自证**（F1 不播种 behavior_events ⇒ 探针该被自己抓住）、
**覆盖核对**（C1 删掉一条 NOT_PROBED ⇒ 静默零不放行）。
C2 顺手复验 #79 那条键集合锁还在咬（同一批次落地，同表读数）。

    python .qoder/tmp-c80-mut80.py --verify
    python .qoder/tmp-c80-mut80.py
"""
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join("src", "memory_agent", "insights", "api.py")
SVC_PY = os.path.join("src", "memory_agent", "insights", "service.py")
SPEC_PY = os.path.join("src", "memory_agent", "tool_schema.py")
GATE = "tests/test_tool_prose_promised_keys.py"
FACADE = "tests/test_insights_facade_contract.py"

A_PLAN_WINDOW = '        data["window"] = plan.time_range.to_dict() if plan.time_range else {}\n'
A_CMP_WINDOW = '"window": _window_meta(_Window(prev.start_ts, cur.end_ts)),\n'
A_DEFAULT_WINDOW = '"filters": {}, "window": {},'
A_FAKE_KEY = "hints（候选追问）"
A_SERVER_TS_REG = ("        \"server_ts\": \"落库列名/排序依据，对应的出键是 `time`（已在载荷核对通过）\",\n")
A_BEHAVIOR_CALL = "_behavior_rows(now))"
A_NOT_PROBED = '    "store": (1, "写侧 store 直通，返回形状由写侧门罩"),\n'

#: (腿名, 说明, 判红用例文件, [(文件, 锚点, 替换)])
LEGS = [
    ("M-0-control", "什么都不改（对照腿：必须全绿）", GATE, []),
    ("P1-prose-promises-a-dead-key", "往 route_question 文案里塞一条载荷没有的键 recommended_tool",
     GATE, [(SPEC_PY, A_FAKE_KEY, A_FAKE_KEY + "与 recommended_tool（下一步工具名）")]),
    ("P2-registration-goes-stale", "把 time 这个真实键也登记成落点 ⇒ 台账变橡皮章该被抓",
     GATE, [(GATE, '    "query_behavior_events": {',
             '    "query_behavior_events": {\n        "time": "占位：载荷里真的有这条键，登记即过期",')]),
    ("N1-registration-deleted", "删掉 server_ts 那条登记（文案仍在点名它）⇒ 门主判据必须红",
     GATE, [(GATE, A_SERVER_TS_REG, "")]),
    ("W1-plan_question-loses-window", "plan_question 不再回显 window（§三 Q2 甲的第一格）",
     GATE, [(API_PY, A_PLAN_WINDOW, "")]),
    ("W2-compare-loses-window", "compare_insights 不再回显 window（§三 Q2 甲的第二格）",
     GATE, [(SVC_PY, A_CMP_WINDOW, "")]),
    ("W3-degraded-envelope-drops-window", "降级信封里 window 整键消失（缺键会被读成窗口无限）",
     GATE, [(SVC_PY, A_DEFAULT_WINDOW, '"filters": {},')]),
    ("F1-probe-stops-seeding-behavior-rows", "夹具不播 behavior_events ⇒ 探针量的是空表",
     GATE, [(GATE, A_BEHAVIOR_CALL, "[] if True else " + A_BEHAVIOR_CALL)]),
    ("C1-coverage-table-loses-a-service", "NOT_PROBED 少登记一条 service ⇒ 覆盖核对必须红",
     GATE, [(GATE, A_NOT_PROBED, "")]),
    ("C2-climate-block-removed", "复验 #79 的键集合锁：温控环比块整个不挂上载荷",
     FACADE, [(SVC_PY, '"climate_comparison": self._climate_comparison(cur, prev),\n', "")]),
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _files(rel):
    return sorted({rel for _n, _d, _t, edits in LEGS for (rel, _a, _r) in edits})


def verify(root):
    bad, seen = [], set()
    for name, _desc, test_file, edits in LEGS:
        if name in seen:
            bad.append("腿名重复：%s" % name)
        seen.add(name)
        if not os.path.isfile(os.path.join(root, test_file)):
            bad.append("用例文件不在：%s（腿 %s）" % (test_file, name))
        for rel, anchor, repl in edits:
            if not os.path.isfile(os.path.join(root, rel)):
                bad.append("文件不在：%s（腿 %s）" % (rel, name))
                continue
            text = _read(root, rel)
            n = text.count(anchor)
            if n != 1:
                bad.append("腿 %s 的锚点 count=%d（应为 1）：%s" % (name, n, anchor[:48]))
            if anchor in repl and repl != anchor and n:
                pass                      # 插入型腿：替换目标本就含锚点，只查下面这条
            if repl and repl != anchor and anchor not in repl and repl not in anchor and repl in text:
                bad.append("腿 %s 的替换目标已在树里，判红读数不可信：%s" % (name, repl[:48]))
            try:
                ast.parse(text.replace(anchor, repl, 1), filename=rel)
            except SyntaxError as exc:
                bad.append("腿 %s 的补丁不合法语法：%s" % (name, exc.msg))
    print("VERIFY_LEGS=%d VERIFY_BAD=%d" % (len(LEGS), len(bad)))
    for row in bad:
        print("  BAD: " + row)
    return 1 if bad else 0


def _pytest(dst, test_file):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.join(dst, "src"), os.path.join(dst, "tests"), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run([sys.executable, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider"],
                          cwd=dst, env=env, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = [ln for ln in out.strip().splitlines()[-3:]]
    failed = len(re.findall(r"^FAILED ", out, flags=re.M))
    match = re.search(r"\d+ (?:passed|failed)", out.strip()[-260:] if out.strip() else "")
    return proc.returncode, failed, (match.group(0) if match else "?"), " | ".join(tail)


def main(root):
    bad = 0
    touched = _files(root)
    before = {rel: _sha(os.path.join(root, rel)) for rel in touched}
    print("%-38s %-4s %s" % ("腿", "RC", "读数"))
    for name, desc, test_file, edits in LEGS:
        dst = tempfile.mkdtemp(prefix="ma_c80_leg_")
        try:
            for sub in ("src", "tests"):
                shutil.copytree(os.path.join(root, sub), os.path.join(dst, sub),
                                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            for rel, anchor, repl in edits:
                path = os.path.join(dst, rel)
                text = _read(dst, rel)
                if text.count(anchor) != 1:
                    bad += 1
                    print("%-38s %-4s 该响 FAILED=?   <<< 副本树锚点不唯一（count=%d）"
                          % (name, "-", text.count(anchor)))
                    break
                patched = text.replace(anchor, repl, 1)
                try:
                    ast.parse(patched, filename=path)
                except SyntaxError as exc:
                    bad += 1
                    print("%-38s %-4s 该响 FAILED=?   <<< 变异副本语法不合法（%s），这条腿无效"
                          % (name, "-", exc.msg))
                    break
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(patched)
            else:
                rc, failed, summary, tail = _pytest(dst, test_file)
                ok = (rc == 0 and failed == 0) if not edits else (rc != 0 or failed > 0)
                if not ok:
                    bad += 1
                print("%-38s %-4s %s failed=%d %s%s" % (
                    name, rc, "对照" if not edits else "该响", failed, summary,
                    "" if ok else "   <<< 这条腿没响，锁无效"))
                if not ok:
                    print("     说明=%s" % desc)
                    print("     尾读数=%s" % tail)
        finally:
            shutil.rmtree(dst, ignore_errors=True)
    print("MUT80_COUNT=%d MUTATION_BAD=%d" % (len(LEGS), bad))
    restored = all(before[rel] == _sha(os.path.join(root, rel)) for rel in touched)
    print("TREE_UNCHANGED=%s  Files=%d" % (restored, len(touched)))
    return 1 if bad or not restored else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--verify"]
    tree = os.path.abspath(args[0] if args else ROOT)
    if "--verify" in sys.argv[1:]:
        sys.exit(verify(tree))
    sys.exit(main(tree))
