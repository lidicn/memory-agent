r"""#75 **只读**扫描：对外文案点名的键，载荷链上到底有没有。

#74 那一族（docstring 承诺 `has_data`/`first_day_with_data`，新引擎载荷里叫 `empty`/`start_day`）
是逐条撞出来的，不是扫出来的。这条把四个对外面一次扫全：

* `mcp_server.py` 里每个 `@mcp.tool()` handler 的 **docstring**（MCP 客户端看到的工具描述）；
* `tool_schema.py` 每条 ToolSpec 的 **summary / description / pitfall / example**（内置 LLM 的 function-calling 描述）；
* `skills_bundle/**/SKILL.md`、`static/js/pages/user_manual.js`（技能包与人读手册）。

**两档判据，宁少勿滥**：

* `PHANTOM`：这个名字在 `src/**.py` 里**根本不出现**（既不是任何标识符/属性，也不在任何字符串常量里）
  ——文案凭空许诺了一个源码中不存在的键；
* `ELSEWHERE`：它确实是某处的 dict 键（在 `KEYS` 全集里），但**不在这条工具的载荷链上**
  ——最像"键名漂移"（旧引擎叫 A、新引擎叫 B，文案还留着 A）。

`CHAIN`（本条链上真有）不计。噪声源头按 #72/#74 的教训先掐掉：工具名、该工具的入参名、
service/method 名一律不参与判定。链的解析 = handler 体里 `rt.<svc>.<method>` 调用，
取不到再回查同名 ToolSpec 的 `service/method`，然后把**该 method 名在全仓的静态可见键**并进来
（含它调用的 `_` 前缀同文件 helper，否则 `_with_window` 这类信封键会假红）。

只做差集报告，不改任何东西：

    python .qoder/tmp-c75-docscan.py            # 全部四张面
"""
import ast
import glob
import io
import os
import re

ROOT = os.environ.get("MA_DOCSCAN_ROOT") or os.path.join("src", "memory_agent")
MCP = os.path.join(ROOT, "mcp_server.py")
SPEC = os.path.join(ROOT, "tool_schema.py")
SKILLS = os.path.join(ROOT, "skills_bundle")
JS = os.path.join(ROOT, "static", "js")

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: 至少含一个下划线，且不以下划线开头（私有名不是对外承诺）
KEYISH = re.compile(r"(?<![A-Za-z0-9_])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![A-Za-z0-9_])")

#: 无下划线但确为载荷键名的一格，显式补，避免正则口径把它整族盖掉
LOOSE_KEYS = ("empty",)

SURFACE_FIELDS = ("summary", "description", "pitfall", "example")


def _read(path):
    return io.open(path, encoding="utf-8").read()


def _parse(path):
    return ast.parse(_read(path), filename=path)


def _src_files():
    out = []
    for path in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
        if "__pycache__" in path:
            continue
        out.append(path)
    return sorted(out)


def _dict_literal_keys(d, keys):
    for k in d.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.add(k.value)


def _collect_universe():
    """KEYS = 全仓 dict 键 / 下标写入 / .get|.pop|.setdefault 字面量首参；
    ALL = KEYS ∪ 全部标识符（含属性、参数名）∪ 全部字符串常量里的单词。

    KEYS 同时留**位置**（`file:line`）：ELSEWHERE 一条不带位置就没法判——
    "这个键只活在 `insights_legacy.py`、而该工具已切新引擎"才是 #74 那一族，
    "这键在别处也造、只是静态走不到"多半是逐行嵌套键，得换成运行时读。
    """
    key_sites, everything = {}, set()

    def site(tok, node, path):
        key_sites.setdefault(tok, []).append("%s:%s" % (os.path.basename(path), node.lineno))

    for path in _src_files():
        try:
            tree = _parse(path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        site(k.value, node, path)
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript):
                s = node.targets[0].slice
                s = s.value if isinstance(s, ast.Constant) else None
                if isinstance(s, str):
                    site(s, node, path)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("get", "pop", "setdefault") and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                site(node.args[0].value, node, path)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                everything.update(IDENT.findall(node.value))
            elif isinstance(node, ast.Name):
                everything.add(node.id)
            elif isinstance(node, ast.Attribute):
                everything.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                everything.add(node.name)
            elif isinstance(node, ast.arg):
                everything.add(node.arg)
    return key_sites, everything | set(key_sites)


