"""第八轮（MA 自查）回归锁：chroma 兜底包装吞掉嵌入端点真实状态码。

实测来路（2026-10-04 容器内只读探针，`docker exec -i memory-agent python -`，RC=0）：
生产 `conflict_scan` 打出的 `HTTPStatusError.__init__() missing 2 required keyword-only
arguments: 'request' and 'response'` 不是 chromadb×httpx 接口不兼容，而是
chromadb 0.5.23 `CollectionCommon.py:93` 的兜底重建 `raise type(e)(msg).with_traceback(...)`
——它假设异常能用**单个 message** 构造；httpx≥0.27 的 `HTTPStatusError.__init__`
是 `(message, *, request, response)`，于是重建这一步自己抛 TypeError，真实状态码就此丢失。

修法（只改 MA 侧）：`history._OpenAICompatEmbeddingFunction` 把端点失败换成
单参可构造的 `history.EmbeddingEndpointError`（状态码写进消息），
这样无论 chroma 的包装在不在，状态码都留得住。

覆盖：
1. 机制本身：httpx 的 `HTTPStatusError` 过 chroma 的包装形状确实变成 TypeError；
   `EmbeddingEndpointError` 过同一个包装活下来且留住状态码；
2. 非 2xx / 传输层失败都被换成单参可构造的异常，消息含状态码或异常类型名；
3. 出境面：api key 与端点地址不进异常消息（日志可能被反馈包带走）；
4. 嵌入函数解析同源：`resolve_embedding_function` 与 `PatternManager` 构造集合时
   真的传入同一个嵌入函数（原先 patterns 不传 → 落到 MiniLM 384 维，
   而生产两个集合实测 1024 维，且 MiniLM 在本容器 `os.makedirs('/.cache')` 直接 PermissionError）。
"""
import inspect
import os
import sys
import types

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import httpx  # noqa: E402

from memory_agent.history import (  # noqa: E402
    EmbeddingEndpointError,
    _OpenAICompatEmbeddingFunction,
    resolve_embedding_function,
)

GW = "http://gw.local/v1/embeddings"
KEY = "sk-SECRETTOKEN-9f3a"


def _chroma_wrap(exc):
    """复刻 chromadb 0.5.23 `CollectionCommon.py:93` 的兜底重建形状。"""
    return type(exc)(f"{str(exc)} in query.")


def _fn():
    return _OpenAICompatEmbeddingFunction("http://gw.local/v1", "bge-m3", KEY)


