"""第六轮审计 CRITICAL-2 静态门：共享连接的使用必须落在 Store 的锁区内。

扫的是「生产实际导入的那棵树」（``memory_agent.__file__``），不是测试文件旁边的 src：
容器里 tests 被 docker cp 到 /tmp/tests，按 __file__/../src 定位会指向不存在的 /tmp/src，
静态半边会空跑（这个坑第五轮踩过一次）。

判定规则（宁可误报，不可漏报）：
* ``X.connect()``（X 是 ``self`` 或名字含 ``store``）交给某个变量 → 该变量持有共享连接；
  ``X.connect().execute(...)`` 这种就地调用同样算；
* 直接抓 ``store._conn`` 属性（含 ``getattr(store, "_conn", None)``）也算共享连接，
  而且更糟——Store 还没建连时它会静默拿到 None；
* 共享连接上的 execute / executescript / executemany / commit / rollback，必须位于
  同一函数内某个 ``with ..._lock`` / ``with ..._db()`` / ``with ...transaction()`` 块里。

``sqlite3.connect(...)`` 自己新建的短连接不在管辖范围（独立连接，关闭无害）。
"""

from __future__ import annotations

import ast
import os

import memory_agent  # noqa: E402  —— 定位生产实际导入的那棵树

_PKG = os.path.dirname(os.path.abspath(memory_agent.__file__))

_OPS = ("execute", "executescript", "executemany", "commit", "rollback")
_GUARD_HINTS = ("_lock", "_db", "transaction")
_CONNECTORS = ("connect",)


def _is_store_connect_call(call: ast.Call) -> bool:
    """``store.connect()`` / ``self.connect()``（不含 ``sqlite3.connect``）。"""
    f = call.func
    if not isinstance(f, ast.Attribute) or f.attr not in _CONNECTORS:
        return False
    owner = ast.unparse(f.value)
    return owner == "self" or "store" in owner


def _is_shared_conn_value(value: ast.expr) -> bool:
    """赋值右侧是不是「从 Store 拿共享连接」。"""
    if not isinstance(value, ast.Call):
        return False
    if _is_store_connect_call(value):
        return True
    # getattr(store, "_conn", None) / store._conn 任何形式的直接抓取
    return "_conn" in ast.unparse(value)


def _guarded_lines(fn) -> set[int]:
    """被「锁区」覆盖的行号（with 块内的一切，含守护表达式自身）。"""
    covered: set[int] = set()
    for st in ast.walk(fn):
        if not isinstance(st, (ast.With, ast.AsyncWith)):
            continue
        if not any(any(h in ast.unparse(item.context_expr) for h in _GUARD_HINTS)
                   for item in st.items):
            continue
        for sub in ast.walk(st):
            line = getattr(sub, "lineno", None)
            if line is not None:
                covered.add(line)
    return covered


def _unlocked_ops(fn) -> list[int]:
    conns: set[str] = set()
    for st in ast.walk(fn):
        if isinstance(st, ast.Assign) and _is_shared_conn_value(st.value):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    conns.add(t.id)
    if not conns:
        # 也可能是 self.store._conn.execute(...) 这种不落变量的直接抓取
        conns.add("_conn")

    covered = _guarded_lines(fn)
    bad: list[int] = []
    for st in ast.walk(fn):
        if not (isinstance(st, ast.Call) and isinstance(st.func, ast.Attribute)):
            continue
        if st.func.attr not in _OPS:
            continue
        base = st.func.value
        inline_connect = isinstance(base, ast.Call) and _is_store_connect_call(base)
        holder = ast.unparse(base)
        owned = holder in conns or holder.endswith("._conn")
        if (inline_connect or owned) and st.lineno not in covered:
            bad.append(st.lineno)
    return sorted(set(bad))


def scan_package(pkg_dir: str = _PKG) -> list[dict]:
    findings: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(pkg_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8-sig") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                bad = _unlocked_ops(node)
                if bad:
                    findings.append({
                        "file": os.path.relpath(path, pkg_dir),
                        "func": node.name,
                        "lines": bad,
                    })
    return sorted(findings, key=lambda f: (f["file"], f["func"]))


def test_shared_connection_is_never_used_outside_the_store_lock():
    """锁是 Store 唯一的并发保护；绕过它的直连 = 事务原子性无保证（审计 CRITICAL-2）。"""
    findings = scan_package()
    report = "\n".join(
        f"  {f['file']}::{f['func']} 未加锁的共享连接操作 @ {f['lines']}"
        for f in findings
    )
    assert not findings, (
        "以下位置在 Store 的锁区之外使用了共享连接（改用 store._db() / "
        f"store.transaction()）：\n{report}"
    )
