r"""#78 的变异档：删掉那份 434 行死目录之后，"唯一真源"三把门 + 读者锁必须真会红。

纪律（台账 §四十八/§五十 那几条坑的正面执行）：
* 只在**一次性副本树**里改，绝不在飞行的权威树或本仓工作树上动手；每条腿用完即删；
* 出网前先跑 `--verify`：锚点在当前树里唯一、文件在、腿名不重复——不跑测试，只静态量；
* M-0 是"什么都不改"的对照腿：必须全绿，否则整表读数为零信息量；
* 判据是"这条腿让 FAILED>0"，不是"输出里出现了某个词"；
* 每条腿打完补丁先 `ast.parse`：注入把源码改坏时测试也会"响"，那响不是断言造成的。

L1–L4 动的是**目录面形状**（唯一绑定 / 值必须是 build_catalog() / 不许有 import 期读者），
L5 动的是**读者**（WebUI 的 MCP 接入页只拿到 48 条）——这条腿罩的正是本次删除的理由：
被删那份镜像与真源差 42 条，若哪天有人"退回旧表"，前端与 caps 会少报能力且一声不响。

    python .qoder/tmp-c78-mut78.py --verify
    python .qoder/tmp-c78-mut78.py
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
SRC_PY = os.path.join("src", "memory_agent", "mcp_server.py")
TEST_FILE = "tests/test_mcp_surface_parity.py"

#: 退回被删那份镜像的最小可用形状（12 条 dict ≥ 门里的"大表"阈值 10，且带派生行）
DEAD_LITERAL = ("TOOL_CATALOG: list[dict] = [\n"
                + "".join('    {"name": "tool_%d", "group": "g", "summary": "s"},\n' % i
                          for i in range(12))
                + "]\n"
                'TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]\n')
ANCHOR_EARLY = "BUNDLED_SKILLS_DIR = "
CATALOG_BINDING = "TOOL_CATALOG = build_catalog()"
CATALOG_LOCAL = "TOOL_CATALOG = list(TOOL_NAMES_FROM_SPEC)"
NAMES_BINDING = "TOOL_NAMES = TOOL_NAMES_FROM_SPEC"
NAMES_DERIVED = 'TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]'
DESCRIBE_READ = '"catalog": TOOL_CATALOG,'
DESCRIBE_TRUNC = '"catalog": TOOL_CATALOG[:48],'

#: (腿名, 说明, 文件, 锚点, 替换成) —— L1/L3/L4 用插入或替换，锚点必须在树里唯一
LEGS = [
    ("M-0", "什么都不改（对照腿：必须全绿）", None, None, None),
    ("L1-dead-literal-restored", "把 434 行镜像连派生行一起放回覆盖之前",
     SRC_PY, ANCHOR_EARLY, DEAD_LITERAL + "\n" + ANCHOR_EARLY),
    ("L2-source-swapped-to-local-assembly", "真源换成就地拼装（不再 build_catalog()）",
     SRC_PY, CATALOG_BINDING, CATALOG_LOCAL),
    ("L3-names-derived-from-catalog", "TOOL_NAMES 退回按目录派生的那一行",
     SRC_PY, NAMES_BINDING, NAMES_DERIVED),
    ("L4-module-level-read", "import 期就读目录（真源绑定之后取一份模块级快照）",
     SRC_PY, NAMES_BINDING, NAMES_BINDING + "\n_EARLY_CATALOG = list(TOOL_CATALOG)"),
    ("L5-describe-page-truncated", "接入页只拿前 48 条（正是旧镜像与真源的差额形状）",
     SRC_PY, DESCRIBE_READ, DESCRIBE_TRUNC),
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def verify(root):
    """只读预检：锚点唯一性在当前树现场量，替换目标必须不在树里，腿名不许重复。"""
    bad = []
    seen = set()
    for name, _desc, rel, anchor, repl in LEGS:
        if name in seen:
            bad.append("腿名重复：%s" % name)
        seen.add(name)
        if rel is None:
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            bad.append("文件不在：%s（腿 %s）" % (rel, name))
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        n = text.count(anchor)
        if n != 1:
            bad.append("腿 %s 的锚点 count=%d（应为 1）：%s" % (name, n, anchor[:44]))
        if repl is not None and not name.startswith("L1") and not name.startswith("L4"):
            # 插入型两条本来就含锚点本身，只查"替换目标已在树里"这一件事
            target = repl.replace(anchor, "", 1)
            if target and target in text:
                bad.append("腿 %s 的替换目标已在树里，判红读数不可信：%s" % (name, target[:44]))
        try:
            ast.parse(text.replace(anchor, repl, 1))
        except SyntaxError as exc:
            bad.append("腿 %s 的补丁不合法语法：%s" % (name, exc.msg))
    if not os.path.isfile(os.path.join(root, TEST_FILE)):
        bad.append("测试文件不在：%s" % TEST_FILE)
    print("VERIFY_LEGS=%d VERIFY_BAD=%d" % (len(LEGS), len(bad)))
    for row in bad:
        print("  BAD: " + row)
    return 1 if bad else 0


def _copy_tree(root, dst):
    """一次性副本树：只带 src/ 与 tests/（用例自己按 ../src 取源码）。"""
    for sub in ("src", "tests"):
        shutil.copytree(os.path.join(root, sub), os.path.join(dst, sub),
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst


def _pytest(dst):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.join(dst, "src"), os.path.join(dst, "tests"), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run([sys.executable, "-m", "pytest", TEST_FILE, "-q", "-p", "no:cacheprovider"],
                          cwd=dst, env=env, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out.strip().splitlines()[-3:]
    if "FAILED" not in out and proc.returncode not in (0, 1):
        tail.append(out.strip()[-160:])
    failed = len(re.findall(r"^FAILED ", out, flags=re.M))
    match = re.search(r"(\d+) (?:passed|failed)", out.strip()[-260:] if out else "")
    return proc.returncode, failed, (match.group(0) if match else "?"), " | ".join(tail)


def main(root):
    bad = 0
    touched = (SRC_PY,)
    before = {rel: _sha(os.path.join(root, rel)) for rel in touched}
    print("%-34s %-4s %s" % ("腿", "RC", "读数"))
    for name, desc, rel, anchor, repl in LEGS:
        dst = tempfile.mkdtemp(prefix="ma_c78_leg_")
        try:
            _copy_tree(root, dst)
            if rel is not None:
                path = os.path.join(dst, rel)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                assert text.count(anchor) == 1, "副本树锚点不唯一：%s" % name
                patched = text.replace(anchor, repl, 1)
                try:
                    ast.parse(patched, filename=path)
                except SyntaxError as exc:
                    bad += 1
                    print("%-34s %-4s 该响 FAILED=?   <<< 变异副本语法不合法（%s），这条腿无效"
                          % (name, "-", exc.msg))
                    continue
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(patched)
            rc, failed, summary, tail = _pytest(dst)
            ok = (rc == 0 and failed == 0) if name == "M-0" else (rc != 0 or failed > 0)
            if not ok:
                bad += 1
            print("%-34s %-4s %s failed=%d %s%s" % (
                name, rc, "该响" if rel else "对照", failed, summary,
                "" if ok else "   <<< 这条腿没响，锁无效"))
            if not ok:
                print("     说明=%s" % desc)
                print("     尾读数=%s" % tail)
        finally:
            shutil.rmtree(dst, ignore_errors=True)
    print("MUT78_COUNT=%d MUTATION_BAD=%d" % (len(LEGS), bad))
    restored = all(before[rel] == _sha(os.path.join(root, rel))
                   and os.path.isfile(os.path.join(root, rel)) for rel in touched)
    print("SRC_UNCHANGED=%s %s" % (
        restored, " ".join("%s_SHA=%s" % (os.path.basename(rel).split(".")[0][:6],
                                          _sha(os.path.join(root, rel))[:12])
                           for rel in touched)))
    return 1 if bad or not restored else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--verify"]
    tree = os.path.abspath(args[0] if args else ROOT)
    if "--verify" in sys.argv[1:]:
        sys.exit(verify(tree))
    sys.exit(main(tree))
