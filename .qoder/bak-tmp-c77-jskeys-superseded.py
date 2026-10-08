r"""#77 的量具：WebUI 前端**读的键** ↔ HTTP 路由 handler **给的键**（只读，不改任何文件）。

为什么这一格和 #74/#75 不是同一件事：那两轮收的是"给模型读的文案"（docstring / ToolSpec / SKILL.md / 手册），
同一族缺陷还有另一面是**给人看的界面**——前端 `res.foo` 读了载荷里没有的键，
JS 不报错，只是 `undefined`，于是那一块面板静默变成空白/`—`。**用户看到的是"功能没了"，日志里一个字都没有。**

读数口径（故意分开，别把"宽松宇宙"当成"精确证明"）：
  RESOLVED = 按真信封语义把 handler 每个 `return` 表达式**解到底**得到的顶层键：
             `ok(helper(x))` 按 `deps.py:47-52` 的 `{"ok": True, **data}` 展开、`{**result}` 同理、
             `return data` 回追该名字在本 handler 内 return 之前的最后一次赋值，最深 `depth` 跳；
             每条 helper 边都留 `helper=file:line#Class.name` 证据，同名多义单列不自行判绿。
  LOOSE    = handler 函数体任意深度的 dict 字面量键并集（宇宙，宽松兜底）。
  HARD     = 前端读的键两者都没有 ⇒ 首要嫌疑；SHAPE = 宇宙里有、顶层解不到 ⇒ 嵌套/分支形状交人判；
  GREEN_BY_HELPER = 只有走通 helper 边才判绿的键 ⇒ **逐条按 file:line 抽查那条边**（存在性结论登记前抽查）。
  UNKNOWN  = 表达式解不动（内建名、动态派发、跨模块同名歧义），单独报数——
             **面数必须打进读数头**，否则又是一次"扫了个空却写了假读数"（§四十九 六）。

用法：
    python .qoder/tmp-c77-jskeys.py            # 全表
    python .qoder/tmp-c77-jskeys.py --selftest # 控制腿：注入一条已知假的读键，尺必须响；真键必须沉默
"""
import ast
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_JS = os.path.join("src", "memory_agent", "static", "js", "api.js")
PAGES_DIR = os.path.join("src", "memory_agent", "static", "js")
ROUTES_DIR = os.path.join("src", "memory_agent", "api")
APP_PY = os.path.join("src", "memory_agent", "app.py")

#: api.js 里 `name: (args) => get('/api/x' + qs(...))` 这一族
RE_METHOD = re.compile(
    r"""^\s{2}(?P<name>[A-Za-z0-9_]+):\s*\((?P<args>[^)]*)\)\s*=>\s*
        (?P<verb>get|post|put|del|streamSSE)\(\s*(?P<url>.*)$""",
    re.M | re.X)
#: 没有形参的写法：`logout: () => post('/api/auth/logout'),`
RE_PATH = re.compile(r"""['"`](/[^'"`\s]+)['"`]""")


def api_methods():
    """api.js 的具名方法 → (verb, path)。返回 (dict, 未解析方法列表)。"""
    text = open(os.path.join(ROOT, API_JS), encoding="utf-8").read()
    out, unparsed = {}, []
    for m in RE_METHOD.finditer(text):
        url_expr = m.group("url")
        paths = RE_PATH.findall(url_expr)
        if not paths:
            unparsed.append(m.group("name"))
            continue
        # 拼接型 URL（`'/api/members/' + id + '/insight-feedback'`）：**所有**字面量段都留下，配对时按
        # "前缀 + 含尾段"筛。只取第一段会把整族 `/api/members/{id}/*` 并成一个宇宙，读键串味——
        # 首跑那 3 条 `memberInsightFeedback key=up/down/memories` 就是这么假出来的。
        out[m.group("name")] = (m.group("verb"), paths)
    return out, unparsed


#: 函数/箭头体头：`function x(...)`、`const x = async (...)`、对象方法简写 `x(...) {`
RE_SCOPE = re.compile(r"""(?:(?:async\s+)?function\s*[A-Za-z0-9_$]*\s*\([^)]*\)|
                        (?:const|let|var)\s+[A-Za-z0-9_$]+\s*=\s*(?:async\s*)?\([^)]*\)|
                        (?:^|[\s{;}])(?P<shorthand>[A-Za-z0-9_$]+)\s*\([^)]{0,200}\))
                        \s*(?P<brace>\{)""", re.M | re.X)


