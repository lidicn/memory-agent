"""MA-裁5 追加 Q-A 的回归锁：`total` = 匹配的事件总数，`count` = 本页条数。

裁定：`decisions/20261004-AF安全审计与MA回执与遗留两批-裁定.md` §三 裁5回执 **Q-A**
（`total` = "匹配的事件总数"（legacy 口径）；**授权** MA 在 Store 侧新增一条不带 LIMIT 的
计数查询；`count` 与 `total` 分开，不许改名冒充）。

为什么这把锁必须存在：裁5 那批 MA 拒绝补 `total`，理由是「`store.list_behavior_events`
在 SQL `LIMIT` **之后**才做 `member` 过滤，引擎手里没有可信的匹配总数」。这条授权把那个
前提拆掉了——过滤下推进 SQL，总数由不带 LIMIT 的 COUNT 给出。几侧各有锁：

- `count_behavior_events` 与 `list_behavior_events` 共用一份 WHERE（否则"总数"和"这一页"
  描述的不是同一批行）；
- `member` 在 SQL 里筛：旧形状下「最新 5 条里没有这个人」会报 0 条，而同一窗口里其实有 16 条；
- 精确等值而不是子串：`LIKE '%名字%'` 会把「小明明白」也算进「小明」的总数；
- 旧格式（persons 是字符串元素）/非法 JSON/非数组的判定口径与 `_deserialize_persons` 对齐；
- 计数查询真的挂掉时宁缺毋假：`total=None` + `total_exact=False`，不许拿本页条数冒充；
- `events` 一侧同一口径：切片被 `max_scan` 截断时 `total` 仍是全量匹配数（上限本身不动）。
"""

import os
import sys
from datetime import timedelta

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.insights.models import InsightConfig, house_now  # noqa: E402
from memory_agent.store import Store  # noqa: E402

# 种子的地面真相（逐条数出来的，改种子必须同时改这里）
TOTAL_ALL = 26          # 表内全部行
TOTAL_LIVING = 25       # room=客厅
MING_LIVING = 16        # 客厅且 persons 里精确出现「小明」（含 1 行 vlm_failed）
MING_LIVING_OK = 15     # 上一条再限定 status='ok'
MING_ANY_ROOM = 17      # 不限房间（多 1 条书房）


def _tmp_store():
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "ma_c7_%d.db" % os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    st = Store(tmp, tz_offset_hours=8.0)
    st.init_schema()
    return st


def _svc(st, cfg=None):
    return InsightService(st, cfg or Config())


