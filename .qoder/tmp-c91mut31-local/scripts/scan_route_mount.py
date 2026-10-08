#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Starlette 路由处理函数「定义了但没挂载」的分诊量具（第五轮 R5-1 / 第十一轮 P3-5）。

为什么要量具而不是"读一遍 behavior_routes.py"：这类缺陷的形状是**代码看着是功能、
请求打过去 404**，函数体本身完全正确，越像正经代码越不会有人去点它。全仓 `api/*_routes.py`
有 24 个模块，人肉核对只能覆盖审计探针碰到的那一层。

口径：
  候选 = `api/*_routes.py` 里模块级定义的、形如 `(request)` / `(request, param)` 的函数
  已挂载 = 出现在任一 `Route(...)` / `WebSocketRoute(...)` 的第 2 个位置参数上
           （Name 或 `module.attr` 属性形式都算，跨模块挂载同样能命中）
  判红 = 候选且未挂载且**全仓 src/ 里没有任何其他引用**
  登记不判红 = 未挂载但被别处引用（例如被别的 handler 当函数调用，或有测试直连）——
           这类是"内部函数长得像 handler"，不是失联路由

用法：
    python scripts/scan_route_mount.py                # 默认扫 src
    python scripts/scan_route_mount.py --self-test
退出码：0 干净 / 1 有失联 handler / 2 量具自检失败
"""
import argparse
import ast
import os
import re
import sys

ROUTES_GLOB = re.compile(r"[^/\\]*_routes\.py$")
MOUNT_CALLS = {"Route", "WebSocketRoute", "Mount"}


def _api_files(root):
    out = []
    api_dir = os.path.join(root, "memory_agent", "api") if os.path.basename(root) == "src" else root
    for dirpath, dirs, files in os.walk(api_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if ROUTES_GLOB.match(fn):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _handler_shape(node):
    """starlette handler 的形状：位置参数 1~2 个，第一个不是 self/cls，且不是私有辅助。"""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if node.name.startswith("_"):
        return False
    args = node.args
    if getattr(args, "defaults", None) or getattr(args, "kw_defaults", None):
        return False
    pos = [a.arg for a in list(args.posonlyargs) + list(args.args)]
    if args.vararg or args.kwonlyargs or args.kwarg:
        return False
    if not pos or pos[0] in ("self", "cls"):
        return False
    return 1 <= len(pos) <= 2


def _mounted_names(tree):
    """Route(path, handler, ...) 里 handler 的名字：Name.id 或 Attribute 链的全串。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in MOUNT_CALLS:
            if len(node.args) < 2:
                continue
            h = node.args[1]
            if isinstance(h, ast.Name):
                out.add(h.id)
            elif isinstance(h, ast.Attribute):
                out.add(ast.unparse(h))
    return out


def _module_defs(tree):
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _refs_across_repo(root, name, node, home):
    """在 root 下搜 `name` 的其他引用。

    跳过两处：定义语句自身所在的行（`def name`），以及这个函数自己的函数体
    （体内的递归/自引用不算"别处在用它"）。同文件其他位置**算**引用——
    一个 handler 被另一个已挂载的 handler 直接调用，是可达代码，不是失联路由。
    """
    pat = re.compile(r"\b" + re.escape(name) + r"\b")
    body_lo, body_hi = node.lineno, getattr(node, "end_lineno", node.lineno)
    hits = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:  # noqa: BLE001
                continue
            lines = text.splitlines()
            for m in pat.finditer(text):
                idx = text[:m.start()].count("\n")
                if p == home and body_lo - 1 <= idx < body_hi:
                    continue
                snippet = lines[idx].strip()
                if snippet.startswith(("def ", "async def ")):
                    continue
                hits.append(f"{p.replace(os.sep, '/')}:{idx + 1}")
    return hits


def scan(root):
    rows = []
    for path in _api_files(root):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except Exception as exc:  # noqa: BLE001
            rows.append({"file": path, "line": 0, "name": "-", "state": "parse_fail",
                         "detail": repr(exc)[:80]})
            continue
        mounted = _mounted_names(tree)
        for name, node in sorted(_module_defs(tree).items()):
            if not _handler_shape(node):
                continue
            if name in mounted:
                rows.append({"file": path, "line": node.lineno, "name": name,
                             "state": "mounted", "detail": ""})
                continue
            refs = _refs_across_repo(root, name, node, path)
            rows.append({"file": path, "line": node.lineno, "name": name,
                         "state": "referenced-elsewhere" if refs else "UNMOUNTED",
                         "detail": ",".join(refs[:3])})
    return rows


def problems_of(rows):
    return [f"PROBLEM UNMOUNTED-HANDLER {r['file'].replace(os.sep, '/')}:{r['line']} "
            f"{r['name']}" for r in rows if r["state"] in ("UNMOUNTED", "parse_fail")]


def counts_of(rows):
    """读数口径交给量具自己给，测试里不再复制一遍 state 字面量。"""
    return {
        "handler_shaped": len(rows),
        "mounted": sum(1 for r in rows if r["state"] == "mounted"),
        "referenced": sum(1 for r in rows if r["state"] == "referenced-elsewhere"),
        "unmounted": sum(1 for r in rows if r["state"] == "UNMOUNTED"),
        "parse_fail": sum(1 for r in rows if r["state"] == "parse_fail"),
    }


