# 给 `scripts/scan_day_bounds.py` 打「类常量档」补丁（可反复用：先算 mutated 字节再写盘）。
# 用法：PY tmp-c37-scanpatch.py <目标 scan_day_bounds.py 路径>
# 锚点命中次数不是 1 就整份不写（绝不 `open(p,'wb')` 串在可能失败的表达式前面）。
import sys

TARGET = sys.argv[1]
INT_LIT = """
def _int_literal(node):
    return (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool))


def _target_names_of(assign_targets):
    names = set()
    for t in assign_targets:
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \\
                and t.value.id in RECEIVERS:
            names.add(("self", t.attr))
    return names


def _class_int_const(tree, lineno, expr_node):
    \"\"\"`self.CONST` 且 CONST 在**同一个类体内**是整数字面量 ⇒ 按 `literal` 归属。

    为什么单独认这一档：`_taint_sets` 把方法的第一个形参（`self`/`cls`）也算进形参污染集，
    于是 `timedelta(days=self.DUAL_TRACK_DAYS)`（类常量 30，现网 `service_tokens.py:434`
    的那一格）被读成 `param-tainted:self` 判红。接收人不是外部输入 ⇒ 这是**量具的过度污染**，
    不是代码缺陷；把它当缺陷去 clamp 一个 30 的常量才是错的。

    但也不能反过来放开成"`self.` 一律免检"：实例属性完全可能在 `__init__` 里被写成请求参数
    （`self.span = req`），类常量也可能被同名赋值悄悄替掉。所以认档要同时满足两条：
    类体顶层赋的是**整数字面量**，且类内（含 `__init__`）**没有** `self.ATTR = <非字面量>`。
    任一条不成立就退回原口径，该红还是红。
    \"\"\"
    if tree is None or not isinstance(expr_node, ast.Attribute):
        return ""
    base = expr_node.value
    if not (isinstance(base, ast.Name) and base.id in RECEIVERS):
        return ""
    attr = expr_node.attr
    cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            if node.lineno <= lineno <= end and (cls is None or node.lineno > cls.lineno):
                cls = node
    if cls is None:
        return ""
    declared = False
    for st in cls.body:                       # 只看类体顶层：嵌套函数里的同名赋值不算
        value = st.value if isinstance(st, (ast.Assign, ast.AnnAssign)) else None
        if value is None or not _int_literal(value):
            continue
        targets = ([t for t in st.targets] if isinstance(st, ast.Assign) else [st.target])
        if any(isinstance(t, ast.Name) and t.id == attr for t in targets):
            declared = True
    if not declared:
        return ""
    for node in ast.walk(cls):                # 类内任何一处把实例属性写成非字面量 ⇒ 不认档
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None or _int_literal(value):
                continue
            targets = ([t for t in node.targets] if isinstance(node, ast.Assign)
                       else [node.target])
            if ("self", attr) in _target_names_of(targets):
                return ""
    return attr


"""

