r"""lint 的自咬：三个档——改前形状必须判红、run13 现状必须判绿、run11 对照必须判绿。

`UNESCAPED=0` 本身不说明量具有效（可能是它压根没在认 `ex "` 行）。所以先喂一份
"把 `\$R` 的反斜杠抹掉"的副本，必须报红；这一格就是 run13 首跑的真实形状。
"""
import io
import os
import re
import subprocess
import sys

PY = sys.executable
SRC = ".qoder/tmp-c37-remote13.sh"
BAD = ".qoder/tmp-c37-remote13-badshape.sh"

text = io.open(SRC, encoding="utf-8", newline="").read()
broken = re.sub(r"\\\$(R|I|U|A|S|N)\b", r"$\1", text)
n = len(re.findall(r"(?<!\\)\$(R|I|U|A|S|N)\b", broken))
print("BROKEN_SHAPED_VARS=%d (应 >0，且与 run13 首跑的 27 同量级)" % n)
assert n > 0, "造不出坏形状 = 自咬失效"
io.open(BAD, "w", encoding="utf-8", newline="").write(broken)

r = subprocess.run([PY, ".qoder/tmp-c37-lint-remote13.py", BAD, SRC,
                    ".qoder/tmp-c37-remote11.sh"],
                   capture_output=True, text=True, encoding="utf-8")
print(r.stdout.strip())
print("LINT_DRIVER_RC=%d" % r.returncode)
os.remove(BAD)
assert r.returncode == 1, "坏形状在场时门必须响"
out = r.stdout
assert "tmp-c37-remote13-badshape.sh UNESCAPED=%d" % n in out, out
assert ".qoder/tmp-c37-remote13.sh UNESCAPED=0 ODD_QUOTE_LINES=[]" in out
assert ".qoder/tmp-c37-remote11.sh UNESCAPED=0 ODD_QUOTE_LINES=[]" in out, \
    "run11（已跑通的对照）被判红 = lint 有假红，不可信"
print("SELF_BITE=OK")