def scopes(text):
    """(start, end) 列表：每个函数体的字符区间（含嵌套）。"""
    out = []
    for m in RE_SCOPE.finditer(text):
        i = m.start("brace")
        depth, closed = 0, None
        for j in range(i, len(text)):
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    closed = j + 1
                    break
        out.append((m.start(), closed if closed else len(text)))
    return sorted(set(out), key=lambda s: (s[1] - s[0]))   # 小（内层）在前


def _scope_of(scopes_, pos):
    for s in scopes_:
        if s[0] <= pos < s[1]:
            return s
    return (0, len(""))


_NON_PAYLOAD = {"then", "catch", "finally", "map", "filter", "forEach", "length", "push", "json",
                "includes", "slice", "join", "find", "findIndex", "some", "every", "reduce", "sort",
                "split", "trim", "toString", "padStart", "padEnd", "toLocaleString", "toFixed",
                "concat", "keys", "values", "entries", "test", "replace", "match", "repeat",
                "querySelector", "querySelectorAll", "addEventListener", "innerHTML", "textContent",
                "value", "style", "classList", "dataset", "scrollTop", "focus", "blur", "appendChild",
                "remove", "insertAdjacentHTML", "createElement", "setAttribute", "getAttribute"}


def js_reads(pages_dir):
    """每个 `const V = await api.X(...)`：读键只算**同一作用域内、该变量最近一次赋值之后**的那些。

    这一条不是洁癖，是 §四十四 之前踩过的 **self 过度污染**（任务表 #60）：整份 `render()` 当一个窗口时，
    `const d = await api.users()` 会把后面 `const d = await api.saveConfig()` 的读键全串到 users 上，
    一把"看起来有 111 条嫌疑"的尺其实几乎全是串味。首跑就是这么红的，改完才可信。
    """
    all_rows, files = [], []
    for base, _dirs, names in os.walk(pages_dir):
        for n in sorted(names):
            if n.endswith(".js"):
                files.append(os.path.join(base, n))
    for path in files:
        text = open(path, encoding="utf-8").read()
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        sc = scopes(text)
        assigns = []   # (pos, var, meth, scope)
        for m in re.finditer(r"""(?:const|let|var)\s+(?P<var>[A-Za-z0-9_$]+)\s*=\s*await\s+api\.(?P<meth>[A-Za-z0-9_]+)\s*\(""", text):
            assigns.append((m.start(), m.group("var"), m.group("meth"), _scope_of(sc, m.start())))
        rows_by_id = {}
        for idx, (pos, var, meth, scope) in enumerate(assigns):
            rows_by_id[idx] = {"file": rel, "line": text[:pos].count("\n") + 1, "meth": meth,
                               "var": var, "keys": set(), "nested": set(), "scope": scope}
        # 读点：VAR.KEY / const {a,b} = VAR / VAR?.KEY
        for m in re.finditer(r"""(?P<var>[A-Za-z0-9_$]+)(?P<op>\?\.|\.)\s*(?!\d)(?P<k>[A-Za-z0-9_$]+)""", text):
            if m.group("k") in _NON_PAYLOAD:
                continue
            best = None
            for idx, (pos, var, _meth, scope) in enumerate(assigns):
                if var != m.group("var") or pos >= m.start():
                    continue
                # 只认同作用域（或该读点所在作用域的外层赋值）；内层箭头体读外层变量算合法
                s2 = _scope_of(sc, m.start())
                same = (scope == s2) or (scope[0] <= s2[0] and s2[1] <= scope[1]) or \
                       (s2[0] <= scope[0] and scope[1] <= s2[1])
                if not same:
                    continue
                if best is None or pos > best[0]:
                    best = (pos, idx)
            if best:
                rows_by_id[best[1]]["keys"].add(m.group("k"))
        for dm in re.finditer(r"""(?:const|let|var)\s*\{(?P<destr>[^{}]{0,400})\}\s*=\s*(?P<var>[A-Za-z0-9_$]+)\b""", text):
            best = None
            for idx, (pos, var, _meth, scope) in enumerate(assigns):
                if var != dm.group("var") or pos >= dm.start():
                    continue
                if best is None or pos > best[0]:
                    best = (pos, idx)
            if best:
                for part in dm.group("destr").split(","):
                    part = part.split(":")[0].split("=")[0].strip()
                    if re.fullmatch(r"[A-Za-z0-9_$]+", part) and part not in _NON_PAYLOAD:
                        rows_by_id[best[1]]["keys"].add(part)
        out = []
        for idx in sorted(rows_by_id):
            r = rows_by_id[idx]
            r["keys"] = sorted(k for k in r["keys"])
            out.append(r)
        # 列表元素第二层：VAR.arr.forEach(el => el.key)
        for im in re.finditer(r"""(?P<var>[A-Za-z0-9_$]+)\.(?P<arr>[A-Za-z0-9_$]+)\s*\.\s*"""
                              r"""(?:forEach|map|find|some|filter|reduce)\s*\(\s*\(?(?P<el>[A-Za-z0-9_$]+)\)?\s*=>""", text):
            best = None
            for idx, (pos, var, _meth, scope) in enumerate(assigns):
                if var != im.group("var") or pos >= im.start():
                    continue
                if best is None or pos > best[0]:
                    best = (pos, idx)
            if best:
                seg = text[im.start():im.start() + 1200]
                for ek in re.finditer(re.escape(im.group("el")) + r"""[.?](?!\d)(?P<k>[A-Za-z0-9_$]+)\b""", seg):
                    if ek.group("k") not in _NON_PAYLOAD:
                        rows_by_id[best[1]]["nested"].add(im.group("arr") + "." + ek.group("k"))
        for r in out:
            r["nested"] = sorted(r["nested"])
        all_rows.extend(out)
    return sorted(all_rows, key=lambda x: (x["file"], x["line"]))


