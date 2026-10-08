"""只读：集合维度与条数（修掉上一版 `numpy array or []` 的真值歧义）。"""
import os
import chromadb

client = chromadb.HttpClient(host=os.getenv("CHROMA_HOST", "chroma"),
                             port=int(os.getenv("CHROMA_PORT", "8000")))
for c in client.list_collections():
    name = getattr(c, "name", str(c))
    try:
        col = client.get_collection(name)
        cnt = col.count()
        g = col.get(limit=1, include=["embeddings"])
        embs = g.get("embeddings")
        dim = "empty"
        if embs is not None and len(embs) > 0 and embs[0] is not None:
            dim = len(embs[0])
        print(f"  {name}: count={cnt} dim={dim} metadata={col.metadata}")
    except Exception as exc:
        print(f"  {name}: ERR {type(exc).__name__}: {exc}")
print("names=", [getattr(x, 'name', str(x)) for x in client.list_collections()])
print("PROBE3_DONE")
