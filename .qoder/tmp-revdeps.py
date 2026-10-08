import importlib.metadata as M
want = {"ecdsa", "oauthlib", "pyjwt", "python-jose", "chromadb"}
rev = {}
for d in M.distributions():
    try:
        name = (d.metadata["Name"] or "").lower()
    except Exception:
        continue
    for r in (d.requires or []):
        base = r.split(";")[0].strip().split("[")[0].split("=")[0].split("<")[0].split(">")[0].strip().lower()
        if base in want:
            rev.setdefault(base, set()).add(name)
print("[REVDEPS] python=", M.version("chromadb") and __import__("sys").version.split()[0])
for k in sorted(want):
    try:
        v = M.version(k if k != "python-jose" else "python-jose")
    except Exception:
        v = "未安装"
    print(f"  {k:<12} 版本={v:<10} 被这些包依赖={sorted(rev.get(k, [])) or '（无人依赖，顶层直接装）'}")
