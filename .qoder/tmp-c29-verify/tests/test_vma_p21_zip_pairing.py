"""A2 §五 / A7 §六「P2-1：19 处 `zip()` 无 `strict`」——静态结论转运行时读数（2026-10-05，#52）。

审计原文只说「19 处 zip 无 strict，静态可确认，需构造长度不等的真实输入」。本批把那些
真实输入**构造出来了**（本机 Python 3.13 + 容器 3.11 两侧同判据），结论是：**19 处不能一刀切**。

| 站点 | 改前实测（同输入） | 改后实测 |
|---|---|---|
| `algo_kernel.compare_labelings`（10 条预测 vs 3 条参照） | `n=3 agreement=1.0`——样本削掉 70%，读数却写「一致率 100%」 | `ValueError: zip() argument 2 is shorter than argument 1` |
| `algo_kernel.HmmActivityModel.map_states`（4 状态 vs 2 标签） | 只有 2 个状态投出票，其余永远 `unknown`，零留痕 | `ValueError` |
| `semantic_dedup._cosine_similarity`（3 维 vs 2 维）**numpy 路径** | `ValueError: shapes (3,) and (2,) not aligned` | `ValueError: 向量维度不一致: 3 vs 2` |
| 同一函数**纯 Python 回退路径** | **0.5976**——一个合法、可信、完全错误的相似度 | 与 numpy 路径同判红 |
| `history.HistoryManager.semantic_search`（3 documents / 2 metadatas） | 返回 **2 行**（静默少一条命中） | 返回 0 行 + stdout 留痕「语义检索失败」 |
| `store` 三处硬编码列名 vs 真实 schema | `bug_reports` 10 列 / 返回 10 键 ⇒ **当前未错位**；`agent_memories` 表 24 列、投影 10 列（口径是投影，不是丢列） | 加 `strict` 后同样 10/10；将来 SELECT 与名单不同步时第一次调用就判红 |

**为什么 5 处必须保持不加 strict**：`zip(x, x[1:])` 是相邻两两比对（睡眠/离家静默窗口、
过程挖掘的转移边、反馈去重分组），两个参数**长度天生差 1**。照审计字面「19 处全加 strict」
修一次就引入 5 个每次必崩的点（本文件倒数第二条用例就是这个反例的真实读数）。
`entity_resolution._common_prefix_len` 同理：两条 id 比前缀，比到短的那条尽头即止，
用 `zip-pair-ok` 标记显式声明。

归属由 `scripts/scan_zip_pairing.py` 收口（`--self-test` 4 条正例全咬、5 条反例不响），
所以这个判断不靠注释自觉，也不靠人记得住哪些是滑窗。
"""

import os
import sys
import types

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory_agent.algo_kernel import HmmActivityModel, compare_labelings  # noqa: E402
from memory_agent.history import HistoryManager  # noqa: E402
from memory_agent.semantic_dedup import SemanticDeduplicator  # noqa: E402
from memory_agent.store import Store  # noqa: E402

import scan_zip_pairing as zipscan  # noqa: E402


# ── 配对函数：长度不等必须判红，等长必须照旧出数 ─────────────────────────────
def test_compare_labelings_rejects_ragged_pairing():
    with pytest.raises(ValueError):
        compare_labelings(["idle"] * 10, ["idle"] * 3)


def test_compare_labelings_still_scores_equal_length():
    """「该不响」档：等长时口径不变，n 就是样本数。"""
    r = compare_labelings(["idle", "study_work", "idle", "user_asleep", "idle"],
                          ["idle", "study_work", "user_asleep", "user_asleep", "idle"])
    assert r["n"] == 5
    assert abs(r["agreement"] - 4 / 5) < 1e-9   # 4 对 5：只有第 3 条不一致


def test_map_states_rejects_ragged_pairing():
    host = types.SimpleNamespace(state_labels_={})
    with pytest.raises(ValueError):
        HmmActivityModel.map_states(host, [0, 1, 2, 3], ["a", "b"])


