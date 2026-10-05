"""A2/A7 静态结论转运行时读数第二批：P3-2「99 处 `timedelta(days=…)`」、P3-4「连接池无界」（2026-10-05，#53）。

审计原文给的是计数（「days 无边界防护」「`_CLIENT_POOL` 只增不减」）。本批把两边都换成
**真调用 + 真状态字**，结论仍然是「不能一刀切」：

**P3-2 改前 / 改后（同一份脚本 `.qoder/tmp-c30-days-pool-probe.py`，sha16=863f7354cce541c7，本机 Python 3.13）**

| 读数 | 改前 | 改后 |
|---|---|---|
| `resolve_nl_window("总结一下", 10**6)` | `OverflowError: date value out of range` | `note=最近3650天 span=3650.26` |
| `resolve_nl_window("总结一下", -5)` | 窗口**反向**（start>end），调用方拿到一段不存在的区间 | `note=最近0天 span=0.26 reversed=no` |
| 从问句里正则出来的 N：「最近 999999 天」 | `OverflowError` | `note=最近3650天`，且**报出的口径 = 真查的跨度** |
| 「最近 0 天」 | start>end 反向窗口 | `note=最近0天`，同一天 |
| `ask_memory(days=10**6)`（真 MCP 工具面） | 本机 `mcp_sdk_unavailable`，容器侧另取 | 同左 |

`days` 一共 99 个站点（run5 读数 `label_literal=41 / config=2 / date_math=6 / local=2 /
external=48`，`guard_bounded=44 / lo_only=0 / unguarded=55`，`SCAN_RC=0`）。
**48 处 external 全部收敛（44 处真带界 + 4 处带 `# day-ok:` 理由），51 处刻意不动**：
`days=1`、`now.weekday()`
这些值不受外部输入影响，给它们加上界只是噪音。更要紧的是**保留期与查询窗口不是一回事**——
把 `[1,3650]` 套到 `data_retention_days` 上，等于把「永久保留」悄悄改成「删掉十年前的数据」，
所以保留期/TTL 走 `LONG_WINDOW_MAX`（≈547 年，取值只为把日期算式留在 `datetime` 域内），
查询窗口走 `DAY_WINDOW_MAX=3650`。下面 `test_two_dispositions_are_not_interchangeable`
就是这条区分的锁。

⚠️ **DCD 20261005 §二.2 Q2 走乙之后，`learning_*` 八模块已移出 `src/` 存进 `attic/learning/`**
⇒ 上面那份 99 是 run5 的历史读数，现在的 src 侧册是 **95 站点**
（`external 44 / bounded 42 / unguarded 53 / marked 2`，`config/date_math/literal/local` 不变；
账要能对得上：**移走 4 处 = 2 处已带界（`learning_api` 的两个 dataclass 窗口）+ 2 处带 `# day-ok:` 标记**）。
`attic` 侧单独量一次是 `total=4 external=4 bounded=2 marked=2`——**它不在门禁口径里**
（`.gates.toml source_roots=["src"]`、pyflakes 只扫 `src/memory_agent`、Dockerfile 只 `COPY src/`），
所以**不许把 95 与 99 混成同一个数引用**，也不许因为 attic 有读数就说"册里还有裸用"。

**`config` 档在本批被收得过窄了**（同族第三处，见 `test_config_exemption_is_two_hops_not_one`）：
量具原先只要变量名叫 `config`/`cfg` 就划成"运维受控"，于是一批 HTTP 可写的键
（`vision_snapshot_retention_days`）和一个根本不是应用配置的 dataclass
（`LearningConfig.window_days`）一起免检。现在的两道前提是「键在 `config.Config` 字段表里」
且「键不在 `config_routes.WRITABLE_FIELDS` 里」，两张表都从真源解析、不复制清单。

`learning_api` 那两处只有**静态**收口、没有运行时锁：该模块与 `learning_*` 一族
作为 `memory_agent` 包成员根本 import 不进来（裸绝对导入，见下一段），运行时读数锁在
DCD 接线裁定之后。**裁定已回（20261005 §二.2 Q2=乙）**：整族移出 `src/` 存档，
所以这两处连"静态锁在 src 册里"都不成立了——它们在 attic，门禁不看，接回去时按 `attic/learning/README.md`
的三问重开。

**P3-4**：`D1 rot=30 → entries=8`（改前 entries=30 且只增不减）；`D2 同令牌 entries=1 reused=yes`
（第七轮买回来的收益没被弄坏）；`D3 created=31 → evicted=23 evicted_closed=23 keep_closed=False`
——淘汰方**真把旧连接关了**，而"最近用过的热连接"没被关。D3 的 `evicted_closed` 改前那条
读数是对着池内数的，恒为 0、不会响，属于量具自己的缺陷，已改成只统计离开池的那些
（口径变更点已在报告 §三十六 登记，改前该子项不可比）。

顺带被这条链抓到的（本机全模块导入烟雾测试，`MODULES_TRIED=127 IMPORT_BAD=7`）：
`learning_*` 一族 7 个模块用裸绝对导入互相引用（`from learning_models import …`），
**作为 `memory_agent` 包成员根本 import 不进来**，也没有任何 src/tests 模块引用它们——
这是死代码的可复现读数，已连同 P3-1 的 4 条未挂载路由一起写进 DCD 呈件，不自单删。
（同一烟雾测试也抓到本批自己写错的一处相对导入层数：`insights/parser/timeframe.py`
在 `parser/` 下，`..day_bounds` 会指向 `insights.day_bounds`，已改 `...day_bounds`。）
"""

