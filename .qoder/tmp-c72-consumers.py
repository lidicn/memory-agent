# -*- coding: utf-8 -*-
"""只读量具：把 keydiff 的 14 条可疑行拿去问"还有人在读这个键吗"。

输入：.qoder/tmp-c72-keydiff.out（←可疑 行）
消费面：mcp_server.py / tool_schema.py / api/*.py（门面切换后仍然在跑的调用点）
判据：某键在新侧不再产出，但消费面仍在字面读取 ⇒ 与 #72 同形（丢键会被 _degrade 或 None 吞掉）。
本脚本不写任何仓库文件，只打印读数。
"""
import io
import os
import re
import sys

ROOT = os.environ.get("MA_KEYDIFF_ROOT", ".")
OUT = os.path.join(ROOT, ".qoder", "tmp-c72-keydiff.out")

CONSUMER_GLOBS = [
    os.path.join(ROOT, "src", "memory_agent", "mcp_server.py"),
    os.path.join(ROOT, "src", "memory_agent", "tool_schema.py"),
]
API_DIR = os.path.join(ROOT, "src", "memory_agent", "api")
if os.path.isdir(API_DIR):
    for name in sorted(os.listdir(API_DIR)):
        if name.endswith(".py"):
            CONSUMER_GLOBS.append(os.path.join(API_DIR, name))

ROW = re.compile(r"^(\S+)\s+(FWD_\S+|LITERAL|OTHER)\s+(\S+)\s+←可疑")


def read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def main():
    if not os.path.exists(OUT):
        print("KEYDIFF_OUT_MISSING=1")
        return 2
    consumers = []
    for p in CONSUMER_GLOBS:
        if os.path.exists(p):
            consumers.append((p, read(p).splitlines()))
    print("CONSUMER_FILES=%d" % len(consumers))

    suspect_rows = []
    for line in read(OUT).splitlines():
        m = ROW.match(line)
        if not m:
            continue
        keys = [] if m.group(3) == "-" else m.group(3).split(",")
        suspect_rows.append((m.group(1), m.group(2), keys))

    total_hits = 0
    for method, shape, keys in suspect_rows:
        # 只认"取值"形状：x["k"] / x.get("k"。字面量 `"k":` 是声明/出参，不是读键。
        hits = []
        for key in keys:
            pat = re.compile(r"""\[\s*(['"])%s\1\s*\]|\.get\(\s*(['"])%s\2""" % (
                re.escape(key), re.escape(key)))
            for p, lines in consumers:
                for i, text in enumerate(lines, 1):
                    if not pat.search(text):
                        continue
                    # 排除读"入参/循环项"的 .get()，只留下可能读服务返回体的取值点
                    if re.search(r"\b(args|kwargs|params|payload|body|cfg|config|item|row|d|"
                                 r"rec|entry|rowd|e)\s*\.\s*get\(\s*['\"]%s" % re.escape(key), text):
                        continue
                    hits.append((key, os.path.basename(p), i, text.strip()[:110]))
        tag = "READ" if hits else "no-reader"
        total_hits += len(hits)
        print("\n== %s [%s] legacy-only=%s -> %s (%d)" % (
            method, shape, ",".join(keys), tag, len(hits)))
        for key, f, i, text in hits[:8]:
            print("   %s  %s:%d  %s" % (key, f, i, text))
    print("\nROWS=%d TOTAL_HITS=%d" % (len(suspect_rows), total_hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