def test_map_states_votes_every_hidden_state():
    host = types.SimpleNamespace(state_labels_={})
    labels = HmmActivityModel.map_states(host, [0, 1, 2, 3], ["a", "b", "a", "b"])
    assert len(labels) == 4                      # 改前这里只有 2 个状态有票
    assert labels[3] == "b"


# ── 两条计算路径对同一输入必须同判据 ─────────────────────────────────────────
def _force_pure_python():
    """把 sys.modules['numpy'] 置 None ⇒ 函数内 `import numpy` 抛 ImportError。"""
    saved = sys.modules.get("numpy")
    sys.modules["numpy"] = None

    def restore():
        if saved is None:
            sys.modules.pop("numpy", None)
        else:
            sys.modules["numpy"] = saved
    return restore


def test_cosine_rejects_dim_mismatch_on_both_paths():
    """两条路径都判红，而且报的是**同一条口径**。

    变异 M3 的第一版没咬住：只断言 `ValueError` 时，删掉显式维度校验后 numpy 路径
    自己会报 `shapes (3,) and (2,) not aligned`、回退路径的 `zip(..., strict=True)`
    会报 `argument 2 is shorter`——两个内部措辞照样让用例通过。所以这里锁的是
    「报的是哪一条」而不是「有没有抛」。
    """
    long_v, short_v = [1.0, 2.0, 3.0], [1.0, 2.0]
    with pytest.raises(ValueError, match=r"维度不一致"):
        SemanticDeduplicator._cosine_similarity(long_v, short_v)   # numpy 路径
    restore = _force_pure_python()
    try:
        with pytest.raises(ValueError, match=r"维度不一致"):
            SemanticDeduplicator._cosine_similarity(long_v, short_v)  # 纯 Python 回退
    finally:
        restore()


def test_cosine_two_paths_agree_on_equal_length():
    a, b = [1.0, 2.0, 2.0], [0.0, 3.0, 4.0]
    numpy_value = SemanticDeduplicator._cosine_similarity(a, b)
    restore = _force_pure_python()
    try:
        pure_value = SemanticDeduplicator._cosine_similarity(a, b)
    finally:
        restore()
    assert abs(numpy_value - pure_value) < 1e-12
    assert numpy_value > 0.0


def test_dedup_falls_back_to_exact_instead_of_crashing(capsys):
    """维度不等来自外部 embedding 服务，调用方必须降级并留痕，不是把异常抛给业务。"""
    def ragged_embed(texts):
        return [[1.0, 2.0, 3.0] if i % 2 == 0 else [1.0, 2.0] for i in range(len(texts))]

    dedup = SemanticDeduplicator(embedding_fn=ragged_embed)
    out = dedup.dedupe(["甲", "乙", "丙"])
    assert out["method"].startswith("exact")
    assert len(out["unique"]) == 3              # 宁可不判重，也不按假相似度删条目


# ── chroma ragged：不交半份结果当完整 ────────────────────────────────────────
class _Col:
    def __init__(self, docs, metas):
        self._docs, self._metas = docs, metas

    def query(self, **kwargs):
        return {"documents": [self._docs], "metadatas": [self._metas]}


def test_semantic_search_ragged_returns_nothing_and_leaves_a_trace(capsys):
    host = types.SimpleNamespace(collection=_Col(["d1", "d2", "d3"], [{"k": 1}, {"k": 2}]))
    out = HistoryManager.semantic_search(host, "q")
    captured = capsys.readouterr().out
    assert out == []
    assert "语义检索失败" in captured          # 改前：静默返回 2 行，什么都不印


def test_semantic_search_aligned_still_returns_all_rows():
    host = types.SimpleNamespace(
        collection=_Col(["d1", "d2", "d3"], [{"k": 1}, {"k": 2}, {"k": 3}]))
    out = HistoryManager.semantic_search(host, "q")
    assert len(out) == 3


