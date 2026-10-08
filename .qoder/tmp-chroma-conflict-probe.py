"""只读探针：conflict_scan 的 HTTPStatusError 到底是哪一层构造的。

不写生产库、不写 chroma（只做 query / get）。判据是 traceback 的 frame 文件名+行号。
用法：ssh nas "docker exec -i memory-agent python -" < probe.py
"""
import os
import sys
import traceback


def v(mod):
    try:
        m = __import__(mod)
        return f"{mod}={getattr(m, '__version__', '?')}"
    except Exception as exc:
        return f"{mod}=IMPORT_ERR({type(exc).__name__})"


print("== 版本 ==")
print(" | ".join(v(x) for x in ("httpx", "chromadb", "requests", "posthog", "numpy")))
print("python=", sys.version.split()[0])

print("== 环境（只报在场与否，不报值）==")
for k in ("EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_API_KEY",
          "CHROMA_HOST", "CHROMA_PORT"):
    print(f"  {k}: set={bool(os.getenv(k))}")
base = (os.getenv("EMBEDDING_BASE_URL") or "").strip()
model = (os.getenv("EMBEDDING_MODEL") or "").strip()
key = (os.getenv("EMBEDDING_API_KEY") or "").strip()
host = os.getenv("CHROMA_HOST", "chroma")
port = int(os.getenv("CHROMA_PORT", "8000"))

print("== ① 嵌入端点直连（不经 chroma，看真实状态码）==")
if base and model:
    import httpx
    body = {"model": model, "input": ["客厅的灯"]}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(f"{base.rstrip('/')}/embeddings", json=body, headers=headers)
        print(f"  status={r.status_code} bytes={len(r.content)}")
        if r.status_code >= 400:
            print("  body(前200，不含凭据):", r.text[:200].replace(key, "***"))
        # 复刻 MA 的调用形状：raise_for_status 在 httpx 0.28 下是否可用
        try:
            r.raise_for_status()
            print("  raise_for_status(): 正常（非 2xx 才抛）")
        except Exception as exc:
            print(f"  raise_for_status() -> {type(exc).__name__}: {exc}")
    except Exception as exc:
        print(f"  直连异常 {type(exc).__name__}: {exc}")
        traceback.print_exc()
else:
    print("  未配置外部嵌入端点 → chroma 走本地 MiniLM")

print("== ② 纯向量 query（绕开嵌入函数，判故障在哪一层）==")
import chromadb
try:
    client = chromadb.HttpClient(host=host, port=port)
except Exception as exc:
    print(f"  HttpClient 失败 {type(exc).__name__}: {exc}")
    sys.exit(1)
try:
    col = client.get_or_create_collection("agent_memory")
    cnt = col.count()
    print(f"  集合在场 count={cnt}")
    for dim in (384, 1024):
        try:
            res = col.query(query_embeddings=[[0.1] * dim], where={"state": "live"},
                            n_results=3)
            print(f"  query_embeddings(dim={dim}) OK ids={len((res.get('ids') or [[]])[0])}")
            break
        except Exception as exc:
            print(f"  query_embeddings(dim={dim}) -> {type(exc).__name__}: {exc}")
except Exception as exc:
    print(f"  纯向量路失败 {type(exc).__name__}: {exc}")
    traceback.print_exc()

print("== ③ query_texts 路（生产 conflict_scan 的形状，全量 traceback）==")
try:
    col2 = client.get_or_create_collection("agent_memory")
    res = col2.query(query_texts=["客厅的灯"], where={"state": "live"}, n_results=3)
    print(f"  query_texts OK ids={len((res.get('ids') or [[]])[0])}")
except Exception as exc:
    print(f"  query_texts -> {type(exc).__name__}: {exc}")
    print("-- traceback --")
    traceback.print_exc()
    seen, cur = [], exc
    while cur is not None and len(seen) < 6:
        seen.append(f"{type(cur).__name__}: {str(cur)[:120]}")
        cur = cur.__cause__ or cur.__context__
    print("-- 异常链 --")
    for s in seen:
        print("  ", s)
print("PROBE_DONE")
