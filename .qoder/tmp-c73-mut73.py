r"""#73 那两格 `window` 的变异腿（DCD 20261005 §Q-A 甲：`coverage` / `data_quality` 的窗口回显）。

用法与教训照旧：
* **只在副本树里跑**（`MUT_ROOT` 指向 `git archive HEAD` 解出来的那棵树），工作树全程零改动；
* 读这张表用 `ast`，**永不 import** 本文件（末尾是 `sys.exit(main())`，import 即开跑）；
* 每腿按字节还原并核对 sha，`restored` 不是 OK 就整腿作废；
* `_run_leg` 的 PYTHONPATH 只**续上**外层条目，不覆盖——run19 的门 3 就是丢在覆盖掉 `/tmp/pylibs`。

腿的形状不是随手挑的：L1/L2 各打一格的"回显根本没接上"（改前形状），L3 打"键在但口径空白"——
把谎从「没有 window」换成「window 永远是个空壳」，这比整格缺席更阴，因为消费方看得到键、
以为有口径，实际拿不到那一段。三腿都只在 service.py 里改，api.py 门面是纯委托，会跟着一起红，
这正是要的形状：门面读数不许比 core 少口径。

    MUT_ROOT=/path/to/copy python .qoder/tmp-c73-mut73.py
    python .qoder/tmp-c73-mut73.py --verify        # 只查锚点与死形状，不开跑
"""
import ast
import hashlib
import io
import os
import re
import subprocess
import sys

SVC = "src/memory_agent/insights/service.py"
TESTS = ["tests/test_vma_insights_window_echo.py", "tests/test_insights_facade_contract.py"]

MUTANTS = [
    {
        "id": "L1",
        "desc": "coverage 没接窗口回显（改前形状）",
        "path": SVC,
        "anchor": '            out = self._fail("coverage", exc, {\n'
                  '                "days": [], "total_events": 0, "day_coverage": 0.0, "hour_coverage": 0.0})\n'
                  "        return self._with_window(out, tr)\n",
        "replacement": '            out = self._fail("coverage", exc, {\n'
                       '                "days": [], "total_events": 0, "day_coverage": 0.0, "hour_coverage": 0.0})\n'
                       "        return out\n",
    },
    {
        "id": "L2",
        "desc": "data_quality 没接窗口回显（改前形状）",
        "path": SVC,
        # 锚点必须带上前一行：`return self._with_window(out, tr)` 在本文件出现 5 次
        # （usage/device_health/behavior_insights/coverage/data_quality），单行锚会撞号。
        "anchor": '"total_events": 0, "filters": {}})\n'
                  "        return self._with_window(out, tr)\n",
        "replacement": '"total_events": 0, "filters": {}})\n'
                       "        return out\n",
    },
    {
        "id": "L3",
        "desc": "window 键在但恒空壳（谎从「没有」变成「口径永远空白」）",
        "path": SVC,
        "anchor": '            if hasattr(tr, "to_dict"):\n                return dict(tr.to_dict() or {})\n',
        "replacement": '            if hasattr(tr, "to_dict"):\n                return {}\n',
    },
]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _read(root, rel):
    return io.open(os.path.join(root, rel), "rb").read()


def _write(root, rel, data):
    with io.open(os.path.join(root, rel), "wb") as fh:
        fh.write(data)


def _run_leg(root):
    inherited = [p for p in (os.environ.get("PYTHONPATH") or "").split(os.pathsep) if p]
    env = dict(os.environ,
               PYTHONPATH=os.pathsep.join([os.path.join(root, "src")] + inherited),
               JWT_SECRET="ci-test")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header",
                        "-p", "no:cacheprovider"] + TESTS,
                       cwd=root, env=env, capture_output=True, text=True)
    tail = [ln for ln in p.stdout.split("\n") if "passed" in ln or "failed" in ln]
    if tail:
        return p.returncode, tail[-1].strip(), _fail_count(p.stdout)
    err = (p.stderr or "").strip().split("\n")[-1] if (p.stderr or "").strip() else ""
    return p.returncode, "NO_PYTEST_OUTPUT stdout=%r stderr=%r" % (
        p.stdout.strip()[-80:], err[-120:]), -1


def _fail_count(stdout):
    m = re.search(r"(\d+) failed", stdout or "")
    return int(m.group(1)) if m else 0


def verify(root):
    """只读预检：锚点必须命中一次、变异后的整文件必须还能解析。`residue` 只报不判。"""
    bad = 0
    for mut in MUTANTS:
        text = _read(root, mut["path"]).decode("utf-8")
        a = text.count(mut["anchor"])
        mutated = text.replace(mut["anchor"], mut["replacement"], 1)
        try:
            ast.parse(mutated)
            syntax = "OK"
        except SyntaxError as exc:
            syntax = "SYNTAX_BAD:%s" % exc.lineno
        flag = ""
        if a != 1:
            flag = "ANCHOR_BAD"
        elif syntax != "OK":
            flag = syntax
        if flag:
            bad += 1
        print("%s %s anchor=%d residue=%d %s" % (
            mut["id"], flag or "OK", a, mutated.count(mut["replacement"]) - 1, mut["desc"]))
    print("MUTANTS_PARSED=%d VERIFY_BAD=%d" % (len(MUTANTS), bad))
    return 1 if bad else 0


def main(argv):
    root = os.environ.get("MUT_ROOT") or os.getcwd()
    if argv and argv[0] == "--verify":
        return verify(root)
    rc, out, _ = _run_leg(root)
    print("NOTHING 什么都不改档 -> RC=%d | %s" % (rc, out))
    if rc != 0:
        print("CONTROL_BAD：控制腿就不绿，整档作废")
        return 1
    bad = 0
    for mut in MUTANTS:
        original = _read(root, mut["path"])
        text = original.decode("utf-8")
        if text.count(mut["anchor"]) != 1:
            print("%s 锚点异常 anchor=%d，跳过" % (mut["id"], text.count(mut["anchor"])))
            bad += 1
            continue
        _write(root, mut["path"], text.replace(mut["anchor"], mut["replacement"], 1).encode("utf-8"))
        try:
            rc, out, failed = _run_leg(root)
        finally:
            _write(root, mut["path"], original)
        restored = "OK" if _sha(_read(root, mut["path"])) == _sha(original) else "FAIL"
        bites = rc != 0 and restored == "OK" and failed > 0
        if not bites:
            bad += 1
        print("%s %s -> RC=%d failed=%d %s %s | %s" % (
            mut["id"], mut["desc"], rc, failed,
            "咬住了" if bites else "没咬住/还原失败/零失败",
            "restored=" + restored, out))
    print("MUT_COUNT=%d MUTATION_BAD=%d" % (len(MUTANTS), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
