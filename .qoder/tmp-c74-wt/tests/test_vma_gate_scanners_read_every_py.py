"""门禁量具读不动的文件 = 没被审的文件：全仓 `.py` 必须能按 UTF-8 原样喂给 `ast.parse`。

起因（本批现读）：`scripts/reindex_embeddings.py`、`tests/test_change_attribution.py`、
`tests/test_wo_ma_012_g1_security.py` 三个在册文件开头带 UTF-8 **BOM**。CPython 的导入器
按 `utf-8-sig` 嗅探，所以 pytest 一路绿；但任何 `open(p, encoding="utf-8")` + `ast.parse`
的量具（day-bounds 扫描、路由挂载扫描、变异 patcher）撞上就是
`SyntaxError: invalid non-printable character U+FEFF`——**这条异常被 except SyntaxError 吞掉时，
那个文件就从审计口径里静默消失了**，跟上一批「工作区 CRLF 让字节级 patcher 报 PATCH_NOT_FOUND，
被读成"这处语义改不动"」是同一族：量具自身故障被读成被测对象的结论。

三条锁：
1. 四个被门禁扫的根目录里 BOM 数 = 0；
2. 同一批文件按 `encoding="utf-8"`（**不给 sig 兜底**）逐个 `ast.parse` 全过；
3. 三个被剥掉 BOM 的在册文件不许长回来（逐名点名，防止有人"顺手改别处"顶包）。

行尾（CRLF/LF）本文件**故意不量**：HEAD 里的 blob 与工作区的行尾在多个在册文件上不一致
（`scripts/reindex_embeddings.py` 的 blob 基本全 CRLF、工作区只有行尾 1 个 CR），
这是既有状态、由交付纪律「改完按字节量 CR」「字节级量具先确认行尾」管，不在这里立红门。
"""

import ast
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BOM = b"\xef\xbb\xbf"
SCAN_ROOTS = ("src", "tests", "scripts", "attic")


def _py_files(root: str) -> list[str]:
    base = os.path.join(_ROOT, root)
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        hits.extend(os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith(".py"))
    return hits


def _rel(path: str) -> str:
    return os.path.relpath(path, _ROOT).replace("\\", "/")


def test_no_bom_in_scanned_roots():
    bad = [p for root in SCAN_ROOTS for p in _py_files(root)
           if open(p, "rb").read(3) == _BOM]
    assert bad == [], [_rel(p) for p in bad]


def test_every_scanned_py_parses_as_plain_utf8():
    """不许用 `utf-8-sig` 蒙过去：量具怎么写，这条就怎么写。"""
    problems = []
    for root in SCAN_ROOTS:
        for path in _py_files(root):
            try:
                with open(path, encoding="utf-8") as fh:
                    ast.parse(fh.read(), filename=path)
            except (SyntaxError, UnicodeDecodeError) as exc:
                problems.append(f"{_rel(path)}: {type(exc).__name__}: {exc}")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("rel", ("scripts/reindex_embeddings.py",
                                 "tests/test_change_attribution.py",
                                 "tests/test_wo_ma_012_g1_security.py"))
def test_the_scanned_population_actually_covers_the_former_bom_files(rel: str):
    """量具自证：点名三个"曾经带 BOM"的文件确实在扫描人口里，别用"扫到 0 个文件"蒙过前两条。"""
    rel_posix = rel.replace("\\", "/")
    pool = {_rel(p) for root in SCAN_ROOTS for p in _py_files(root)}
    assert rel_posix in pool, sorted(x for x in pool if x.endswith(".py"))[:3]
    assert len(pool) >= 300, len(pool)
    assert open(os.path.join(_ROOT, rel), "rb").read(3) != _BOM


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
