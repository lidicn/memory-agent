"""#43 向量面 fail-closed 四条锁的变异自证（改源码 → 跑测试 → 回写还原 → RESTORED）。"""
import os
import subprocess
import sys

PY = r"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe"
HIST = "src/memory_agent/history.py"
PAT = "src/memory_agent/patterns.py"
TEST = "tests/test_vma_r8_chroma_error_masking.py"

MUTS = [
    ("M1 自检建集合丢掉 embedding_function（= 落回 chroma 默认 MiniLM）", HIST,
     '            col = client.get_or_create_collection(\n'
     '                name=self.COLLECTION_NAME,\n'
     '                metadata={"description": "家庭行为历史摘要"},\n'
     '                embedding_function=embed_fn,\n',
     '            col = client.get_or_create_collection(\n'
     '                name=self.COLLECTION_NAME,\n'
     '                metadata={"description": "家庭行为历史摘要"},\n'),
    ("M2 自检的不可用短路失活（if False）", HIST,
     '        if embed_fn is _EMBEDDING_UNAVAILABLE:\n'
     '            steps.append(\n'
     '                {\n'
     '                    "step": "解析嵌入端点",',
     '        if False:\n'
     '            steps.append(\n'
     '                {\n'
     '                    "step": "解析嵌入端点",'),
    ("M3 模式库建集合丢掉 embedding_function", PAT,
     '                metadata={"hnsw:space": "cosine"},\n'
     '                embedding_function=_embed,\n',
     '                metadata={"hnsw:space": "cosine"},\n'),
    ("M4 embedding_status 文案回到「使用 chroma 默认模型」", HIST,
     '                "reason": "未配置 embedding 端点 ⇒ 向量面不可用（不回退本地 MiniLM）",',
     '                "reason": "未配置 embedding 端点（使用 chroma 默认模型）",'),
]

ENVS = {k: os.environ[k] for k in ("PATH", "TEMP", "SYSTEMROOT", "USERPROFILE") if k in os.environ}
ENVS["JWT_SECRET"] = "ci-test"


def run_tests():
    p = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=".", env=ENVS)
    failed = sorted({ln.split("::")[1].split(" ")[0] for ln in p.stdout.splitlines()
                     if ln.startswith("FAILED ")})
    tail = [ln for ln in p.stdout.splitlines() if " passed" in ln or " failed" in ln]
    return p.returncode, failed, (tail[-1] if tail else p.stdout[-300:])


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


orig = {p: read(p) for p in (HIST, PAT)}
rc, failed, line = run_tests()
print("BASELINE rc=%s %s failed=%s" % (rc, line, failed))
if rc != 0:
    print("基线不绿，终止")
    sys.exit(2)

overall = 0
for label, path, old, new in MUTS:
    old_b, new_b = old.encode("utf-8"), new.encode("utf-8")
    data = read(path)
    n = data.count(old_b)
    if n != 1:
        print("ABORT %s：锚点命中 %d 次（应为 1）" % (label, n))
        overall = 1
        break
    write(path, data.replace(old_b, new_b, 1))
    rc2, failed2, line2 = run_tests()
    write(path, read(path).replace(new_b, old_b, 1))
    restored = read(path) == orig[path]
    print("MUT %-56s rc=%s %s | red=%s | RESTORED=%s" % (label, rc2, line2, failed2, restored))
    if rc2 == 0 or not restored:
        overall = 1
    if not restored:
        write(path, orig[path])
        print("  已强制回写原始内容")

for p, blob in orig.items():
    if read(p) != blob:
        print("残留未还原：%s" % p)
        overall = 1
print("MUT_RC=%d" % overall)
