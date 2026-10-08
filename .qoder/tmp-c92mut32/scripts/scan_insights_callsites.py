"""对 insights 门面每个调用点做静态绑定对账（审计 §十六 的复现器）。

Phase 4 把 `runtime.insights` 换成 `insights/` 包的门面时，方法名没变、参数形状重排了，
调用点没重接。这类缺陷不看运行时读数看不出来（`_degrade` 会把 TypeError 静默收成空页），
所以用一条静态判据把它钉死。

两类判定：
  硬伤（默认判红）
    ARITY  位置实参个数超出门面可收 -> 运行时必抛 TypeError
    KW     传了门面没收的关键字 -> 同上
  advisory（默认只报不判红）
    SLOT   第 i 个位置参的变量名与门面第 i 个形参名不同。串位一定表现为 SLOT，
           但 SLOT 不等于串位（`device_usage(eid, …)` 里变量本来就叫 eid），
           所以它只作为"这里需要人看一眼"的信号。

写法覆盖 `X.foo(args)` 与 `asyncio.to_thread(X.foo, args)` 两种。

用法：
    PYTHONPATH=src python scripts/scan_insights_callsites.py [仓库根目录]
    --strict 时有硬伤即 EXIT=1（供门禁调用）
"""

import argparse
import ast
import inspect
import os
import sys


def facade_signatures():
    from memory_agent.insights import InsightService

    sigs = {}
    for name, member in inspect.getmembers(InsightService, predicate=callable):
        if name.startswith("__"):
            continue
        try:
            sigs[name] = inspect.signature(member)
        except (TypeError, ValueError):
            pass
    return sigs


def chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def is_insights_target(parts):
    if not parts:
        return False
    if "insights" in parts:
        return True
    return parts[-1] == "ins"


def arg_label(a):
    if isinstance(a, ast.Name):
        return a.id
    if isinstance(a, ast.Constant):
        return repr(a.value)
    if isinstance(a, ast.Attribute):
        return ".".join(chain(a)) or "<expr>"
    return "<expr>"


def collect(root, sigs):
    sites = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                calls = []
                f = node.func
                fname = ".".join(chain(f)) if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if fname.endswith(("to_thread", "run_in_executor")) and node.args:
                    head = node.args[0]
                    if isinstance(head, ast.Attribute) and is_insights_target(chain(head)):
                        calls.append((head.attr, list(node.args[1:]), list(node.keywords)))
                elif isinstance(f, ast.Attribute) and is_insights_target(chain(f)):
                    if f.attr in sigs:
                        calls.append((f.attr, list(node.args), list(node.keywords)))
                for method, args, kws in calls:
                    sig = sigs.get(method)
                    if sig is None:
                        continue
                    params = [p for p in sig.parameters.values() if p.name != "self"]
                    pos = [p.name for p in params
                           if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                    has_varpos = any(p.kind == p.VAR_POSITIONAL for p in params)
                    has_varkw = any(p.kind == p.VAR_KEYWORD for p in params)
                    hard, advisory = [], []
                    if not has_varpos and len(args) > len(pos):
                        hard.append(f"ARITY 传{len(args)}>门面{len(pos)}")
                    if not has_varkw:
                        accepted = {p.name for p in params}
                        unknown = [k.arg for k in kws if k.arg and k.arg not in accepted]
                        if unknown:
                            hard.append("KW " + ",".join(unknown))
                    slots = [f"{pos[i]}<-{a.id}" for i, a in enumerate(args)
                             if i < len(pos) and isinstance(a, ast.Name) and a.id != pos[i]]
                    if slots:
                        advisory.append("SLOT " + ",".join(slots))
                    sites.append({
                        "file": path,
                        "line": node.lineno,
                        "method": method,
                        "hard": hard,
                        "advisory": advisory,
                        "args": ",".join(arg_label(a) for a in args)[:70],
                    })
    return sites


def main():
    ap = argparse.ArgumentParser(description="insights 调用点静态绑定对账")
    ap.add_argument("root", nargs="?", default=os.path.join("src", "memory_agent"),
                    help="扫描根目录（默认 src/memory_agent）")
    ap.add_argument("--strict", action="store_true", help="有硬伤时 EXIT=1")
    ap.add_argument("--show-ok", action="store_true", help="附带打印可绑定的调用点")
    ns = ap.parse_args()

    sigs = facade_signatures()
    sites = collect(ns.root, sigs)
    hard = [s for s in sites if s["hard"]]
    adv = [s for s in sites if not s["hard"] and s["advisory"]]

    print(f"扫描根 {ns.root}：调用点 {len(sites)}，硬伤 {len(hard)}，advisory {len(adv)}")
    for s in sorted(hard, key=lambda x: (x["file"], x["line"])):
        print(f"[HARD] {s['file']}:{s['line']} {s['method']} :: {'; '.join(s['hard'])}\n"
              f"       args=({s['args']})")
    for s in sorted(adv, key=lambda x: (x["file"], x["line"])):
        print(f"[ADV ] {s['file']}:{s['line']} {s['method']} :: {'; '.join(s['advisory'])}\n"
              f"       args=({s['args']})")
    if ns.show_ok:
        for s in [x for x in sites if not x["hard"] and not x["advisory"]]:
            print(f"[OK  ] {s['file']}:{s['line']} {s['method']} args=({s['args']})")
    return 1 if (ns.strict and hard) else 0


if __name__ == "__main__":
    sys.exit(main())
