r"""#72 同族的**只读**对账：同名方法在 legacy 有、在门面没有的返回键。

`get_data_quality` 那条不是"写错一个键"，是**门面切换时整块输出没了、且没人红**。
要判断这是不是一族，就把两边的返回字典键集各取一次做差。

分四种新侧形状，只有后两种能产生可疑差集：

* `FWD_LEGACY`：体里只有 `return self.legacy.<m>(…)` —— 形状由 legacy 决定，不比；
* `FWD_CORE`：`return self.core.<m>(…)` —— 键集取自 `insights/service.BehaviorService.<m>`；
* `LITERAL`：门面自己写 `return {…}`；
* `OTHER`：看得见的键集不完整（多层调用、报告构造器把字典吃进去了），**如实标注不硬判**。

只做差集报告，不改任何东西。

    python .qoder/tmp-c72-keydiff.py
"""
import ast
import io
import os

ROOT = os.environ.get("MA_KEYDIFF_ROOT") or os.path.join("src", "memory_agent")


def _methods(path, class_name):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name: n for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return {}


def _dict_keys(fn):
    """函数体里能静态看到的顶层字符串键。

    只看 `return {…}` 会一片假红：新引擎惯用的写法是 `out = {…}` 再 `out["k"] = …`
    最后 `return out`。所以这里同时收：dict 字面量、按名跟踪的赋值、
    `d["k"] = …` 下标写入、`d.update({…})`，以及 `return out` 这种按名回查。
    """
    keys, spread = set(), False
    named = {}

    def add_dict(d):
        got = set()
        for k in d.keys:
            if k is None:
                nonlocal spread
                spread = True
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                got.add(k.value)
        return got

    # 趟 1：哪些名字被 return 过。只收这些名字的 dict，
    # 否则函数里随手构造的行内字典（列表元素、日志载荷）会把键集撑大，把真缺口盖成假绿。
    returned = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Return):
            if isinstance(n.value, ast.Name):
                returned.add(n.value.id)

    # 趟 2：这些名字的 dict 字面量赋值（ast.walk 是 BFS，不按源码顺序，所以必须先攒名字再取值）
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Dict):
            tgt = n.target if isinstance(n, ast.AnnAssign) else (n.targets[0] if n.targets else None)
            if isinstance(tgt, ast.Name) and tgt.id in returned:
                named.setdefault(tgt.id, set()).update(add_dict(n.value))

    # 趟 3：字面量返回 + 按名回查 + 下标写入 + update
    keys |= set().union(*named.values()) if named else set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
            keys |= add_dict(n.value)
        elif isinstance(n, ast.Assign) and n.targets and isinstance(n.targets[0], ast.Subscript):
            base = n.targets[0].value
            s = n.targets[0].slice
            s = s.value if isinstance(s, ast.Constant) else None
            if isinstance(s, str) and isinstance(base, ast.Name) and base.id in returned:
                keys.add(s)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and \
                n.func.attr == "update" and isinstance(n.func.value, ast.Name) and \
                n.func.value.id in returned:
            for a in n.args:
                if isinstance(a, ast.Dict):
                    keys |= add_dict(a)
    return keys, spread


def _forwards(fn):
    """返回该体里出现的 `self.legacy.<m>` / `self.core.<m>` 调用名集合。"""
    fwd = {"legacy": set(), "core": set()}
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and \
           isinstance(n.func.value, ast.Attribute) and \
           isinstance(n.func.value.value, ast.Name) and n.func.value.value.id == "self":
            if n.func.value.attr in fwd:
                fwd[n.func.value.attr].add(n.func.attr)
    return fwd


def main():
    legacy = _methods(os.path.join(ROOT, "insights_legacy.py"), "InsightService")
    facade = _methods(os.path.join(ROOT, "insights", "api.py"), "InsightService")
    service = _methods(os.path.join(ROOT, "insights", "service.py"), "BehaviorService")
    rows, skipped = [], 0
    for name, fnode in sorted(facade.items()):
        lnode = legacy.get(name)
        if lnode is None:
            continue
        fwd = _forwards(fnode)
        lit, spread = _dict_keys(fnode)
        new_keys, shape = set(lit), []
        if fwd["core"]:
            shape.append("FWD_CORE")
            for m in fwd["core"]:
                s = service.get(m)
                if s is not None:
                    new_keys |= _dict_keys(s)[0]
        if fwd["legacy"]:
            shape.append("FWD_LEGACY")
        if lit:
            shape.append("LITERAL")
        shape = "+".join(shape) or "OTHER"
        lkeys, _ = _dict_keys(lnode)
        if shape == "FWD_LEGACY":          # 形状由 legacy 决定，差集恒空是构造上的真
            skipped += 1
            continue
        missing = sorted(lkeys - new_keys)
        rows.append((name, shape, spread, missing, sorted(new_keys)[:6]))
    print("%-26s %-22s %s" % ("门面方法", "新侧形状", "legacy 有而新侧没有的键"))
    hit = 0
    for name, shape, spread, missing, sample in rows:
        mark = "  ←可疑" if missing else ""
        if missing:
            hit += 1
        print("%-26s %-22s %-40s%s  新侧样本=%s%s" % (
            name, shape, ",".join(missing) if missing else "-", mark, sample,
            " (含 **spread)" if spread else ""))
    print("PAIRS=%d FWD_LEGACY_only=%d SUSPECT=%d" % (len(rows) + skipped, skipped, hit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