def _dict_keys(node):
    """dict 字面量的顶层键（含 dict(...) 的 keyword 形）。"""
    if isinstance(node, ast.Dict):
        return [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return [kw.arg for kw in node.keywords if kw.arg]
    return []


def _all_dict_keys(node):
    out = []
    for sub in ast.walk(node):
        out.extend(_dict_keys(sub))
    return out


#: 信封三件（api.js 的 request() 统一处理，页面读它们不算读载荷键）
ENVELOPE = ("ok", "error", "message")

#: 不当 helper 追的名字：内建、标准库、信封件。首版没这张表，`foo.get(...)` 会命中
#: 全仓所有叫 `get` 的 def（`acp_server.py:86#get` 等），把无关键并进来＝假绿。
_NON_HELPER = {
    "ok", "error", "dict", "list", "set", "tuple", "len", "str", "int", "float", "bool", "bytes",
    "get", "keys", "values", "items", "sorted", "enumerate", "min", "max", "sum", "abs", "round",
    "isinstance", "issubclass", "print", "repr", "hash", "id", "type", "super", "open", "range",
    "zip", "map", "filter", "any", "all", "reversed", "getattr", "setattr", "hasattr", "delattr",
    "asyncio", "to_thread", "json", "loads", "dumps", "os", "sys", "time", "datetime", "uuid",
    "Route", "Query", "Body", "Path", "Request", "Response", "app", "runtime",
}


def _callee_chain(f):
    """调用名的限定段：`rt.signal_learning.list_rules(...)` → (`list_rules`, ['signal_learning'])。"""
    segs = []
    while isinstance(f, ast.Attribute):
        segs.append(f.attr)
        f = f.value
    root = f.id if isinstance(f, ast.Name) else None
    return root, list(reversed(segs))


def build_def_index(pkg_dir):
    """全仓 `def` 名 → [(file, lineno, 宿主类或 None, node, 是否嵌套 def)]；宿主用来过滤同名边。

    嵌套 def 也要收：`member_routes.py:259 def _fetch()` 这种**闭包型取数器**正是载荷的真正产地
    （`data = await asyncio.to_thread(_fetch)` → `return ok(data)`），只收顶层会永远解不动。
    代价是同名歧义变大，所以 `_resolve_by_name` **优先用非嵌套那条**，并按 def 节点去重防成环。
    """
    idx = {}
    for base, _dirs, names in os.walk(pkg_dir):
        if "__pycache__" in base:
            continue
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            p = os.path.join(base, n)
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except (SyntaxError, OSError):
                continue
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            top_ids = set()
            for holder in tree.body:
                if isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    top_ids.add(id(holder))
                    idx.setdefault(holder.name, []).append((rel, holder.lineno, None, holder, False))
                elif isinstance(holder, ast.ClassDef):
                    for node in holder.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            top_ids.add(id(node))
                            idx.setdefault(node.name, []).append((rel, node.lineno, holder.name, node, False))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in top_ids:
                    idx.setdefault(node.name, []).append((rel, node.lineno, None, node, True))
    return idx


def _accept_host(cand, chain_segs):
    """同名 helper 的宿主过滤：调用链上的段名（`store`、`signal_learning`）与宿主类名/文件名对得上才算这条边。"""
    if not chain_segs:
        return True
    _rel, _lineno, cls, _node, _nested = cand
    stem = os.path.basename(_rel)[:-3].lower()
    for seg in chain_segs:
        s = seg.lower()
        if cls and (s == cls.lower() or s in cls.lower() or cls.lower() in s):
            return True
        if s == stem or s in stem or stem in s:
            return True
    return False


#: 线程卸载壳：`await asyncio.to_thread(validate_all, rt, True)` —— 真 helper 是**第一个实参**，
#: 按名字传函数引用，不这样解就永远看不清（`template_validate_api` 首跑就卡在这）。
_THREAD_WRAPPERS = {"to_thread", "run_sync", "run", "gather"}
#: 失败信封：`return error("…")` / `err("…")`，顶层形状固定（`deps.py:38-44`，extra 才带明细）。
FAIL_ENVELOPES = {"error", "err"}
#: 鉴权早退的变量名：全仓统一写法 `_, err = require_user(request)` → `if err: return err`（返回的是 JSONResponse）。
FAIL_VARS = {"err", "error"}


def _resolve_by_name(nm, host_segs, idx, depth, visited, evidence):
    """按调用名查 def，宿主段过滤同名；返回 (顶层键并集, 交人判的标记)。`visited` 按 def 节点去重防成环。"""
    keys, needs_human = set(), set()
    cands = idx.get(nm, [])
    if not cands:
        return keys, needs_human | {nm}
    host_ok = [c for c in cands if _accept_host(c, host_segs)] or cands
    picked = [c for c in host_ok if not c[4]] or host_ok      # 优先非嵌套那条边
    tags = []
    for rel, lineno, cls, node, _nested in picked:
        tag = "%s:%d#%s%s%s" % (rel, lineno, (cls + ".") if cls else "", nm, "(嵌套)" if _nested else "")
        evidence.append(tag)
        tags.append(tag)
        if id(node) in visited or depth < 0:
            needs_human.add("已到深度 " + tag)
            continue
        sub, nh = def_return_keys(node, idx, depth - 1, visited | {id(node)}, evidence)
        keys.update(sub)
        needs_human |= nh
    if len(picked) > 1:
        needs_human.add("同名多义 " + ",".join(tags))
    return keys, needs_human


def resolve_payload(expr, idx, depth, visited, evidence, handler_node=None, before_line=None):
    """把一个 return 表达式解析成**顶层键集合**；解不动的登记进 evidence/`needs_human`，不猜。

    语义按真信封来（`api/deps.py:47-52`）：`ok(d)` 在 d 是 dict 时做 `{"ok": True, **d}`，
    所以 ok() 不改变顶层形状；`ok(非 dict)` 才包成 `{"data": ...}`。`{**x}` 同理是并顶层。
    `visited` 存的是 **def 节点的 id**，不是名字：首版按名字排除，于是 handler `health`
    永远解不开 `runtime(request).health()`（自己叫这名字，把唯一那条真边当环掐了）。
    """
    keys, needs_human = set(), set()
    if expr is None:
        return keys, needs_human
    v = expr
    while isinstance(v, ast.Await):
        v = v.value
    if isinstance(v, ast.Tuple):
        return {"data"}, needs_human
    if isinstance(v, ast.Dict):
        keys.update(_dict_keys(v))
        for k, val in zip(v.keys, v.values):
            if k is not None:
                continue
            sub, nh = resolve_payload(val if not isinstance(val, ast.Name)
                                      else _local_assign(val.id, handler_node, v.lineno),
                                      idx, depth, visited, evidence, handler_node, before_line)
            keys.update(sub)
            needs_human |= nh
        return keys, needs_human
    if isinstance(v, ast.Call):
        root, segs = _callee_chain(v.func)
        nm = segs[-1] if segs else root
        if nm is None:
            return keys, needs_human | {"<表达式调用>"}
        if nm in ("ok", "JSONResponse"):
            # `ok(d)`＝`{"ok": True, **d}`；`JSONResponse({...})` 的直接返回也按第一个实参当载荷
            # （`system_routes.py:110-116` 走的是这条，首版把 JSONResponse 当内建跳过 ⇒ 两条 SHAPE 假嫌疑）。
            return resolve_payload(v.args[0] if v.args else None, idx, depth,
                                   visited, evidence, handler_node, before_line)
        if nm in FAIL_ENVELOPES:
            return {"ok", "error"}, needs_human
        if nm in _THREAD_WRAPPERS and v.args:
            inner = v.args[0]
            if isinstance(inner, (ast.Name, ast.Attribute)):
                iroot, isegs = _callee_chain(inner)
                inm = isegs[-1] if isegs else iroot
                if inm:
                    return _resolve_by_name(inm, ([iroot] if iroot else []) + isegs[:-1],
                                            idx, depth, visited, evidence)
            return resolve_payload(inner, idx, depth, visited, evidence,
                                   handler_node, before_line)
        if nm in _NON_HELPER or depth < 0:
            return keys, needs_human | {nm}
        return _resolve_by_name(nm, ([root] if root else []) + segs[:-1], idx, depth, visited, evidence)
    if isinstance(v, ast.Name):
        if v.id in FAIL_VARS:          # `_, err = require_user(request)` 之后 `return err`：鉴权失败早退
            return {"ok", "error"}, needs_human
        target = _local_assign(v.id, handler_node, getattr(expr, "lineno", None))
        if target is None:
            return keys, needs_human | {"<追不到赋值 %s>" % v.id}
        return resolve_payload(target, idx, depth, visited, evidence, handler_node, before_line)
    return keys, needs_human | {("<非 dict 载荷>" if isinstance(v, (ast.List, ast.Constant)) else "<看不清>")}


def _local_assign(var, handler_node, before_line):
    """handler 体内 `var = <expr>`（取 return 之前最后一次赋值）的表达式；找不到给 None。"""
    best = None
    if handler_node is None:
        return None
    for a in ast.walk(handler_node):
        target = None
        if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == var for t in a.targets):
            target = a.value
        elif isinstance(a, (ast.AnnAssign, ast.AugAssign)) and isinstance(a.target, ast.Name) and a.target.id == var:
            target = a.value
        if target is None:
            continue
        if before_line is not None and a.lineno >= before_line:
            continue
        if best is None or a.lineno > best[0]:
            best = (a.lineno, target)
    return best[1] if best else None


