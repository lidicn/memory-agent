import importlib.util, os
spec = importlib.util.spec_from_file_location("g", os.path.join(".qoder", "tmp-c77-jskeys.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
idx = g.build_def_index(os.path.join(g.ROOT, "src", "memory_agent"))
routes, handlers = g.route_keysets(g.ROUTES_DIR and os.path.join(g.ROOT, g.ROUTES_DIR), os.path.join(g.ROOT, g.APP_PY), idx)
for nm in ("health", "signal_rules", "member_insight_feedback", "sweep", "template_validate_api", "vision_test_llm"):
    h = handlers.get(nm)
    print("== %s present=%s" % (nm, h is not None))
    if not h: continue
    print("   strict=%s" % ",".join(sorted(h["strict"]) or ["-"]))
    print("   unknown=%s" % ",".join(sorted(h["unknown"]) or ["-"]))
    print("   evidence=%s" % h["evidence"][:6])
    print("   loose_x_has: collecting=%s counts=%s up=%s sweep=%s answer=%s scene=%s" % (
        "collecting" in h["loose_x"], "counts" in h["loose_x"], "up" in h["loose_x"],
        "sweep" in h["loose_x"], "answer" in h["loose_x"], "scene" in h["loose_x"]))
