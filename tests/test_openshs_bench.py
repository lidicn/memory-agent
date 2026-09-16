"""OpenSHS 基准适配器 / 转换器的单元测试 + 轻量端到端。

这些测试只依赖 ``memory_agent.store`` / ``memory_agent.insights``（纯 sqlite3，
无 chroma/LLM 依赖），可在本机直接跑：

    PYTHONPATH=src python -m pytest tests/test_openshs_bench.py -q
"""

import os

import pytest

from benchmarks.openshs_convert import (
    ground_truth,
    start_end,
    wide_to_events,
)
from benchmarks.openshs_schema import (
    ACTIVITY_MAP,
    EVALUATED_ACTIVITIES,
    OPENSHS_COLUMNS,
    SENSOR_MAP,
)
from benchmarks.gen_sample import generate as generate_sample
from benchmarks.openshs_bench import run_benchmark

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "..", "benchmarks", "data", "openshs_sample.csv")


@pytest.fixture(scope="session", autouse=True)
def ensure_sample():
    """保证合成回归样本存在（全新 clone 也能直接 pytest）。

    ``openshs_sample.csv`` 是约 9 MB / 11 万行的**生成物**，不纳入版本控制
    （``.gitignore`` 的 ``data/`` 规则会忽略整个 benchmarks/data/）。
    样本缺失时用 ``gen_sample.generate`` 按需重建；其输出是确定性的
    （固定 SEGMENTS + base_date=2016-04-01），故下面的完美基线断言依然成立。
    """
    if not os.path.exists(SAMPLE):
        os.makedirs(os.path.dirname(SAMPLE), exist_ok=True)
        generate_sample(SAMPLE)
    return SAMPLE


def test_sensor_map_covers_all_columns():
    # 29 个 OpenSHS 传感器列必须全部映射（不含 Activity / timestamp）。
    sensor_cols = [c for c in OPENSHS_COLUMNS if c not in ("Activity", "timestamp")]
    assert len(sensor_cols) == 29
    for col in sensor_cols:
        assert col in SENSOR_MAP, f"{col} 未映射到 MA 实体"
        eid, room, _domain = SENSOR_MAP[col]
        assert eid and room


def test_wide_to_events_is_edge_triggered():
    events = wide_to_events(SAMPLE)
    # 边沿触发：远少于「每秒 × 传感器数」的原始行数（原始约 11 万行）。
    assert 30 < len(events) < 200, len(events)
    # 首行不应产生 29 个全 off 的误触发事件（prev 初始化为 off）。
    ts0 = min(e["ts"] for e in events)
    first_row_events = [e for e in events if e["ts"] == ts0]
    assert len(first_row_events) < 10, first_row_events
    # 每条事件都有必要的字段。
    for e in events:
        assert e["entity_id"] and e["room"] and e["new_state"] in ("on", "off")


def test_ground_truth_maps_expected_activities():
    gt = ground_truth(SAMPLE)
    # 样本里 04-01 含全部 6 个有 MA 对应物的活动。
    assert gt["days"]["2016-04-01"] == EVALUATED_ACTIVITIES, gt["days"]


def test_run_benchmark_end_to_end():
    # 在精心构造的样本上，转换器 + 检测器应对齐到完美基线。
    r = run_benchmark(SAMPLE)
    assert r["n_events"] > 0
    for act in EVALUATED_ACTIVITIES:
        assert r["per_activity"][act]["f1"] == 1.0, (act, r["per_activity"][act])
    assert r["macro_f1"] == 1.0


def test_activity_map_only_known_labels():
    # 所有映射值都应是受评估的活动（避免出现未评估的标签混入）。
    assert set(ACTIVITY_MAP.values()) == set(EVALUATED_ACTIVITIES)