def def_return_keys(node, idx, depth, visited, evidence):
    """一个 def 的所有 `return` 表达式顶层键的并集（多分支＝形状条件性，交人判）。"""
    keys, needs_human = set(), set()
    for r in [x for x in ast.walk(node) if isinstance(x, ast.Return)]:
        k, nh = resolve_payload(r.value, idx, depth, visited, evidence, node, r.lineno)
        keys.update(k)
        needs_human |= nh
    return keys, needs_human


def route_keysets(routes_dir, app_py, idx=None, depth=2):
    """路由模块：①`Route("/api/x", handler, methods=…)` 表；②handler 的 RESOLVED/LOOSE/UNKNOWN。

    RESOLVED = 按真信封语义把 return 表达式解到底得到的顶层键（`ok(helper(x))`、`{**result}` 都能解），
    每条解出的边都留 `helper=file:line#name` 证据，**同名多义单列**——同名函数在 core/legacy 两家都有时
    不带限定词的边会把两家的键并成一锅（§四十九 六），所以多义那格尺不自行判绿。
    LOOSE = handler 函数体任意深度 dict 字面量键的并集（宇宙，宽松判）。
    """
    handlers = {}   # fn name -> dict(resolved, loose, unknown, evidence, needs_human)
    routes = []     # (path, verb, handler name, module, lineno)
    for fn in sorted(os.listdir(routes_dir)) + [""]:
        path = os.path.join(routes_dir, fn) if fn else app_py
        if not os.path.isfile(path):
            continue
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        rel = os.path.relpath(path, os.path.dirname(routes_dir)).replace("\\", "/")
        defs = {n.name: n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Route":
                if not (n.args and isinstance(n.args[0], ast.Constant)):
                    continue
                p = n.args[0].value
                if not (isinstance(p, str) and p.startswith("/")):
                    continue
                target = None
                if len(n.args) > 1:
                    t = n.args[1]
                    target = t.id if isinstance(t, ast.Name) else (
                        t.func.id if isinstance(t, ast.Attribute) and isinstance(t.func, ast.Name) else None)
                verbs = ["GET"]
                for kw in n.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, ast.List):
                        verbs = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
                routes.append((p, ",".join(sorted(verbs)), target, rel, n.lineno))
        for name, node in defs.items():
            resolved, needs_human, evidence = set(), set(), []
            for r in [x for x in ast.walk(node) if isinstance(x, ast.Return)]:
                k, nh = resolve_payload(r.value, idx or {}, depth, {id(node)}, evidence, node, r.lineno)
                resolved.update(k)
                needs_human |= nh
            handlers[name] = {"resolved": resolved, "loose": set(_all_dict_keys(node)),
                              "unknown": set(needs_human), "evidence": evidence}
    return routes, handlers