class _FakeClient:
    """替身：只接管 post 的返回/抛出，其余走 httpx 真实现（raise_for_status 要真的跑）。"""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def __call__(self, *a, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        if self._error is not None:
            raise self._error
        return self._response


def _status_response(code):
    return httpx.Response(code, request=httpx.Request("POST", GW))


# ── 1. 机制 ─────────────────────────────────────────────────────────────────

def test_httpx_status_error_cannot_survive_the_chroma_wrap():
    """判据是机制，不是类名：关键字专用的构造器过不了 `type(e)(msg)`。"""
    params = inspect.signature(httpx.HTTPStatusError.__init__).parameters
    keyword_only = {n for n, p in params.items() if p.kind is inspect.Parameter.KEYWORD_ONLY}
    assert {"request", "response"} <= keyword_only, \
        "httpx 已改回单参可构造，本锁的机制前提失效——请连 chroma 包装一起重判"

    with pytest.raises(httpx.HTTPStatusError) as caught:
        _status_response(503).raise_for_status()
    with pytest.raises(TypeError) as masked:
        _chroma_wrap(caught.value)
    # 生产日志里那行就是它——真实状态码不在消息里
    assert "503" not in str(masked.value)


def test_embedding_endpoint_error_survives_the_chroma_wrap_with_the_status_code():
    err = EmbeddingEndpointError("embedding 端点返回 HTTP 503（model=bge-m3）")
    wrapped = _chroma_wrap(err)
    assert isinstance(wrapped, EmbeddingEndpointError)
    assert "503" in str(wrapped)


# ── 2. 端点失败的真实形状 ────────────────────────────────────────────────────

def test_non_2xx_becomes_single_arg_error_carrying_the_status_code(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _FakeClient(response=_status_response(503)))
    with pytest.raises(EmbeddingEndpointError) as caught:
        _fn()(["客厅的灯"])
    assert "503" in str(caught.value)
    assert "bge-m3" in str(caught.value)
    assert caught.value.__cause__ is not None          # 原始 HTTPStatusError 保留在 cause 上
    assert "503" in str(_chroma_wrap(caught.value))    # 过 chroma 包装仍看得见


def test_4xx_from_gateway_also_keeps_the_code(monkeypatch):
    """网关 401/429 是这条链路最常见的失败形状，状态码不能只覆盖 5xx。"""
    monkeypatch.setattr(httpx, "Client", _FakeClient(response=_status_response(429)))
    with pytest.raises(EmbeddingEndpointError) as caught:
        _fn()(["书房空调"])
    assert "429" in str(caught.value)


def test_transport_failure_becomes_single_arg_error(monkeypatch):
    err = httpx.ConnectError("connection refused", request=httpx.Request("POST", GW))
    monkeypatch.setattr(httpx, "Client", _FakeClient(error=err))
    with pytest.raises(EmbeddingEndpointError) as caught:
        _fn()(["客厅的灯"])
    assert "ConnectError" in str(caught.value)
    assert isinstance(_chroma_wrap(caught.value), EmbeddingEndpointError)


def test_success_path_still_returns_vectors(monkeypatch):
    """门自证的"什么都不改"档：2xx 时行为不变，不能被新包装误伤。"""
    import numpy as np

    body = {"data": [{"index": 1, "embedding": [0.2, 0.2]},
                     {"index": 0, "embedding": [0.1, 0.1]}]}
    resp = httpx.Response(200, json=body, request=httpx.Request("POST", GW))
    monkeypatch.setattr(httpx, "Client", _FakeClient(response=resp))
    out = _fn()(["a", "b"])
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32
    assert np.allclose(out, [[0.1, 0.1], [0.2, 0.2]], atol=1e-6)   # 仍按 index 排序


# ── 3. 出境面 ────────────────────────────────────────────────────────────────

def test_error_message_does_not_leak_endpoint_or_key(monkeypatch):
    monkeypatch.setattr(httpx, "Client", _FakeClient(response=_status_response(500)))
    with pytest.raises(EmbeddingEndpointError) as caught:
        _fn()(["客厅的灯"])
    text = str(caught.value)
    assert KEY not in text and "sk-SECRETTOKEN" not in text
    assert "gw.local" not in text


# ── 4. 嵌入函数同源 ──────────────────────────────────────────────────────────

def _app_config(**overrides):
    base = dict(
        chroma_host="127.0.0.1", chroma_port=8000,
        embedding_base_url="http://gw.local/v1", embedding_model="bge-m3",
        embedding_api_key=KEY,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_resolver_prefers_the_configured_gateway():
    fn = resolve_embedding_function(_app_config())
    assert isinstance(fn, _OpenAICompatEmbeddingFunction)


def test_resolver_without_endpoint_returns_unavailable_sentinel():
    """DCD 20261004 Q1=B：未配置外部端点 ⇒ 返回 _EMBEDDING_UNAVAILABLE 哨兵，不回退 MiniLM，不抛异常。"""
    from memory_agent.history import _EMBEDDING_UNAVAILABLE
    fn = resolve_embedding_function(_app_config(embedding_base_url="", embedding_model=""))
    assert fn is _EMBEDDING_UNAVAILABLE, "未配端点应返回不可用哨兵，而非 None 或 MiniLM"


def test_pattern_manager_passes_the_shared_embedding_function(tmp_path, monkeypatch):
    """这条锁住的是本轮新发现的缺陷本体：patterns 建集合时不传 embedding_function。"""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace())
    import memory_agent.patterns as pmod

    captured = {}

    class _FakeHttpClient:
        def __init__(self, host, port):
            captured["host"] = (host, port)

        def get_or_create_collection(self, **kwargs):
            captured.update(kwargs)
            return object()

    monkeypatch.setattr(pmod.chromadb, "HttpClient", _FakeHttpClient, raising=False)
    cfg = _app_config(templates_dir=str(tmp_path / "t"), imported_dir=str(tmp_path / "i"))
    pmod.PatternManager(cfg)

    assert captured.get("name") == "behavior_patterns"
    assert isinstance(captured.get("embedding_function"), _OpenAICompatEmbeddingFunction), \
        "模式库必须与 behavior_history / agent_memory 同一套嵌入，否则同一 chroma 里两套维度"


def test_pattern_manager_still_builds_when_resolver_is_unavailable(tmp_path, monkeypatch):
    """DCD 20261004 Q1=B：解析器坏了也不能让 PatternManager 构造失败——用 _NullCollection 代理，不阻断启动。"""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace())
    import memory_agent.history as hmod
    import memory_agent.patterns as pmod

    monkeypatch.setattr(pmod.chromadb, "HttpClient", lambda host, port: object(), raising=False)
    monkeypatch.setattr(hmod, "resolve_embedding_function",
                        lambda config: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = _app_config(templates_dir=str(tmp_path / "t"), imported_dir=str(tmp_path / "i"))
    pm = pmod.PatternManager(cfg)
    assert pm.collection is not None
    assert isinstance(pm.collection, pmod._NullCollection), "解析失败应落 _NullCollection，不阻断构造"


def test_no_minilm_fallback_when_endpoint_unconfigured(tmp_path, monkeypatch):
    """DCD Q1=B 判据①：未配端点 ⇒ 判定不可用且不落 MiniLM（grep DefaultEmbeddingFunction 应为 0）。"""
    import memory_agent.history as hmod
    src = hmod.__file__
    with open(src, encoding="utf-8") as f:
        code = f.read()
    assert "DefaultEmbeddingFunction" not in code, "history.py 不应引用 DefaultEmbeddingFunction"


def test_startup_not_blocked_when_vector_unavailable(tmp_path, monkeypatch):
    """DCD Q1=B 判据②：向量面不可用时采集/启动未被阻断（PatternManager 构造成功且可调用方法）。"""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace())
    import memory_agent.patterns as pmod

    monkeypatch.setattr(pmod.chromadb, "HttpClient", lambda host, port: object(), raising=False)
    cfg = _app_config(
        embedding_base_url="", embedding_model="",
        templates_dir=str(tmp_path / "t"), imported_dir=str(tmp_path / "i"),
    )
    pm = pmod.PatternManager(cfg)
    # 构造不抛异常 = 启动未被阻断
    assert isinstance(pm.collection, pmod._NullCollection)
    # 空集合代理方法不抛异常 = 采集路径未被阻断
    assert pm.collection.count() == 0
    assert pm.list_patterns() is not None


