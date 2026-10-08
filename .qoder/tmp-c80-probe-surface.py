r"""#80 建门前的现场量具 v2：散文里点名的键形状候选，按「出键 / 入参 / 待登记」三分。

口径（DCD 20261007 §三 Q1 建门 + 裁5 Q-B「不允许静默忽略」）：
* 从 ToolSpec 的 `summary/description/pitfall` 抽小写标识符形状的 token；
* 该 token 若是**同一工具的入参名**（ToolSpec.params）⇒ 输入侧，不算承诺键；
* 若是运行时探针载荷里**真实存在的键**（递归收 keys）⇒ 承诺成立；
* 其余进**待登记**清单（枚举取值、子路径、散文惯用语），门要求逐条给理由。

只读量具：不跑测试、不改树。

    python .qoder/tmp-c80-probe-surface.py
"""
import ast
import keyword
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
SPEC = os.path.join(ROOT, "src", "memory_agent", "tool_schema.py")
TOKEN = re.compile(r"(?<![A-Za-z0-9_.-])[a-z][a-z0-9_]{1,}(?![A-Za-z0-9_-])")
STOP = set("""a an and any are as at by for from in into is it no not of on or that the to
use with via when which yes can may must be been if then else per s.t etc eg i.e
one two three first next other also only same such very more most than too
""".split())


def prose_of(node):
    out, names = [], []
    for kw in node.keywords:
        if kw.arg in ("summary", "description", "pitfall"):
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                out.append(kw.value.value)
            elif isinstance(kw.value, ast.JoinedStr):
                out.append(" ".join(str(v.value) for v in kw.value.values
                                    if isinstance(v, ast.Constant)))
        if kw.arg == "params" and isinstance(kw.value, (ast.List, ast.Tuple)):
            for el in kw.value.elts:
                if isinstance(el, ast.Call):
                    for ik in el.keywords:
                        if ik.arg == "name" and isinstance(ik.value, ast.Constant):
                            names.append(ik.value.value)
    return " ".join(out), names


specs = {}
with open(SPEC, encoding="utf-8") as fh:
    tree = ast.parse(fh.read(), filename=SPEC)
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ToolSpec":
        nm = None
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                nm = kw.value.value
        if nm is None and node.args and isinstance(node.args[0], ast.Constant):
            nm = node.args[0].value
        if nm:
            specs[nm] = prose_of(node)

print("SPECS=%d" % len(specs))
resid = {}
for name, (prose, params) in sorted(specs.items()):
    toks = sorted({t for t in TOKEN.findall(prose)
                   if t not in STOP and not keyword.iskeyword(t)
                   and ("_" in t or len(t) >= 4)})
    p = set(params)
    cand = [t for t in toks if t not in p]
    if cand:
        resid[name] = cand
print("TOOLS_WITH_CANDIDATES=%d CANDIDATES=%d" %
      (len(resid), sum(len(v) for v in resid.values())))
for name, cand in sorted(resid.items()):
    print("%-32s %s" % (name, " ".join(cand)))