import os
import sys
import types
from collections import OrderedDict
from datetime import datetime, timedelta

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory_agent.day_bounds import (DAY_WINDOW_MAX, DAY_WINDOW_MIN,
                                     LONG_WINDOW_MAX, clamp_days)  # noqa: E402
from memory_agent.store import Store, now_local  # noqa: E402

import scan_day_bounds as dayscan  # noqa: E402
import scan_route_mount as mtscan  # noqa: E402

HUGE = 10 ** 6          # 100 万天：改前必 OverflowError
HUGE_INT = 10 ** 400    # 连 float() 都会自己溢出的整数
NEG = -5


def _days_between(iso_start, iso_end):
    a = datetime.fromisoformat(iso_start)
    b = datetime.fromisoformat(iso_end)
    return (b - a).total_seconds() / 86400.0


def _note_days(meta_or_text):
    """从「最近N天」话术里取 N——报出的口径必须等于真查的窗口。"""
    text = meta_or_text.get("note") if isinstance(meta_or_text, dict) else meta_or_text
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


# ── clamp_days 单元 ─────────────────────────────────────────────────────────
def test_clamp_days_bounds_the_query_window():
    assert clamp_days(HUGE) == DAY_WINDOW_MAX == 3650
    assert clamp_days(NEG) == DAY_WINDOW_MIN == 1
    assert clamp_days(0) == 1                 # 0 天窗口会反转，落到下界
    assert clamp_days(30) == 30               # 正常值一字不改
    assert clamp_days(7) == 7


def test_clamp_days_survives_inputs_that_break_naive_float_casting():
    """`float(10**400)` 自己就 OverflowError——所以整数走独立分支。"""
    assert clamp_days(HUGE_INT) == DAY_WINDOW_MAX
    assert clamp_days(-HUGE_INT) == DAY_WINDOW_MIN
    assert clamp_days(float("inf")) == DAY_WINDOW_MAX
    assert clamp_days(float("-inf")) == DAY_WINDOW_MIN
    assert clamp_days(float("nan"), default=7) == 7
    assert clamp_days(None, default=14) == 14
    assert clamp_days("not-a-number", default=7) == 7
    assert clamp_days(True) == 1              # bool 是 int，不该被当字符串丢掉


def test_clamp_days_keeps_fractional_windows():
    assert clamp_days(2.5) == 2.5             # 半小时/半天口径不许被 int() 抹平
    assert clamp_days(0.5) == 1               # 越界才收，界内不动
    assert clamp_days(1e-9, lo=0) == pytest.approx(1e-9)
    # 夹到边界时 min/max 交出的是 int 边界本身：返回值类型要锁死，
    # 且不许依赖 `int.is_integer()`（那是 3.12+ 才有的方法，生产容器是 3.11）
    assert clamp_days(1e6) == 3650 and type(clamp_days(1e6)) is int


