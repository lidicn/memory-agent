"""只读探针：`conflict_scan` 那行 `HTTPStatusError.__init__() missing 2 required keyword-only
arguments` 的真身到底在哪一层。

生产症状（第六轮重启后首见，`agent_memory.conflict_scan` 打印）只有一行 TypeError，
真实状态码看不见；先前把它记成"chromadb×httpx 接口不兼容"，本脚本用可复跑的读数把它证伪：

* 判据 ①（机制，不需要网络）：chromadb 0.5.23 `CollectionCommon.py:93` 的兜底是
  `raise type(e)(msg)` —— 用**单个 message** 重建异常。httpx≥0.27 的
  `HTTPStatusError.__init__(message, *, request, response)` 关键字专用，重建这一步自身抛
  TypeError，于是原始状态码在"重新抛出"的路上就丢了。**任何**多参构造的异常穿过这层包装都会被吞，
  这不是版本不兼容，是包装器的构造假设。
* 判据 ②（分层定位）：分别跑「嵌入端点直连」「纯 query_embeddings」「query_texts」三条路，
  看哪一条红——红在嵌入那一步就和 chroma 的检索面无关。

安全边界：**只读**。不发 `add`/`upsert`/`delete`，不碰 SQLite，只读集合与向量。

跑法（NAS 容器内，本机没有 chromadb）：
    tr -d '\\r' < scripts/probe_conflict_scan_error_source.py | \\
      ssh lidicn@<NAS> "docker exec -i memory-agent python -"
"""
import inspect
import os
import sys
import traceback


def _versions():
    out = []
    for mod in ("httpx", "chromadb", "requests", "posthog", "numpy"):
        try:
            m = __import__(mod)
            out.append(f"{mod}={getattr(m, '__version__', '?')}")
        except Exception as exc:
            out.append(f"{mod}=IMPORT_ERR({type(exc).__name__})")
    return " | ".join(out)


def _chroma_wrap(exc):
    """chromadb 0.5.23 `CollectionCommon.py:93` 的重建形状。"""
    return type(exc)(f"{str(exc)} in query.")


def part1_mechanism():
    print("== ① 机制：包装器假设异常单参可构造 ==")
    print("   " + _versions())
    import httpx

    kinds = {n: p.kind for n, p in
             inspect.signature(httpx.HTTPStatusError.__init__).parameters.items()}
    print(f"   HTTPStatusError.__init__ 参数形态: {kinds}")
    err = httpx.HTTPStatusError("server error",
                                request=httpx.Request("POST", "http://gw.local/v1/embeddings"),
                                response=httpx.Response(503))
    try:
        wrapped = _chroma_wrap(err)
        print(f"   !! 重建成功（httpx 已改回单参构造？本判据前提失效）: {wrapped}")
    except TypeError as exc:
        print(f"   重建 httpx.HTTPStatusError -> TypeError: {exc}")
        print("   ⇒ 真实状态码 503 不在 TypeError 的消息里，生产日志因此只看得到这行 TypeError")
    try:
        from memory_agent.history import EmbeddingEndpointError
    except Exception as exc:
        print(f"   (memory_agent.history 不可导入，跳过对照: {type(exc).__name__})")
        return
    ours = EmbeddingEndpointError("embedding 端点返回 HTTP 503（model=bge-m3）")
    print(f"   同一个包装过 MA 的 EmbeddingEndpointError -> {type(_chroma_wrap(ours)).__name__}: "
          f"{_chroma_wrap(ours)}")


