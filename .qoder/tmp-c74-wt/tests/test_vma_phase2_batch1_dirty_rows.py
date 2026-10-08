"""第二期审计（`doc/审计报告/2期/` 十轮，2026-10-06/07 新增）确认缺陷的回归锁 · 第一批。

覆盖面（每条都对应审计报告里的一次实测，不是我自己想加的字段）：
- MA-01（第一轮）：`list_activity_rules` 循环内裸 `json.loads` ⇒ 一条脏 tags_json 让
  **整批**活动规则读不出来。全仓唯一调用点 `insights_legacy.py` 有 try 但静默，
  用户视角是「自定义规则全部不生效、系统不报错」。
- MA-02（第一轮）：`member_insight_feedback` 同形状 + 裸 `int()`/`float()` ⇒ 一条脏记录
  让整个成员洞察页 HTTP 500。对照样本是 30 行外的 `researcher_direction_feedback`
  （tags 有 try、数字列同样裸调 ⇒ 它自己也只修了一半）。
- MA-03（第一轮）：`get_session_agent_trust` 的列表推导裸 `float()` ⇒ 声誉回路抛在
  写库之前，该 session 的新记忆一条也进不去。真实表把 trust 声明成 REAL 仍收得下 'bad'。
- MA-04 / MA-14（第一、五轮，同形状两处）：坏 `time_window` 走 `except: return True` ⇒
  约束**静默消失**而不是收紧。判据取自同仓 `patterns._json_object` 的注释：
  坏 condition 不能退化成「不限」。
- MA-15（第五轮）：覆盖预检失败返回 None，与「覆盖良好」逐字节相同 ⇒ 它致力于消除
  「含糊的未命中」，自己失败时却制造同样含糊的沉默。
- MA-16（第五轮）：坏时间戳判 `ok`（本该 `no_data`）。M3 一族四处的第三处，
  第四处 `identity._health_allows` 是 docstring 声明的有意 fail-open，不在本批。
"""

from __future__ import annotations

import types
from datetime import datetime

import pytest

from memory_agent import activity_inference as ai
from memory_agent import intent_inference as ii
from memory_agent import template_validate as tv
from memory_agent.insights_legacy import InsightService
from memory_agent.store import Store


def _store(tmp_path) -> Store:
    st = Store(str(tmp_path / "ma.db"), tz_offset_hours=8.0)
    st.init_schema()
    return st


def _dirty(st: Store, sql: str, *args) -> None:
    """把某一列改成库里真存得下的坏值——SQLite 弱类型是这批缺陷的前提。"""
    with st.transaction() as conn:
        conn.execute(sql, args)


def _by_id(data: dict) -> dict:
    return {m["memory_id"]: m for m in data["memories"]}


# ── MA-01：一条脏规则不能带走整批 ──────────────────────────────────────────

def test_one_dirty_activity_rule_does_not_wipe_the_whole_batch(tmp_path):
    st = _store(tmp_path)
    for name, tags in (("nap", ["light"]), ("workout", ["tv"]), ("cooking", ["stove"])):
        st.upsert_activity_rule({"name": name, "room": "客厅", "tags": tags})

    # 控制档：干净数据必须三条都在，否则「跳过脏行」就退化成「少读一条也测不出」
    assert {r["name"] for r in st.list_activity_rules()} == {"nap", "workout", "cooking"}

    _dirty(st, "UPDATE activity_rules SET tags_json=? WHERE name=?", "[坏 JSON", "workout")

    got = st.list_activity_rules()
    assert {r["name"] for r in got} == {"nap", "cooking"}
    assert all(isinstance(r["tags"], list) for r in got)
    # 两种 enabled_only 走的是同一条循环，两条腿都得活
    assert {r["name"] for r in st.list_activity_rules(enabled_only=True)} == {"nap", "cooking"}
    assert {r["name"] for r in st.list_activity_rules(enabled_only=False)} == {"nap", "cooking"}


def test_dirty_activity_rule_is_skipped_not_read_as_unconstrained(tmp_path):
    """方向锁：坏 tags_json 不能退化成 `[]`——`[]` 的语义是不限标签，那条规则就此匹配全屋。"""
    st = _store(tmp_path)
    st.upsert_activity_rule({"name": "nap", "room": "客厅", "tags": ["light"]})
    st.upsert_activity_rule({"name": "ghost", "room": "客厅", "tags": ["tv"]})
    _dirty(st, "UPDATE activity_rules SET tags_json=? WHERE name=?", "{不是数组", "ghost")
    names = {r["name"] for r in st.list_activity_rules()}
    assert "ghost" not in names          # 读不通的规则不参与推断
    assert names == {"nap"}


# ── MA-02：一条脏记录不能打穿成员洞察页 ────────────────────────────────────

