import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("g", os.path.join(".qoder", "tmp-c77-jskeys.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
idx = g.build_def_index(os.path.join(g.ROOT, "src", "memory_agent"))
for nm in ("sweep_and_reconcile", "member_insight_feedback", "list_rules", "validate_all",
           "generate", "health", "analyze_room", "test_llm", "arena_analytics"):
    hits = idx.get(nm, [])
    print("== %s defs=%d" % (nm, len(hits)))
    for rel, lineno, ks, node in hits[:3]:
        print("   %s:%d keys=%s" % (rel, lineno, ",".join(sorted(ks)) or "-"))
        # 一跳：这个 def 体内调了谁
        callees = sorted({g._callee_last(c.func) for c in __import__("ast").walk(node)
                          if isinstance(c, __import__("ast").Call)} - {None})
        print("     callees=%s" % ",".join(callees[:14]))
