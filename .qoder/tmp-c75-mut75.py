r"""#75 的变异档：**每条锁都必须在"文案改回旧名字"时真的红**。

纪律（台账 §四十八 那两条坑的正面执行）：
* 只在**一次性副本树**里改，绝不在飞行的权威树或本仓工作树上动手；每条腿用完即删；
* 出网前先跑 `--verify`：锚点在**当前树里唯一**、文件在、腿名不重复——不跑测试，只静态量；
* M-0 是"什么都不改"的对照腿：它必须 10 条全过，否则整表读数为零信息量；
* 判据是"这条腿让 FAILED>0"，不是"输出里出现了某个词"；
* 每条腿打完补丁先 `ast.parse` 一遍：注入句把源码改坏时，测试也会"响"，但那响不是断言造成的
  （L4 首版就是靠行尾逗号把 pitfall 的隐式拼接打断，failed=3 全是 `_spec()` 里的 SyntaxError）。

L6/L7 两条腿动的是**引擎代码**而不是文案：这条档罩的是"文案许诺 vs 载荷实际"两向，
只按"退回旧文案"打一遍，锁不住载荷侧（温度链断掉、把 attrs 名当输出键发出去）。

    python .qoder/tmp-c75-mut75.py --verify
    python .qoder/tmp-c75-mut75.py
"""
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

#: 本脚本在 `.qoder/` 下，仓库根是它的上一层——预检第一腿就把这个抓出来了
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PY = os.path.join("src", "memory_agent", "mcp_server.py")
SRC_SPEC = os.path.join("src", "memory_agent", "tool_schema.py")
SRC_LEGACY = os.path.join("src", "memory_agent", "insights_legacy.py")
SRC_UTILS = os.path.join("src", "memory_agent", "insights", "utils.py")
TESTS = ["tests/test_vma_promised_keys_runtime_contract.py",
         "tests/test_vma_coverage_docstring_contract.py"]
TEST_FILE = "tests/test_vma_promised_keys_runtime_contract.py"

MCP_QUALITY_NEW = '"""聚合数据质量：逐项 `checks`，未通过项的名字列在 `issues`。'
MCP_QUALITY_OLD = '"""聚合数据质量：现有 data_quality_issues + 镜像缺口（mirror_dirty）。'
MCP_HINTS_NEW = "return_hints=True 时另给顶层键 `hints`（规划阶段生成的候选追问，调试/混合用）。"
MCP_HINTS_OLD = "return_hints=True 时返回 semantic_hints / agent_memory_hints（调试/混合）。"
SPEC_HINTS_NEW = '"True 时保留顶层 hints（规划阶段生成的候选追问，调试用）"'
SPEC_HINTS_OLD = '"True 时保留 semantic_hints/agent_memory_hints（调试用）"'
SPEC_PITFALL_NEW = '"取值是意图名（device_usage/behavior/anomaly/rhythm/activity/persona，认不出来时 auto），"'
#: 注入句**不许带行尾逗号**：pitfall 是三行隐式拼接，带逗号会把中间那行变成
#: 一条独立实参 ⇒ `positional argument follows keyword argument`，腿会因语法错而"响"，
#: 响的原因却不是断言。首版就是这么响的（failed=3 全是 _spec() 里的 SyntaxError）。
SPEC_PITFALL_OLD = '"这是规划工具，调用后必须接着按计划调用 recommended_tool 才能真正取数。"'
SPEC_PITFALL_FAKE = '"取值是意图名（device_usage/climate_sessions/TV_WATCHING，认不出来时 auto），"'
#: 温度链腿：把 attrs 的室温字段名读错 ⇒ 会话给不出 room_temp_c
CHAIN_NEW = 'cur_temp = _as_float(attrs.get("current_temperature"))'
CHAIN_BAD = 'cur_temp = _as_float(attrs.get("room_temperature"))'
#: 输入侧键名当输出键腿：会话格子长出 current_temperature ⇒ 区分锁必须拦住
OUTKEY_NEW = '"room_temp_c": round(sum(rt) / len(rt), 1) if rt else None,'
OUTKEY_BAD = '"current_temperature": round(sum(rt) / len(rt), 1) if rt else None,'

