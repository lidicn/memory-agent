"""DCD 20261004 裁6 Q6-2=A + DCD 20261005 §二.1 乙′ 的分层扫描回归锁。

两代约束叠在同一条读路径上，这里逐条对上号：

* 约束①（裁6）：**返回行数**硬上限 = `scan_limit`，按日分摊不新增预算。
  20261005 §二.1 把这句的口径重述为「返回行数受限、**引擎内部扫描行数不再受限**」，
  引用这一格必须带上口径。
* 约束②（裁6）：改前/改后 30 天窗耗时成对交 —— 由 `scripts/probe_q62_q2_readings.py`
  在生产库上量，本锁只保证功能形状。
* 约束③（裁6 → 乙′ 两轮改写）：截断判据。旧判据 `len(events) >= scan_limit` 在分摊下失效；
  裁6 的"任一天命中日配额"在按小时分层下漏报一层；乙′ 第一版把"某格砍满"当终判据又反了
  一层（砍满只是**嫌疑**，整天读完了照样能报截断）。**现判据：只认已证明的丢失**——
  抓回来却没交进结果集的行，或补读一轮后仍未探底的格，或窗口里有天没轮到扫。
* 判据①（乙′，裁定 :41）：30 天窗内任一日的**夜间时段（20:00–23:00）必须有事件返回**。
  这条在按天前缀的旧形状下判红 —— 它咬的是"每天只剩凌晨"这个形状，不是给新实现站岗。
* 判据②（本批自加，来自两条既有锁同时判红）：小时配额是**第一波的公平份额**，不是天花板。
  日预算装得下的一天才就必须一条不少地读回来；`scan_truncated` 只在真有行没交出时为真。

乙′ 的形状：每天一条语句，内含 24 个各带 `LIMIT k+1` 的小时格子查询 `UNION ALL`，
`k = 日配额 // 24`；轮转分配先把每格第一行收进结果（夜间因此必出场），余量再喂给被砍断的格，
补读最多一条语句（`test_concentrated_day_uses_the_leftover_budget_with_one_extra_statement`
钉住这个代价上限）。**驳回甲**（按天倒序分摊，30 天窗退化成"看昨天"）、
**驳回丙**（扫描下推到聚合，超出本件射程）。

小时分支的 WHERE 里**只有一段夹紧到窗口后的 ts 半开区间**，没有 `day` 谓词、也没有整窗
`ts BETWEEN`（`test_one_statement_per_day_with_24_hour_buckets` 钉形状，
`test_window_edges_clamped_into_the_hour_range` 钉首尾两截的归属）。这是本批在生产库上
量出来的：同一天 24 格 `day = ?` 408ms（规划器选 `idx_events_day` ⇒ 全天重扫 24 遍）、
加整窗 `ts BETWEEN` 6109ms（索引范围被撑回整窗）、只留夹紧区间 18.5ms（`idx_events_ts`），
**三者行集逐字相同**——等价性由 `day != substr(ts,1,10)` 全表 0 行（1,010,348 行）背书。

时间几何全部钉在固定墙钟锚点上，**不用 `house_now()`**：原先把事件铺在
「今天 00:00 + i*10 分钟」、窗口却取 `[now-3d, now]`，于是家庭墙钟 00:00~01:30
之间跑必然有一半事件落在窗外（00:08 实跑 5/6 条、00:13 实跑判红各一次），
`n_days` 也会随当天是否已开始而 ±1，日配额跟着变。那是**运行时刻依赖**，
不是跨平台等价位——2026-10-05 00:13 本机(+8)与容器(UTC)同形判红已证。
"""

import os
import sys
from datetime import date, datetime, timedelta

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.insights.models import InsightConfig, TimeRange  # noqa: E402
from memory_agent.insights.repository import StoreRepository, _to_iso  # noqa: E402
from memory_agent.store import Store  # noqa: E402

# 固定锚点：任何机器、任何钟点跑，日键与日配额都算得出同一个数
D0 = date(2026, 4, 7)