def main():
    methods, unparsed = api_methods()
    idx = build_def_index(os.path.join(ROOT, "src", "memory_agent"))
    routes, handlers = route_keysets(os.path.join(ROOT, ROUTES_DIR),
                                     os.path.join(ROOT, APP_PY), idx)
    reads = js_reads(os.path.join(ROOT, PAGES_DIR))
    with_helper = sum(1 for h in handlers.values() if h["evidence"])
    print("FACES api_methods=%d unparsed=%d route_entries=%d handler_defs=%d js_callsites=%d" % (
        len(methods), len(unparsed), len(routes), len(handlers), len(reads)))
    print("FACES2 defs_indexed=%d handlers_with_helper_edge=%d" % (len(idx), with_helper))
    if "--dump-reads" in sys.argv[1:]:
        for r in reads:
            print("  %-14s %-3s %s:%s keys=%s nested=%s" % (
                r["meth"], r["var"], r["file"].split("/")[-1], r["line"],
                ",".join(r["keys"]), ",".join(r["nested"])))
        return 0
    # path → 宇宙合并（多条同 path 的并起来：多模块挂同一前缀）
    by_path = {}
    for p, verb, hname, rel, lineno in routes:
        e = by_path.setdefault(p, {"resolved": set(), "loose": set(), "unknown": set(),
                                   "mods": set(), "evidence": []})
        e["mods"].add("%s:%s" % (rel, lineno))
        h = handlers.get(hname)
        if h is None:
            e["mods"].add("%s:%s#NOHANDLER" % (rel, lineno))
            continue
        e["resolved"] |= h["resolved"]
        e["loose"] |= h["loose"]
        e["unknown"] |= h["unknown"]
        e["evidence"] += h["evidence"]
    hard, shape, green_by_helper, unknown_chain = [], [], [], []
    orphan_callsites, nested_miss, pairings = [], [], []
    for r in reads:
        meth = r["meth"]
        if meth not in methods:
            orphan_callsites.append(r)
            continue
        verb, paths = methods[meth]
        head, tail = paths[0], (paths[-1] if len(paths) > 1 else None)
        label = head if tail is None else head + "*" + tail
        cands = set()
        for p in paths:
            if p in by_path:
                cands.add(p)
        if not cands:
            for p in by_path:
                if not p.startswith(head):
                    continue
                if tail is None or tail in p:
                    cands.add(p)
        entry = {"resolved": set(), "loose": set(), "unknown": set(), "mods": set(), "evidence": []}
        for c in cands:
            for kk in ("resolved", "loose", "unknown", "mods"):
                entry[kk] |= by_path[c][kk]
            entry["evidence"] += by_path[c]["evidence"]
        if not cands:
            unknown_chain.append((meth, label, "路径在路由表里找不到"))
            continue
        pairings.append((meth, label, len(cands)))
        ev = ",".join(sorted(set(entry["evidence"]))[:3])
        if entry["unknown"]:
            unknown_chain.append((meth, label, "看不清=%s" % ",".join(sorted(entry["unknown"])[:4])))
        for k in r["keys"]:
            if k in ENVELOPE:                       # api.js 的信封三件，单独口径
                continue
            if k in entry["resolved"]:
                if k not in entry["loose"]:         # 只有走通 helper 边才绿 ⇒ 列出来给人抽查这条边
                    green_by_helper.append((meth, label, k, r["file"], r["line"], ev))
            elif k in entry["loose"]:
                shape.append((meth, label, k, r["file"], r["line"]))
            else:
                hard.append((meth, label, k, r["file"], r["line"], ",".join(sorted(entry["mods"]))[:60]))
        # 第二层（列表元素）：只按"该键在 handler 函数体里出现过吗"宽松判，够用且不误报形状
        for nk in r["nested"]:
            arr, _dot, key = nk.partition(".")
            if key not in ENVELOPE and key not in entry["loose"] and arr not in entry["loose"]:
                nested_miss.append((meth, label, nk, r["file"], r["line"]))
    print("CALLSITES_ORPHAN=%d PATH_UNMATCHED=%d CHAIN_UNKNOWN=%d" % (
        len(orphan_callsites), len([x for x in unknown_chain if x[2].startswith("路径")]), len(unknown_chain)))
    print("READKEY_HARD=%d SHAPE=%d NESTED_MISS=%d GREEN_BY_HELPER=%d" % (
        len(hard), len(shape), len(nested_miss), len(green_by_helper)))
    print("\n=== HARD（前端读键既不在返回顶层解析结果、也不在 handler 函数体宇宙 ⇒ 首要嫌疑，%d 条）===" % len(hard))
    for meth, path, k, f, line, mods in sorted(hard):
        print("  %s %s  key=%s  读点=%s:%s  路由=%s" % (meth, path, k, f, line, mods))
    print("\n=== GREEN_BY_HELPER（只靠 helper 边才判绿的键 ⇒ 逐条按 file:line 抽查，%d 条）===" % len(green_by_helper))
    for meth, path, k, f, line, ev in sorted(green_by_helper):
        print("  %s %s  key=%s  读点=%s:%s  helper=%s" % (meth, path, k, f, line, ev))
    print("\n=== SHAPE（函数体宇宙里有、返回顶层解析没有 ⇒ 嵌套/分支形状要人工判，%d 条）===" % len(shape))
    for meth, path, k, f, line in sorted(shape)[:60]:
        print("  %s %s  key=%s  读点=%s:%s" % (meth, path, k, f, line))
    print("\n=== NESTED_MISS（列表元素读键在 handler 宇宙里没有，%d 条）===" % len(nested_miss))
    for meth, path, nk, f, line in sorted(nested_miss):
        print("  %s %s  nested=%s  读点=%s:%s" % (meth, path, nk, f, line))
    print("\n=== CHAIN_UNKNOWN（载荷由 helper 造，本尺不追，%d 条）===" % len(unknown_chain))
    for meth, path, why in sorted(unknown_chain):
        print("  %s %s  %s" % (meth, path, why))
    multi = [p for p in pairings if p[2] > 1]
    print("\nPAIR_CALLSITES=%d PAIR_MULTI_ROUTE=%d（>1 条路由并进同一宇宙＝宽松判，只作候选）" % (
        len(pairings), len(multi)))
    for meth, label, n in sorted(multi)[:12]:
        print("  MULTI %s %s routes=%d" % (meth, label, n))
    if orphan_callsites:
        print("\n=== ORPHAN（页面调了 api.js 里没有的方法名，%d 条）===" % len(orphan_callsites))
        for r in orphan_callsites:
            print("  api.%s  读点=%s:%s" % (r["meth"], r["file"], r["line"]))
    return 0


