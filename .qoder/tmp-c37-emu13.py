r"""把 remote13 的 SOURCES 格渲染成"容器那一份命令"，在本机跑一遍。

两层引号的门（NAS bash 的双引号串 -> 容器 sh -c）最容易出的事不是语法错，而是
**串走形后照样跑通、只是量错了东西**：`\$(grep … \$R)` 少一个反斜杠就变成在外层展开，
容器里读到的是一串 0，看起来像"这棵树没有这个零件"。所以出网前先在本地把外层
展开一次、拿到容器真正会执行的字符串，再逐格对读数。

`python` 在本机 Git Bash 里不一定在 PATH， emulation 副本里换成 Python313 的绝对路径；
其余命令（grep/awk/wc/ls）与容器口径一致。
"""
import io
import subprocess

P = ".qoder/tmp-c37-remote13.sh"
lines = io.open(P, encoding="utf-8", newline="").read().split("\n")
hits = [ln for ln in lines if 'R=src/memory_agent/insights/repository.py' in ln]
print("LINE_HITS=%d" % len(hits))
assert len(hits) == 1, hits

body = hits[0]
assert body.startswith('ex "'), body[:10]
body = body[len('ex "'):]
idx = body.rindex('" > ')
body = body[:idx]
print("TAIL_OK=%s" % ("${L}_sources.log 2>&1" in hits[0][idx:]))

# 外层（NAS bash 的双引号）会吃掉这些反斜杠，容器收到的是展开后的形状。
# 口径注意：双引号里 bash 只在 `$ ` ` \n ` 反斜杠 ` " ` 这几种前吃掉反斜杠，
# `\[` 不是特殊序列 ⇒ 反斜杠**原样留给容器**（grep BRE 把它当字面 `[`）。
# 之前把它也展开成 `[`， emulation 里那格变成字符类起始、报 Invalid regular expression，
# 是量具错，不是被测对象错——这类"自己写歪了导致读数缺失"必须先排除再说话。
for a, b in (("\\$", "$"), ("\\\"", "\"")):
    body = body.replace(a, b)
body = body.replace("cd $L", "cd .")
body = body.replace("python -c",
                    "/c/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe -c")
print("REMAIN_BS_DOLLAR=%d" % body.count("\\$"))

data = (body + "\n").encode("utf-8")
with open(".qoder/tmp13emu-body.sh", "wb") as fh:
    fh.write(data)
r = subprocess.run(["sh", ".qoder/tmp13emu-body.sh"], capture_output=True,
                   text=True, encoding="utf-8")
print("EMU_RC=%d" % r.returncode)
print(r.stdout)
if r.stderr.strip():
    print("STDERR:", r.stderr.strip()[:400])