def test_two_dispositions_are_not_interchangeable():
    """保留期/TTL 与查询窗口是两种处置：把 3650 套到保留期上会**删数据**。

    `data_retention_days` 配 100000 天（≈273 年）= 事实上永久保留。若按查询口径
    clamp 到 3650，下一次周期清理就会把十年之前的历史整段删掉——这是把「修一个
    溢出」做成「丢一批数据」。
    """
    assert LONG_WINDOW_MAX > DAY_WINDOW_MAX
    assert clamp_days(100_000, hi=LONG_WINDOW_MAX) == 100_000
    # 天花板存在的唯一理由：让 `now ± N 天` 留在 datetime 的定义域内（双向都成立）
    now = now_local(8.0)
    assert datetime.min.year < (now - timedelta(days=LONG_WINDOW_MAX)).year
    assert (now + timedelta(days=LONG_WINDOW_MAX)).year <= datetime.max.year


# ── 真调用：改前会 OverflowError / 反向窗口的每一条路径 ──────────────────────
def test_resolve_nl_window_extremes_and_echo_consistency():
    from memory_agent.insights.utils import resolve_nl_window

    for days, want_note in ((7, 7), (DAY_WINDOW_MAX, DAY_WINDOW_MAX), (HUGE, DAY_WINDOW_MAX)):
        start, end, meta = resolve_nl_window("总结一下", days)
        assert _days_between(start, end) > 0            # 反向窗口=查不到东西还报成功
        assert _note_days(meta) == want_note            # 报出的口径 = 真查的跨度
        assert abs(_days_between(start, end) - want_note) < 1.5

    start, end, meta = resolve_nl_window("总结一下", NEG)   # 改前：start>end
    assert _days_between(start, end) >= 0
    assert _note_days(meta) == 0


def test_nl_window_parsed_out_of_the_question_text_is_bounded():
    """N 来自文本而不是形参——审计没报这一格，名字也不叫 days。"""
    from memory_agent.insights.utils import resolve_nl_window

    for q, want in (("最近 30 天发生了什么", 30),
                    ("最近 999999 天发生了什么", DAY_WINDOW_MAX),
                    ("最近 0 天发生了什么", 0)):
        start, end, meta = resolve_nl_window(q, 7)
        assert _note_days(meta) == want
        assert _days_between(start, end) >= 0


def test_voice_window_label_matches_the_window_it_scans():
    from memory_agent.voice_util import resolve_window

    today = datetime(2026, 10, 5, 12, 0, 0)
    for text, want in (("最近999999天", DAY_WINDOW_MAX), ("最近7天", 7), ("最近0天", 1)):
        w = resolve_window(text, today)
        assert w is not None
        assert _note_days(w["label"]) == want
        assert datetime.fromisoformat(w["start"]) <= datetime.fromisoformat(w["end"])
        assert abs(_days_between(w["start"], w["end"]) - (want - 1)) < 1.1


def test_timeframe_resolve_range_extremes():
    from memory_agent.insights.parser.timeframe import resolve_range

    now = datetime(2026, 10, 5, 12, 0, 0)
    for days, want in ((7, 7.0), (HUGE, float(DAY_WINDOW_MAX)), (NEG, 1.0)):
        tr = resolve_range(start="", end="", days=days, now=now)
        assert tr.start <= tr.end
        assert abs(tr.days - want) < 0.01


def test_insight_service_days_to_range_bounded():
    from memory_agent.insights.api import InsightService

    start, end = InsightService._days_to_range(None, HUGE, "", "")
    assert _days_between(start, end) == pytest.approx(DAY_WINDOW_MAX, abs=0.01)
    start, end = InsightService._days_to_range(None, NEG, "", "")
    assert _days_between(start, end) == pytest.approx(DAY_WINDOW_MIN, abs=0.01)
    assert InsightService._days_to_range(None, HUGE, "2026-01-01", "2026-01-07") == \
        ("2026-01-01", "2026-01-07")          # 显式区间优先，不被动过