def _funcs(tree):
    """按名字收全仓（同一文件内）函数节点，名字撞车就并到同一条目。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)
    return out


def _static_keys(fn):
    """函数体里能静态看到的顶层键（沿用 #72 的口径：字面量返回、按名跟踪、下标写入、update）。"""
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            _dict_literal_keys(node, keys)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript):
            s = node.targets[0].slice
            s = s.value if isinstance(s, ast.Constant) else None
            if isinstance(s, str):
                keys.add(s)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("update", "setdefault") and node.args:
            for a in node.args:
                if isinstance(a, ast.Dict):
                    _dict_literal_keys(a, keys)
    return keys


def _calls(fn):
    """该体里的调用边：**(限定, 被调名)**。限定决定下一跳允许落在哪个文件。

    控制腿量出的盲区：按名字全局并键会把 legacy 的同名方法并进来——`coverage` 在
    `insights/service.py` 与 `insights_legacy.py` 里各有一个，于是"只有 legacy 才有的
    `has_data`/`first_day_with_data`"在门面链上看起来是真的，往 docstring 注入旧文案
    **一条都不响**。边不带限定，这一族就扫不出来。
    """
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        segs, cur = [], node.func
        while isinstance(cur, ast.Attribute):
            segs.append(cur.attr)
            cur = cur.value
        root = cur.id if isinstance(cur, ast.Name) else None
        if not segs:
            continue
        name = segs[0]
        if root == "self" and len(segs) >= 2:
            mid = segs[1]
            out.append(("core" if mid == "core" else
                        "legacy" if mid == "legacy" else ("stem", mid), name))
        elif root == "rt" and len(segs) >= 2:
            out.append((("stem", segs[-2]) if len(segs) == 2 else ("local",), name))
        else:
            out.append(("local", name))
    return out


class Chain:
    """载荷链：从 (service, method) 出发按限定边行走，最多三跳，把静态可见键并进来。"""

    MAX_DEPTH = 3

    def __init__(self):
        self.by_name = {}

    def add(self, path):
        for name, nodes in _funcs(_parse(path)).items():
            for node in nodes:
                self.by_name.setdefault(name, []).append((path, node))

    def _resolve(self, hint, name, caller_path):
        cand = self.by_name.get(name, [])
        if hint == "core":
            return [c for c in cand if os.path.basename(c[0]) == "service.py"]
        if hint == "legacy":
            return [c for c in cand if os.path.basename(c[0]) == "insights_legacy.py"]
        if isinstance(hint, tuple) and hint[0] == "stem":
            want = "insights/api.py" if hint[1] == "insights" else None
            if want:
                return [c for c in cand if c[0].replace("\\", "/").endswith(want)]
            return [c for c in cand
                    if os.path.splitext(os.path.basename(c[0]))[0] == hint[1]]
        if caller_path is None:
            return list(cand)
        return [c for c in cand if c[0] == caller_path]

    def keys(self, svc, method):
        if not method:
            return set()
        frontier = self._resolve(("stem", svc), method, None) if svc \
            else list(self.by_name.get(method, []))
        got, seen = set(), set()
        for _ in range(self.MAX_DEPTH):
            if not frontier:
                break
            nxt = []
            for path, fn in frontier:
                if id(fn) in seen:
                    continue
                seen.add(id(fn))
                got |= _static_keys(fn)
                for hint, name in _calls(fn):
                    nxt.extend(self._resolve(hint, name, path))
            frontier = nxt
        return got


def _rt_calls(fn):
    """handler 体里的 `rt.<service>.<method>` 调用，返回 (service, method) 列表。"""
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        path, cur = [], node.func
        while isinstance(cur, ast.Attribute):
            path.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id == "rt" and len(path) >= 2:
            # path[0] 是被调名（最内层属性），path[-2] 才是 service——写反了会把
            # `rt._db.store.query_unified_events` 读成 svc=_db/method=store，整条链落空。
            out.append((path[-2], path[0]))
    return out


def _is_tool_decorator(fn):
    for d in fn.decorator_list:
        names = []
        cur = d.func if isinstance(d, ast.Call) else d
        while isinstance(cur, ast.Attribute):
            names.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            names.append(cur.id)
        if "tool" in names:
            return True
    return False


def _spec_entries():
    """ToolSpec(...) 调用 → {name, service, method, params, 文案字段, 行号}。"""
    tree = _parse(SPEC)
    entries = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ToolSpec":
            row = {"line": node.lineno, "params": set()}
            for kw in node.keywords:
                if kw.arg in SURFACE_FIELDS or kw.arg in ("name", "service", "method"):
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        row[kw.arg] = kw.value.value
                elif kw.arg == "params":
                    for el in getattr(kw.value, "elts", []):
                        if isinstance(el, ast.Call):
                            for sub in el.args[:1]:
                                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                                    row["params"].add(sub.value)
            entries.append(row)
    return entries


