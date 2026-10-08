#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""#78 形状锁的对照档（控制腿）：把三把门各自打到判红，并证明"什么都不改"那档是绿的。

口径：只在一次性副本树上打补丁，绝不动 src；每份补丁打完先 ast.parse 自证语法没坏。
判据读的是 tests/test_mcp_surface_parity.py 里同一份 `_catalog_source_readings()`，
所以这里红了就是那三把 test 真会红（不是量具与门两套尺）。
"""
import ast
import importlib.util
import io
import os
import shutil
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(REPO, "src", "memory_agent", "mcp_server.py")

spec = importlib.util.spec_from_file_location(
    "parity_lock", os.path.join(REPO, "tests", "test_mcp_surface_parity.py"))
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)

DEAD_LITERAL = (
    "TOOL_CATALOG: list[dict] = [\n"
    + "".join('    {"name": "tool_%d", "group": "g", "summary": "s"},\n' % i for i in range(12))
    + "]\n"
    'TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]\n'
)

VARIANTS = [
    # (名字, 打补丁函数, 期望判红的门集合)
    ("M0_control_nothing_changed", lambda t: t, set()),
    ("M1_dead_literal_restored",
     lambda t: t.replace("BUNDLED_SKILLS_DIR = ", DEAD_LITERAL + "\nBUNDLED_SKILLS_DIR = ", 1),
     {"single_binding", "no_big_table", "runtime_only"}),
    ("M2_source_swapped_to_local_assembly",
     lambda t: t.replace("TOOL_CATALOG = build_catalog()", "TOOL_CATALOG = list(TOOL_NAMES_FROM_SPEC)", 1),
     {"single_binding"}),
    ("M3_module_level_read",
     lambda t: t.replace("BUNDLED_SKILLS_DIR = ", "_EARLY = TOOL_NAMES\nBUNDLED_SKILLS_DIR = ", 1),
     {"runtime_only"}),
    ("M4_names_derived_from_catalog",
     lambda t: t.replace("TOOL_NAMES = TOOL_NAMES_FROM_SPEC",
                         'TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]', 1),
     {"single_binding", "runtime_only"}),
]

GATES = ("single_binding", "no_big_table", "runtime_only")


def verdict(r):
    """把读数换算成三把门各自的绿/红（与 test 里的断言逐条同构）。"""
    out = set()
    cat, names = r["catalog"], r["names"]
    ok_binding = (len(cat) == 1 and isinstance(cat[0][2], ast.Call)
                  and isinstance(cat[0][2].func, ast.Name)
                  and cat[0][2].func.id == "build_catalog"
                  and not cat[0][2].args and not cat[0][2].keywords
                  and len(names) == 1 and isinstance(names[0][2], ast.Name)
                  and names[0][2].id == "TOOL_NAMES_FROM_SPEC")
    if not ok_binding:
        out.add("single_binding")
    if r["big_dict_tables"]:
        out.add("no_big_table")
    if r["import_time_reads"]:
        out.add("runtime_only")
    return out


tmp = tempfile.mkdtemp(prefix="c78ctl")
bad = 0
try:
    for name, patch, expected in VARIANTS:
        dst = os.path.join(tmp, name + ".py")
        text = io.open(SRC, encoding="utf-8").read()
        new = patch(text)
        assert new != text or name.startswith("M0"), "%s 的补丁没打到任何东西" % name
        ast.parse(new, filename=dst)                      # 语法护栏
        io.open(dst, "w", encoding="utf-8", newline="").write(new)
        got = verdict(parity._catalog_source_readings(dst))
        same = "OK" if got == set(expected) else "FAIL"
        if same == "FAIL":
            bad += 1
        print("%-38s expected=%s got=%s %s" % (
            name, ",".join(sorted(expected)) or "-", ",".join(sorted(got)) or "-", same))
    # 对照腿之外，正树本身必须是绿的（否则上面全 FAIL 也看不出来）
    real = verdict(parity._catalog_source_readings(SRC))
    print("TREE_GREEN=%s" % (real == set()), "TREE_BAD=%s" % (sorted(real),))
    if real:
        bad += 1
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("CTL_VARIANTS=%d CTL_BAD=%d" % (len(VARIANTS) + 1, bad))
raise SystemExit(1 if bad else 0)
