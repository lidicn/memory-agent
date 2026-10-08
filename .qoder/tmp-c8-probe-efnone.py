"""容器内现读：chromadb 对「显式 embedding_function=None」到底怎么处理（Q1=B 回执要求）。

只读 + EphemeralClient（进程内，不连生产 chroma 服务，不在生产集合上建任何东西）。
"""
import inspect
import sys

import chromadb

print("chromadb.__version__ =", chromadb.__version__)
print("python =", sys.version.split()[0])

client = chromadb.EphemeralClient()
found = None
for klass in type(client).__mro__:
    if "get_or_create_collection" in klass.__dict__:
        found = klass
        break
print("实现类 =", found.__qualname__ if found else None)
if found:
    src = inspect.getsource(found.__dict__["get_or_create_collection"])
    print("---- get_or_create_collection 源码中与 embedding_function 相关的行 ----")
    for i, line in enumerate(src.splitlines(), start=1):
        if "embedding_function" in line:
            print("%3d | %s" % (i, line.rstrip()))
    print("---- _get_create_payload / 默认嵌入相关（同模块） ----")
    mod = inspect.getmodule(found)
    mod_src = inspect.getsource(mod)
    for i, line in enumerate(mod_src.splitlines(), start=1):
        s = line.strip()
        if ("DefaultEmbeddingFunction" in s or "embedding_function is " in s
                or "embedding_function =" in s):
            print("%5d | %s" % (i, s[:160]))

print("---- 实证：EphemeralClient 上「省略 kwarg」与「显式 None」----")
for label in ("省略 kwarg", "显式 None"):
    name = "probe_%d" % (abs(hash(label)) % 100000)
    kwargs = {} if label.startswith("省略") else {"embedding_function": None}
    try:
        col = client.get_or_create_collection(name=name, **kwargs)
        print("CASE %s -> 建集合成功, metadata=%s" % (label, col.metadata or {}))
        try:
            col.add(ids=["p1"], documents=["书房 空调 开启"])
            res = col.query(query_texts=["空调"], n_results=1)
            print("CASE %s -> 写入+检索成功（说明跑起了某个嵌入函数）ids=%s" % (label, res.get("ids")))
        except Exception as exc:
            print("CASE %s -> 写入/检索失败 %s: %s" % (
                label, type(exc).__name__, str(exc)[:280]))
    except Exception as exc:
        print("CASE %s -> 建集合失败 %s: %s" % (label, type(exc).__name__, str(exc)[:280]))

print("PROBE_DONE")
