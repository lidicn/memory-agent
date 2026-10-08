from memory_agent import algo_eval
from memory_agent.identity_splink_eval import difflib_baseline, evaluate_splink


def test_pm4py_missing_graceful():
    # 环境未装 pm4py 时应优雅返回缺失标记，不抛异常
    res = algo_eval.eval_pm4py([["a", "b"], ["a", "c"]])
    assert res["ok"] is False
    assert "error" in res


def test_river_missing_graceful():
    res = algo_eval.eval_river_baseline([1.0, 2.0, 3.0])
    assert res["ok"] is False
    assert "error" in res


def test_splink_missing_graceful():
    res = evaluate_splink([{"name": "爸爸"}, {"name": "爷爷"}])
    assert res["ok"] is False
    assert "error" in res


def test_difflib_baseline_matches():
    # 现有启发式基线应能在相近名字间配对
    pairs = difflib_baseline(["爸爸"], ["爸爸", "妈妈"])
    assert ("爸爸", "爸爸") in pairs
