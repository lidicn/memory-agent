"""三路径缺口C：route_question 未命中埋点（route_misses）回归锁。

依据 DCD `20261120-MA-挂门四项启动条件与契约v1事件族埋点口径-裁定.md` Q4 甲：
- 新表 `route_misses`：原始说法／已抽槽位／是否命中／时间戳；
- 保留 30 天或 10 万行，先到先 purge；
- 未命中样本（hit=0）可导出成 badcase 候选。

钉的是四件事：
1. 写进去的能被按「是否命中」过滤读出来，槽位是结构化 dict；
2. 导出只给未命中（hit=0）；
3. 过期裁（按天）与超额裁（按行数、先到先 purge）都对；
4. `route_question`（= `InsightService.plan_question`）真的在跑时落点：未知意图记 hit=0、
   已知意图记 hit=1；无库门面（store=None）不炸。
"""

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402


def _fresh(tmp_path, name="rm.db"):
    st = Store(str(tmp_path / name), tz_offset_hours=0.0)
    st.init_schema()
    return st


# ── 1. 读写 + 按命中过滤 ────────────────────────────────────────────────────

def test_add_list_count_and_slots_roundtrip(tmp_path):
    st = _fresh(tmp_path)
    slots = {"intent": "device_usage", "room": "客厅", "query": "空调", "entity_ids": []}
    st.add_route_miss("昨天客厅空调用了多久", slots=slots, hit=True,
                      intent="device_usage", ts="2026-11-20T10:00:00")
    st.add_route_miss("随便说句话", slots={"intent": "unknown"}, hit=False,
                      intent="unknown", ts="2026-11-20T10:00:01")

    rows = st.list_route_misses()
    assert len(rows) == 2
    # 倒序：ts 大的在前
    assert rows[0]["question"] == "随便说句话"
    hit_row = next(r for r in rows if r["hit"])
    assert hit_row["question"] == "昨天客厅空调用了多久"
    assert hit_row["slots"] == slots, "槽位必须以结构化 dict 原样读回"

    assert st.count_route_misses() == 2
    assert st.count_route_misses(hit=True) == 1
    assert st.count_route_misses(hit=False) == 1
    assert st.count_route_misses(since="2026-11-20T10:00:01") == 1


# ── 2. badcase 导出只给未命中 ───────────────────────────────────────────────

def test_export_badcases_only_misses(tmp_path):
    st = _fresh(tmp_path)
    st.add_route_miss("昨天书房电脑用了多久", {"intent": "device_usage"}, hit=True,
                      intent="device_usage", ts="2026-11-20T09:00:00")
    st.add_route_miss("那个啥情况", {"intent": "unknown"}, hit=False,
                      intent="unknown", ts="2026-11-20T09:00:01")

    cases = st.export_route_miss_badcases()
    assert len(cases) == 1
    assert cases[0]["question"] == "那个啥情况"
    assert cases[0]["slots"] == {"intent": "unknown"}
    assert "ts" in cases[0] and "miss_id" in cases[0]


# ── 3a. 过期裁（按天）────────────────────────────────────────────────────────

def test_purge_expired_by_age(tmp_path):
    st = _fresh(tmp_path)
    st.add_route_miss("旧问题", {"intent": "unknown"}, hit=False,
                      ts="2026-01-01T00:00:00")          # 远早于 30 天
    st.add_route_miss("新问题", {"intent": "unknown"}, hit=False,
                      ts="2026-11-20T00:00:00")          # 恰在当下
    res = st.purge_route_misses(days=30, now_ts="2026-11-20T00:00:00")

    assert res["deleted_expired"] == 1, res
    assert res["deleted_over_cap"] == 0, res
    assert res["remaining"] == 1
    assert [r["question"] for r in st.list_route_misses()] == ["新问题"]


# ── 3b. 超额裁（按行数、先到先 purge）────────────────────────────────────────

def test_purge_over_cap_keeps_newest(tmp_path):
    st = _fresh(tmp_path)
    # 5 条全在 30 天内（不会被按天裁），时刻递增
    for i in range(5):
        st.add_route_miss(f"q{i}", {"intent": "unknown"}, hit=False,
                          ts=f"2026-11-19T00:00:0{i}")
    res = st.purge_route_misses(days=30, max_rows=3, now_ts="2026-11-20T00:00:00")

    assert res["deleted_expired"] == 0, res
    assert res["deleted_over_cap"] == 2, res
    assert res["remaining"] == 3
    # 留下的应是最新的三条 q2/q3/q4
    kept = {r["question"] for r in st.list_route_misses()}
    assert kept == {"q2", "q3", "q4"}, kept


# ── 4. plan_question（route_question）真的落点 ───────────────────────────────

def test_plan_question_records_hit_and_miss(tmp_path):
    st = _fresh(tmp_path)
    svc = InsightService(st, Config())

    svc.plan_question("昨天客厅空调用了多久")   # device_usage ⇒ 命中
    svc.plan_question("随便说句话")             # unknown ⇒ 未命中

    rows = st.list_route_misses()
    assert len(rows) == 2, rows
    by_q = {r["question"]: r for r in rows}
    hit = next(r for r in rows if r["hit"])
    miss = next(r for r in rows if not r["hit"])
    assert hit["intent"] == "device_usage", hit
    assert hit["slots"].get("intent") == "device_usage"
    assert miss["intent"] == "unknown", miss
    assert st.count_route_misses(hit=False) == 1


def test_plan_question_without_store_does_not_crash():
    svc = InsightService(None, Config())
    out = svc.plan_question("昨天客厅空调用了多久")
    assert out.get("intent") == "device_usage", out


def test_record_route_miss_swallows_store_errors_without_breaking_query():
    """store 写埋点抛异常时，plan_question 仍要正常返回（旁路埋点不许阻塞查询）。"""

    class _BoomStore:
        def add_route_miss(self, **kwargs):
            raise RuntimeError("db gone")

    svc = InsightService(None, Config())
    svc.store = _BoomStore()          # 绕过构造，直接塞一个会炸的 store
    out = svc.plan_question("随便说句话")
    assert out.get("intent") == "unknown", out