# ── 5. 建集合调用点不许留「默认嵌入」落点（DCD 20261004 Q1=B 的字面要求） ──────

def _bare_history_manager(config):
    """只装 `chroma_selftest` 用到的字段，绕开 Store/真连接。"""
    import memory_agent.history as hmod

    hm = hmod.HistoryManager.__new__(hmod.HistoryManager)
    hm.config = config
    hm._embed_fn_cache = None
    hm._embed_resolved = False
    hm._chroma_error = ""
    hm._collection = None
    hm._client = None
    return hm


class _FakeCol:
    def __init__(self):
        self.stored_id = None

    def count(self):
        return 3

    def add(self, ids=None, documents=None, metadatas=None):
        self.stored_id = (ids or [None])[0]

    def query(self, query_texts=None, n_results=3):
        return {"ids": [[self.stored_id] if self.stored_id else []]}

    def delete(self, ids=None):
        self.stored_id = None


def _fake_chromadb(monkeypatch, captured):
    class _FakeClient:
        def get_or_create_collection(self, **kwargs):
            captured.append(kwargs)
            return _FakeCol()

    def _http_client(host=None, port=None):
        captured.append({"_client": (host, port)})
        return _FakeClient()

    monkeypatch.setitem(
        sys.modules, "chromadb", types.SimpleNamespace(HttpClient=_http_client)
    )