def test_time_range_shift_stays_bidirectional():
    from memory_agent.insights.models import TimeRange

    base = TimeRange(datetime(2026, 10, 5), datetime(2026, 10, 6), "x")
    assert base.shift(0).start == base.start
    back = base.shift(HUGE)
    assert (back.start - base.start).days == -DAY_WINDOW_MAX
    fwd = base.shift(-HUGE)                   # 环比窗口向前移是合法用途
    assert (fwd.start - base.start).days == DAY_WINDOW_MAX
    assert base.shift(None).start == base.start   # 改前：timedelta(days=None) TypeError


def test_analysis_resolve_range_bounded():
    from memory_agent.analysis import AnalysisService

    host = types.SimpleNamespace(config=types.SimpleNamespace(tz_offset_hours=8.0))
    for days, want in ((HUGE, DAY_WINDOW_MAX), (NEG, DAY_WINDOW_MIN), (0, 7)):
        start, end = AnalysisService.resolve_range(host, "", "", days)
        assert abs(_days_between(start, end) - want) < 0.01


def test_summary_queries_window_bounded():
    from memory_agent.summary_queries import _resolve_window

    store = types.SimpleNamespace(tz_offset_hours=8.0)
    for days, want in ((HUGE, DAY_WINDOW_MAX), (NEG, DAY_WINDOW_MIN), (None, 7)):
        start, end = _resolve_window(store, "", "", days)
        assert _days_between(start, end) > 0
        assert abs(_days_between(start, end) - want) < 0.01


def test_history_range_bounded():
    from memory_agent.history import HistoryManager

    host = types.SimpleNamespace(config=types.SimpleNamespace(tz_offset_hours=8.0))
    for days, want in ((HUGE, DAY_WINDOW_MAX), (NEG, DAY_WINDOW_MIN), (14, 14)):
        start, end = HistoryManager._range(host, days)
        assert abs(_days_between(start, end) - want) < 0.01


def test_template_within_window_bounded():
    from memory_agent.template_validate import _within_window

    rt = types.SimpleNamespace(config=types.SimpleNamespace(tz_offset_hours=8.0))
    now = now_local(8.0)
    old = (now - timedelta(days=400)).isoformat(sep="T")
    assert _within_window(old, HUGE, rt) is True     # 改前：OverflowError 走 except → True
    assert _within_window(old, 30, rt) is False      # 正常口径不许被 clamp 顺手放宽
    assert _within_window(old, NEG, rt) is False


def test_change_attribution_lookback_bounded():
    from memory_agent.change_attribution import (analyze_conditional_causes,
                                                 counterfactual_query,
                                                 search_candidate_causes)
    ts = "2026-10-01T12:00:00"
    assert search_candidate_causes([], "p", ts, lookback_days=HUGE) == []
    out = analyze_conditional_causes([], "p", "温度", ts, lookback_days=HUGE)
    assert isinstance(out, dict)
    out2 = counterfactual_query([], "p", "温度", "turn_on", ts, lookback_days=HUGE)
    assert isinstance(out2, dict)
    for days in (HUGE, NEG):
        r = search_candidate_causes([], "p", ts, lookback_days=days)
        assert r == []


def test_daily_profile_window_bounded():
    from memory_agent.daily_profile import get_return_time_profile

    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return []

    store = types.SimpleNamespace(tz_offset_hours=8.0, list_behavior_events=_spy)
    out = get_return_time_profile(store, "成员1", days=HUGE)
    assert isinstance(out, dict)
    expect = (now_local(8.0) - timedelta(days=DAY_WINDOW_MAX)).strftime("%Y-%m-%d")
    assert seen["day_from"] == expect           # 取数窗口就是收敛后的那个