def _win(n_days: int):
    """[D0 00:00:00, D0+n_days-1 天末] 的整日窗口（naive 家庭墙钟）。"""
    last = datetime.combine(D0 + timedelta(days=n_days - 1), datetime.min.time())
    return TimeRange(
        datetime.combine(D0, datetime.min.time()),
        last.replace(hour=23, minute=59, second=59),
    )


def _tmp_store():
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "ma_q62_%d.db" % os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    st = Store(tmp, tz_offset_hours=8.0)
    st.init_schema()
    return st


def _repo(st, max_scan=30000):
    cfg = InsightConfig(max_scan=max_scan)
    return StoreRepository(st, cfg)


def _insert_events(st, day: date, count, room="客厅", entity="light.living"):
    """在该 day 的 00:00 起每 10 分钟一条，共 count 条（最多铺到次日 00:00 之前）。"""
    base = datetime.combine(day, datetime.min.time())
    for i in range(count):
        ts = (base + timedelta(minutes=i * 10)).isoformat(timespec="seconds")
        st.insert_events([{
            "entity_id": entity,
            "ts": ts,
            "new_state": "on" if i % 2 == 0 else "off",
            "room": room,
            "domain": "light",
            "attrs": {"friendly_name": "客厅灯"},
        }])


def _insert_at_hour(st, day: date, hour: int, count, room="客厅", entity="light.living"):
    """把 count 条事件钉在**指定小时**内（每分钟一条），用来构造"日内不同时段都有货"。"""
    base = datetime.combine(day, datetime.min.time()).replace(hour=hour)
    for i in range(count):
        ts = (base + timedelta(minutes=i)).isoformat(timespec="seconds")
        st.insert_events([{
            "entity_id": entity,
            "ts": ts,
            "new_state": "on" if i % 2 == 0 else "off",
            "room": room,
            "domain": "light",
            "attrs": {"friendly_name": "客厅灯"},
        }])


def _insert_at_ts(st, ts: str, room="客厅", entity="light.living"):
    """把**一条**事件钉在精确的 ts 串上（窗口边界那一秒的归属要逐秒说话）。"""
    st.insert_events([{
        "entity_id": entity,
        "ts": ts,
        "new_state": "on",
        "room": room,
        "domain": "light",
        "attrs": {"friendly_name": "客厅灯"},
    }])


def _day_keys(events):
    return {_to_iso(e.ts)[:10] for e in events}


def _hours(events):
    """返回 {日键: {出现的小时(int)}}——分层扫描的判据是"时段有没有出现"，不是条数。"""
    seen = {}
    for e in events:
        iso = _to_iso(e.ts)
        seen.setdefault(iso[:10], set()).add(int(iso[11:13]))
    return seen


def test_stratified_scan_covers_every_day_in_window():
    """多天窗口下**每一天**都有数据被扫到（一条 LIMIT 打完时只覆盖窗口首日）。"""
    st = _tmp_store()
    try:
        for i in range(4):
            _insert_events(st, D0 + timedelta(days=i), 5)
        # max_scan=6，n_days=4 ⇒ 日配额 = 1，小时配额 = max(1, 1//24) = 1
        repo = _repo(st, max_scan=6)
        events = repo.load_events(_win(4))
        assert len(events) <= 6, f"返回行数 {len(events)} 超过硬上限 6"
        assert _day_keys(events) == {
            "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"
        }, _day_keys(events)
    finally:
        st.close()
        os.remove(st.db_path)


