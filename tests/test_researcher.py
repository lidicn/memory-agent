"""记忆研究员（v0.8）单元测试：四轴切片 / 安全闸 / staging 写入 / 去重。

不依赖真实 DB / Chroma / LLM —— 用最小内存 fake 验证核心逻辑，
可在容器内 `python -m pytest tests/test_researcher.py -q` 运行。
"""

import asyncio
import json

import pytest

from memory_agent.researcher import ResearcherService, DIRECTIONS


def make_config(**kw):
    defaults = dict(
        researcher_enabled=True,
        researcher_daily_token_budget=80000,
        researcher_scheduler_time="03:00",
        researcher_unit_cap=50,
        researcher_call_timeout=30,
        researcher_max_consecutive_failures=3,
        researcher_staging_ttl_days=30,
        tz_offset_hours=8,
        agent_dup_sim=0.92,
    )
    defaults.update(kw)
    from types import SimpleNamespace

    return SimpleNamespace(**defaults)


class _StoreFake:
    def __init__(self):
        self.calls = []

    def add_agent_memory(self, session_id, text, topic_key, tags_json, source_refs_json,
                         ttl_days, state, source, memory_id=None):
        mid = memory_id or f"mem_{len(self.calls)}"
        self.calls.append({"id": mid, "text": text, "topic_key": topic_key, "memory_id": memory_id})
        return mid

    def list_insight_jobs(self, enabled_only=False):
        return []

    def researcher_daily_token_used(self, day):
        return 0

    def record_researcher_run(self, run):
        self.last_run = run

    def touch_insight_job_last_run(self, job_id):
        self.last_touch = job_id


def make_runtime(config=None, store=None):
    from types import SimpleNamespace

    config = config or make_config()
    store = store or _StoreFake()
    return SimpleNamespace(
        config=config,
        store=store,
        insights=SimpleNamespace(
            behavior_insights=lambda **k: {"daily_rhythm": {}, "rooms": {}},
            infer_activities=lambda **k: {"activities": []},
        ),
        llm=SimpleNamespace(
            chat=lambda **k: asyncio.sleep(
                0,
                result={
                    "choices": [{"message": {"content": json.dumps(
                        {"has_insight": True, "text": "主卧就寝延迟", "confidence": 0.8})}}],
                    "usage": {"total_tokens": 120},
                },
            )
        ),
        agent_memory=SimpleNamespace(_upsert_mirror=lambda m: None),
        history=SimpleNamespace(agent_collection=None),
    )


def test_expand_units_single():
    rs = ResearcherService(make_runtime())
    units = rs.expand_units({"direction": "rhythm", "area": "", "member": "", "window_days": 7})
    assert len(units) == 1
    assert units[0] == {"direction": "rhythm", "area": "", "member": ""}


def test_expand_units_multi_axis():
    rs = ResearcherService(make_runtime())
    units = rs.expand_units(
        {"direction": "rhythm,anomaly", "area": "主卧,客厅", "member": "a,b", "window_days": 7}
    )
    assert len(units) == 8  # 2 方向 × 2 区域 × 2 对象


def test_unit_cap_truncation():
    rs = ResearcherService(make_runtime(make_config(researcher_unit_cap=3)))
    units = rs.expand_units(
        {"direction": "rhythm,anomaly,habit,member_diff,linkage,energy",
         "area": "主卧,客厅,厨房", "member": "a,b,c,d", "window_days": 7}
    )
    assert len(units) == 3  # 笛卡尔积爆炸防护：截断到 unit_cap


def test_topic_key():
    rs = ResearcherService(make_runtime())
    assert rs._topic_key({"direction": "rhythm", "area": "主卧", "member": "lidicn"}) == \
        "researcher:rhythm:主卧:lidicn"


def test_parse_llm_json():
    r = ResearcherService._parse_llm('{"has_insight":true,"text":"x","confidence":0.5}', 10)
    assert r["has_insight"] is True
    assert r["token"] == 10


def test_parse_llm_degraded():
    r = ResearcherService._parse_llm("这不是 JSON", 5)
    assert r["has_insight"] is False


def test_directions_enum():
    assert set(DIRECTIONS.keys()) == {
        "rhythm", "anomaly", "habit", "member_diff", "linkage", "energy"
    }


async def test_run_job_writes_staging():
    store = _StoreFake()
    rs = ResearcherService(make_runtime(store=store))
    job = {"job_id": "j1", "direction": "rhythm", "area": "主卧", "member": "lidicn", "window_days": 7}
    stats = await rs.run_job(job, {"budget_left": 10000, "date": "2026-09-11"})
    assert stats["units_processed"] == 1
    assert stats["hits"] == 1
    assert len(store.calls) == 1
    assert store.calls[0]["topic_key"].startswith("researcher:rhythm:主卧:lidicn")


async def test_run_job_budget_stop():
    store = _StoreFake()
    rs = ResearcherService(make_runtime(store=store))
    # 单单元约 120 token，预算仅 50 → 首单元跑完即触底，次单元被预算闸门拦下
    job = {"job_id": "j2", "direction": "rhythm,anomaly", "area": "主卧", "member": "", "window_days": 7}
    stats = await rs.run_job(job, {"budget_left": 50, "date": "2026-09-11"})
    assert stats["stopped_by_budget"] is True


async def test_run_all_skipped_when_disabled():
    rs = ResearcherService(make_runtime(make_config(researcher_enabled=False)))
    result = await rs.run_all()
    assert result.get("skipped") == "researcher_enabled=false"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