def part2_layers():
    print("\n== ② 分层：三条路各跑一次（只读）==")
    base = (os.getenv("EMBEDDING_BASE_URL") or "").strip()
    model = (os.getenv("EMBEDDING_MODEL") or "").strip()
    key = (os.getenv("EMBEDDING_API_KEY") or "").strip()
    for k, v in (("EMBEDDING_BASE_URL", base), ("EMBEDDING_MODEL", model),
                 ("EMBEDDING_API_KEY", key)):
        print(f"   {k}: set={bool(v)}")
    host = os.getenv("CHROMA_HOST", "chroma")
    port = int(os.getenv("CHROMA_PORT", "8000"))

    if base and model:
        import httpx
        try:
            with httpx.Client(timeout=20) as c:
                r = c.post(f"{base.rstrip('/')}/embeddings",
                           json={"model": model, "input": ["客厅的灯"]},
                           headers={"Authorization": f"Bearer {key}"} if key else {})
            print(f"   [嵌入端点直连] status={r.status_code} bytes={len(r.content)}")
        except Exception as exc:
            print(f"   [嵌入端点直连] {type(exc).__name__}: {exc}")
    else:
        print("   [嵌入端点直连] 未配置外部端点 → 落 chroma 默认 MiniLM")

    try:
        import chromadb
    except Exception as exc:
        print(f"   chromadb 不可用: {type(exc).__name__}: {exc}")
        return
    client = chromadb.HttpClient(host=host, port=port)
    for name in ("agent_memory", "behavior_history"):
        try:
            col = client.get_or_create_collection(name)
            print(f"   [{name}] count={col.count()}")
        except Exception as exc:
            print(f"   [{name}] get_or_create -> {type(exc).__name__}: {exc}")
            continue
        for dim in (384, 1024):
            try:
                res = col.query(query_embeddings=[[0.1] * dim], n_results=1)
                print(f"   [{name}] query_embeddings(dim={dim}) OK "
                      f"ids={len((res.get('ids') or [[]])[0])}")
                break
            except Exception as exc:
                print(f"   [{name}] query_embeddings(dim={dim}) -> "
                      f"{type(exc).__name__}: {str(exc)[:120]}")
        # 生产 conflict_scan 的形状：query_texts 触发**客户端侧**嵌入函数。
        # 注意这里刻意**不传** embedding_function —— 与 PatternManager 原先的写法一致，
        # 用来演示"落到 chroma 默认 MiniLM"在本容器的下场；MA 的真实生产路径见 ③。
        try:
            res = col.query(query_texts=["客厅的灯"], n_results=1)
            print(f"   [{name}] query_texts OK ids={len((res.get('ids') or [[]])[0])}")
        except Exception as exc:
            print(f"   [{name}] query_texts -> {type(exc).__name__}: {str(exc)[:160]}")
            if "--traceback" in sys.argv:
                traceback.print_exc()


def part3_ma_path():
    """MA 的真实生产形状：用 `resolve_embedding_function` 解析后再建集合、再 query_texts。

    这条是"修好之后真响过"的凭据：嵌入走配置好的网关（不落 MiniLM 的 `/.cache` 墙），
    `where={"state": "live"}` 与 `conflict_scan` 完全一致；全程只读。
    """
    print("\n== ③ MA 生产形状：嵌入函数同源后再跑 query_texts（只读）==")
    try:
        import chromadb

        from memory_agent.config import get_config
        from memory_agent.history import resolve_embedding_function
    except Exception as exc:
        print(f"   依赖不可用，跳过: {type(exc).__name__}: {exc}")
        return
    cfg = get_config()
    fn = resolve_embedding_function(cfg)
    print(f"   解析到的嵌入函数: {type(fn).__name__}（未配置外部端点时才会是 ONNXMiniLM_L6_V2）")
    try:
        client = chromadb.HttpClient(host=cfg.chroma_host, port=cfg.chroma_port)
        col = client.get_or_create_collection("agent_memory", embedding_function=fn)
        res = col.query(query_texts=["客厅的灯"], where={"state": "live"}, n_results=3)
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        print(f"   query_texts OK ids={len(ids)} 最近距离={round(dists[0], 4) if dists else 'n/a'}"
              f"（距离读数不外传，只证明确实取回了向量）")
    except Exception as exc:
        print(f"   !! MA 生产形状仍然失败 -> {type(exc).__name__}: {str(exc)[:200]}")
        if "--traceback" in sys.argv:
            traceback.print_exc()


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
    part1_mechanism()
    part2_layers()
    part3_ma_path()
    print("\nPROBE_RC=0")