@pytest.fixture
def store(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    st.init_schema()
    yield st
    conn = getattr(st, "_conn", None)
    if conn is not None:
        conn.close()


def test_rule_engine_reports_the_window_it_queried(store):
    from memory_agent.rule_engine import ActiveRuleEngine

    eng = ActiveRuleEngine(store)
    for days, want in ((HUGE, DAY_WINDOW_MAX), (NEG, DAY_WINDOW_MIN), (30, 30)):
        stats = eng.get_rule_stats("no-such-rule", days)
        assert stats["days"] == want            # 回显口径 == 真查口径（越界不静默）
    assert eng.get_overall_stats(HUGE)["days"] == DAY_WINDOW_MAX


def test_mcp_attribution_fetch_survives_extremes(store):
    from memory_agent.mcp_server import _fetch_attribution_events

    assert _fetch_attribution_events(store, days=HUGE) == []
    assert _fetch_attribution_events(store, days=NEG) == []
    assert _fetch_attribution_events(store, days=3) == []


def test_member_schedule_window_bounded(store):
    out = store.member_schedule("成员1", days=HUGE)
    assert isinstance(out, dict)
    out_neg = store.member_schedule("成员1", days=NEG)
    assert isinstance(out_neg, dict)


def test_purge_mcp_audit_is_a_retention_window_not_a_query_window(store):
    """`keep_days` 是「留多久才删」，套上查询口径的 3650 就会把十年前的审计删掉。

    改前那一版我给它用了默认 `hi=DAY_WINDOW_MAX`——同一个批子里我自己写下的
    「两种处置不能混用」，被我自己违反了一处，这条就是那条反例锁。
    """
    conn = store.connect()
    conn.execute("INSERT INTO mcp_audit(ts,token_name,tool) "
                 "VALUES('2000-01-01T00:00:00','t','ask_memory')")
    conn.commit()

    assert store.purge_mcp_audit(keep_days=HUGE) == 0     # 100 万天保留 ≠ 删成 10 年
    q = conn.execute("SELECT COUNT(*) c FROM mcp_audit").fetchone()
    assert q["c"] == 1
    assert store.purge_mcp_audit(keep_days=30) == 1       # 该删的照删
    assert conn.execute("SELECT COUNT(*) c FROM mcp_audit").fetchone()["c"] == 0
    assert store.purge_mcp_audit(keep_days=NEG) == 0      # 负数不反向，也不再删（表已空）


def test_purge_old_keeps_what_retention_asks_and_still_deletes(store):
    """这一条是「修溢出不能顺手删数据」的反例锁。"""
    conn = store.connect()
    conn.execute("INSERT INTO events(id,ts,day,room,entity_id) "
                 "VALUES('e-1','2000-01-01T00:00:00','2000-01-01','客厅','sensor.a')")
    conn.commit()

    assert store.purge_old(0) == 0                       # 0 = 不清理（原语义不动）
    assert store.purge_old(HUGE) == 0                    # 100 万天保留 ≠ 删成 10 年
    assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 1
    assert store.purge_old(30) == 1                      # 该删的照删
    assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0


def test_agent_memory_ttl_and_feedback_bounded(store):
    mid = store.add_agent_memory("s-1", "内容", "topic", "[]", "[]", ttl_days=HUGE)
    row = store.get_agent_memory(mid) if hasattr(store, "get_agent_memory") else None
    if row is not None:
        exp = datetime.fromisoformat(row["expires_at"])
        assert exp > datetime(2200, 1, 1)                # 极值 TTL 不再 OverflowError
    assert store.record_agent_feedback(mid, True) is not None
    assert store.record_agent_feedback(mid, False, comment="没帮上") is not None


# ── HTTP 可写的配置键：`config` 不再是免检档（本批新发现的同一族漏洞）──────────
class _CutoffRecordingStore:
    """只记录服务层递下来的 `before_day`，不碰文件——量的是口径，不是磁盘。"""

    def __init__(self):
        self.cutoffs = []

    def clear_behavior_snapshots(self, before_day):
        self.cutoffs.append(before_day)
        return []


def _vision_with_retention(days):
    from memory_agent.vision_service import VisionService

    svc = VisionService.__new__(VisionService)      # 不起 go2rtc/VLM，只要这条清理链
    svc.config = types.SimpleNamespace(vision_snapshot_retention_days=days,
                                       tz_offset_hours=8, data_dir="/data")
    svc.store = _CutoffRecordingStore()
    return svc


def test_vision_snapshot_retention_extreme_does_not_overflow_or_delete():
    """`vision_snapshot_retention_days` 在 `config_routes.WRITABLE_FIELDS` 里。

    设置页一个 number 输入框就能把它写成 100 万天——改前这里直接
    `OverflowError`，周期任务整条抛异常；把它按 `config.*` 当"运维受控"放过，
    就是这个漏洞能在量具里隐身的原因。
    """
    svc = _vision_with_retention(HUGE)
    with pytest.raises(OverflowError):                   # 改前那一步：裸算式确实会崩
        now_local(8) - timedelta(days=HUGE)
    assert svc._cleanup_snapshots() == 0                 # 收口后不抛、且什么都没删
    cutoff = svc.store.cutoffs[0]
    assert int(cutoff[:4]) < 1600, cutoff                # ≈547 年前，不是"删到十年前"
    # 上界取的是保留期口径（LONG_WINDOW_MAX），不是查询窗口的 3650：
    # 混用的话 cutoff 会落在 3650 天前后，等于把"永久保留"悄悄改成"删掉十年内的快照"。
    assert cutoff < (now_local(8) - timedelta(days=DAY_WINDOW_MAX)).strftime("%Y-%m-%d")


def test_vision_snapshot_retention_normal_value_is_untouched():
    """收口不许顺手改小合法保留期：7 天还是 7 天。"""
    svc = _vision_with_retention(7)
    assert svc._cleanup_snapshots() == 0
    gap = (now_local(8).date() - datetime.fromisoformat(svc.store.cutoffs[0]).date()).days
    assert abs(gap - 7) <= 1                             # ±1：服务内部自己取的 now，跨午夜差一天


def test_vision_snapshot_retention_zero_still_means_no_cleanup():
    svc = _vision_with_retention(0)
    assert svc._cleanup_snapshots() == 0
    assert svc.store.cutoffs == []                       # 原语义：0 = 不触发清理


# ── P3-4：连接池有界 + 淘汰即真关 ────────────────────────────────────────────
class _FakeClient:
    """只关心两件事：谁被建出来、谁被关掉（状态字与 httpx 同名）。"""

    created = 0

    def __init__(self, *a, **kw):
        _FakeClient.created += 1
        self.id = _FakeClient.created
        self.is_closed = False

    def close(self):
        if self.raise_on_close:
            raise RuntimeError("拆除失败")
        self.is_closed = True

    raise_on_close = False


@pytest.fixture
def pool(monkeypatch):
    import httpx

    from memory_agent import ha_client
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    saved = OrderedDict(ha_client._CLIENT_POOL)
    ha_client._CLIENT_POOL.clear()
    yield ha_client
    ha_client._CLIENT_POOL.clear()
    ha_client._CLIENT_POOL.update(saved)


def _ha(ha_client, token, base="http://192.0.2.1:8123"):
    return ha_client.HAClient(types.SimpleNamespace(
        hass_server=base, hass_token=token, tz_offset_hours=8.0))


def test_pool_is_bounded_and_closes_what_it_evicts(pool):
    n = pool.POOL_MAX_ENTRIES
    made = []
    for i in range(n + 5):
        with _ha(pool, f"tok-{i}")._session() as c:
            made.append(c)
    assert len(pool._CLIENT_POOL) == n                    # 改前：与轮换次数同增
    evicted = [c for c in made if not any(c is p for p in pool._CLIENT_POOL.values())]
    assert len(evicted) == 5
    assert all(c.is_closed for c in evicted)              # 淘汰必须真关闭
    assert all(not c.is_closed for c in pool._CLIENT_POOL.values())


def test_pool_still_reuses_one_connection_per_token(pool):
    with _ha(pool, "stable")._session() as a:
        pass
    with _ha(pool, "stable")._session() as b:
        pass
    assert a is b and len(pool._CLIENT_POOL) == 1
    with _ha(pool, "other")._session() as c:
        pass
    assert c is not a                                     # 换令牌不能复用旧凭证的连接
    assert pool.POOL_MAX_ENTRIES >= 2                     # 上面这条断言的前提


def test_pool_lru_recycles_the_cold_entry_not_the_hot_one(pool):
    """被淘汰的必须是「最久没被回看」的那格——刚借出的、每轮都在用的都不许动。"""
    n = pool.POOL_MAX_ENTRIES
    for i in range(n):                                    # 先用冷连接填满
        with _ha(pool, f"cold-{i}")._session():
            pass
    with _ha(pool, "hot")._session() as hot:               # 插入即淘汰 cold-0
        pass
    assert len(pool._CLIENT_POOL) == n
    for i in range(n):
        with _ha(pool, f"churn-{i}")._session() as just_borrowed:
            pass
        with _ha(pool, "hot")._session():                 # 每轮回看一次热连接
            pass
        assert not just_borrowed.is_closed                # 刚借出的那条不许被拆
    assert any(hot is c for c in pool._CLIENT_POOL.values())
    assert not hot.is_closed


def test_pool_close_failure_does_not_break_the_borrower(pool, monkeypatch, capsys):
    """关不掉旧连接要留痕，但留痕里不许出现令牌（异常串会带 URL/头）。"""
    n = pool.POOL_MAX_ENTRIES
    attempts = []
    real_close = _FakeClient.close

    def _bad_close(self):
        attempts.append(self.id)
        real_close(self)              # 先照常回收资源，再模拟拆连接报错
        raise RuntimeError("secret-token-在异常串里 socket 拆除失败")

    monkeypatch.setattr(_FakeClient, "close", _bad_close)
    for i in range(n + 2):
        with _ha(pool, f"secret-token-{i}")._session():
            pass
    assert len(attempts) == 2                             # 确实尝试过关连接
    assert len(pool._CLIENT_POOL) == n                    # 关失败也不该把借方带崩
    out = capsys.readouterr().out
    assert "淘汰连接关闭失败" in out
    assert "secret-token" not in out and "socket 拆除失败" not in out


# ── 量具闸门：归属收口，不靠注释自觉 ─────────────────────────────────────────
def test_day_bounds_instrument_self_test_passes():
    assert dayscan.self_test() == 0


def test_every_timedelta_days_site_is_classified_and_guarded():
    rows = dayscan.scan_root(os.path.join(_ROOT, "src", "memory_agent"))
    counts = dayscan.counts_of(rows)
    assert counts["total_timedelta_days"] >= 90           # 站点群没被误删
    assert counts["label_external"] > 0
    assert counts["guard_lo_only"] == 0                   # 「只防负、不防极」不许存在
    problems = dayscan.problems_of(rows)
    assert problems == [], problems
    # 外部可达的站点要么收敛，要么带 `# day-ok: 理由`
    ext = [r for r in rows if r["label"] == "external"]
    assert all(r["guard"] == "bounded" or r["marker"] for r in ext)


def test_config_exemption_is_two_hops_not_one():
    """`config` 档的两道前提都要有读数：键在应用配置里，且不在 HTTP 可写白名单里。

    这条锁的是量具自己的一处假豁免：它原先只看"变量名叫 config/cfg"就把站点划成
    运维受控，于是 `vision_snapshot_retention_days`（设置页一个输入框就能写）和
    `LearningConfig.window_days`（模块自己的 dataclass，根本不是应用配置字段）
    一起躲过了检查。
    """
    rows = dayscan.scan_root(os.path.join(_ROOT, "src", "memory_agent"))
    by_detail = sorted(((r["file"].split("/")[-1], r["line"], r["label"], r["guard"],
                         r["detail"].split(":")[0])
                        for r in rows
                        if r["detail"].startswith(("writable-config:", "not-app-config:"))))
    assert by_detail == [
        ("vision_service.py", 504, "external", "bounded", "writable-config"),  # 可写键，已按保留期收口
    ], by_detail
    # 残余的 `config` 站点必须逐条是真·应用配置字段，且不在可写白名单里
    # （`attr:` 只列键名，携带键名的局部变量在 `var:` 段，别混进来）
    cfg_keys = {k for r in rows if r["label"] == "config"
                for k in r["detail"].split(" var:")[0][len("attr:"):].split(",") if k}
    assert cfg_keys == {"drift_retention_days", "process_mining_retention_days"}, cfg_keys
    assert cfg_keys <= dayscan.APP_CONFIG_KEYS, cfg_keys - dayscan.APP_CONFIG_KEYS
    assert not (cfg_keys & dayscan.WRITABLE_CONFIG_KEYS)
    # 白名单里的键必须都是 Config 字段：不是的话，设置页提交会被静默丢弃
    # （config_routes.py 顶部注释警告过的那种缺字段），这条读数就该响。
    assert dayscan.WRITABLE_CONFIG_KEYS <= dayscan.APP_CONFIG_KEYS
    assert len(dayscan.WRITABLE_CONFIG_KEYS) > 50             # 清单没被解析成空集/半集


def test_learning_sites_moved_to_attic_not_vanished():
    """DCD 20261005 Q2=乙 之后：那两处 `not-app-config` 是**移走**，不是**消失**。

    门禁不扫 attic，所以这条必须由测试自己拿读数证明"存档里那两处仍是
    external+bounded"，否则下一次有人把"src 册少了两条"读成"缺陷被消音"就无从对照。
    """
    attic = os.path.join(_ROOT, "attic", "learning")
    if not os.path.isdir(attic):
        pytest.skip("attic/learning 不在场（精简检出），存档形状无从比对")
    rows = dayscan.scan_root(attic)
    counts = dayscan.counts_of(rows)
    assert counts["total_timedelta_days"] == 4, counts
    assert counts["label_external"] == 4 and counts["label_config"] == 0, counts
    assert counts["guard_bounded"] == 2 and counts["marked"] == 2, counts
    assert dayscan.problems_of(rows) == [], dayscan.problems_of(rows)
    not_app = sorted((r["file"].split("/")[-1], r["line"]) for r in rows
                     if r["detail"].startswith("not-app-config:"))
    assert not_app == [("learning_api.py", 118), ("learning_api.py", 152)], not_app


def test_route_mount_instrument_self_test_passes():
    assert mtscan.self_test() == 0


def test_all_rule_writes_are_mounted_through_the_channel():
    """DCD 20261005 §二.2 Q1=乙+丁 落地后：P3-1 那 4 条缺口收口，未挂载归零。

    两向都锁：① 未挂载清单为空（再冒出一条就是范围扩散）；② 收口必须是「挂进
    R3 通道」这一种形状——路径与方法逐字核对。只锁 ① 的话，有人把
    ``behaviors_add_rule`` 挪回一条直写 ``active_rules`` 的路径上，量具照样绿，
    四条红线却已经被绕过。
    """
    rows = mtscan.scan(os.path.join(_ROOT, "src"))
    counts = mtscan.counts_of(rows)
    assert counts["handler_shaped"] >= 195
    assert counts["mounted"] >= 195
    assert counts["parse_fail"] == 0
    assert counts["unmounted"] == 0
    assert mtscan.problems_of(rows) == []

    from memory_agent.api import behavior_routes
    mounted = {(r.path, m) for r in behavior_routes.ROUTES for m in r.methods}
    assert {
        ("/api/behaviors/active-rules", "GET"),          # 只读面
        ("/api/behaviors/candidate-rules/add", "POST"),   # 写侧落候选
        ("/api/behaviors/candidate-rules/{rule_id}", "PUT"),
        ("/api/behaviors/candidate-rules/{rule_id}", "DELETE"),
        ("/api/behaviors/rule-channel/test-rule", "POST"),  # Q4=乙 的可达入口
    } <= mounted, sorted({
        ("/api/behaviors/active-rules", "GET"),
        ("/api/behaviors/candidate-rules/add", "POST"),
        ("/api/behaviors/candidate-rules/{rule_id}", "PUT"),
        ("/api/behaviors/candidate-rules/{rule_id}", "DELETE"),
        ("/api/behaviors/rule-channel/test-rule", "POST"),
    } - mounted)

    # 裁定原文给只读面的路径字面是 ``/api/behaviors/rules``，但那条早就被读 STATIC
    # 常量表的 ``behaviors_rules`` 占着——两个数据源不是一条路径装得下的。差异已登记，
    # 这条锁挡的是"为了对齐裁定字面"把引擎只读面盖上去，让常量表读数悄悄换成库表读数。
    static_route = [r for r in behavior_routes.ROUTES
                    if getattr(r, "path", "") == "/api/behaviors/rules"]
    assert len(static_route) == 1, [getattr(r, "path", "") for r in static_route]
    methods = set(static_route[0].methods)
    # Starlette 给 GET 自动带上 HEAD；除此以外必须是只读——这条路径一旦能吃写请求，
    # 常量表那一格就变成了可写面。
    assert "GET" in methods and methods <= {"GET", "HEAD"}, methods
    assert getattr(static_route[0].endpoint, "__name__", "") == "behaviors_rules"
