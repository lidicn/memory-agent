import sys

doc, sec, marker = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(doc, "rb").read()
add = open(sec, "rb").read()
assert b"\r" not in add, "section file contains CR"
idx = raw.rfind(marker.encode("utf-8"))
assert idx > 0, "marker not found"
cut = raw.rfind(b"\n---\n", 0, idx)
assert cut > 0, "no --- rule before marker"
new = raw[:cut] + b"\n" + add + raw[cut:]
tmp = doc + ".insert.tmp"
with open(tmp, "wb") as fh:
    fh.write(new)
assert open(tmp, "rb").read() == new, "tmp mismatch"
import os
os.replace(tmp, doc)
back = open(doc, "rb").read()
print("doc=%s before=%d after=%d CR=%d BOM=%s ends_nl=%s marker_after_add=%s" % (
    doc, len(raw), len(back), back.count(b"\r"), back[:3] == b"\xef\xbb\xbf",
    back[-1:] == b"\n",
    back.index(add[:60]) < back.index(marker.encode("utf-8"))))
INSERT_RC = 0
print("INSERT_RC=%d" % INSERT_RC)
