"""vMA-1.2.3 路线图 3.1.3（pyflakes 存量清零）的回归锁。

清掉 71 条台账不是终点，重点是别让它长回来。这四条各自盯住一处"修完就会复发"的结构：
1. `.gates/pyflakes-baseline.txt` 必须保持为空——往台账里加条目等于关掉门禁；
2. `insights` 包门面不得再用 star import，且 `__all__` 里每个名字都真实存在；
3. `Store` 不得再出现同名方法的第二份定义（曾有两份 add_bug_report/list_bug_reports）；
4. `identity` 不再中转 `entity_resolution` 的名字，消费方直接指向唯一来源。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import memory_agent

# 源码路径从"被 import 的那个包"反查，而不是按 tests/ 的相对深度猜：
# 容器里 tests/ 在 /tmp/tests、源码在 /app/src，两者不同根，按深度拼路径会扑空。
PKG_DIR = Path(memory_agent.__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[1] if PKG_DIR.parents[0].name == "src" else PKG_DIR.parents[0]
BASELINE = REPO_ROOT / ".gates" / "pyflakes-baseline.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_pyflakes_baseline_stays_empty():
    """基线为空 = 任何 pyflakes 输出都是新增。加回条目必须先修问题。"""
    if not BASELINE.exists():
        pytest.skip(f"非仓库根运行，取不到 {BASELINE}；由 CI 与本地口径负责拦")
    assert _read(BASELINE).strip() == "", (
        ".gates/pyflakes-baseline.txt 已归零，重新写入条目即放行新缺陷；"
        "请先修掉 pyflakes 报出的问题，再考虑是否真的要放行"
    )


def test_insights_facade_has_no_star_import_and_all_names_resolve():
    import memory_agent.insights as pkg

    tree = ast.parse(_read(Path(pkg.__file__).resolve()))
    stars = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
    ]
    assert not stars, "门面不得使用 star import：pyflakes 无法判定未定义名，消费面也无人知道"

    missing = [name for name in pkg.__all__ if not hasattr(pkg, name)]
    assert not missing, f"__all__ 声明了不存在的名字：{missing}"


@pytest.mark.parametrize("name", [
    "InsightService",
    "DEFAULT_DEBOUNCE_SECONDS",
    "fmt_duration",
    "_as_float",
    "_parse_attrs",
])
def test_insights_facade_keeps_its_actual_consumers(name):
    """仓内真实消费方（runtime.py / templates.py / insights_legacy.py）取名字的入口。"""
    import memory_agent.insights as pkg

    assert getattr(pkg, name, None) is not None
    assert name in pkg.__all__, f"{name} 被外部 import，就必须出现在 __all__ 里"


def test_store_has_no_duplicate_method_definitions():
    tree = ast.parse(_read(PKG_DIR / "store.py"))
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        seen: dict[str, int] = {}
        for body in cls.body:
            if isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen[body.name] = seen.get(body.name, 0) + 1
        dup = {k: v for k, v in seen.items() if v > 1}
        assert not dup, f"{cls.name} 里 {dup} 有重复定义：后一份会静默覆盖前一份"


def test_identity_no_longer_relays_entity_resolution_names():
    """相似度口径的唯一来源是 entity_resolution；identity 只做身份解析。"""
    from memory_agent import entity_resolution, identity

    for relayed in ("similarity", "_common_prefix_len", "PREFIX_MERGE_MIN"):
        assert not hasattr(identity, relayed), f"identity 又变成了 {relayed} 的中转站"
        assert hasattr(entity_resolution, relayed)

    from memory_agent import template_validate

    assert template_validate._identity_similarity is entity_resolution.similarity
