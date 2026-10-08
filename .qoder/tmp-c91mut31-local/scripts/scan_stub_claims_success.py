#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「桩体谎称成功」的静态分诊量具（第十四轮 MA-29 / MA-30 建议门禁，报告 :213-214 原句）。

审计员原话：**「断言函数体内含 `TODO`/`FIXME` 注释时，其返回/响应中不得出现 `ok=True`
或"已执行/已完成/成功"类措辞。」**（第十四轮 :213-214），并指出这一把能封住整族、
与"基线只准减少"的机制契合（:216）。

为什么这一族非量具不可：MA-30（`POST /api/nr/execute-action` 零实现却回「动作已执行」）
的形状是**函数体完全正确、返回也完全合法**，只有拿"注释里自己承认没做"与"回执里说做了"
两条线对撞才看得见。人肉读码只会读那一个入口，读不到第二个。

口径（三条都写死，避免把自己读红）：
  候选 = 任意函数（含 async），其**行区间内**有 `# TODO` / `# FIXME` / `# 未实现` 注释
  判红 = 该函数任一 `return` 里出现
           a) dict 字面量 `"ok": True`（含 `{"ok": True, …}` 后随键）
           b) 字符串字面量含「已执行 / 已完成 / 已发送 / 校验通过 / 已写入 / 成功」
  不算 = ① docstring 里的 "TODO" 字样（注释按 tokenize 取，docstring 是 Expression 不是 COMMENT）
         ② 别的函数里的注释与本函数的返回（按行区间严格归属）
         ③ `{"ok": False, "reason": "not_implemented"}` —— 如实降级正是本量具要的写法
  豁免 = `EXEMPT` 在册（呈 DCD 待裁的桩），豁免**必须仍然命中**；桩体消失而豁免还留着
         也算红（`EXEMPT_STALE`），对应"基线只准减"

用法：
    python scripts/scan_stub_claims_success.py                 # 默认扫 src/memory_agent
    python scripts/scan_stub_claims_success.py --self-test
