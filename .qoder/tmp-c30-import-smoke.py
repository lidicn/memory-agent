"""导入烟雾测试：把 src 下每个模块都真导入一次，抓「相对导入层数写错」这类 pyflakes 抓不到的缺陷。

用法：PYTHONPATH=<repo>/src python .qoder/tmp-c30-import-smoke.py
退出码：0 全绿 / 1 有 IMPORT_BAD（缺第三方依赖的模块按 SKIP_DEP 记，不判红）
"""
import importlib
import os
import sys

DEPS = {"chromadb", "mcp", "fastapi", "numpy", "uvicorn", "starlette", "pydantic", "yaml", "httpx"}

root = sys.argv[1] if len(sys.argv) > 1 else "src/memory_agent"
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_REPO, "src"), os.path.join(_REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
bad, skipped, n = [], [], 0
for dirpath, dirs, files in os.walk(root):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for f in sorted(files):
        if not f.endswith(".py"):
            continue
        rel = os.path.join(dirpath, f)
        mod = rel[:-3]
        parts = mod.replace("\\", "/").split("/")
        if "src" in parts:
            parts = parts[parts.index("src") + 1:]
        mod = ".".join(parts)
        if mod.endswith(".__init__"):
            mod = mod[:-9]
        n += 1
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            top = (getattr(exc, "name", "") or "").split(".")[0]
            if isinstance(exc, ModuleNotFoundError) and top in DEPS:
                skipped.append((mod, top))
                continue
            bad.append((mod, repr(exc)[:120]))

print("PY=" + sys.version.split()[0])
print("MODULES_TRIED=" + str(n) + " IMPORT_BAD=" + str(len(bad)) + " SKIP_DEP=" + str(len(skipped)))
for m, e in bad:
    print("IMPORT_BAD", m, e)
for m, d in skipped:
    print("SKIP_DEP", m, d)
print("SMOKE_RC=" + ("1" if bad else "0"))
sys.exit(1 if bad else 0)