def _seed_member_memories(st: Store) -> dict:
    good_up = st.add_agent_memory("s1", "文本一", "topic", '["member:alice"]', "[]", 30)
    # 截断的 JSON，但仍含 LIKE 要匹配的 member:alice 子串 ⇒ 这行一定进结果集
    broken = st.add_agent_memory("s1", "文本二", "topic", '["member:alice", ', "[]", 30)
    bad_trust = st.add_agent_memory("s1", "文本三", "topic", '["member:alice"]', "[]", 30)
    _dirty(st, "UPDATE agent_memories SET feedback_up=? WHERE memory_id=?", 3, good_up)
    _dirty(st, "UPDATE agent_memories SET feedback_up=? WHERE memory_id=?", "abc", broken)
    _dirty(st, "UPDATE agent_memories SET trust=? WHERE memory_id=?", "bad", bad_trust)
    return {"good_up": good_up, "broken": broken, "bad_trust": bad_trust}


def test_dirty_row_does_not_500_the_member_insight_page(tmp_path):
    st = _store(tmp_path)
    ids = _seed_member_memories(st)

    data = st.member_insight_feedback("alice", "Alice")   # 改前：JSONDecodeError → 整页 500
    assert data["count"] == 3, "整批一条都不能少"
    by = _by_id(data)
    assert set(by) == set(ids.values())
    assert all(isinstance(m["tags"], list) for m in data["memories"])
    assert by[ids["broken"]]["tags"] == []        # 展示元数据按空兜底，不是约束
    # 好列照常计数，坏列按 0 计入并留 WARNING：既不带走整页，也不把 3 读成 0
    assert by[ids["good_up"]]["feedback_up"] == 3
    assert by[ids["broken"]]["feedback_up"] == 0
    assert data["up"] == 3 and data["down"] == 0
    assert all(isinstance(m["trust"], float) for m in data["memories"])
    assert by[ids["bad_trust"]]["trust"] == 0.0


def test_direction_aggregate_survives_a_dirty_numeric_column(tmp_path):
    """MA-02 的对照样本自己也有裸 int()/float()：tags 有 try 不代表整批安全。"""
    st = _store(tmp_path)
    a = st.add_agent_memory("s1", "方向甲", "t", '["auto-researcher","direction:alpha"]', "[]", 30)
    st.add_agent_memory("s1", "方向乙", "t", '["auto-researcher","direction:beta"]', "[]", 30)
    _dirty(st, "UPDATE agent_memories SET feedback_down=? WHERE memory_id=?", "xyz", a)
    out = st.researcher_direction_feedback()
    assert {d["direction"] for d in out} == {"alpha", "beta"}
    alpha = next(d for d in out if d["direction"] == "alpha")
    assert alpha["down"] == 0 and alpha["count"] == 1


# ── MA-03：脏 trust 不能把声誉回路炸在写库之前 ─────────────────────────────

def test_dirty_trust_row_still_returns_a_usable_reputation_read(tmp_path):
    st = _store(tmp_path)
    good = st.add_agent_memory("s1", "一", "t", "[]", "[]", 30)
    bad = st.add_agent_memory("s1", "二", "t", "[]", "[]", 30)
    _dirty(st, "UPDATE agent_memories SET trust=? WHERE memory_id=?", 0.4, good)
    _dirty(st, "UPDATE agent_memories SET trust=? WHERE memory_id=?", "bad", bad)

    info = st.get_session_agent_trust("s1")    # 改前：ValueError → 调用点裸调 → 写入被阻断
    assert info["count"] == 2
    assert info["avg_trust"] == 0.2            # (0.4 + 0.0 中性) / 2，读数仍然可用
    assert info["strict"] is False


def test_session_trust_strict_flag_still_fires_on_genuinely_bad_reputation(tmp_path):
    """收紧判据不能被「坏值按 0 兜底」顺带改掉：真差声誉仍要 strict。"""
    st = _store(tmp_path)
    m = st.add_agent_memory("s2", "一", "t", "[]", "[]", 30)
    _dirty(st, "UPDATE agent_memories SET trust=? WHERE memory_id=?", -0.9, m)
    assert st.get_session_agent_trust("s2")["strict"] is True


# ── MA-04 / MA-14：坏时间窗 = 约束不成立，不是放行 ─────────────────────────

BAD_WINDOWS = ["bad", "abc-def", "08:00-", "08:00", "08:00-22:00-06", {"w": "22:00"}, 12345]


