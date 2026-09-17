"""P2 spike 评估：用 Splink（Fellegi-Sunter）概率实体解析替代 identity.py 的 difflib 启发式。

``evaluate_splink`` 为懒加载骨架：未安装时返回缺失标记，不阻断主流程。
``difflib_baseline`` 复刻现有启发式，用于与 Splink 输出做对照（谁更接近人工标注）。
"""
from __future__ import annotations

from typing import Any


def evaluate_splink(records: list[dict]) -> dict:
    """用 Splink 做概率记录链接，输出每个实体的匹配概率 + 不确定性。

    未安装时返回缺失标记；安装后在此填充 ``splink.Splink`` 的
    ``compare`` 配置与 ``predict`` 调用（替换 identity.py 的 difflib 近似）。
    """
    try:
        import splink  # noqa: F401
    except Exception:
        return {"ok": False, "error": "splink_not_installed"}
    # TODO(P2): 接入真实概率实体解析
    return {"ok": False, "error": "not_implemented", "received": len(records)}


def difflib_baseline(names_a: list[str], names_b: list[str], cutoff: float = 0.6) -> list[tuple[str, str]]:
    """现有启发式基线（供对照）：返回相似度最高的配对。"""
    import difflib

    pairs: list[tuple[str, str]] = []
    for a in names_a:
        best = difflib.get_close_matches(a, names_b, n=1, cutoff=cutoff)
        if best:
            pairs.append((a, best[0]))
    return pairs
