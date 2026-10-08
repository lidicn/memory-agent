import io

P = "tests/test_feedback_pack.py"
MARK = '\n\nif __name__ == "__main__":\n'

src = io.open(P, encoding="utf-8", newline="").read().replace("\r\n", "\n")
block = io.open(".qoder/tmp-cd3-newtests.txt", encoding="utf-8", newline="").read().replace("\r\n", "\n")

assert src.count(MARK) == 1, f"marker hits={src.count(MARK)}"
out = src.replace(MARK, "\n" + block + MARK.lstrip("\n"))
io.open(P, "w", encoding="utf-8", newline="").write(out)
print("SPLICE_OK lines=%d" % out.count("\n"))