@pytest.mark.parametrize("window", BAD_WINDOWS)
def test_intent_time_window_fails_closed_on_unparsable_window(window):
    ref = datetime(2026, 1, 1, 3, 0)          # 凌晨 3 点，落在 08:00-22:00 之外
    assert ii._match_time_window({"time_window": "08:00-22:00"}, ref) is False
    assert ii._match_time_window({"time_window": window}, ref) is False
    # 空窗口仍是显式「不限」——收紧的是「读不通」，不是「没声明」
    assert ii._match_time_window({"time_window": ""}, ref) is True
    assert ii._match_time_window({}, ref) is True


def test_intent_time_window_normal_cases_unchanged():
    assert ii._match_time_window({"time_window": "08:00-22:00"},
                                 datetime(2026, 1, 1, 10, 0)) is True
    assert ii._match_time_window({"time_window": "22:00-02:00"},   # 跨午夜
                                 datetime(2026, 1, 1, 1, 0)) is True
    assert ii._match_time_window({"time_window": "22:00-02:00"},
                                 datetime(2026, 1, 1, 3, 0)) is False


@pytest.mark.parametrize("window", BAD_WINDOWS)
def test_activity_time_window_fails_closed_on_unparsable_window(window):
    ts = "2026-01-01T03:00:00"
    assert ai._in_time_window(ts, "08:00-22:00") is False
    assert ai._in_time_window(ts, window) is False
    assert ai._in_time_window(ts, "") is True           # 显式不限
    assert ai._in_time_window("2026-01-01T10:00:00", "08:00-22:00") is True


@pytest.mark.parametrize("ts", ["", "not-a-time", "2026-13-45T99:99:99"])
def test_activity_time_window_fails_closed_on_unparsable_ts(ts):
    """末步事件时间戳读不通时也不能放行：改前 `_parse_ts` 返回 None 就 `return True`。"""
    assert ai._in_time_window(ts, "08:00-22:00") is False
    assert ai._in_time_window(ts, "") is True


# ── MA-15：预检失败必须和「覆盖良好」长得不一样 ────────────────────────────

def _raise_on_scan(*a, **kw):
    raise RuntimeError("表损坏（模拟 DB 故障）")


def _coverage_stub(store, scan=_raise_on_scan):
    stub = types.SimpleNamespace(
        store=store, tz=8.0, name_map=lambda: {},
        _iter_all_events=scan,
        _tags_of=lambda eid, disp: {"presence"},
    )
    # define_activity 经 self 调预检，桩件得把这枚方法挂上（真实实现，不另造口径）
    stub._check_activity_rule_coverage = lambda room, tags, window_days=14: (
        InsightService._check_activity_rule_coverage(stub, room, tags, window_days))
    return stub


def test_coverage_precheck_failure_is_not_read_as_good_coverage(tmp_path):
    st = _store(tmp_path)
    stub = _coverage_stub(st)
    warn = InsightService._check_activity_rule_coverage(stub, "客厅", ["presence"])
    assert warn is not None, "预检失败原先 return None，调用方读不出「没查成」"
    assert warn.get("unverified") is True
    assert "未能完成" in warn["message"]
    assert warn["missing_tags"] == []       # 没查成就不能断言缺哪个标签
    # 对照档：真的没 tags 时仍然是 None（那是「无需预检」，不是「预检没跑成」）
    assert InsightService._check_activity_rule_coverage(stub, "客厅", []) is None


def test_define_activity_exposes_the_unverified_state(tmp_path):
    st = _store(tmp_path)
    out = InsightService.define_activity(
        _coverage_stub(st), "nap", room="客厅", tags=["presence"])
    assert out["ok"] is True                       # 诊断不阻塞注册（有意设计）
    assert out["coverage_unverified"] is True      # 但响应形状必须分得开
    assert "⚠️" in out["message"]
    # 覆盖良好时不加这两枚键——否则「未判定」又变成另一种说谎
    ok_stub = _coverage_stub(st, scan=lambda *a, **kw: [
        {"entity_id": "sensor.p_1", "room": "客厅"}])
    clean = InsightService.define_activity(ok_stub, "workout", room="客厅", tags=["presence"])
    assert "coverage_unverified" not in clean and "coverage_warning" not in clean


# ── MA-16：坏时间戳不能判 ok ───────────────────────────────────────────────

def _rt():
    return types.SimpleNamespace(config=types.SimpleNamespace(tz_offset_hours=8.0))


def test_unparsable_last_ts_is_not_within_window():
    assert tv._within_window("2026-10-01T10:00:00", 3000, _rt()) is True   # 控制档
    assert tv._within_window("not-a-time", 30, _rt()) is False
    assert tv._within_window("2026-13-45T99:99:99", 30, _rt()) is False
    assert tv._within_window("", 30, _rt()) is False
    # 越界（超出窗口）本来就是 False，方向不能被一起改掉
    assert tv._within_window("2000-01-01T00:00:00", 30, _rt()) is False
