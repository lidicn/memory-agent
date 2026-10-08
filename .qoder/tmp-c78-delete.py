#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务表 #78（DCD 20261007 §一 裁甲）：删 mcp_server.py 的死目录字面量。

期望值全部当场从文件量出来，不写上轮记忆：
  - 字面量条目数 48（本档先扫一遍再断言）
  - 删除区间 167..605（含两行 `#:` 说明 + 434 行字面量 + 空行 + 派生行 + 空行）= 439 行
  - 覆盖后模块级 TOOL_CATALOG 绑定恰好一处，且值是 build_catalog() 调用

写盘只发生在全部断言通过之后，且走 bytes（不让行尾从 LF 变成 CRLF）。
"""
import ast
import io
import sys

P = "src/memory_agent/mcp_server.py"
OLD = (
    "# 以下两行覆盖上方手工 TOOL_CATALOG / TOOL_NAMES，使 help / describe / selftest\n"
    "# 与内置 LLM 共用同一份工具定义（name/summary/params/example/pitfall）。\n"
    "# 上方手工 TOOL_CATALOG / _TOOL_NAMES 为兼容历史保留，实际以 tool_schema.TOOL_SPECS 为准。\n"
)
NEW = (
    "# 目录唯一真源 = `tool_schema.build_catalog()`（DCD 20261007 §一 裁甲）。\n"
    "# help / describe / selftest / WebUI 的 MCP 接入页与内置 LLM 共用同一份工具定义\n"
    "# （name/summary/params/example/pitfall）；曾经并存的手写字面量已于本批删除。\n"
)

data = io.open(P, "rb").read()
assert b"\r" not in data, "入口条件不成立：文件含 CR，本档只按 LF 行号定位"
text = data.decode("utf-8")
lines = text.split("\n")

# ── 尺 1：删除区间的每一行边界当场核对 ──
assert lines[166] == "#: 工具目录。``help`` 与 WebUI 的 MCP 接入页都直接消费这份元数据，", lines[166]
assert lines[167] == "#: 避免「文档写一套、实现另一套」。", lines[167]
assert lines[168] == "TOOL_CATALOG: list[dict] = [", lines[168]
assert lines[601] == "]", lines[601]
assert lines[602] == "", repr(lines[602])
assert lines[603] == 'TOOL_NAMES = [t["name"] for t in TOOL_CATALOG]', lines[603]
assert lines[604] == "", repr(lines[604])
assert lines[605].startswith("# 网关随包发布的内置技能目录"), lines[605]

# ── 尺 2：条目数当场量（AST 口径，不靠数引号）──
tree = ast.parse(text)
literal = None
for n in tree.body:
    t = n.target if isinstance(n, ast.AnnAssign) else (n.targets[0] if isinstance(n, ast.Assign) else None)
    if isinstance(t, ast.Name) and t.id == "TOOL_CATALOG" and isinstance(n.value, ast.List):
        literal = n
assert literal is not None, "没找到手写字面量那一条绑定"
assert literal.lineno == 169, "字面量不在 169 行：HEAD 已漂移，行号尺作废"
assert len(literal.value.elts) == 48, "字面量条目数不是 48（实为 %d）" % len(literal.value.elts)
names = [e for e in literal.value.elts if isinstance(e, ast.Dict)]
assert len(names) == 48, "字面量里有非 dict 条目"

# ── 尺 3：函数体读者 = 4 处，且都不在 import 期求值的位置上 ──
readers = sorted(n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id in ("TOOL_CATALOG", "TOOL_NAMES")
                 and n.lineno > 605)
print("READERS_AT_OR_AFTER_606=%s" % (readers,))
assert readers, "覆盖块之后没有任何读者 ⇒ 这份目录整体就是死码，裁定要重呈"
import_time = []
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        for dec in n.decorator_list:
            for c in ast.walk(dec):
                if isinstance(c, ast.Name) and c.id in ("TOOL_CATALOG", "TOOL_NAMES"):
                    import_time.append(("decorator", n.lineno))
    elif not (getattr(n, "lineno", -1) in (169, 604, 3350, 3351)):
        for c in ast.walk(n):
            if isinstance(c, ast.Name) and c.id in ("TOOL_CATALOG", "TOOL_NAMES"):
                import_time.append((type(n).__name__, n.lineno))
print("IMPORT_TIME_REFS_OUTSIDE_BINDINGS=%d" % len(import_time))
assert len(import_time) == 0, import_time

# ── 尺 4：待改写的注释块唯一 ──
assert text.count(OLD) == 1, "覆盖块说明注释不唯一/不匹配"

# ── 改动 ──
kept = lines[:166] + lines[605:]
out = "\n".join(kept).replace(OLD, NEW)
new_lines = out.split("\n")
print("OLD_TOTAL=%d NEW_TOTAL=%d DELETED_LINES=%d (NEW 比 OLD %d)" % (
    len(lines), len(new_lines), len(lines) - len(new_lines),
    len(NEW.split("\n")) - len(OLD.split("\n"))))

# ── 尺 5：语法 + 形状锁的前置形状 ──
ntree = ast.parse(out)
binds = []
for n in ntree.body:
    t = n.target if isinstance(n, ast.AnnAssign) else (n.targets[0] if isinstance(n, ast.Assign) else None)
    if isinstance(t, ast.Name) and t.id == "TOOL_CATALOG":
        binds.append((n.lineno, type(n.value).__name__,
                      getattr(getattr(n.value, "func", None), "id", None)))
print("MODULE_TOOL_CATALOG_BINDINGS=%s" % (binds,))
assert len(binds) == 1, "模块级 TOOL_CATALOG 绑定不是恰好一处：%s" % (binds,)
assert binds[0][1] == "Call" and binds[0][2] == "build_catalog", binds[0]
assert out.count("TOOL_CATALOG") == 4, "TOOL_CATALOG 字面出现次数=%d（期望 4：三处函数体读者 + 一处真源绑定）" % out.count("TOOL_CATALOG")
assert out.count("list[dict] = [\n    {\n        \"name\"") == 0, "手写字面量还在"
assert "\r" not in out

if "--write" in sys.argv:
    with open(P, "wb") as fh:
        fh.write(out.encode("utf-8"))
    print("WRITTEN=1 bytes=%d" % (len(out.encode("utf-8")),))
else:
    print("DRY_RUN=1 未写盘")