def test_hour_boundary_is_half_open_so_no_row_vanishes():
    """小时格的边界是半开区间：带毫秒的 `09:59:59.500` 归 09 格，`10:00:00` 归 10 格。

    闭区间写法（`ts BETWEEN 'T09:00:00' AND 'T09:59:59'`）会让带毫秒的尾行**同时不在
    任何一格**里 —— 不报错，只是消失。分层扫描每格都带 LIMIT，掉一行就永久掉一行。
    23 点的上界越到次日 00:00，所以隔壁那天的 `00:00:00` 必须**不**在场（`day = ?` 那一支
    就是为这一格留的；把它删掉这条就判红）。

    窗口自身的上界口径也在这里钉住：`end_iso` 到 `23:59:59`，所以带毫秒的
    `23:59:59.900` 按定义就在窗外——第一次写这一格时把它当成"分层掉了行"，
    实际是窗口边界，两回事，各锁各的。
    """
    st = _tmp_store()
    try:
        day_iso = D0.isoformat()
        nxt_iso = (D0 + timedelta(days=1)).isoformat()
        for ts in ("%sT09:59:59.500" % day_iso, "%sT10:00:00" % day_iso,
                   "%sT23:00:00.400" % day_iso, "%sT00:00:00" % nxt_iso):
            st.insert_events([{
                "entity_id": "light.living", "ts": ts, "new_state": "on",
                "room": "客厅", "domain": "light", "attrs": {"friendly_name": "客厅灯"},
            }])
        base = datetime.combine(D0, datetime.min.time())
        one_day = TimeRange(base, base.replace(hour=23, minute=59, second=59))
        repo = _repo(st, max_scan=24)                 # 日配额 24 ⇒ 小时配额 1
        got = {_to_iso(e.ts)[:19] for e in repo.load_events(one_day)}
        assert sorted(got) == ["%sT09:59:59" % day_iso, "%sT10:00:00" % day_iso,
                               "%sT23:00:00" % day_iso], sorted(got)
        assert repo.last_scan_truncated is False
    finally:
        st.close()
        os.remove(st.db_path)


def test_night_hours_are_visible_every_day():
    """判据①（裁定 :41）：夜间时段必须有事件返回——这条在"每天取前缀"的形状下判红。

    形状：每天 00 点堆 200 条（ dense 前缀），20:00–23:00 各 3 条。
    * 按天前缀（裁6 首版 / 改前）：日配额 24 条全被 00 点吃掉，**夜间 0 条**；
    * 按小时分层（乙′）：每小时格各取 1 条，20/21/22/23 点**都要出场**。
    """
    st = _tmp_store()
    try:
        for i in range(4):
            day = D0 + timedelta(days=i)
            _insert_at_hour(st, day, 0, 200)
            for h in (20, 21, 22, 23):
                _insert_at_hour(st, day, h, 3)
        repo = _repo(st, max_scan=96)          # 日配额 24 ⇒ 小时配额 1
        events = repo.load_events(_win(4))
        per_day = _hours(events)
        assert len(per_day) == 4, per_day
        for day_key, hours in per_day.items():
            assert {20, 21, 22, 23} <= hours, (day_key, sorted(hours))
        assert len(events) <= 96
    finally:
        st.close()
        os.remove(st.db_path)


def test_truncated_flag_needs_evidence_not_coincidence():
    """截断位只认**已证明的丢失**：抓回来没交出的行，或补读后仍未探底的格。

    对照组（防判据恒真）：三个小时各 1 条，小时配额 1 —— 旧写法 `count >= hour_cap`
    在这里假红；反过来"某格满额"也只是**嫌疑**，补读一轮探到底、整天读完了就不该报
    （`test_ample_budget_reads_a_concentrated_day_completely` 是它的另一半）。
    """
    st = _tmp_store()
    try:
        for i in range(4):
            day = D0 + timedelta(days=i)
            for h in (8, 14, 21):
                _insert_at_hour(st, day, h, 1)
        repo = _repo(st, max_scan=96)          # 日配额 24 ⇒ 小时配额 1
        events = repo.load_events(_win(4))
        assert len(events) == 12, len(events)
        assert repo.last_scan_truncated is False, "没有小时格溢出就不该报截断"
    finally:
        st.close()
        os.remove(st.db_path)

    st = _tmp_store()
    try:
        # 真被砍：00 点 30 条 > 日配额 24 ⇒ 预算用满仍装不下，判据必须响
        _insert_at_hour(st, D0, 0, 30)
        repo = _repo(st, max_scan=24)                  # 单天：日配额 24 ⇒ 小时配额 1
        one_day = TimeRange(
            datetime.combine(D0, datetime.min.time()),
            datetime.combine(D0, datetime.min.time()).replace(hour=23, minute=59, second=59),
        )
        events = repo.load_events(one_day)
        # 返回行数正好等于预算：分层不是"每格砍到 1 条就交回"（那样只会交回 1 条、
        # 剩下 23 条预算空着），而是先用公平份额保证时段可见、再把余量喂给被砍断的格。
        assert len(events) == 24, len(events)
        assert repo.last_scan_truncated is True, "窗口里还有没交出的行时必须标记截断"
    finally:
        st.close()
        os.remove(st.db_path)