ANCHORS = [
    ("RECEIVERS 常量",
     'MARKER = "day-ok"\n',
     'MARKER = "day-ok"\nRECEIVERS = ("self", "cls")\n'),
    ("助手插在 _markers_in_range 之前",
     "def _markers_in_range(lines, lo, hi):\n",
     INT_LIT + "def _markers_in_range(lines, lo, hi):\n"),
    ("classify 签名带上 tree/lineno",
     "def classify(func_node, expr_node):\n"
     '    """返回 (归属标签, 依据)。配置链优先于形参污染，字面量与日期算式最先。"""\n',
     "def classify(func_node, expr_node, tree=None, lineno=0):\n"
     '    """返回 (归属标签, 依据)。配置链优先于形参污染，字面量与日期算式最先。"""\n'),
    ("类常量档插在配置链之后、形参污染之前",
     '    hit = names & tainted\n',
     "    const = _class_int_const(tree, lineno, expr_node)\n"
     '    if const:\n'
     '        return "literal", "class-const:" + const\n'
     "    hit = names & tainted\n"),
    ("调用点传 tree/lineno",
     "            label, why = classify(func, expr)\n",
     "            label, why = classify(func, expr, tree, node.lineno)\n"),
    ("归属口径表补一行",
     "  literal    —— 整数字面量（含负字面量）\n",
     "  literal    —— 整数字面量（含负字面量），以及同一类体内赋的整型类常量"
     "（`days=self.CONST`，现网一例 `service_tokens.py:434`）\n"),
    ("负样本：整型类常量 + 实例属性被改写 = 红（不认档）",
     '    "n8_bounded_var.py": ("from datetime import timedelta\\n\\n\\ndef f(days):\\n"',
     '    "n9_class_const.py": ("from datetime import timedelta\\n\\n\\nclass Svc:\\n"\n'
     '                          "    LIMIT = 30\\n\\n    def f(self, now):\\n"\n'
     '                          "        return timedelta(days=self.LIMIT)\\n"),\n'
     '    "n8_bounded_var.py": ("from datetime import timedelta\\n\\n\\ndef f(days):\\n"'),
    ("正样本：类常量被实例属性改写 / 非字面量 / 纯实例属性，三条都不许放开",
     '    "p8_bounded_only_after_site.py": ("from datetime import timedelta\\n\\n\\ndef f(n):\\n"\n'
     '                                      "    first = timedelta(days=n)\\n"\n'
     '                                      "    n = clamp_days(n)\\n    return first, timedelta(days=n)\\n"),\n',
     '    "p8_bounded_only_after_site.py": ("from datetime import timedelta\\n\\n\\ndef f(n):\\n"\n'
     '                                      "    first = timedelta(days=n)\\n"\n'
     '                                      "    n = clamp_days(n)\\n    return first, timedelta(days=n)\\n"),\n'
     '    # 类常量档的三条反例：同名实例属性被写成非字面量、类体里赋的不是字面量、只有实例属性\n'
     '    "p9_class_const_overwritten.py": ("from datetime import timedelta\\n\\n\\nclass Svc:\\n"\n'
     '                                      "    LIMIT = 30\\n\\n    def __init__(self, req):\\n"\n'
     '                                      "        self.LIMIT = req\\n\\n    def f(self):\\n"\n'
     '                                      "        return timedelta(days=self.LIMIT)\\n"),\n'
     '    "p10_class_const_nonliteral.py": ("from datetime import timedelta\\n\\n\\nclass Svc:\\n"\n'
     '                                      "    LIMIT = compute_span()\\n\\n    def f(self):\\n"\n'
     '                                      "        return timedelta(days=self.LIMIT)\\n"),\n'
     '    "p11_self_attr_only.py": ("from datetime import timedelta\\n\\n\\nclass Svc:\\n"\n'
     '                              "    def __init__(self, req):\\n        self.span = req\\n\\n"\n'
     '                              "    def f(self):\\n        return timedelta(days=self.span)\\n"),\n'),
    ("死代码：重复的 return",
     "        WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS = real_keys, real_app_keys\n"
     "    return 0 if ok else 2\n    return 0 if ok else 2\n",
     "        WRITABLE_CONFIG_KEYS, APP_CONFIG_KEYS = real_keys, real_app_keys\n"
     "    return 0 if ok else 2\n"),
]

with open(TARGET, "rb") as fh:
    original = fh.read()
text = original.decode("utf-8")

mutated_text = text
bad = []
for name, old, new in ANCHORS:
    n = mutated_text.count(old)
    if n != 1:
        bad.append("%s 锚点命中 %d 次" % (name, n))
        continue
    mutated_text = mutated_text.replace(old, new)
    print("ANCHOR_OK %s" % name)

if bad:
    print("PATCH_NOT_FOUND -> %s" % "; ".join(bad))
    print("WRITTEN=0（原文件未动）")
    sys.exit(1)

mutated = mutated_text.encode("utf-8")
with open(TARGET, "wb") as fh:
    fh.write(mutated)
print("WRITTEN=1 bytes=%d->%d" % (len(original), len(mutated)))
