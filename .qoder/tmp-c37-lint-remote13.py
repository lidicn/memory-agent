r"""两层引号脚本的**外层展开 lint**（run13 第一次出网就折在这上面）。

形状：`ex "cd $L && R=… && echo X=\$(grep -c pat $R)"`。
外层 NAS bash 的双引号只吃掉 `\$ \` \"` 三种反斜杠序列，**裸 `$R` 会在 NAS 侧展开**。
NAS 侧 `set -u` ⇒ 直接 `R: unbound variable`、整跑作废（run13 实测 REMOTE_DRIVER_RC=1）；
更坏的情况是 NAS 侧**恰好**有同名变量（`$L` 就有），那就不报错，而是把一个本机路径
传进容器去 grep —— 读数变成一串 0，看起来像"这棵树没有这个零件"。

为什么仿真器抓不到这一类：仿真器把外层展开**自己演一遍**，`R=… && grep $R` 在同一个
sh 里前后脚执行，展开得刚刚好——它验的是"串走形后跑什么"，验不出"该 escapes 的没 escape"。
所以判据必须是静态的：`ex "…"` 串里允许裸展开的变量**只有**外层定义的那几个
（`$L`、`$SNAP`），其余一律要求 `\$`。

    python .qoder/tmp-c37-lint-remote13.py [脚本…]
"""
import io
import re
import sys

#: 外层（remote 脚本自身）定义、应当在 NAS bash 侧展开的变量。
OUTER_OK = {"L", "SNAP"}
#: `$` 后面跟标识符/`?`/`(` 的展开点；先剔掉 `\$` 再查，剩下的就是漏 escape 的。
EXPANDED = re.compile(r"(?<!\\)\$([A-Za-z_][A-Za-z0-9_]*|\?|\()")


def _ex_statements(lines):
    """把跨行续写的 `ex "…"` 合并成语句：[(起始行号, 语句全文)]。

    按行数会两头错：引号奇偶在续写行上是假红，`$R` 写在第二行会被漏掉。
    """
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not ln.lstrip().startswith('ex "'):
            i += 1
            continue
        start = i + 1
        chunk = [ln]
        acc = len(re.findall(r'(?<!\\)"', ln))
        while acc % 2 and i + 1 < len(lines):
            i += 1
            chunk.append(lines[i])
            acc += len(re.findall(r'(?<!\\)"', lines[i]))
        out.append((start, "\n".join(chunk), acc % 2))
        i += 1
    return out


def lint(path):
    text = io.open(path, encoding="utf-8", newline="").read()
    statements = _ex_statements(text.split("\n"))
    bad = []
    for start, body, _ in statements:
        for name in EXPANDED.findall(body):
            if name not in OUTER_OK:
                bad.append((start, name))
    # 语句到文件末尾仍未收口 = 少了一个引号，容器侧会 `unexpected EOF while looking
    # for matching "' ——这是 run13 出网前真在仿真里抓到过的一类走形。
    quote_odd = [start for start, _, odd in statements if odd]
    return bad, quote_odd


def main(argv):
    targets = argv or [".qoder/tmp-c37-remote13.sh"]
    rc = 0
    for t in targets:
        bad, qodd = lint(t)
        print("%s UNESCAPED=%d ODD_QUOTE_LINES=%s" % (t, len(bad), qodd))
        seen = {}
        for no, name in bad:
            seen.setdefault(name, []).append(no)
        for name, lines in sorted(seen.items()):
            print("    $%s -> %d 处（行 %s）" % (name, len(lines), lines[:6]))
        if bad or qodd:
            rc = 1
    print("LINT_RC=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
