"""对锚预检：把三份既有变异 harness 的锚点拿到**当前工作树**里数一遍命中次数。

命中的必须恰好 1 次；命中 0 = 靶面被本轮改动挪走（该档要重锚或显式跳过并给不交集证据），
命中 >1 = 锚点变得不确定。这一步只读，不注入、不跑 pytest。
"""
import importlib.util
import io
import os
import sys

WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {
    "c81": ".qoder/tmp-c81-mut.py",
    "c82": ".qoder/tmp-c82-mut.py",
    "r20": ".qoder/tmp-r20-mut.py",
    "c85": ".qoder/tmp-c85-mut.py",
}


def load(rel):
    spec = importlib.util.spec_from_file_location("h", os.path.join(WT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_eol(text, eol):
    """和被检 harness 同一条规则：本机工作副本按 `core.autocrlf=true` 落成 CRLF、入库 blob 是 LF，
    跨行锚点只写 `\\n` 时在 CRLF 盘上命中 0 次 —— 那是预检自己瞎，不是靶面被挪走。"""
    if eol == "\r\n":
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return text.replace("\r\n", "\n")


bad = 0
for tag, rel in FILES.items():
    mod = load(rel)
    for leg in mod.LEGS:
        leg_id, file_tag, anchor = leg[0], leg[1], leg[2]
        rel_path = mod.FILES[file_tag]
        path = os.path.join(WT, rel_path)
        with io.open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        anchor = to_eol(anchor, "\r\n" if "\r\n" in text else "\n")
        n = text.count(anchor)
        if n != 1:
            bad += 1
            print(f"DEAD_ANCHOR {tag}/{leg_id} {rel_path} 命中={n} :: {leg[-1][:60]}")
        else:
            print(f"OK {tag}/{leg_id} {os.path.basename(rel_path)}")
print(f"ANCHOR_PRECHECK_BAD={bad}")
sys.exit(1 if bad else 0)
