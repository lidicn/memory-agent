"""元宝第九轮 P2-8：`learning_*` 八模块「不可达 + 不可导入」的现状锁。

P2-8 的原始证据（第九轮 :30 起）：1524 行 / 722 语句、`build_router` 全仓无调用方、
家族内 18 处**裸绝对导入**（`from learning_models import …`）⇒ 以包方式加载立刻
`ModuleNotFoundError: No module named 'learning_models'`。作者的结论是二选一：改包内导入并接线，
或者删。DCD 20261005 §二.2 Q2 走乙 = 既不接线也不删，**移出 `src/` 存进 `attic/learning/`**。

所以这条 P2 的"核销口径"不是"缺陷被修好了"，而是"它不再出现在被审计的在册人口里，
且搬家不是消失"。本文件把两件事都钉住，并把第九轮那条**只有静态结论**的"不可导入"
换成运行时读数：

1. `src/memory_agent/learning_*.py` = 0，全仓（src + tests）没有任何 `learning_*` 导入语句；
2. `attic/learning/` 仍是 8 个模块 + 1 份 README（数量守恒：搬走的没被顺手删）；
3. `import memory_agent.learning_*` 八个名字**逐个**抛 ModuleNotFoundError（运行时读数）；
4. attic 里那 18 处裸绝对导入**原样保留**——这是"接回必须先改包内导入"的在册证据，
   不许有人把 attic 顺手改成相对导入后宣称"learning 已经能用了"。
"""

import ast
import glob
import importlib
import importlib.util
import os
import re
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC_PKG = os.path.join(_ROOT, "src", "memory_agent")
_ATTIC = os.path.join(_ROOT, "attic", "learning")

_MODULES = ("learning_api", "learning_analyzer", "learning_evaluator", "learning_feedback",
            "learning_models", "learning_optimizer", "learning_report", "learning_store")

_IMPORT_STMT = re.compile(r"^[ \t]*(from|import)[ \t]+(memory_agent\.)?learning_", re.M)


def _scan_py_files(root: str) -> list[str]:
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git"}]
        hits.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".py"))
    return hits


# ── 1. src 侧在册人口归零 ──────────────────────────────────────────────────
def test_no_learning_modules_left_in_src():
    assert glob.glob(os.path.join(_SRC_PKG, "learning_*.py")) == [], (
        "src 里又长出 learning_* ⇒ P2-8 的「不可达块」回来了，且门禁不扫它")
    for path in _scan_py_files(_SRC_PKG) + _scan_py_files(os.path.join(_ROOT, "tests")):
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        lines = [ln for ln in body.splitlines() if _IMPORT_STMT.match(ln)]
        assert not lines, f"{os.path.relpath(path, _ROOT)} 仍导入 learning_*：{lines}"


# ── 2. 搬家不是消失 ────────────────────────────────────────────────────────
def test_attic_still_holds_all_eight_plus_the_note():
    if not os.path.isdir(_ATTIC):
        pytest.skip("attic/learning 不在场（精简检出）")
    found = sorted(os.path.basename(p)[:-3]
                   for p in glob.glob(os.path.join(_ATTIC, "learning_*.py")))
    assert found == sorted(_MODULES), found
    assert os.path.isfile(os.path.join(_ATTIC, "README.md")), "搬家必须带口径说明"


# ── 3. 运行时读数：八个名字全都导入不了 ───────────────────────────────────
@pytest.mark.parametrize("mod", _MODULES)
def test_memory_agent_learning_namespace_is_unimportable(mod):
    """第九轮的「不可导入」当时只有静态结论；这一条是它在当前解释器里的实测。

    移出 src 之前抛的是 `No module named 'learning_models'`（模块存在但加载即炸），
    现在抛的是 `No module named 'memory_agent.learning_api'`（包命名空间里根本没有）。
    两种都是 ModuleNotFoundError，但**含义不同**——别把「改不了」读成「修好了」。
    """
    sys.modules.pop(f"memory_agent.{mod}", None)
    with pytest.raises(ModuleNotFoundError) as got:
        importlib.import_module(f"memory_agent.{mod}")
    assert "memory_agent." in str(got.value), got.value


# ── 4. 根因仍在 attic 里在册 ──────────────────────────────────────────────
def test_attic_still_uses_bare_absolute_imports():
    """18 处裸绝对导入 = 第九轮点名的数；接回时先改包内导入，这里是那笔账的现状。"""
    if not os.path.isdir(_ATTIC):
        pytest.skip("attic/learning 不在场（精简检出）")
    total = 0
    per_file: dict[str, int] = {}
    for name in _MODULES:
        with open(os.path.join(_ATTIC, f"{name}.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=name)
        n = sum(1 for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level == 0
                and (node.module or "").split(".")[0] in _MODULES)
        per_file[name] = n
        total += n
    assert total == 18, per_file
    assert per_file["learning_api"] == 7 and per_file["learning_models"] == 0, per_file


def test_attic_module_still_crashes_on_package_style_load():
    """把「即便接线也立即崩溃」跑一遍真的：以包限定名加载 attic 里那份，仍须是 ModuleNotFoundError。

    取 `learning_analyzer`（纯 stdlib，不牵连 fastapi）：它顶层 `from learning_models import …`，
    而 `attic/learning` 不在任何 import 路径上 ⇒ 崩在第九轮指名的同一格。
    """
    path = os.path.join(_ATTIC, "learning_analyzer.py")
    if not os.path.isfile(path):
        pytest.skip("attic/learning 不在场（精简检出）")
    spec = importlib.util.spec_from_file_location("memory_agent.learning_analyzer", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    with pytest.raises(ModuleNotFoundError) as got:
        spec.loader.exec_module(mod)
    assert "learning_models" in str(got.value), got.value


# ── 5. 唯一入口依然无人调用 ───────────────────────────────────────────────
def test_build_router_still_has_no_caller_outside_attic():
    """第九轮「全项目检索 build_router 无任何调用方」——搬家之后这条必须仍然成立。

    按 AST 找调用点而不是按子串：本文件自己的说明文字里就写着 `build_router`。
    """
    callers = []
    for root in (_SRC_PKG, os.path.join(_ROOT, "tests")):
        for path in _scan_py_files(root):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    if name == "build_router":
                        callers.append(f"{os.path.relpath(path, _ROOT)}:{node.lineno}")
    assert callers == [], f"learning_api 又有人接了：{callers}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
