import sys

doc, sec = sys.argv[1], sys.argv[2]
before = open(doc, "rb").read()
add = open(sec, "rb").read()
assert b"\r" not in add, "section file has CR"
tail = b"" if before.endswith(b"\n\n") else (b"\n" if before.endswith(b"\n") else b"\n\n")
with open(doc, "ab") as fh:
    fh.write(tail + add)
after = open(doc, "rb").read()
print("before=%d after=%d delta=%d CR=%d BOM=%s ends_nl=%s LF=%d" % (
    len(before), len(after), len(after) - len(before),
    after.count(b"\r"), after[:3] == b"\xef\xbb\xbf", after[-1:] == b"\n",
    after.count(b"\n")))
APPEND_RC = 0
print("APPEND_RC=%d" % APPEND_RC)
