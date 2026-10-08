r"""把 remote13 的 `ex "…"` 串里那些**该 escape 却没 escape** 的内层变量补上反斜杠。

run13 首跑读数：`/tmp/ma_c37_remote13_unix.sh: line 64: R: unbound variable`
+ `REMOTE_DRIVER_RC=1` —— SOURCES 格整格没跑，后面十一格全部没跑。判据来自
`.qoder/tmp-c37-lint-remote13.py`（`UNESCAPED=27`，对照 run11 同一格 `UNESCAPED=0`）。

只动 `$R $I $U $A $S $N` 这六个内层变量；`$L`/`${L}` 是外层变量，必须保持裸写
（它们要在 NAS bash 侧展开成 `/tmp/<snap>`）。行内替换用正则，不用整串 replace，
因为 `$A` 这类两字母形状会撞到别的地方。
"""
import io
import re
import sys

P = ".qoder/tmp-c37-remote13.sh"
INNER = re.compile(r"(?<!\\)\$(R|I|U|A|S|N)\b")

text = io.open(P, encoding="utf-8", newline="").read()
lines = text.split("\n")
touched = 0
for i, ln in enumerate(lines):
    if not ln.lstrip().startswith('ex "'):
        continue
    new = INNER.sub(lambda m: "\\" + m.group(0), ln)
    if new != ln:
        lines[i] = new
        touched += 1
out = "\n".join(lines)
# 先算完整字符串再开文件：`open(p,'w')` 会立刻截断，写在可能失败的表达式之后会清空原文件
remaining = len(INNER.findall(out))
print("LINES_TOUCHED=%d REMAINING_UNESCAPED=%d" % (touched, remaining))
assert remaining == 0, remaining
assert out.count("$L") == text.count("$L"), "外层 $L 被误伤"
io.open(P, "w", encoding="utf-8", newline="").write(out)
print("WROTE=%s CR=%d" % (P, out.count("\r")))