退出码：0 干净 / 1 有判红 / 2 量具自检失败
"""
import argparse
import ast
import io
import os
import tokenize

CLAIM_WORDS = ("已执行", "已完成", "已发送", "已写入", "校验通过", "成功")
TODO_MARKS = ("TODO", "FIXME", "未实现")

#: 呈 DCD 待裁的桩（登记 = 承认它在，不假装它绿）。裁定落地后**必须**从这张表里删掉，
#: 删不掉就会以 EXEMPT_STALE 反向判红。
EXEMPT = {
    ("rule_engine.py", "_action_alert"): "MA-29 五动作桩体：呈 DCD（20261008 回执 §四 Q1，甲=接真实现 / 乙=如实降级）",
    ("rule_engine.py", "_action_webhook"): "MA-29 同上",
    ("rule_engine.py", "_action_tts"): "MA-29 同上",
    ("rule_engine.py", "_action_light"): "MA-29 同上",
    ("rule_engine.py", "_action_camera"): "MA-29 同上",
}


def _comments(path):
    """返回 [(行号, 注释文本)]。只认真注释——docstring 走 ast，不会到这里。"""
    out = []
    with io.open(path, "rb") as fh:
        try:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type == tokenize.COMMENT:
                    out.append((tok.start[0], tok.string))
        except Exception:
            return out
    return out


def _claims(node):
    """该函数 return 里的"成功口径"：返回 (ok_true, 文案命中列表)。"""
    ok_true = 0
    phrases = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Return) or sub.value is None:
            continue
        for lit in ast.walk(sub.value):
            if isinstance(lit, ast.Dict):
                for k, v in zip(lit.keys, lit.values):
                    if (isinstance(k, ast.Constant) and k.value == "ok"
                            and isinstance(v, ast.Constant) and v.value is True):
                        ok_true += 1
            elif isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                for w in CLAIM_WORDS:
                    if w in lit.value:
                        phrases.append(w)
    return ok_true, sorted(set(phrases))


def scan(root):
    rows = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            text = io.open(path, encoding="utf-8", errors="replace").read()
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            comments = _comments(path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                lo, hi = node.lineno, getattr(node, "end_lineno", node.lineno)
                marks = sorted({m for ln, c in comments if lo <= ln <= hi
                                for m in TODO_MARKS if m in c})
                if not marks:
                    continue
                ok_true, phrases = _claims(node)
                if not (ok_true or phrases):
                    continue
                rows.append({
                    "file": path, "name": node.name, "line": node.lineno,
                    "marks": marks, "ok_true": ok_true, "phrases": phrases,
                    "key": (fn, node.name),
                })
    return rows


def problems_of(rows):
    probs = []
    for r in rows:
        if r["key"] in EXEMPT:
            continue
        probs.append("STUB_CLAIMS_OK %s:%d %s 注释=%s 返回 ok:True×%d 文案=%s"
                     % (r["file"].replace(os.sep, "/"), r["line"], r["name"],
                        "/".join(r["marks"]), r["ok_true"], ",".join(r["phrases"]) or "-"))
    return probs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="*", default=["src/memory_agent"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    rows = []
    for root in args.root:
        rows.extend(scan(root))
    probs = problems_of(rows)
    hit = {r["key"] for r in rows}
    stale = [k for k in EXEMPT if k not in hit]
    print("ROOT=%s STUBS=%d PROBLEM=%d EXEMPT_TOTAL=%d EXEMPT_HIT=%d EXEMPT_STALE=%d"
          % (",".join(args.root), len(rows), len(probs), len(EXEMPT),
             len([r for r in rows if r["key"] in EXEMPT]), len(stale)))
    if args.verbose:
        for r in rows:
            print("  %s %s:%d %s ok:True×%d 文案=%s"
                  % ("EXEMPT" if r["key"] in EXEMPT else "PROBLEM",
                     r["file"].replace(os.sep, "/"), r["line"], r["name"],
                     r["ok_true"], ",".join(r["phrases"]) or "-"))
    for k in stale:
        print("EXEMPT_STALE %s :: %s（桩已经不在了，豁免要一起删）" % ("/".join(k), EXEMPT[k]))
    for p in probs:
        print(p)
    if probs or stale:
        print("SCAN_RC=1 判红 %d 条 / 失效豁免 %d 条" % (len(probs), len(stale)))
        return 1
    print("SCAN_RC=0 除在册豁免外，没有「带 TODO 却回成功」的桩")
    return 0


POSITIVE = {
    "p1_ok_true.py": (
        "def h(a):\n"
        "    # TODO: 实际调用\n"
        "    return {\"ok\": True, \"value\": a}\n"
    ),
    "p2_phrase.py": (
        "def h2(a):\n"
        "    # FIXME 还没接真设备\n"
        "    return {\"status\": \"动作已执行\"}\n"
    ),
}
NEGATIVE = {
    "n1_no_todo.py": (
        "def g(a):\n"
        "    return {\"ok\": True, \"value\": a}\n"
    ),
    "n2_honest.py": (
        "def g2(a):\n"
        "    # TODO: 接线等 ADM 侧\n"
        "    return {\"ok\": False, \"reason\": \"not_implemented\"}\n"
    ),
    "n3_docstring_only.py": (
        "def g3(a):\n"
        "    \"\"\"TODO 早做完了，只是文档里留着这个词。\"\"\"\n"
        "    return {\"ok\": True}\n"
    ),
    "n4_other_function.py": (
        "def helper(x):\n"
        "    # TODO: 这支是真待办\n"
        "    return x\n\n\n"
        "def g4(a):\n"
        "    return {\"ok\": True, \"v\": helper(a)}\n"
    ),
}


def _self_test():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for name, body in list(POSITIVE.items()) + list(NEGATIVE.items()):
            with io.open(os.path.join(tmp, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        rows = scan(tmp)
        names = {r["name"] for r in rows}
        for want in ("h", "h2"):
            if want not in names:
                print("SELFTEST_MISS %s 应判红却没抓到" % want)
                ok = False
            else:
                print("SELFTEST_HIT %s" % want)
        for bad in ("g", "g2", "g3", "g4"):
            if bad in names:
                print("SELFTEST_FALSE %s 不该判红却被抓进来了" % bad)
                ok = False
            else:
                print("SELFTEST_CLEAN %s" % bad)
        # n4 的形状是"注释在上一支、成功回执在下一支"：两支都不该进读数
        # （注释按行区间严格归属，跨函数不串）
        if [r["name"] for r in rows].count("helper") or "g4" in names:
            print("SELFTEST_FALSE n4 跨函数归属漏了：helper/g4 进了读数")
            ok = False
        else:
            print("SELFTEST_CLEAN n4 注释按行区间归属（helper 无回执、g4 无注释 ⇒ 两支都不判红）")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