def _capture():
    """跑一次 main()，按小节把输出切开（控制腿要的是"落在哪一格"，不是"这行里有没有这个词"）。"""
    buf = []

    class Cap:
        def write(self, s):
            buf.append(s)

    old = sys.stdout
    sys.stdout = Cap()
    try:
        main()
    finally:
        sys.stdout = old
    lines = "".join(buf).splitlines()
    sections, cur = {}, "<头部>"
    for l in lines:
        if l.startswith("==="):
            cur = l.split("（")[0].strip("= ")
            sections[cur] = []
            continue
        sections.setdefault(cur, []).append(l)
    return lines, sections


def selftest():
    """四条控制腿（尺自己也得被咬，§四十九 六）：

    L1 注入一条**已知不存在**的读键 ⇒ 必须落在 HARD；
    L2 真键（handler 直接返回的顶层 `trend`）⇒ 三条嫌疑格都不许出现——
       首版这条腿自己写错了口径：注入的是 `r.total_events`，而那键在**载荷的第二层**
       （`collect_stats` 返回 `{stats, trend, rooms}`，`total_events` 在 `stats` 里），
       于是"真键"落进嫌疑格，看着像尺误报、其实是腿选错了键。改注顶层键才算控制腿。
    L3 helper 边判绿的键（`sweep` 来自 `agent_memory.py:701`，handler 体内从没写过）⇒ 必须落在
       GREEN_BY_HELPER 而不是 HARD——这一条证明"新加的 helper 解析"真接着边，不是一把绿灯糊过去；
    L4 **把 helper 的家删掉**再跑一遍 ⇒ L3 那个键必须改判 HARD。尺不能"两边都响"，
       否则 L3 的绿是巧合不是解析。

    副本树开在 `.qoder/` 下面，不用 `tempfile`：本机仓库在 E: 而 `%TEMP%` 在 C:，
    跨盘时 `os.path.relpath` 直接 `ValueError: path is on mount 'E:', start on mount 'C:'`（首跑就撞）。
    """
    global ROOT
    real_root, ROOT = ROOT, None
    dst = os.path.join(real_root, ".qoder", "tmp-c77-ctl_run")
    shutil.rmtree(dst, ignore_errors=True)
    try:
        shutil.copytree(os.path.join(real_root, "src", "memory_agent"),
                        os.path.join(dst, "src", "memory_agent"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"))
        ROOT = dst
        page = os.path.join(dst, "src", "memory_agent", "static", "js", "pages", "dashboard.js")
        with open(page, encoding="utf-8") as fh:
            text = fh.read()
        probe = ("\n// __c77_ctl__\nasync function __c77_ctl(){const r=await api.collectStats();"
                 "const s=await api.agentMemorySweep();"
                 "return r.trend.length + r.__key_that_does_not_exist__ + s.sweep;}\n")
        with open(page, "w", encoding="utf-8", newline="") as fh:
            fh.write(text + probe)

        lines, sec = _capture()
        suspect_keys = set()
        for k in ("HARD", "SHAPE", "NESTED_MISS"):
            for l in sec.get(k, []):
                m = re.search(r"key=([A-Za-z0-9_]+)", l)
                if m:
                    suspect_keys.add(m.group(1))
        l1 = any("__key_that_does_not_exist__" in l for l in sec.get("HARD", []))
        l2 = "trend" not in suspect_keys          # `collect_stats` 返回顶层真有 `trend`
        l3_green = any("key=sweep" in l for l in sec.get("GREEN_BY_HELPER", []))
        l3_hard = any("key=sweep" in l for l in sec.get("HARD", []))

        os.remove(os.path.join(dst, "src", "memory_agent", "agent_memory.py"))
        _lines2, sec2 = _capture()
        l4 = any("key=sweep" in l for l in sec2.get("HARD", []))

        header = [l for l in lines if l.startswith("FACES")]
        print("SELFTEST_FACES %s" % (header[0] if header else "?"))
        print("SELFTEST_LEGS L1_fake_fires_in_HARD=%s L2_toplevel_key_silent=%s "
              "L3_helper_key_green_not_hard=%s L4_helper_home_removed_flips_to_HARD=%s" % (
                  l1, l2, (l3_green and not l3_hard), l4))
        bad = sum([not l1, not l2, not (l3_green and not l3_hard), not l4])
        print("SELFTEST_BAD=%d" % bad)
        print("SELFTEST_RC=%d" % (0 if bad == 0 else 1))
        return 0 if bad == 0 else 1
    finally:
        ROOT = real_root
        shutil.rmtree(dst, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else main())


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else main())