def _be(st, minutes_ago, room, persons, status="ok"):
    """写一条行为事件，时间相对"现在"回退，保证 days=1 的窗口一定覆盖它。"""
    ts = (house_now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return st.insert_behavior_event({
        "server_ts": ts, "room": room, "persons": persons, "count": len(persons),
        "action": "坐着", "status": status,
    })


def _raw_be(st, minutes_ago, room, persons_json):
    """绕过 `_serialize_persons` 直接写 persons_json——旧格式与脏数据只有这条路能造出来。"""
    ts = (house_now() - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    conn = st.connect()
    with st._lock:
        conn.execute(
            "INSERT INTO behavior_events(server_ts, day, room, persons_json, count, status)"
            " VALUES(?,?,?,?,?,?)",
            (ts, ts[:10], room, persons_json, 0, "ok"))
        conn.commit()


def _seed(st):
    """16 条含「小明」的客厅事件 + **更新的 5 条**无人员客厅事件 + 一堆口径陷阱。

    那 5 条空白行是故意放在最近端的：旧实现在 `LIMIT` 之后才筛人，`limit=5` 先取这 5 条
    再筛 ⇒ 报 0 条，而同一窗口里其实有 16 条。这个形状就是 Q-A 之前的死因。
    """
    for i in range(5):
        _be(st, i * 3, "客厅", [])                        # 最近的一批：没有人
    for i in range(13):
        _be(st, 30 + i * 7, "客厅", [{"name": "小明"}])   # 13 条客厅含小明
    _be(st, 40, "书房", [{"name": "小明"}])               # 房间过滤必须点得掉
    _be(st, 41, "客厅", [{"name": "小明明白"}])            # 子串陷阱：不许算进「小明」
    _be(st, 42, "客厅", [{"detail": "没有人名键"}])        # 无 name 键
    _be(st, 43, "客厅", [{"name": "小明"}, {"name": "小刚"}])  # 同行多人：只算一行
    _be(st, 47, "客厅", [{"name": "小明"}], status="vlm_failed")
    _raw_be(st, 44, "客厅", '["小明"]')                    # 旧格式：字符串元素本身即姓名
    _raw_be(st, 45, "客厅", "{not json")                   # 非法 JSON：不匹配，也不许把查询带崩
    _raw_be(st, 46, "客厅", '"小明"')                      # 合法 JSON 但不是数组 ⇒ 无人员


def test_count_behavior_events_is_uncapped_and_agrees_with_the_page():
    """`count_*` 与 `list_*` 共用一份 WHERE，且计数不受任何 LIMIT/分页影响。"""
    st = _tmp_store()
    try:
        _seed(st)
        assert st.count_behavior_events() == TOTAL_ALL
        assert st.count_behavior_events(room="客厅") == TOTAL_LIVING
        assert st.count_behavior_events(room="客厅", member="小明") == MING_LIVING
        assert st.count_behavior_events(member="小明") == MING_ANY_ROOM
        # status 也在同一份 WHERE 里
        assert st.count_behavior_events(room="客厅", member="小明",
                                       status="ok") == MING_LIVING_OK
        assert st.count_behavior_events(status="vlm_failed") == 1
        # day 范围下推进 SQL：开区间给全量，闭区间给 0
        assert st.count_behavior_events(day_from="2000-01-01", day_to="2999-12-31") == TOTAL_ALL
        assert st.count_behavior_events(day_from="2999-01-01") == 0
        assert st.count_behavior_events(day_to="2000-01-01") == 0
        # 「不带 LIMIT」是真命题：limit=1 的页面远小于总数
        assert len(st.list_behavior_events(room="客厅", member="小明", limit=1)) == 1
        assert st.count_behavior_events(room="客厅", member="小明") == MING_LIVING
    finally:
        st.close()
        os.remove(st.db_path)


def test_member_filter_is_pushed_into_sql_not_applied_after_the_limit():
    """Q-A 拆掉的那条前提：旧形状下 `limit=5` 撞上"最近 5 条没人"就报 0 条。

    改前实测：`list_behavior_events(room='客厅', member='小明', limit=5)` 返回 **0 行**
    （先取最近 5 条空白行，再在 Python 里筛人），而同一窗口里有 16 条。
    """
    st = _tmp_store()
    try:
        _seed(st)
        page1 = st.list_behavior_events(room="客厅", member="小明", limit=5)
        assert len(page1) == 5, page1
        for row in page1:
            assert any(p.get("name") == "小明" for p in row["persons"]), row
        ids1 = [r["id"] for r in page1]
        page2 = st.list_behavior_events(room="客厅", member="小明", limit=5, offset=5)
        ids2 = [r["id"] for r in page2]
        assert len(page2) == 5 and not (set(ids1) & set(ids2)), (ids1, ids2)
        # 最后一页翻不完：16 条按 5 条一页要 4 页，第 5 页空
        assert len(st.list_behavior_events(room="客厅", member="小明", limit=5,
                                          offset=15)) == 1
        assert st.list_behavior_events(room="客厅", member="小明", limit=5, offset=16) == []
    finally:
        st.close()
        os.remove(st.db_path)


def test_member_matching_is_exact_and_follows_the_deserialize_semantics():
    """口径与 `_deserialize_persons` 逐字对齐，且**精确等值**（子串不算命中）。"""
    st = _tmp_store()
    try:
        _seed(st)
        assert st.count_behavior_events(member="小明明白") == 1
        assert st.count_behavior_events(room="客厅", member="小刚") == 1
        assert st.count_behavior_events(member="不存在的名字") == 0
        # 「小明」的总数不含"小明明白"那条，也不含无 name 键、非法 JSON、非数组三行
        assert st.count_behavior_events(room="客厅", member="小明") == MING_LIVING
        rows = st.list_behavior_events(room="客厅", member="小明", limit=100)
        assert len(rows) == MING_LIVING, len(rows)
        # 精确等值的反面证据：「小明明白」那条必须在结果集之外
        ming_ids = {r["id"] for r in rows}
        mingming_ids = {r["id"] for r in st.list_behavior_events(member="小明明白")}
        assert mingming_ids and not (mingming_ids & ming_ids), (mingming_ids, ming_ids)
    finally:
        st.close()
        os.remove(st.db_path)


def test_query_behavior_events_keeps_total_and_count_as_different_things():
    """门面层：`total` 是匹配总数，`count` 是本页条数，谁也不许冒充谁。

    裁5 那批这里**没有** `total`（理由见模块 docstring）；追加 Q-A 之后必须补上，
    而且偏要在 `limit` 远小于总数的时候补——那是两键最容易混的场合。
    """
    st = _tmp_store()
    try:
        _seed(st)
        svc = _svc(st)
        out = svc.query_behavior_events(member="小明", days=1, limit=5)
        assert out.get("ok") is True, out
        assert out["count"] == 5, out
        assert out["total"] == MING_ANY_ROOM, out
        assert out["total"] > out["count"], out
        assert out["total_exact"] is True, out
        assert out["has_more"] is True, out
        assert len(out["events"]) == out["count"], out
        # legacy 承诺键一字不动（裁5 Q2=A 两代键并存）
        for key in ("ok", "window", "room", "member_filter", "events"):
            assert key in out, key
    finally:
        st.close()
        os.remove(st.db_path)


def test_total_is_left_unknown_when_the_count_query_fails():
    """取不到总数时报 `None` + `total_exact=False`，不许退回"本页条数"充数。"""
    st = _tmp_store()
    try:
        _seed(st)

        class Boom(Store):
            def count_behavior_events(self, *args, **kwargs):
                raise RuntimeError("计数查询挂了")

        boom = Boom(st.db_path, tz_offset_hours=8.0)
        try:
            out = _svc(boom).query_behavior_events(member="小明", days=1, limit=5)
            assert out.get("ok") is True, out
            assert out["count"] == 5, out
            assert out["total"] is None, out
            assert out["total_exact"] is False, out
        finally:
            boom.close()
    finally:
        st.close()
        os.remove(st.db_path)


def test_events_total_is_exact_even_when_the_scan_slice_is_truncated():
    """`events` 一侧同一口径：切片被 `max_scan` 截断，`total` 仍是全量匹配数。

    钉的是"两件事同时成立"——`truncated=True`（切片只覆盖窗口前段）与
    `total_exact=True`（总数来自不带 LIMIT 的 COUNT）。裁5 Q4=A 的"不提高上限"
    在这里原样成立：`max_scan` 一个字节都没动，只是不再拿切片行数冒充总数。
    """
    st = _tmp_store()
    try:
        base = house_now() - timedelta(hours=2)
        for i in range(10):
            st.insert_events([{
                "entity_id": "light.hall",
                "ts": (base + timedelta(minutes=i)).isoformat(timespec="seconds"),
                "new_state": "on", "room": "客厅", "domain": "light",
                "attrs": {"friendly_name": "客厅灯"},
            }])
        start = base.isoformat(timespec="seconds")
        end = (base + timedelta(minutes=30)).isoformat(timespec="seconds")

        capped = _svc(st, InsightConfig(max_scan=6))
        out = capped.get_events(start=start, end=end, room="客厅", limit=2)
        assert out["total"] == 10, out                # 全窗口真值，不受上限约束
        assert out["count"] == 2 and len(out["events"]) == 2, out
        assert out["truncated"] is True, out           # 切片只读了 6 条
        assert out["scan_limit"] == 6, out
        assert out["total_exact"] is True, out
        assert out["has_more"] is True, out

        full = _svc(st, InsightConfig(max_scan=50))
        ok = full.get_events(start=start, end=end, room="客厅", limit=2)
        assert ok["total"] == 10 and ok["truncated"] is False, ok
        assert ok["count"] == 2, ok
    finally:
        st.close()
        os.remove(st.db_path)