def test_chroma_selftest_binds_the_resolved_embedding_function(monkeypatch):
    """配了端点 ⇒ 自检建集合必须显式带上解析出的嵌入函数，不许交给 chroma 默认。"""
    _fake_chromadb(monkeypatch, captured := [])
    hm = _bare_history_manager(_app_config())
    result = hm.chroma_selftest()

    assert result["ok"] is True, result
    kwargs = [c for c in captured if "name" in c]
    assert kwargs, "自检没有走到建集合这一步"
    assert isinstance(kwargs[0].get("embedding_function"), _OpenAICompatEmbeddingFunction), \
        "自检建集合没绑定外部嵌入函数 ⇒ 会落回 chroma 默认 MiniLM"


def test_chroma_selftest_never_creates_collection_without_endpoint(monkeypatch):
    """未配端点 ⇒ 自检直接判定向量面不可用，且**一次都不建集合**。

    这条锁的是本轮实测到的剩余落点：`chroma_selftest` 原先不传 embedding_function，
    未配端点时照样建集合，于是落进 chroma 默认 MiniLM ——「没有任何一条路径还能落回
    chroma 默认 MiniLM」这句证明当时并不成立。
    """
    import memory_agent.history as hmod

    _fake_chromadb(monkeypatch, captured := [])
    hm = _bare_history_manager(_app_config(embedding_base_url="", embedding_model=""))
    result = hm.chroma_selftest()

    assert result["ok"] is False
    assert result["error"] == "embedding_endpoint_not_configured", result
    assert "向量面不可用" in result["summary"], result
    assert "MiniLM" in result["hint"], result
    assert [c for c in captured if "name" in c] == [], \
        "未配端点时仍然建了集合 = 落回 chroma 默认 MiniLM"
    assert hm._embedding_function() is hmod._EMBEDDING_UNAVAILABLE


def test_no_src_call_site_creates_collection_without_embedding_function():
    """AST 扫 src/：每个 `get_or_create_collection` / `create_collection` 调用点都要绑定嵌入函数。

    注释与文档字符串里的历史描述不算调用点（AST 天然只看真调用）。
    `**kwargs` 转发也算违规——这条要的是「读代码就能看见嵌入函数被绑上」，
    转发形状让证明变弱（patterns.py 原先就是 `_kwargs["embedding_function"]` 转发，
    已改成显式实参）。将来若必须转发，改写本锁并请一并给出等价证明。
    """
    import ast

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "memory_agent"))
    offenders = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in ("get_or_create_collection", "create_collection"):
                    continue
                kw_names = {kw.arg for kw in node.keywords if kw.arg}
                segment = ast.get_source_segment(source, node) or ""
                if "embedding_function" in kw_names or "embedding_function" in segment:
                    continue
                offenders.append(
                    "%s:%d %s" % (
                        os.path.relpath(path, root).replace(os.sep, "/"),
                        node.lineno, func.attr,
                    )
                )
    assert offenders == [], "这些建集合调用点没绑定嵌入函数，会落回 chroma 默认 MiniLM：%s" % (offenders,)


def test_embedding_status_reason_does_not_claim_default_model():
    """`embedding_status` 的未配置文案不许再说「使用 chroma 默认模型」——Q1=B 后那是假话。"""
    hm = _bare_history_manager(_app_config(embedding_base_url="", embedding_model=""))
    st = hm.embedding_status()
    assert st["configured"] is False
    assert "默认模型" not in st["reason"], st
    assert "MiniLM" in st["reason"], st