POSITIVE = {
    # 形状对、没进 ROUTES、也没别处引用 → 必须红
    "p1_unmounted.py": ("from starlette.routing import Route\n\n\n"
                        "async def handler_missing(request):\n    return None\n\n\n"
                        "async def handler_ok(request):\n    return None\n\n"
                        "ROUTES = [Route('/x', handler_ok, methods=['GET'])]\n"),
    # 带路径参数的第二形参也算 handler
    "p2_path_param_shape.py": ("from starlette.routing import Route\n\n\n"
                               "async def h(request, item_id):\n    return None\n\n"
                               "ROUTES = []\n"),
}
NEGATIVE = {
    # 已挂载：Route 与 WebSocketRoute 两种容器，外加一条跨模块属性形式（本模块不该被它牵连）
    "n1_mounted.py": ("from starlette.routing import Route, WebSocketRoute\n\n\n"
                      "async def h1(request):\n    return None\n\n\n"
                      "async def h2(request, sub):\n    return None\n\n"
                      "ROUTES = [Route('/a', h1, methods=['GET']),\n"
                      "          WebSocketRoute('/ws', h2),\n"
                      "          Route('/b', other_module.h3, methods=['GET'])]\n"),
    # 非 handler 形状（辅助函数、带默认值、self）
    "n2_not_handler_shape.py": ("def helper(a, b=1):\n    return a + b\n\n\n"
                                "def _inner(request):\n    return None\n\n\n"
                                "async def ok(request):\n    return None\n\n"
                                "ROUTES = [Route('/a', ok, methods=['GET'])]\n"),
    # 未挂载但全仓有引用 → 只登记不判红
    "n3_referenced_elsewhere.py": ("from starlette.routing import Route\n\n\n"
                                   "async def called_directly(request):\n    return None\n\n\n"
                                   "def use_it(request):\n    return called_directly(request)\n\n"
                                   "ROUTES = [Route('/u', use_it, methods=['GET'])]\n"),
}


def self_test():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        api = os.path.join(tmp, "memory_agent", "api")
        os.makedirs(api)
        for name, body in list(POSITIVE.items()) + list(NEGATIVE.items()):
            # 落盘文件名遵循 api/*_routes.py 命名口径（量具只认这个形状）
            fn = name[:-3] + "_routes.py" if not name.endswith("_routes.py") else name
            with open(os.path.join(api, fn), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        rows = scan(tmp)
        st = {r["name"]: r["state"] for r in rows}
        print("SELFTEST_STATES " + " ".join(f"{k}={v}" for k, v in sorted(st.items())))
        for want, name in (("handler_missing", "p1_unmounted.py"), ("h", "p2_path_param_shape.py")):
            if st.get(want) != "UNMOUNTED":
                print(f"SELFTEST_MISS {name} want={want} got={st.get(want)}")
                ok = False
            else:
                print(f"SELFTEST_HIT {name} -> {want}")
        # 不该出现在读数里的形状：私有辅助、带默认值的工具函数
        for leaked in ("_inner", "helper", "called_directly"):
            if leaked in st and st[leaked] not in ("referenced-elsewhere",):
                print(f"SELFTEST_FALSE {leaked} state={st[leaked]}")
                ok = False
        if st.get("called_directly") != "referenced-elsewhere":
            print(f"SELFTEST_FALSE n3 called_directly={st.get('called_directly')}")
            ok = False
        else:
            print("SELFTEST_CLEAN n3_referenced_elsewhere -> referenced-elsewhere")
        if st.get("h1") != "mounted" or st.get("h2") != "mounted":
            print(f"SELFTEST_FALSE n1 h1={st.get('h1')} h2={st.get('h2')}")
            ok = False
        else:
            print("SELFTEST_CLEAN n1_mounted -> mounted（含跨模块属性形式）")
        if "_inner" in st or "helper" in st:
            print(f"SELFTEST_FALSE n2 shape rows {sorted(k for k in st if k in ('_inner', 'helper'))}")
            ok = False
        else:
            print("SELFTEST_CLEAN n2_not_handler_shape -> 候选形状外")
    return 0 if ok else 2


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="*", default=["src"])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    rows = []
    for root in args.root:
        rows.extend(scan(root))
    probs = problems_of(rows)
    c = counts_of(rows)
    print(f"ROOT={','.join(args.root)} " + " ".join(f"{k}={v}" for k, v in sorted(c.items()))
          + f" problems={len(probs)}")
    for r in rows:
        if r["state"] != "mounted" and args.verbose:
            print(f"  {r['state']:20s} {r['file'].replace(os.sep, '/')}:{r['line']} {r['name']}"
                  f" {r['detail']}")
    for p in probs:
        print(p)
    if probs:
        print(f"SCAN_RC=1 共 {len(probs)} 个失联 handler")
        return 1
    print("SCAN_RC=0 每个 handler 形状的函数都挂在 ROUTES 上（或有明确的其他引用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