# ── store 的「硬编码列名 vs SELECT」投影：现在没错位，将来错位会当场响 ────────
@pytest.fixture
def store(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    st.init_schema()
    yield st
    conn = getattr(st, "_conn", None)
    if conn is not None:
        conn.close()


def test_store_projections_carry_every_selected_column(store):
    store.add_bug_report("tool_probe", "描述")
    rows = store.list_bug_reports(status="all", limit=10)
    assert len(rows) == 1
    assert len(rows[0]) == len(store.db_query("PRAGMA table_info(bug_reports)"))
    assert "expected" in rows[0] and "actual" in rows[0]

    conn = store.connect()
    conn.execute(
        "INSERT INTO agent_memories(memory_id,session_id,text,created_at,updated_at,"
        "expires_at,feedback_down,feedback_question,feedback_comment) "
        "VALUES('m-1','s-1','内容','2026-10-01','2026-10-01','2026-12-01',1,'问题','备注')")
    conn.commit()
    neg = store.list_negative_feedback(limit=10, with_question_only=False)
    assert len(neg) == 1
    assert set(neg[0]) == {"memory_id", "text", "topic_key", "state", "trust",
                           "feedback_up", "feedback_down", "feedback_question",
                           "feedback_comment", "updated_at"}


def test_db_query_maps_positional_columns(store):
    got = store.db_query("SELECT 1 AS a, 2 AS b, 3 AS c")
    assert got == [{"a": 1, "b": 2, "c": 3}]


# ── 反方向的锁：滑窗加 strict 才是错的 ───────────────────────────────────────
def test_sliding_window_would_crash_if_strict_were_added():
    """审计「19 处无 strict」不能照字面一刀切——这条用例就是那 5 处的真实读数。"""
    events = [{"dt": 1}, {"dt": 2}, {"dt": 3}]
    assert len(list(zip(events, events[1:]))) == 2          # 现行写法：2 对
    with pytest.raises(ValueError):
        list(zip(events, events[1:], strict=True))          # 一刀切：每次必崩


def test_common_prefix_len_still_tolerates_unequal_strings():
    from memory_agent.entity_resolution import _common_prefix_len
    assert _common_prefix_len("living_room_light", "living_room") == 11
    assert _common_prefix_len("ab", "xy") == 0


# ── 门：全仓每个 zip 站点都要有归属，且量具自己先自证 ─────────────────────────
def test_zip_pairing_scanner_self_test_passes():
    assert zipscan.self_test() == 0


def test_every_zip_site_in_src_is_classified():
    counts, problems = zipscan.scan_root(os.path.join(_ROOT, "src", "memory_agent"))
    total = sum(counts.values())
    assert problems == []
    assert total > 0
    assert counts.get("unclassified", 0) == 0
    # 三类都在：说明门同时管住了「该加 strict」和「不该加 strict」两个方向
    assert counts.get("strict", 0) >= 13
    assert counts.get("window", 0) >= 5
    assert counts.get("marked", 0) >= 1


def test_scanner_bites_on_a_bare_zip(tmp_path):
    """新判红先找反例：不咬的门不算门。"""
    p = tmp_path / "bad.py"
    p.write_text("def f(a, b):\n    return list(zip(a, b))\n", encoding="utf-8", newline="")
    counts, problems = zipscan.scan_file(str(p))
    assert counts.get("unclassified") == 1
    assert problems and ":2:" in problems[0]


def test_scanner_bites_on_strict_added_to_a_window(tmp_path):
    p = tmp_path / "worse.py"
    p.write_text("def f(a):\n    return list(zip(a, a[1:], strict=True))\n",
                 encoding="utf-8", newline="")
    counts, problems = zipscan.scan_file(str(p))
    assert counts.get("CONFLICT:window+strict") == 1
    assert problems
