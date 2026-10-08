"""P2-1 运行时读数（A7 §六「13 项仍为静态结论」的那一格）：zip 静默截断逐站点取真读数。

改前/改后各跑一遍，同一份文件、同一组输入。只印数量与比值，不印任何文本内容。
用法：PYTHONPATH 由外部给（本机 = ../src，容器 = /tmp/<snap>/src）。
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("MA_SRC", os.path.join(_ROOT, "src")))

RC = 0


def say(tag, **kv):
    print(f"{tag} " + " ".join(f"{k}={v}" for k, v in kv.items()), flush=True)


# ── 站点 1/2：algo_kernel 的两个纯配对函数 ────────────────────────────────────
try:
    from memory_agent.algo_kernel import HmmActivityModel, compare_labelings

    # 10 条预测 vs 3 条参照：改前=只用前 3 条算一致率（静默丢掉 7 条），改后=判红
    try:
        r = compare_labelings(["idle"] * 10, ["idle"] * 3)
        say("S1_compare_labelings", n=r["n"], agreement=r["agreement"], raised="no")
    except ValueError as exc:
        say("S1_compare_labelings", raised="ValueError", detail=repr(exc)[:90])

    # map_states 只要 self.state_labels_，用 SimpleNamespace 当宿主即可
    ns = types.SimpleNamespace(state_labels_={})
    try:
        labels = HmmActivityModel.map_states(ns, [0, 1, 2, 3], ["a", "b"])
        say("S2_map_states", voted_states=len(labels), raised="no")
    except ValueError as exc:
        say("S2_map_states", raised="ValueError", detail=repr(exc)[:90])
except Exception as exc:  # noqa: BLE001
    say("S1_S2_IMPORT_FAIL", detail=repr(exc)[:120])
    RC = 1

# ── 站点 3：semantic_dedup 纯 Python 回退 vs numpy 的分歧 ─────────────────────
try:
    from memory_agent.semantic_dedup import SemanticDeduplicator as SemanticDedup

    v_long, v_short = [1.0, 2.0, 3.0], [1.0, 2.0]
    try:
        sim = SemanticDedup._cosine_similarity(v_long, v_short)
        say("S3_cosine_dim_mismatch", value=round(sim, 4), raised="no")
    except ValueError as exc:
        say("S3_cosine_dim_mismatch", raised="ValueError", detail=repr(exc)[:90])
    except Exception as exc:  # noqa: BLE001
        say("S3_cosine_dim_mismatch", raised=type(exc).__name__, detail=repr(exc)[:90])

    # 强制走纯 Python 回退：sys.modules['numpy']=None ⇒ 函数内 import numpy 抛 ImportError
    saved = sys.modules.get("numpy")
    sys.modules["numpy"] = None
    try:
        try:
            sim = SemanticDedup._cosine_similarity(v_long, v_short)
            say("S3b_cosine_fallback", value=round(sim, 4), raised="no")
        except ValueError as exc:
            say("S3b_cosine_fallback", raised="ValueError", detail=repr(exc)[:90])
        except Exception as exc:  # noqa: BLE001
            say("S3b_cosine_fallback", raised=type(exc).__name__, detail=repr(exc)[:90])
    finally:
        if saved is None:
            sys.modules.pop("numpy", None)
        else:
            sys.modules["numpy"] = saved

    # 等长必须照旧给出数值（这一档是"该不响"）
    same = SemanticDedup._cosine_similarity([1.0, 2.0], [1.0, 2.0])
    say("S3c_cosine_equal_len", value=round(same, 4))
except Exception as exc:  # noqa: BLE001
    say("S3_IMPORT_FAIL", detail=repr(exc)[:120])
    RC = 1

# ── 站点 4：history.semantic_search 的 docs/metas 配对（chroma 返回不等长）────
try:
    from memory_agent.history import HistoryManager as History

    class _RaggedCol:
        @staticmethod
        def query(**kwargs):
            return {
                "documents": [["d1", "d2", "d3"]],
                "metadatas": [[{"id": 1}, {"id": 2}]],
            }

    h = types.SimpleNamespace(collection=_RaggedCol())
    out = History.semantic_search(h, "q", n_results=5)
    say("S4_semantic_search_ragged", returned_rows=len(out), expected_docs=3)
    # 等长对照：三对三必须原样给三行
    class _AlignedCol:
        @staticmethod
        def query(**kwargs):
            return {
                "documents": [["d1", "d2", "d3"]],
                "metadatas": [[{"id": 1}, {"id": 2}, {"id": 3}]],
            }

    h2 = types.SimpleNamespace(collection=_AlignedCol())
    say("S4b_semantic_search_aligned", returned_rows=len(History.semantic_search(h2, "q")))
except Exception as exc:  # noqa: BLE001
    say("S4_IMPORT_FAIL", detail=repr(exc)[:120])
    RC = 1

# ── 站点 5：store.py 六处「硬编码列名 vs SELECT 实际列」是否已错位 ────────────
try:
    import tempfile
    from memory_agent.store import Store

    tmp = tempfile.mkdtemp(prefix="c29zip")
    st = Store(os.path.join(tmp, "t.db"))
    st.init_schema()

    # 直接问数据库：每个方法用的表，真实列数是多少（PRAGMA 一行 = 一列）
    def ncols(table):
        return len(st.db_query(f"PRAGMA table_info({table})"))

    for table in ("voice_answer_cache", "candidate_rules", "bug_reports", "agent_memories"):
        try:
            say("S5_schema", table=table, cols=ncols(table))
        except Exception as exc:  # noqa: BLE001
            say("S5_schema", table=table, detail=repr(exc)[:80])

    # 走真实方法：有数据时返回的字典键数必须等于表列数（错位=静默丢列）
    try:
        st.add_bug_report("tool_probe", "desc")
        got = st.list_bug_reports(status="all", limit=10)
        say("S5b_list_bug_reports", rows=len(got),
            keys=len(got[0]) if got else 0,
            table_cols=ncols("bug_reports"))
    except Exception as exc:  # noqa: BLE001
        say("S5b_list_bug_reports", detail=repr(exc)[:120])
    try:
        got = st.list_answer_cache(limit=10)
        say("S5c_list_answer_cache", rows=len(got), keys=len(got[0]) if got else 0)
    except Exception as exc:  # noqa: BLE001
        say("S5c_list_answer_cache", detail=repr(exc)[:120])
    try:
        got = st.list_negative_feedback(limit=10, with_question_only=False)
        say("S5d_list_negative_feedback", rows=len(got), keys=len(got[0]) if got else 0,
            table_cols=ncols("agent_memories"))
    except Exception as exc:  # noqa: BLE001
        say("S5d_list_negative_feedback", detail=repr(exc)[:120])
except Exception as exc:  # noqa: BLE001
    say("S5_IMPORT_FAIL", detail=repr(exc)[:160])
    RC = 1

print(f"PROBE_RC={RC}")
sys.exit(RC)
