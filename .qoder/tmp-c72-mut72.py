r"""#72 那三条锁的变异腿（`get_data_quality` 的 `agent_memory` 格）。

用法与教训照旧：
* **只在副本树里跑**（`MUT_ROOT` 指向 `git archive HEAD` 解出来的那棵树），工作树全程零改动；
* 读这张表用 `ast`，**永不 import** 本文件（末尾是 `sys.exit(main())`，import 即开跑）；
* 每腿按字节还原并核对 sha，`restored` 不是 OK 就整腿作废。

腿的形状不是随手挑的：三条各打一种"承诺与实现对不上"的方式——
整格缺席（当初的真缺陷）／格在但恒空（把谎从"没有"改成"永远干净"）／
保护摘掉让附属块炸穿整页（`_degrade` 会把电量倒流/心跳/陈旧那些读数一起抹掉）。

    MUT_ROOT=/path/to/copy python .qoder/tmp-c72-mut72.py
    python .qoder/tmp-c72-mut72.py --verify        # 只查锚点与死形状，不开跑
"""
import ast
import hashlib
import io
import os
import subprocess
import sys

API = "src/memory_agent/insights/api.py"
MERGE_LINE = '        out["agent_memory"] = self._agent_memory_health()\n'
TEST_FILE = "tests/test_insights_facade_contract.py"

MUTANTS = [
    {
        "id": "L1",
        "desc": "丢键整格（改前形状：只回 core.data_quality）",
        "path": API,
        "anchor": '        out = self.core.data_quality(tr)\n' + MERGE_LINE
                  + "        return out\n",
        "replacement": "        return self.core.data_quality(tr)\n",
    },
    {
        "id": "L2",
        "desc": "格还在但恒空（谎从「没有」变成「镜像永远干净」）",
        "path": API,
        "anchor": MERGE_LINE,
        "replacement": '        out["agent_memory"] = {}\n',
    },
    {
        "id": "L3",
        "desc": "摘掉附属块的保护（附属块炸掉时整页被 _degrade 打空）",
        "path": API,
        "anchor": "        except Exception:  # noqa: BLE001 - 附属块读不到时不许把整个质量面打成降级\n"
                  '            return {"ok": False, "error": "agent_memory 不可用"}\n',
        "replacement": "        except Exception:\n            raise\n",
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
    # PYTHONPATH 必须**续上**外层已有的条目：容器里 pytest 住在 /tmp/pylibs，
    # 直接覆盖成 root/src 会让 `python -m pytest` 起不来（stdout 空、RC=1），
    # 而本机 3.13 的 pytest 是系统装的——同一份腿在两档表现不同就是这个坑。
    inherited = [p for p in (os.environ.get("PYTHONPATH") or "").split(os.pathsep) if p]
    env = dict(os.environ,
               PYTHONPATH=os.pathsep.join([os.path.join(root, "src")] + inherited),
               JWT_SECRET="ci-test")
    p = subprocess.run([sys.executable, "-m", "pytest", TEST_FILE, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=root, env=env, capture_output=True, text=True)
    tail = [ln for ln in p.stdout.split("\n") if "passed" in ln or "failed" in ln]
    if tail:
        return p.returncode, tail[-1].strip()
    # stdout 空 = 这条腿根本没跑起来；把 stderr 头一行报出来，否则"作废"没有原因可查
    err = (p.stderr or "").strip().split("\n")[-1] if (p.stderr or "").strip() else ""
    return p.returncode, "NO_PYTEST_OUTPUT stdout=%r stderr=%r" % (
        p.stdout.strip()[-80:], err[-120:])


def _fail_count(stdout):
    import re
    m = re.search(r"(\d+) failed", stdout or "")
    return int(m.group(1)) if m else 0


def verify(root):
    """只读预检：锚点必须命中一次、变异后的整文件必须还能解析。

    `residue` 只报不判：`replacement` 在文件别处出现是正常的（`return self.core.…`
    这种形状别的方法也有），真正判"有没有漏还原"的是运行腿里的按字节 sha 对账。
    """
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
    rc, out = _run_leg(root)
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
            rc, out = _run_leg(root)
        finally:
            _write(root, mut["path"], original)
        restored = "OK" if _sha(_read(root, mut["path"])) == _sha(original) else "FAIL"
        bites = rc != 0 and restored == "OK"
        if not bites:
            bad += 1
        print("%s %s -> RC=%d %s %s | %s" % (mut["id"], mut["desc"], rc,
                                             "咬住了" if bites else "没咬住/还原失败",
                                             "restored=" + restored, out))
    print("MUT_COUNT=%d MUTATION_BAD=%d" % (len(MUTANTS), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
