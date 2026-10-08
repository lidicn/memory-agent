#!/usr/bin/env python3
"""扫「协程里直调的同步慢函数」——审计 A3 §八「少量未卸载的同步 I/O」的可复现量具。

为什么需要它：A3 说"少量未卸载的同步调用在高并发下会造成 P99 劣化"却没给位置，
一句没有位置的结论既不能修也不能验收。本量具把这一格变成逐条 `文件:行号` 的读数。

口径（**只判这些**，不是"全库阻塞调用"的证明）：
- 只进 `async def` 的作用域，且只判**直接**待在协程体里的调用（嵌套同步函数是给
  `to_thread` 准备的料，不算直调）；
- 命中 = 调用落在下面几种"会冻住事件循环"的形状之一，且**不在** `to_thread` /
  `run_in_executor` 的实参范围内：
    * `db`：调用名在 `DB_METHODS`（store 的同步查询/写）；
    * `module`：`MODULE_CALLS` 里列的模块函数（`time.sleep`、`requests.*`、`shutil.*`）；
    * `store`：接收者路径里出现 `store` **且被调节点的身体走到 SQL**（`rt.store.list_members()`
      这一族）；解不到定义时按保守判红；
  加 `--transitive` 再算两种（都要展开一层才知道，一层口径下看不见）：
    * `transitive`：直调的同步函数**自己**在若干层内碰上述面（`路由 → service.foo() → store`）；
    * `subsys`：接收者属性按 `self.<属性> = <类>()` 解析到某个类，且**那个类里的方法节点**
      的体走到阻塞面（`rt.agent_memory.feedback_memory()` 这一族；别处有无同名协程不影响判定，
      而"同步但只查字典"的方法不算阻塞）。
- `bcrypt.*`、`jwt.*`、账号文件读写这类不走 store 的调用不在表里——它们要靠人读命中所在的路由。

**本量具自己的四段历史**（都是"读数说没事、其实有事"或"读数有事、其实没事"，和它要抓的缺陷同族）：
1. 首版只看一层，`/api/auth/login` 那种"路由 → 同步方法 → bcrypt"的传递性阻塞看不见，
   所以 HITS=0 ≠ 循环没被冻住（docstring 原先只承认到这一条）。
2. 第二期 MA-24 撞出更基础的一条：首版 `DB_METHODS` 只列**裸 SQL 动词**
   （`db_query`/`execute`/`commit`…），而路由清一色调 `rt.store.<领域方法>()`，
   于是全仓读数 **HITS=0**，同一次实测按"协程直调 store 面"口径量到 **18 处 / 13 个 handler**。
   ⇒ 尺子的命中集必须覆盖"项目实际怎么写代码"的形状，否则 0 是尺子坏了，不是代码干净。
3. 把 store 面补上之后，`--transitive` 仍漏掉一批：展开按**全局简单名**建索引，
   同名既有 `def` 又有 `async def` 的名字整条剔除（防 `self.llm.ping()` 假红），
   于是 `rt.agent_memory.feedback_memory()`（服务类里是同步、`mcp_server.py` 里有个同名协程）
   被当成"分辨不了"而放过——实测 `agent_memory_routes.py` 10 个 handler 里只报了 2 个。
   ⇒ 加 `subsys` 面：按 `self.<属性> = <类>(…)` 把属性名解析到类，再看**那个类**里的方法节点
   的体是否走到阻塞面（按作用域消歧，不再按全局名一刀切；同步 ≠ 阻塞，纯查字典的不算）。

4. 还有反向的一段：补完 store 面之后它按**变量名**一刀切，把鉴权中间件里的
   `store.verify / count / kind / scopes` 报成"协程直调 SQLite"——那两个 `store` 实际是
   `MCPTokenStore`（`config.agent_tokens` 内存字典 + `compare_digest`，只有 `_touch`
   在节流窗口到点时才写 config.json）。照着这条读数给中间件加了 `to_thread`，
   等于每个请求多一次线程跳转去躲一次纳秒级查表，还推翻了 `#51` 里"已看过、判定不改"的现读理由
   ⇒ 那一腿已回退（备份 `%TEMP%/ma24_auth_offload.reverted.patch`）。
   ⇒ store 面从此**看身体不看名字**：被调节点解得到就要求它的体走到裸 SQL，解不到才保守判红。

量具自证（改前必红的那一半）：`--self-test` 拿八份内置样本跑一遍——
未卸载的正例必须被抓住、`to_thread` 包裹的反例必须不响，
`store`/`transitive`/`subsys`/身体门 各一对（身体门那对：同叫 `store`，一个叫 SQL、一个纯内存）。
任一不成立就退出 2 且不出表。

用法：
    python scripts/scan_unloaded_async_io.py [路径…]         # 默认 src/memory_agent
    python scripts/scan_unloaded_async_io.py --transitive     # 加一层跨函数展开
    python scripts/scan_unloaded_async_io.py --self-test      # 量具自证
退出码：0=无命中，1=有命中（逐条打印），2=自证不通过或解析失败。
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
import tempfile

DB_METHODS = {"db_query", "db_execute", "executescript", "query_rows",
              "execute", "commit"}
MODULE_CALLS = {
    "time": {"sleep"},
    "requests": {"get", "post", "put", "delete", "request", "head"},
    "shutil": {"copytree", "rmtree", "move"},
}
OFFLOAD_FUNCS = {"to_thread", "run_in_executor"}
# 接收者路径里出现这个名字 ⇒ 领域方法面（`rt.store.x()` / `self.store.x()` / `store.x()`）。
STORE_ROLES = {"store"}

POSITIVE_SAMPLE = '''
class A:
    async def bad(self):
        return self.store.db_query("SELECT 1")

    async def also_bad(self):
        time.sleep(1)
'''
NEGATIVE_SAMPLE = '''
class A:
    async def good(self):
        return await asyncio.to_thread(self.store.db_query, "SELECT 1")

    async def also_good(self):
        await loop.run_in_executor(None, self.store.db_query, "SELECT 1")

    async def nested_good(self):
        def _fetch():                      # 嵌套同步函数是"给线程用的料"，不算协程直调
            return self.store.db_query("SELECT 1")
        return await asyncio.to_thread(_fetch)
'''
# store 面（MA-24 那族）：正例两条 = 领域方法直调 + 传递性（直调的同步函数下一层碰 store）
POSITIVE_STORE_SAMPLE = '''
def service_fetch(rt):
    return rt.store.list_members()


class A:
    async def store_bad(self, rt):
        return rt.store.list_members()

    async def transitive_bad(self, rt):
        return service_fetch(rt)
'''
NEGATIVE_STORE_SAMPLE = '''
def service_fetch(rt):
    return rt.store.list_members()


class A:
    async def store_good(self, rt):
        return await asyncio.to_thread(rt.store.list_members)

    async def transitive_good(self, rt):
        return await asyncio.to_thread(service_fetch, rt)
'''
# subsys 面：同名方法在别处有 `async def` 双胞胎，但**接收者那个类**里是同步 `def`。
# 老规则按全局名剔除 ⇒ 这类调用整批漏报（MA-24 实测漏 8 个 handler）。
POSITIVE_SUBSYS_SAMPLE = '''
class Runtime:
    def __init__(self):
        self.svc = Svc()


class Svc:
    def twin_method(self, x):
        return self.store.query(x)


class Elsewhere:
    async def twin_method(self, x):          # 另一类里的同名协程，不该干扰判定
        return x


class A:
    async def subsys_bad(self, rt):
        return rt.svc.twin_method(1)         # 服务类里是同步 ⇒ 冻循环
'''
NEGATIVE_SUBSYS_SAMPLE = '''
class Runtime:
    def __init__(self):
        self.svc = Svc()


class Svc:
    def sync_only(self, x):                  # 同步但只查字典 ⇒ 同步 ≠ 阻塞
        return {"stream": x}

    async def twin_method(self, x):          # 本类里就是协程 ⇒ 不算阻塞
        return x


class A:
    async def subsys_good(self, rt):
        return await rt.svc.twin_method(1)

    async def pure_good(self, rt):
        return rt.svc.sync_only(1)

    async def offloaded_good(self, rt):
        return await asyncio.to_thread(rt.svc.sync_only, 1)
'''
# store 面的**身体门**（第 4 段历史）：接收者变量叫 `store` 不代表它是 SQLite。
# 两条都叫 store、都直待在协程里，判的只是各自的身体。
POSITIVE_STORE_BODY_SAMPLE = '''
class Store:
    def list_members(self):
        return self.db_query("SELECT 1")


class A:
    async def real_store(self, store):
        return store.list_members()          # 一步就到裸 SQL
'''
NEGATIVE_STORE_BODY_SAMPLE = '''
class TokenStore:
    def verify(self, token):
        return self._lookup(token)           # 纯内存 compare_digest

    def _lookup(self, token):
        return token in self.table


class A:
    async def fake_store(self, store):
        return store.verify("t")             # 变量名叫 store，身体不碰 I/O
'''


def _func_name(node: ast.Call) -> tuple:
    """回 (root, attr)：`a.b.c(...)` ⇒ ("a", "c")；`c(...)` ⇒ ("c", "c")。"""
    f = node.func
    if isinstance(f, ast.Attribute):
        cur = f.value
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            return cur.id, f.attr
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
            return cur.func.id, f.attr
        return "?", f.attr
    if isinstance(f, ast.Name):
        return f.id, f.id
    return "?", "?"


def _receiver_path(node: ast.Call) -> list:
    """调用名链条，从根到属性：`runtime(x).store.list_members` ⇒ ["runtime", "store", "list_members"]。

    `_func_name` 把链条压成 (root, attr)，判断"接收者是不是 store 面"需要中间那节。
    """
    f = node.func
    names = []
    if isinstance(f, ast.Attribute):
        names.append(f.attr)
        cur = f.value
        while isinstance(cur, ast.Attribute):
            names.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            names.append(cur.id)
        elif isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
            names.append(cur.func.id)
        names.reverse()
    elif isinstance(f, ast.Name):
        names.append(f.id)
    return names


def verb_kind(root: str, attr: str) -> str | None:
    """表里硬列的裸动词面：`db` = 裸 SQL 动词，`module` = 明确阻塞的模块函数。"""
    if attr in DB_METHODS:
        return "db"
    if root in MODULE_CALLS and attr in MODULE_CALLS[root]:
        return "module"
    return None


def _is_store_face(chain: list) -> bool:
    return bool(STORE_ROLES.intersection(chain[:-1]))


def _call_index(files) -> dict:
    """建三张表，供 `--transitive` 按**作用域**展开（见文档字符串第 3 段）。

    * `attr_classes`：`self.<属性> = <类>(…)` ⇒ 属性名 → 类名集合；
    * `class_defs`：(类名, 方法名) → 同步 `FunctionDef`（同类里有同名协程的不进）；
    * `name_defs`：简单名 → 同步 `FunctionDef` 列表（全局有同名协程的不进）。

    有了前两张，`rt.svc.m()` 判的是「`svc` 那个类里的 `m` 是不是同步、且它的体走到阻塞面」，
    别处有没有同名协程不相干；`name_defs` 只兜住"接收者解不出"的裸函数调用。
    同步 ≠ 阻塞：`frame_url()` 这类"同步但只查字典"的方法要靠体判定，否则读数会被无关调用淹掉。
    """
    attr_classes, class_defs, name_defs, async_class = {}, {}, {}, set()
    async_names = set()
    for tree in files.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for m in node.body:
                    if isinstance(m, ast.AsyncFunctionDef):
                        async_class.add((node.name, m.name))
                    elif isinstance(m, ast.FunctionDef):
                        class_defs[(node.name, m.name)] = m
            elif isinstance(node, ast.AsyncFunctionDef):
                async_names.add(node.name)
            elif isinstance(node, ast.FunctionDef):
                name_defs.setdefault(node.name, []).append(node)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                t, v = node.targets[0], node.value
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"
                        and isinstance(v, ast.Call) and isinstance(v.func, ast.Name)):
                    attr_classes.setdefault(t.attr, set()).add(v.func.id)
    for key in async_class:
        class_defs.pop(key, None)      # 同类里既 def 又 async def ⇒ 不判（宁可漏报）
    for name in async_names:
        name_defs.pop(name, None)
    return {"attr_classes": attr_classes, "class_defs": class_defs, "name_defs": name_defs}


def _resolve_calls(chain: list, ctx: dict) -> list:
    """调用链条 → 候选被调同步节点；解不出回 []。

    属性→类解析优先且**只看解到的那些类**（`rt.store.count()` 不该被 `MCPTokenStore.count()`
    那条 `len(dict)` 稀释成"不阻塞"）；解不到才退到全局简单名。
    """
    if len(chain) >= 2:
        recv, meth = chain[-2], chain[-1]
        classes = ctx["attr_classes"].get(recv)
        if classes:
            hit = [ctx["class_defs"][(c, meth)] for c in classes
                   if (c, meth) in ctx["class_defs"]]
            if hit:
                return hit
    return list(ctx["name_defs"].get(chain[-1], ())) if chain else []


def _resolve_via_attr(chain: list, ctx: dict) -> bool:
    """接收者是不是「属性 → 类」解析出来的（subsys），而不是靠全局简单名兜到的（transitive）。"""
    return len(chain) >= 2 and bool(ctx["attr_classes"].get(chain[-2]))


def _span_contains(outer: ast.AST, lineno: int) -> bool:
    return outer.lineno <= lineno <= (outer.end_lineno or outer.lineno)


def _parents(tree):
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _offload_spans(async_fn):
    return [c for c in ast.walk(async_fn)
            if isinstance(c, ast.Call) and _func_name(c)[1] in OFFLOAD_FUNCS]


def _owner_is(node, call, parents):
    owner = call
    while isinstance(owner, ast.AST) and not isinstance(
            owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        owner = parents.get(owner)
    return owner is node


def _node_calls(fn, ctx) -> list:
    """同步函数体里的调用摊平成 `(裸动词面, store 面?, 候选被调节点)`，不动点每轮只查表。

    store 面**不再按名字一刀切**：`rt.store.x()` 里那个变量叫 `store` 不代表它真是 SQLite
    （MA-24 这一批就把 `MCPTokenStore.verify/count/kind/scopes` 当成 SQLite 直调，
    照着读数给鉴权中间件加了线程跳转——实际是纯内存字典查，见文档字符串第 4 段）。
    所以这里只标"接收者链条里出现 store"这个**形状**，阻塞与否交给候选节点的身体。
    """
    out = []
    for call in ast.walk(fn):
        if not isinstance(call, ast.Call):
            continue
        root, attr = _func_name(call)
        if attr in OFFLOAD_FUNCS:
            continue                                   # 已卸载的那条腿不算它自己阻塞
        chain = _receiver_path(call)
        out.append((verb_kind(root, attr), _is_store_face(chain), _resolve_calls(chain, ctx)))
    return out


def _blocking_sets(ctx):
    """不动点求两个节点集合：`(浅=一步就到裸动词, 深=若干步走到)`，键是**节点身份**而非全局名。

    一个调用点算阻塞的条件：候选被调节点**全部**阻塞（同名多定义时宁可漏报，
    也不要在验收口制造假红）；解不到任何定义、但接收者形状是 store 面的，按保守算浅。
    """
    calls = {}
    for defs in ctx["name_defs"].values():
        for d in defs:
            calls.setdefault(id(d), _node_calls(d, ctx))
    for d in ctx["class_defs"].values():
        calls.setdefault(id(d), _node_calls(d, ctx))

    def shallow(cl):
        return any(kind or (store_face and not cand) for kind, store_face, cand in cl)

    shallow_set = {nid for nid, cl in calls.items() if shallow(cl)}
    blocking = set(shallow_set)
    while True:
        added = {nid for nid, cl in calls.items() if nid not in blocking
                 if any(kind or (store_face and not cand)
                        or (cand and all(id(c) in blocking for c in cand))
                        for kind, store_face, cand in cl)}
        if not added:
            return frozenset(shallow_set), frozenset(blocking)
        blocking |= added


def _hit_reason(call, shallow_ids, blocking_ids, ctx, transitive):
    """这条协程直调算什么形状；None = 不报。`transitive=False` 时只报一层就能定性的。"""
    root, attr = _func_name(call)
    kind = verb_kind(root, attr)
    if kind:
        return kind
    chain = _receiver_path(call)
    if _is_store_face(chain):
        cand = _resolve_calls(chain, ctx) if ctx else []
        if not cand:
            return "store"             # 形状是 store 面又解不到定义 ⇒ 保守判红
        if all(id(c) in shallow_ids for c in cand):
            return "store"             # 一步就到裸 SQL ⇒ 一层口径足以定性
        if transitive and all(id(c) in blocking_ids for c in cand):
            return "store"             # 要展开更深一层才到 SQL
        return None                    # 同步但纯内存（MCPTokenStore 那一族）⇒ 不是缺陷
    if not ctx or not blocking_ids:
        return None
    cand = _resolve_calls(chain, ctx)
    if not cand or not all(id(c) in blocking_ids for c in cand):
        return None                    # 同步但不碰 I/O ⇒ 不是缺陷（lesson 118）
    return "subsys" if _resolve_via_attr(chain, ctx) else "transitive"


def scan_file(path, lines, tree=None, shallow_ids=frozenset(), blocking_ids=frozenset(),
              ctx=None, transitive=False) -> list:
    tree = tree if tree is not None else ast.parse("\n".join(lines), filename=str(path))
    parents = _parents(tree)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        offload_spans = _offload_spans(node)
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            # 只判"直接待在协程体里"的调用：最近的函数层必须是本 async def。
            # 嵌套同步函数是给 to_thread 准备的料，算进去就是假阳性
            # （首跑就是把 `def _fetch()` 里的 conn.execute 当成了直调，HITS 虚报 3）。
            if not _owner_is(node, call, parents):
                continue
            parent = parents.get(call)
            if isinstance(parent, ast.Await) and parent.value is call:
                continue        # await 的是协程，不是同步直调
            reason = _hit_reason(call, shallow_ids, blocking_ids, ctx, transitive)
            if reason is None:
                continue
            if any(_span_contains(o, call.lineno) for o in offload_spans):
                continue
            hits.append("%s:%d [%s] %s" % (path, call.lineno, reason,
                                           lines[call.lineno - 1].strip()))
    return hits


def scan_paths(paths, transitive: bool = False) -> list:
    trees, line_map = {}, {}
    for base in paths:
        root = pathlib.Path(base)
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for p in files:
            try:
                src = p.read_text(encoding="utf-8")
                trees[p] = ast.parse(src, filename=str(p))
                line_map[p] = src.splitlines()
            except (SyntaxError, UnicodeDecodeError, OSError) as exc:
                print("解析失败 %s: %s: %s" % (p, type(exc).__name__, exc))
                raise
    ctx = _call_index(trees)
    shallow_ids, blocking_ids = _blocking_sets(ctx)
    if not transitive:
        blocking_ids = frozenset()
    hits = []
    for p, tree in trees.items():
        hits += scan_file(p, line_map[p], tree=tree, shallow_ids=shallow_ids,
                          blocking_ids=blocking_ids, ctx=ctx, transitive=transitive)
    return hits


def self_test() -> int:
    """八份样本：未卸载的正例必须被抓住、已卸载/不该响的反例必须安静，否则这把尺子本身是坏的。"""
    with tempfile.TemporaryDirectory() as td:
        cases = [
            ("pos", POSITIVE_SAMPLE, 2),
            ("neg", NEGATIVE_SAMPLE, 0),
            ("pos_store", POSITIVE_STORE_SAMPLE, 2),   # store 面 + transitive 面各一条
            ("neg_store", NEGATIVE_STORE_SAMPLE, 0),
            ("pos_subsys", POSITIVE_SUBSYS_SAMPLE, 1),  # 同名协程在别处，不豁免
            ("neg_subsys", NEGATIVE_SUBSYS_SAMPLE, 0),
            ("pos_body", POSITIVE_STORE_BODY_SAMPLE, 1),  # 身体门的正例：叫 store、真碰 SQL
            ("neg_body", NEGATIVE_STORE_BODY_SAMPLE, 0),  # 反例：叫 store、纯内存 ⇒ 不该响
        ]
        readings = {}
        for name, sample, _expect in cases:
            f = pathlib.Path(td) / (name + ".py")
            f.write_text(sample, encoding="utf-8")
            readings[name] = [h for h in scan_paths([str(f)], transitive=True)
                              if h.startswith(str(f))]
        ok = True
        for name, _sample, expect in cases:
            got = len(readings[name])
            expect_line = "期望 %d 实得 %d" % (expect, got)
            if name.startswith("pos") and got < expect:
                ok = False
                print("FAIL %s：未卸载的正例没抓全（%s）" % (name, expect_line))
            if name.startswith("neg") and got:
                ok = False
                print("FAIL %s：已卸载的反例仍在响（%s）" % (name, expect_line))
        for name, _sample, _expect in cases:
            for h in readings[name]:
                print("  %-9s %s" % (name, h.split(" ", 1)[1]))
        if not ok:
            print("量具自证不通过。")
            return 2
        print("SELFTEST OK pos=2 neg=0 pos_store=2 neg_store=0 pos_subsys=1 neg_subsys=0 "
              "pos_body=1 neg_body=0")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="扫协程内直调的同步慢函数")
    ap.add_argument("paths", nargs="*", default=["src/memory_agent"])
    ap.add_argument("--self-test", action="store_true", help="只跑量具自证")
    ap.add_argument("--transitive", action="store_true",
                    help="把「路由 → 同步函数 → store」这类传递性阻塞也算进来")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    hits = scan_paths(args.paths, transitive=args.transitive)
    print("HITS=%d" % len(hits))
    for h in hits:
        print(h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
