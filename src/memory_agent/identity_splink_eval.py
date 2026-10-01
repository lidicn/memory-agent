"""P2 spike 评估：用 Splink（Fellegi-Sunter）概率实体解析替代 identity.py 的 difflib 启发式。

``evaluate_splink`` 为懒加载骨架：未安装时返回缺失标记，不阻断主流程。
``difflib_baseline`` 复刻现有启发式，用于与 Splink 输出做对照（谁更接近人工标注）。
"""
from __future__ import annotations


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


def compare_resolution(records: list[dict], match_threshold: float = 0.95,
                       review_threshold: float = 0.5, max_diff: int = 20) -> dict:
    """对照评估：内置 Fellegi-Sunter vs 原 difflib 硬阈值（P2 升级前后差异）。

    ``records`` 需含 ``entity_id / name / domain / room``。按 ``(room, domain)``
    分块后块内两两比较（与 ``IdentityReconciler._cluster`` 同口径）。

    返回两法各自的判定计数与**差异清单**——差异主要来自：旧法把"不像"的配对
    一律判否（丢失"有点像、值得看一眼"的信息），新法把这类配对标为 ``review``。
    """
    from .entity_resolution import AGREE, ProbabilisticMatcher, name_level

    matcher = ProbabilisticMatcher(match_threshold=match_threshold,
                                   review_threshold=review_threshold)
    blocks: dict[tuple, list[dict]] = {}
    for r in records or []:
        if not isinstance(r, dict):
            continue
        key = (r.get("room") or "", r.get("domain") or "")
        blocks.setdefault(key, []).append(r)

    fs: dict[str, int] = {"match": 0, "review": 0, "non": 0}
    base = {"match": 0, "non": 0}
    diffs: list[dict] = []
    for rows in blocks.values():
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                res = matcher.compare(a, b)
                fs[res["band"]] += 1
                # 旧启发式基线：与 FS 完全同口径（含公共前缀规则），否则对比不公平
                old = "match" if name_level(a.get("name") or "", b.get("name") or "") == AGREE else "non"
                base[old] += 1
                if res["band"] != old and len(diffs) < max_diff:
                    diffs.append({
                        "a": a.get("entity_id"), "b": b.get("entity_id"),
                        "name_a": a.get("name"), "name_b": b.get("name"),
                        "room": a.get("room"),
                        "fs": res["band"], "fs_probability": res["probability"],
                        "difflib": old,
                    })
    return {
        "ok": True,
        "engine": "fellegi-sunter(pure-python, splink optional)",
        "records": len(records or []),
        "pairs": sum(fs.values()),
        "fellegi_sunter": fs,
        "difflib": base,
        "diff_count": len(diffs),
        "diffs": diffs,
    }
