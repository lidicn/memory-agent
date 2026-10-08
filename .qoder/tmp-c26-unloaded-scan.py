"""A3 §八「少量未卸载的同步 I/O」的现读量具：在 async 函数作用域里找**直接**同步 DB/阻塞调用。

口径：
- 只判 src/memory_agent 下的 `async def` 作用域；
- 命中条件 = 调用形如 store.db_query / store.execute / self.store.* / *.db_query /
  requests.* / time.sleep / sqlite3.* ，且**该调用表达式里没有** to_thread / run_in_executor 包裹；
- 输出「文件:行号  调用文本」，人再看是不是真在同一线程里阻塞事件循环。
不读库、不写任何东西。
"""
import ast
import pathlib
import sys

BLOCKING_ATTRS = {"db_query", "db_execute", "execute", "executescript", "commit",
                  "query", "sleep", "get", "post"}
BLOCKING_ROOTS = {"requests", "time", "sqlite3", "shutil", "os"}
OFFLOAD_NAMES = {"to_thread", "run_in_executor", "to_thread", }


def _calls(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def _root_and_attr(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        attr = f.attr
        cur = f.value
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        if isinstance(cur, ast.Name):
            return cur.id, attr
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
            return cur.func.id, attr
        return "?", attr
    if isinstance(f, ast.Name):
        return f.id, f.id
    return None, None


def _looks_blocking(call):
    root, attr = _root_and_attr(call)
    if attr is None:
        return False
    if root in BLOCKING_ROOTS and attr in BLOCKING_ATTRS:
        return True
    if attr in ("db_query", "db_execute", "executescript", "query_rows"):
        return True
    if root in ("store", "self") and attr in ("execute", "commit"):
        return True
    return False


def _wrapped_in_offload(src_segment):
    return any(n in src_segment for n in ("to_thread", "run_in_executor"))


def main(root):
    hits = []
    for p in sorted(pathlib.Path(root).rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef,)):
                continue
            seg = "\n".join(lines[node.lineno - 1: node.end_lineno])
            if _wrapped_in_offload(seg):
                # 同一函数里既有卸载也有直调：仍需逐调用判断，不能整段放过
                pass
            for call in _calls(node):
                if not _looks_blocking(call):
                    continue
                # 该调用是否处在某个 to_thread(...) 的实参里
                inside_offload = False
                for outer in _calls(node):
                    o_root, o_attr = _root_and_attr(outer)
                    if o_attr not in OFFLOAD_NAMES:
                        continue
                    if outer.lineno <= call.lineno <= (outer.end_lineno or outer.lineno):
                        inside_offload = True
                        break
                if inside_offload:
                    continue
                hits.append("%s:%d %s" % (p, call.lineno,
                                          lines[call.lineno - 1].strip()[:90]))
    print("HITS=%d" % len(hits))
    for h in hits:
        print(h)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "src/memory_agent"))
