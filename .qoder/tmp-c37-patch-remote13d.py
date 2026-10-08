r"""run13 首跑之后对 remote13 的四处修订（都是量具自己的错或本批 api.py 又改了一次）。

1) SCANS2 格的 PYTHONPATH 少了 `$L/src` —— 两支在册扫描器 `import memory_agent` 时直接
   `ModuleNotFoundError`（run13 读数：CALLSITE_RC=1 / ATTRS_RC=1，各带一整页 traceback）。
   同一支扫描器在本机 `PYTHONPATH=src` 下 RC=0（`门面引擎指向 35，空指向 0`），所以这一格红
   **与代码无关**：量具跑不起来 ≠ 被测对象有问题。这条区别必须写明，否则下一批读成回归。
2) 本批 api.py 又被仓内门禁判红一次（`fake-ok-const` @ api.py:724 的字面量 `ok=True`），
   `ok` 位改为回读不变式，故加两条成对指纹：`API_ANOM_CLOSED_OK` 必须 1、
   `API_ANOM_LITERAL_OK` 必须 0（区域内 `"ok": True` 归零）。
3) MUT13 从 7 条变 9 条（N8 拆回字面量、N9 把回读函数改成空壳），grep 面板随之 N[1-9]。
4) 期望读数与 wc 全部按本机现读更新：api 936 / service 1343 / nlquery 202 / 量具 272 /
   qb 测试 271（合计 3024），QBLAND_TESTDEFS 7→8。
"""
import io
import re

P = ".qoder/tmp-c37-remote13.sh"
text = io.open(P, encoding="utf-8", newline="").read()
orig = text
marks = []

# 1) PYTHONPATH 补 src
#    注意串里 `$L` 是**裸写**的外层变量（要在 NAS 侧展开成 /tmp/<snap>），
#    所以这里不能按 `\$L` 去找——第一版按转义形状搜，hits=0、assert 拦住了写盘。
old = 'PYTHONPATH=$L:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights'
n1 = text.count(old)
text = text.replace(old, 'PYTHONPATH=$L:$L/src:/tmp/pylibs GATES_REQUIRE=1 python scripts/scan_insights')
marks.append(("SCANS2_PATCHED", n1, 2))

# 2) 两条成对指纹（接在 QBLAND_TESTDEFS 之前）
anchor = " && echo QBLAND_TESTDEFS="
add = (" && echo API_ANOM_CLOSED_OK=\\$(awk '/def anomaly_report/,/def device_health/' \\$A"
       " | grep -c 'self._closed_ok(anomalies, summary, filters)')"
       " && echo API_ANOM_LITERAL_OK=\\$(awk '/def anomaly_report/,/def device_health/' \\$A"
       " | grep -c '\\\"ok\\\": True' || true)")
n2 = text.count(anchor)
text = text.replace(anchor, add + anchor, 1)
marks.append(("FINGERPRINT_ANCHOR", n2, 1))

# 3) MUT13 面板从 N[1-7] 收到 N[1-9]
old3 = "grep -E '^(N[1-7] "
n3 = text.count(old3)
text = text.replace(old3, "grep -E '^(N[1-9] ")
marks.append(("MUT13_GREP", n3, 1))

# 4) 头部期望读数
reps = [
    ("#     NL_ANOM_CALL=1 NL_PLAN_EID=3 QBLAND_TESTDEFS=7\n",
     "#     NL_ANOM_CALL=1 NL_PLAN_EID=3 API_ANOM_CLOSED_OK=1 API_ANOM_LITERAL_OK=0\n"
     "#     QBLAND_TESTDEFS=8\n"),
    ("#   wc -l：api 915 / service 1343 / nlquery 202 / scan_qb_param_landing 272 / qb 测试 246（合计 2978）",
     "#   wc -l：api 936 / service 1343 / nlquery 202 / scan_qb_param_landing 272 / qb 测试 271（合计 3024）"),
    ("#   4) 变异三套：MUT13（本批 7 条，其中 N4..N6 咬量具自身零件）+ MUT11（8）+ MUT10（10）。",
     "#   4) 变异三套：MUT13（本批 9 条，N4..N6 咬量具自身零件、N8..N9 咬本批引入的那一格）\n"
     "#      + MUT11（8）+ MUT10（10）。"),
]
for a, b in reps:
    c = text.count(a)
    marks.append((a[:28], c, 1))
    text = text.replace(a, b, 1)

for name, got, want in marks:
    print("%-30s hits=%d want=%d %s" % (name, got, want, "OK" if got == want else "<< FAIL"))
assert all(g == w for _, g, w in marks), marks
assert re.search(r"API_ANOM_LITERAL_OK=\\\$\(awk", text), "新指纹没进串"
io.open(P, "w", encoding="utf-8", newline="").write(text)
print("WROTE=%s CHANGED=%s CR=%d" % (P, text != orig, text.count("\r")))