def test_ample_budget_reads_a_concentrated_day_completely():
    """小时配额是第一波的公平份额，不是天花板：预算装得下就**一条不许少**。

    这一次改动就是为了堵乙′ 第一版的反向漏报：00 点堆 5 条、日配额 24 ⇒ 按整数切
    只交回 1 条还标截断，语义侧凭空少 5 倍证据、门面分页交出半页（`get_events`
    的 `count` 比 `limit` 小，而窗口里明明有货）。那两条既有锁
    （`test_vma_activity_semantic` / `test_vma_dcd_20261004b_event_total`）
    当时同时判红，就是这一格的现读。
    """
    st = _tmp_store()
    try:
        _insert_at_hour(st, D0, 0, 5)
        repo = _repo(st, max_scan=24)
        one_day = TimeRange(
            datetime.combine(D0, datetime.min.time()),
            datetime.combine(D0, datetime.min.time()).replace(hour=23, minute=59, second=59),
        )
        events = repo.load_events(one_day)
        assert len(events) == 5, len(events)
        assert repo.last_scan_truncated is False, "整天读完了就不许报截断"
    finally:
        st.close()
        os.remove(st.db_path)


def test_single_day_window_is_also_stratified():
    """单天窗口不再"取当日前缀"：小时配额 = `scan_limit // 1 // 24`，全天 24 格各有权。

    裁6 首版把 `n_days<=1` 当特例走旧的单次查询；乙′ 之后单天窗同样分层——
    否则 `days=1` 这一档仍是"只看得见凌晨"，而那恰恰是日报最常看的一档。
    """
    st = _tmp_store()
    try:
        _insert_at_hour(st, D0, 0, 100)
        for h in (12, 19, 23):
            _insert_at_hour(st, D0, h, 2)
        repo = _repo(st, max_scan=48)          # 日配额 48 ⇒ 小时配额 2
        one_day = TimeRange(
            datetime.combine(D0, datetime.min.time()),
            datetime.combine(D0, datetime.min.time()).replace(hour=23, minute=59, second=59),
        )
        events = repo.load_events(one_day)
        hours = _hours(events)[_to_iso(events[0].ts)[:10]]
        assert {0, 12, 19, 23} <= hours, sorted(hours)
        assert len(events) <= 48
        assert repo.last_scan_truncated is True    # 00 点 100 条被砍到 2
    finally:
        st.close()
        os.remove(st.db_path)


def test_return_row_volume_hard_cap_respected():
    """约束①（口径已重述）：**返回行数**硬上限 = scan_limit，分层不新增预算。"""
    st = _tmp_store()
    try:
        for d in range(10):
            day = D0 + timedelta(days=d)
            for h in range(24):
                _insert_at_hour(st, day, h, 5)          # 每天 120 条，全天铺开
        repo = _repo(st, max_scan=50)                   # 日配额 5 ⇒ 小时配额 1
        events = repo.load_events(_win(10))
        assert len(events) == 50, (
            f"返回行数应正好等于硬上限 50，实际 {len(events)}（分摊不得新增预算）"
        )
        assert repo.last_scan_truncated is True
    finally:
        st.close()
        os.remove(st.db_path)


def test_day_budget_smaller_than_24_hours_still_capped():
    """退化档：日配额 < 24 时（小时配额取整为 1）仍不许超预算。

    只有窗口跨 1250 天以上、或调用方把 `max_scan` 压到极小才会走到这一档。
    截断按小时升序截掉并如实标记——不静默超支。
    """
    st = _tmp_store()
    try:
        for d in range(2):
            day = D0 + timedelta(days=d)
            for h in range(24):
                _insert_at_hour(st, day, h, 3)
        repo = _repo(st, max_scan=10)                   # 日配额 5（< 24 格）
        events = repo.load_events(_win(2))
        assert len(events) <= 10, len(events)
        assert repo.last_scan_truncated is True
    finally:
        st.close()
        os.remove(st.db_path)


