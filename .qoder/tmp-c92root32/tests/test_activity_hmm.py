from memory_agent.activity_hmm import HMMActivityInferrer


def _train():
    return HMMActivityInferrer().fit([
        {"tags": ["door_open", "computer_on"], "label": "working"},
        {"tags": ["door_open", "computer_on", "typing"], "label": "working"},
        {"tags": ["no_motion"], "label": "idle"},
        {"tags": ["no_motion", "sofa"], "label": "idle"},
    ])


def test_hmm_infers_working():
    inf = _train()
    res = inf.infer(["door_open", "computer_on"])
    assert res is not None
    assert res["activity"] == "working"
    assert res["confidence"] > 0.5


def test_hmm_infers_idle():
    inf = _train()
    res = inf.infer(["no_motion"])
    assert res is not None
    assert res["activity"] == "idle"


def test_hmm_untrained_returns_none():
    inf = HMMActivityInferrer()
    assert inf.infer(["x"]) is None


def test_hmm_backend_name():
    # 未安装 hmmlearn 时回退 reference；安装后应为 hmmlearn，二者皆合法
    assert HMMActivityInferrer()._backend() in ("reference", "hmmlearn")
