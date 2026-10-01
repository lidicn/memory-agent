"""P1 spike 评估入口：pm4py（过程挖掘）/ river（在线异常与概念漂移）。

均为**懒加载**：未安装时调用返回 ``{"ok": False, "error": "missing_lib"}``，不阻断主流程。
装好依赖后可在 ``fit``/``update`` 内补真实逻辑，接口保持不变。
"""
from __future__ import annotations


def eval_pm4py(sequences: list[list[str]]) -> dict:
    """用 pm4py 从事件序列挖掘过程模型 + 一致性检验（异常=偏离过程）。

    未安装时返回缺失标记；安装后在此填充 ``pm4py.convert_to_event_log`` /
    ``pm4py.discover_process_tree_inductive`` / ``conformance`` 逻辑。
    """
    try:
        import pm4py  # noqa: F401
    except Exception:
        return {"ok": False, "error": "pm4py_not_installed"}
    # TODO(P1): 接入真实过程挖掘与一致性检验
    return {"ok": False, "error": "not_implemented", "received": len(sequences)}


def eval_river_baseline(stream_stats: list[float]) -> dict:
    """用 river 做行为指标的在线异常检测（Half-Space Trees）。

    未安装时返回缺失标记；安装后在此填充 ``river.anomaly.HalfSpaceTrees`` 的
    ``learn_one`` / ``score_one`` 逻辑。
    """
    try:
        import river  # noqa: F401
    except Exception:
        return {"ok": False, "error": "river_not_installed"}
    # TODO(P1): 接入真实在线异常检测
    return {"ok": False, "error": "not_implemented", "received": len(stream_stats)}