def test_one_statement_per_day_with_24_hour_buckets():
    """形状锁：乙′ 的代价边界 =「一天一条语句、每语句 24 格」，补读最多再加一条。

    语句数若变成 24 条/天，裁6 约束② 的耗时比就不成立了；
    格子数若少掉，就是"分层"退回了前缀。
    这一档的构造是**宽松**的一天（每格都没被砍满）⇒ 恰好一条语句/天，没有补读。
    """
    st = _tmp_store()
    try:
        for i in range(3):
            _insert_at_hour(st, D0 + timedelta(days=i), 10, 2)
        repo = _repo(st, max_scan=240)
        seen = []
        real_execute = repo._execute

        def _spy(sql, sql_params=()):
            seen.append((sql, list(sql_params)))
            return real_execute(sql, sql_params)

        repo._execute = _spy
        events = repo.load_events(_win(3))
        assert len(seen) == 3, [s[0][:60] for s in seen]
        sql, sql_params = seen[0]
        assert sql.count("UNION ALL") == 23, sql.count("UNION ALL")
        assert sql.count("hour_bucket") == 24
        assert "substr(" not in sql, (
            "小时格必须走 ts 的半开区间（可落 idx_events_ts）；"
            "`substr(ts,12,2)=?` 走不了索引 ⇒ 一条语句把当天扫 24 遍"
        )
        # 每格绑定：下界 + 上界 + LIMIT + OFFSET。分支里**不带**任何 day / 整窗 ts 谓词——
        # 这正是本批改形状的全部内容：多一个 `day = ?`，规划器就弃 `idx_events_ts` 改选
        # `idx_events_day`（生产同一天 24 格实测 408ms vs 18.5ms，行集逐字相同）；
        # 多一条整窗 `ts BETWEEN`，索引范围被撑回整月（实测 6109ms）。窗口边界改由
        # `_hour_bounds` 折进每格区间，见 `test_window_edges_clamped_into_hour_range`。
        per_branch = 4
        assert len(sql_params) == 24 * per_branch, len(sql_params)
        assert "day = ?" not in sql and "day BETWEEN" not in sql
        assert "ts BETWEEN" not in sql
        # LIMIT = 小时配额 + 探针 1、OFFSET = 0。配额从 max_scan 与天数现算，不写死：
        # 写死过一次（把 240/3 天读成小时配额 1），跑出来是 0 == 24。
        hour_cap = max(1, (240 // 3) // StoreRepository.SCAN_HOURS)
        assert set(sql_params[per_branch - 2::per_branch]) == {hour_cap + 1}
        assert set(sql_params[per_branch - 1::per_branch]) == {0}
        # 每格的下界/上界是相邻且不重叠的小时区间；23 点的上界越到次日 00:00（半开）
        bounds = [sql_params[i * per_branch:i * per_branch + 2] for i in range(24)]
        assert bounds[0] == ["2026-04-07T00:00:00", "2026-04-07T01:00:00"], bounds[0]
        assert bounds[1] == ["2026-04-07T01:00:00", "2026-04-07T02:00:00"], bounds[1]
        assert bounds[22] == ["2026-04-07T22:00:00", "2026-04-07T23:00:00"], bounds[22]
        assert bounds[23] == ["2026-04-07T23:00:00", "2026-04-08T00:00:00"], bounds[23]
        assert all(bounds[i][1] == bounds[i + 1][0] for i in range(23)), bounds
        assert events
        assert repo.last_scan_truncated is False
    finally:
        st.close()
        os.remove(st.db_path)


def test_window_edges_clamped_into_the_hour_range():
    """窗口不整天对齐时，边界由小时区间自己夹紧（分支里没有 day / 整窗 ts 谓词）。

    形状锁把 `day = ?` 与整窗 `ts BETWEEN` 从分支里拿掉了——它们各自把规划器推向慢档
    （生产同一天 24 格实测：带 `day=?` 408ms、带整窗 `ts BETWEEN` 6109ms、只留夹紧区间
    18.5ms，三者行集逐字相同），于是窗口**首尾两截**必须由夹紧后的区间表达：

    * 首日 `start_iso` 之前的小时 ⇒ `lo > hi` 的空区间，一行不返（旧口径由 `ts >= start_iso` 筛掉）；
    * `ts == start_iso` 与 `ts == end_iso` 两行都**含**，`end_iso + 1s` **不含**——与旧的
      `ts BETWEEN start AND end`（两端闭）同判。上界取 `end_iso` 的下一跳：`end_iso` 由
      `_to_iso(timespec=\"seconds\")` 产出、必是整秒，朝外一档是"宁可多一档也不漏一行"的方向；
      生产库 1,010,348 行 ts 全部定长 19 位、无小数秒（`ts LIKE '%.%'` 命中 0），
      所以这一档在现网数据上与闭区间取到同一批行。
    """
    first_noon = datetime.combine(D0, datetime.min.time()).replace(hour=12)
    last_edge = datetime.combine(D0 + timedelta(days=1),
                                 datetime.min.time()).replace(hour=13, minute=30)
    st = _tmp_store()
    try:
        def _ts(dt):
            return dt.isoformat(timespec="seconds")

        out_early = _ts(first_noon - timedelta(hours=3))           # 首日 09:00，窗口之前
        in_start = _ts(first_noon)                                  # 正好是下界
        in_night = _ts(first_noon + timedelta(hours=9))             # 首日 21:00，窗内夜间格
        in_next_day = _ts(last_edge - timedelta(hours=4, minutes=30))  # 末日 09:00
        in_end = _ts(last_edge)                                     # 正好是上界
        out_late = _ts(last_edge + timedelta(seconds=1))            # 上界下一跳
        for ts in (out_early, in_start, in_night, in_next_day, in_end, out_late):
            _insert_at_ts(st, ts)

        repo = _repo(st, max_scan=240)
        events = repo.load_events(TimeRange(first_noon, last_edge))
        got = {_to_iso(e.ts) for e in events}
        assert got == {in_start, in_night, in_next_day, in_end}, sorted(got)
        assert out_early not in got and out_late not in got
        # 半截窗里夜间格也要出场（判据① 在窗口不整天对齐时同样成立）
        assert {_to_iso(e.ts)[11:13] for e in events} == {"12", "21", "09", "13"}
        assert repo.last_scan_truncated is False
    finally:
        st.close()
        os.remove(st.db_path)


def test_concentrated_day_uses_the_leftover_budget_with_one_extra_statement():
    """补读的代价上限：流量挤在一个小时、日配额远大于该小时的第一波 ⇒ **最多两条语句**。

    钉这一格是因为它同时是两件事的证据：
    * 功能——余量必须真被用掉（交回 `day_limit` 条，不是小时配额那 1 条）；
    * 代价——裁定 :43 说"Python 侧仍只收 967 行"，所以补读只能问一次，
      不能变成"每格各发一条"（那就退回 24 条/天，约束② 的耗时比不成立）。
    """
    st = _tmp_store()
    try:
        _insert_at_hour(st, D0, 20, 50)               # 傍晚一小时 50 条，其余时段空
        repo = _repo(st, max_scan=24)                 # 日配额 24 ⇒ 小时配额 1
        seen = []
        real_execute = repo._execute
        repo._execute = lambda sql, sql_params=(): (
            seen.append(sql) or real_execute(sql, sql_params))
        one_day = TimeRange(
            datetime.combine(D0, datetime.min.time()),
            datetime.combine(D0, datetime.min.time()).replace(hour=23, minute=59, second=59),
        )
        events = repo.load_events(one_day)
        assert len(seen) == 2, len(seen)
        assert len(events) == 24, len(events)
        stamps = [_to_iso(e.ts) for e in events]
        assert len(set(stamps)) == len(stamps), (
            "补读必须从上次取到的位置接着取（每格自己的 OFFSET），不许重复交行"
        )
        assert seen[1].count("UNION ALL") == 0, "补读只问被砍断的那一格 ⇒ 一条分支"
        assert repo.last_scan_truncated is True
    finally:
        st.close()
        os.remove(st.db_path)


def test_desc_order_keeps_latest_rows_inside_each_hour_bucket():
    """`order=desc` 决定**小时格内留哪一段**：被砍的格留下该格最晚的行，`asc` 留最早。

    形状：09/10 两点各 15 条，日配额 24 ⇒ 每点各交 12 条、两点都还有没交出的行。
    只有当"格内取向"和"格子数"分开钉住，才看得出 desc 不是把整天倒过来那么简单。
    """
    st = _tmp_store()
    try:
        _insert_at_hour(st, D0, 9, 15)                # 09:00 … 09:14
        _insert_at_hour(st, D0, 10, 15)               # 10:00 … 10:14
        repo = _repo(st, max_scan=24)                 # 日配额 24 ⇒ 小时配额 1
        one_day = TimeRange(
            datetime.combine(D0, datetime.min.time()),
            datetime.combine(D0, datetime.min.time()).replace(hour=23, minute=59, second=59),
        )
        asc = {_to_iso(e.ts)[11:19] for e in repo.load_events(one_day, order="asc")}
        assert len(asc) == 24, len(asc)
        assert asc == {"%02d:%02d:00" % (h, m) for h in (9, 10) for m in range(12)}, sorted(asc)
        desc = {_to_iso(e.ts)[11:19] for e in repo.load_events(one_day, order="desc")}
        assert desc == {"%02d:%02d:00" % (h, m) for h in (9, 10) for m in range(3, 15)}, sorted(desc)
        assert repo.last_scan_truncated is True
    finally:
        st.close()
        os.remove(st.db_path)


def test_scan_truncated_is_thread_local_on_a_shared_repository():
    """截断位是「本次切片的属性」，不是仓库实例的属性。

    `StoreRepository` 一个 facade 一个实例（`build_repository`），MCP/HTTP 并发与
    runtime 周期任务会在不同线程同时调 `load_events`。判据若挂在实例上，
    A 线程就会读到 B 线程刚写入的截断位 —— 对外 `scan_truncated` 张冠李戴，
    而 DCD 裁6 Q6-2 约束③要的就是这一格可信。
    """
    import threading

    st = _tmp_store()
    try:
        # 第 0 天 00 点 100 条（日配额 24 装不下，必定有行没交出），第 1~3 天各 3 条
        # 铺在不同小时（整天读得完，不许报截断）
        _insert_at_hour(st, D0, 0, 100)
        for i in (1, 2, 3):
            day = D0 + timedelta(days=i)
            for h in (7, 15, 22):
                _insert_at_hour(st, day, h, 1)

        repo = _repo(st, max_scan=96)                 # 日配额 24 ⇒ 小时配额 1
        busy_tr = _win(4)
        quiet_last = datetime.combine(D0 + timedelta(days=3), datetime.min.time())
        quiet_tr = TimeRange(
            quiet_last.replace(hour=0, minute=0, second=0),
            quiet_last.replace(hour=23, minute=59, second=59),
        )                                             # 单看第 3 天：三个小时格各 1 条，无溢出

        loaded_busy = threading.Event()
        loaded_quiet = threading.Event()
        seen = {}

        def _busy():
            repo.load_events(busy_tr)
            loaded_busy.set()
            loaded_quiet.wait(5)
            seen["busy"] = repo.last_scan_truncated

        def _quiet():
            loaded_busy.wait(5)
            repo.load_events(quiet_tr)
            loaded_quiet.set()
            seen["quiet"] = repo.last_scan_truncated

        t1 = threading.Thread(target=_busy)
        t2 = threading.Thread(target=_quiet)
        t1.start(); t2.start(); t1.join(10); t2.join(10)

        assert seen == {"busy": True, "quiet": False}, seen
    finally:
        st.close()
        os.remove(st.db_path)