#: (腿名, 说明, 文件, 新文案, 旧文案) —— 前五条是"把文案退回承诺死键的那一版"，后两条动引擎
LEGS = [
    ("M-0", "什么都不改（对照腿：必须全绿）", None, None, None),
    ("L1-quality-docstring", "get_data_quality 的 docstring 退回 data_quality_issues",
     SRC_PY, MCP_QUALITY_NEW, MCP_QUALITY_OLD),
    ("L2-hints-docstring", "ask_memory 的 docstring 退回 semantic_hints/agent_memory_hints",
     SRC_PY, MCP_HINTS_NEW, MCP_HINTS_OLD),
    ("L3-hints-param", "ToolSpec 的 return_hints 说明退回那一对 legacy 名字",
     SRC_SPEC, SPEC_HINTS_NEW, SPEC_HINTS_OLD),
    ("L4-recommended-tool", "route_question 的 pitfall 退回 recommended_tool 那句",
     SRC_SPEC, SPEC_PITFALL_NEW, SPEC_PITFALL_OLD),
    ("L5-fake-intent", "route_question 的 pitfall 列举一个不存在的意图名",
     SRC_SPEC, SPEC_PITFALL_NEW, SPEC_PITFALL_FAKE),
    ("L6-temperature-chain", "气候会话的 attrs 室温字段读错 ⇒ 温度链断（第 10 条该红）",
     SRC_LEGACY, CHAIN_NEW, CHAIN_BAD),
    ("L7-attrs-name-as-key", "会话格子把 attrs 名 current_temperature 当输出键发出（第 9 条该红）",
     SRC_UTILS, OUTKEY_NEW, OUTKEY_BAD),
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def verify(root):
    """只读预检：锚点唯一性在当前树现场量，腿名不许重复。"""
    bad = []
    seen = set()
    for name, _desc, rel, new, old in LEGS:
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
        for label, needle in (("新文案锚点", new),):
            n = text.count(needle)
            if n != 1:
                bad.append("腿 %s 的%s锚点 count=%d（应为 1）：%s" % (name, label, n, needle[:44]))
        if text.count(old) != 0:
            bad.append("腿 %s 的旧文案仍在树里，替换会撞车" % name)
    for rel in TESTS:
        if not os.path.isfile(os.path.join(root, rel)):
            bad.append("测试文件不在：%s" % rel)
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
    touched = (SRC_PY, SRC_SPEC, SRC_LEGACY, SRC_UTILS)
    before = {rel: _sha(os.path.join(root, rel)) for rel in touched}
    print("%-24s %-6s %s" % ("腿", "RC", "读数"))
    for name, desc, rel, new, old in LEGS:
        dst = tempfile.mkdtemp(prefix="ma_c75_leg_")
        try:
            _copy_tree(root, dst)
            if rel is not None:
                path = os.path.join(dst, rel)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                assert text.count(new) == 1, "副本树锚点不唯一：%s" % name
                patched = text.replace(new, old)
                try:
                    ast.parse(patched, filename=path)
                except SyntaxError as exc:
                    bad += 1
                    print("%-24s %-6s 该响 FAILED=%s   <<< 变异副本本身语法不合法（响=%r），"
                          "这条腿无效，改锚点而不是改判据" % (name, "-", exc.msg))
                    continue
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(patched)
            rc, failed, summary, tail = _pytest(dst)
            if name == "M-0":
                ok = (rc == 0 and failed == 0)
            else:
                ok = (rc != 0 or failed > 0)
            if not ok:
                bad += 1
            print("%-24s %-6s %s failed=%d %s%s" % (
                name, rc, "该响" if rel else "对照", failed, summary,
                "" if ok else "   <<< 这条腿没响，锁无效"))
            if not ok:
                print("     说明=%s" % desc)
                print("     尾读数=%s" % tail)
        finally:
            shutil.rmtree(dst, ignore_errors=True)
    print("MUT75_COUNT=%d MUTATION_BAD=%d" % (len(LEGS), bad))
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