def _js_universe():
    """JS 面的自有词表（路由名、局部变量、示例里的实体名）。
    不并进来会把 JS 内部名字判成"全仓无此名"，那是假红——Python 宇宙本来就不含它们。"""
    got = set()
    for path in glob.glob(os.path.join(ROOT, "static", "**", "*.js"), recursive=True):
        got.update(IDENT.findall(_read(path)))
    return got


def _tokens(text):
    got = set(KEYISH.findall(text or ""))
    for loose in LOOSE_KEYS:
        if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % loose, text or ""):
            got.add(loose)
    return got


def main():
    key_sites, everything = _collect_universe()
    keys = set(key_sites)
    chain = Chain()
    for path in _src_files():
        try:
            chain.add(path)
        except SyntaxError:
            pass
    specs = _spec_entries()
    spec_by_name = {}
    for e in specs:
        spec_by_name.setdefault(e.get("name", ""), e)
    tool_names = set(spec_by_name)
    phantom, elsewhere = [], []
    handler_keys = {}

    def record(surface, line, tok, svc, method, own=frozenset()):
        text = "%s:%s  token=%s  链=%s.%s" % (surface, line, tok, svc or "-", method or "-")
        if tok not in everything:
            phantom.append(text)
        elif tok not in keys:
            pass                       # 源码里有这个词，但它从来不是键 ⇒ 不参与判定
        else:
            chain_keys = set(own)
            if method:
                chain_keys |= chain.keys(svc, method)
            if tok not in chain_keys:
                sites = key_sites[tok]
                legacy_only = all(s.startswith("insights_legacy.py") for s in sites)
                elsewhere.append("%s  键位置=%s%s" % (
                    text, ",".join(sites[:4]) + ("…" if len(sites) > 4 else ""),
                    "  【只在 legacy】" if legacy_only else ""))

    # 面 1：mcp_server.py handler docstring
    # 量具盲区自纠：handler **不在模块顶层**（顶层 def 只有 29 个，全树 def 124 个），
    # 只遍历 tree.body 会得到 handlers=0——#74 那一族就一条都扫不到，且是静默的零。
    mcp_tree = _parse(MCP)
    handlers = 0
    for node in ast.walk(mcp_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_tool_decorator(node):
            handlers += 1
            handler_keys[node.name] = _static_keys(node)
            doc = ast.get_docstring(node) or ""
            calls = _rt_calls(node)
            if calls:
                svc, method = calls[0]
            else:
                e = spec_by_name.get(node.name, {})
                svc, method = e.get("service", ""), e.get("method", "")
            ok = {a.arg for a in node.args.args} | spec_by_name.get(node.name, {}).get("params", set())
            for tok in sorted(_tokens(doc) - ok - tool_names):
                record("docstring:" + node.name, node.lineno, tok, svc, method,
                       handler_keys[node.name])

    # 面 2：tool_schema.py 文案四字段
    for e in specs:
        ok = set(e.get("params", set())) | tool_names
        # service=static 的工具没有后端方法：实现就在 mcp_server 那个 handler 体里，
        # 链必须把 handler 自己的键并进来，否则整族 `链=static.-` 全是假红。
        own = handler_keys.get(e.get("name", ""), frozenset())
        for f in SURFACE_FIELDS:
            for tok in sorted(_tokens(e.get(f, "")) - ok):
                record("ToolSpec.%s:%s" % (f, e.get("name", "?")), e["line"], tok,
                       e.get("service", ""), e.get("method", ""), own)

    # 面 3/4：随包文案（SKILL.md / 用户手册 JS）——没有逐工具链，只对全仓键集判存在性
    js_words = _js_universe()
    for path in sorted(glob.glob(os.path.join(SKILLS, "**", "*.md"), recursive=True) +
                       glob.glob(os.path.join(JS, "**", "*.js"), recursive=True)):
        surface = "SKILL.md" if path.endswith(".md") else "manual_js"
        text = _read(path)
        for lineno, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
            for tok in sorted(_tokens(line) - tool_names - js_words):
                if tok not in everything:
                    phantom.append("%s %s:%s  token=%s  链=-" % (surface, path, lineno, tok))

    print("SURFACES handlers=%d specs=%d  universe keys=%d tokens=%d" % (
        handlers, len(specs), len(keys), len(everything)))
    print("\n=== PHANTOM（源码里根本不存在的名字，%d 条）===" % len(phantom))
    for row in phantom:
        print("  " + row)
    print("\n=== PRESENT_ELSEWHERE（是全仓的键，但不在这条工具的链上，%d 条）===" % len(elsewhere))
    for row in elsewhere:
        print("  " + row)
    print("\nPHANTOM=%d ELSEWHERE=%d" % (len(phantom), len(elsewhere)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
