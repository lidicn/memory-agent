#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「文案声明的语义必须登记 + 配一条真断言的验证用例」的门（第十二轮建议门禁，报告 :201-202 原句）。

审计员原话：**「`check_claimed_semantics.py`：把 docstring 里出现"自增/递增/唯一真源/fail-closed/幂等"
的函数登记为待验证契约，配套一个断言脚本逐条跑；新增声明必须带验证用例。」**

为什么值得单独一门：第二期里反复出现的不是"代码写错"，而是**"代码自洽，但它不是文案说的那件事"**
（MA-26 的"自增 version"其实只从入参取基数；MA-27 的 fail-closed 其实只回公共记忆、响应却把自己
说成跨成员全量；MA-28 的 dry_run 在校验前就回"参数校验通过"；MA-33 的"真源=最新版本"其实只判
文件在不在）。普通断言抓不到这一族，因为被测函数从来没"报错"——不自洽的是它和文档之间的契约。

两件套口径（缺任何一半都不算修好）：
  候选 = 函数的 **docstring**（AST 取，注释行不算）里出现 CLAIM_WORDS 的任意一个。
  REGISTRY —— {(源文件名, 函数名): {"claims": [...], "cases": [(测试文件名, 用例名), …]}}
      只收"那条用例真的断言了那条语义"的条目（用例名自己就带着 fail_closed / idempotent /
      never_regresses / dedup / publishes_nothing_without_it 这类断言意图）。
      **拿"测试体里提到过这个名字"冒充验证用例 = 本门要判红的假登记**，所以登记是手写的，不自动填。
  UNVERIFIED —— 首批基线（存量声明里还没配到真断言的那些）。语义照仓规"基线只准减"：
      条目只能删（补了用例就移进 REGISTRY，或删除该声明文案），不许为把 SCAN_RC 压成 0 而新增；
      上限由 tests/test_vma_phase2_claims_gauge.py 的 `BASELINE_CAP` 钉住。
      每行必须自己写清"缺的是哪一半"，不许写"同上"——同一份锁会量 note 长度，因为基线的价值
      全在下一只手看得见该补什么；靠上下文才读得懂的登记等于没登记。
  判红：
    DECLARED_UNREGISTERED —— 有声明、两本台账都不在册 ⇒ "新增声明必须带验证用例"被打断。
    REGISTRY_WITHOUT_CASE —— 登记了却没写用例。
    CASE_NOT_FOUND —— 登记的用例在测试文件里找不到 `def <name>(` ⇒ 假登记/写错名/用例已被删。
    CLAIM_NOT_DECLARED —— 登记写了某声明词，docstring 里却没有 ⇒ 台账与文案脱节。
    CONTRACT_ORPHANED —— 登记的函数在源码里已经不存在。
    UNVERIFIED_* —— 基线条目的函数没了（必须删）或声明词已不成立（必须删）；不删就是台账说谎。

用例名口径（首跑真实扫描用四条假红换来的，放宽不可收回）：测试函数按
`^[ \\t]*(?:async[ \\t]+)?def[ \\t]+(test[^\\s(]+)[ \\t]*\\(` 收集 —— 必须允许缩进（本仓大量
`class Test…(unittest.TestCase)` 内的用例），也必须允许中文名（如
`test_list_agent_memories_public档不谎称_all`）。第一版按"首列 + 纯 ASCII"收，把四条**确实存在**
的用例判成 CASE_NOT_FOUND；量具若把真用例说成不存在，下一只手就会去删登记，门反而变瞎。

读数（同一棵树两次现读，2026-10-08）：
    首批      SRC=src/memory_agent DECLARED=41 REGISTERED=15 CASES=17 UNVERIFIED=26 PROBLEM=0 / SCAN_RC=0
    减三格后  SRC=src/memory_agent DECLARED=41 REGISTERED=18 CASES=23 UNVERIFIED=23 PROBLEM=0 / SCAN_RC=0
    减五格后  SRC=src/memory_agent DECLARED=41 REGISTERED=23 CASES=36 UNVERIFIED=18 PROBLEM=0 / SCAN_RC=0
    首批那次还把 retrieve_agent_memories 从 REGISTRY 踢进 UNVERIFIED：它的 403 分支没有直接断言，
    原先登记的用例其实断的是 service 层的另一件事——这正是本门要抓的"用例名字像、断的不是那条语义"。
    后来补了工具本体的两条用例（正例 403 + 空参数对偶档）才把它移回 REGISTRY。
    那两条要 mcp>=2.0 才取到已注册工具函数：本机旧 SDK ⇒ skip，容器（3.11 + mcp 2.x）实跑，
    权威档在容器那一侧；若两侧都 skip，这条登记就是假的，须退回 UNVERIFIED。

命名偏离登记：审计员给的名字是 `check_claimed_semantics.py`；本仓量具族是 `scan_*`
（scripts/ 现读 4 个 check_*.py（`git ls-files` 只 1 个在册）+ 12 个 scan_*.py，本轮新建的四把在内；
第二十轮 :322 说"你们已经有 15 个 check_*.py"与盘面对不上）。新量具随仓规落名 `scan_claimed_semantics.py`，偏离如实登记。

用法：
    python scripts/scan_claimed_semantics.py                  # 扫 src/memory_agent + tests
    python scripts/scan_claimed_semantics.py --self-test
    python scripts/scan_claimed_semantics.py --verbose        # 逐条打印声明 ↔ 台账对照
退出码：0 干净 / 1 有判红 / 2 量具自检失败
"""
import argparse
import ast
import io
import os
import re

#: 审计员点名的五个声明词（照抄，不自行加码，避免门越扫越宽变成第二个文案工程）。
CLAIM_WORDS = ("自增", "递增", "唯一真源", "fail-closed", "幂等")
SRC_DEFAULT = "src/memory_agent"
TESTS_DEFAULT = "tests"

#: 已配到"真断言用例"的契约。cases 里的名字必须在测试文件里现读得到。
REGISTRY = {
    ("mcp_server.py", "save_skill"): {
        "claims": ["自增", "唯一真源"],
        "cases": [("test_rounds11_19_ma25_35_fixes.py", "test_save_skill_version_never_regresses")],
    },
    ("perception_ingest.py", "ingest_event"): {
        "claims": ["fail-closed"],
        "cases": [("test_perception_ingest.py", "test_ingest_event_fail_closed")],
    },
    ("service_tokens.py", "scope_matches"): {
        "claims": ["fail-closed"],
        "cases": [("test_service_tokens.py", "test_scope_matches_fails_closed_on_empty")],
    },
    ("store.py", "purge_old"): {
        "claims": ["幂等"],
        "cases": [("test_db_purge_and_indexes.py", "test_purge_old_is_idempotent")],
    },
    ("store.py", "upsert_behavior_drift"): {
        "claims": ["幂等"],
        "cases": [("test_drift.py", "test_drift_upsert_idempotent_by_bucket_and_kind")],
    },
    ("store.py", "get_idempotency"): {
        "claims": ["幂等"],
        "cases": [("test_idempotency.py", "test_idempotency_roundtrip")],
    },
    ("store.py", "save_idempotency"): {
        "claims": ["幂等"],
        "cases": [("test_idempotency.py", "test_idempotency_roundtrip")],
    },
    ("store.py", "finalize_idempotency"): {
        "claims": ["幂等"],
        "cases": [("test_vma_r6_longrun_fixes.py", "test_c1_concurrent_same_key_executes_once"),
                  ("test_idempotency.py", "test_idempotency_roundtrip")],
    },
    ("store.py", "purge_idempotency"): {
        "claims": ["幂等"],
        "cases": [("test_vma_r6_longrun_fixes.py", "test_m1_purge_sweeps_stale_pending_rows")],
    },
    ("store.py", "insert_perception_event"): {
        "claims": ["幂等"],
        "cases": [("test_perception_ingest.py", "test_event_id_dedup")],
    },
    ("store.py", "upsert_signal_exclusion"): {
        "claims": ["幂等"],
        "cases": [("test_signal_learning.py", "test_store_signal_exclusion_crud")],
    },
    ("summary_queries.py", "member_daily_pattern"): {
        "claims": ["fail-closed"],
        "cases": [("test_summary_queries.py", "test_member_daily_fail_closed")],
    },
    ("feedback_pack.py", "build_feedback_pack"): {
        "claims": ["fail-closed"],
        "cases": [("test_feedback_pack.py", "test_snapshot_path_whitelist"),
                  ("test_feedback_pack.py", "test_label_path_traversal")],
    },
    ("mqtt_bridge.py", "publish_notify"): {
        "claims": ["fail-closed"],
        "cases": [("test_vma_step1_adm_presence.py",
                   "test_notify_requires_trace_id_and_publishes_nothing_without_it")],
    },
    ("runtime.py", "adm_caps"): {
        "claims": ["唯一真源"],
        "cases": [("test_vma_dcd_20261002_payload.py",
                   "test_adm_caps_version_is_the_plan_number_not_the_package_version")],
    },
    # 2026-10-08 减基线三格（UNVERIFIED 只准减）。前两格纯本机可跑；第三格（工具本体 403）
    # 需要 mcp>=2.0 才取到已注册工具函数：本机旧 SDK ⇒ skip，容器权威档实跑 ⇒ 登记时以此为准，
    # 若两侧都 skip 就是假登记，须退回 UNVERIFIED。
    ("repository.py", "health"): {
        "claims": ["fail-closed"],
        "cases": [("test_vma_a8_insights_clock_and_tags.py",
                   "test_health_is_false_when_the_query_raises_not_true_and_not_a_crash"),
                  ("test_vma_a8_insights_clock_and_tags.py",
                   "test_health_true_only_when_select_one_roundtrips_exactly")],
    },
    ("repository.py", "activity_rules"): {
        "claims": ["fail-closed"],
        "cases": [("test_vma_a8_insights_clock_and_tags.py",
                   "test_activity_rules_query_failure_raises_instead_of_faking_no_rules"),
                  ("test_vma_a8_insights_clock_and_tags.py",
                   "test_activity_rules_enabled_only_adds_the_where_clause")],
    },
    ("mcp_server.py", "retrieve_agent_memories"): {
        "claims": ["fail-closed"],
        "cases": [("test_rounds11_19_ma25_35_fixes.py",
                   "test_retrieve_agent_memories_tool_body_403s_when_scope_is_not_admin"),
                  ("test_rounds11_19_ma25_35_fixes.py",
                   "test_retrieve_agent_memories_tool_body_refuses_empty_question")],
    },
    # 2026-10-08 第二批减基线五格：这五个函数此前 tests 里 grep 零命中（"覆盖了"是别人替它背的）。
    # 每一格都配了 fail-open 那侧的对偶档（缺 IP/坏 CIDR/第二次执行/同键第二次/只差微秒），
    # 七腿变异（run31）逐条把对偶档退回去，全部必须红。
    ("app.py", "_is_trusted_source"): {
        "claims": ["fail-closed"],
        "cases": [("test_vma_phase3_claims_direct.py",
                   "test_is_trusted_source_accepts_only_loopback_rfc1918_and_ula"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_is_trusted_source_rejects_reserved_documented_and_link_local_ranges"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_is_trusted_source_fails_closed_when_the_ip_is_absent_or_unparsable")],
    },
    ("auth.py", "_trusted_proxy_networks"): {
        "claims": ["fail-closed"],
        "cases": [("test_vma_phase3_claims_direct.py",
                   "test_trusted_proxy_networks_keeps_the_valid_items"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_trusted_proxy_networks_skips_unparsable_items_without_trusting_everyone"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_trusted_proxy_networks_cache_follows_the_config_string")],
    },
    ("mcp_tokens.py", "migrate_legacy"): {
        "claims": ["幂等"],
        "cases": [("test_vma_phase3_claims_direct.py",
                   "test_migrate_legacy_replaces_plaintext_with_hash_and_never_keeps_the_secret"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_migrate_legacy_second_run_migrates_nothing_and_changes_nothing"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_migrate_legacy_does_not_duplicate_the_default_token_on_rerun")],
    },
    ("task_record.py", "upsert_task_record"): {
        "claims": ["幂等"],
        "cases": [("test_vma_phase3_claims_direct.py",
                   "test_upsert_task_record_same_key_twice_stays_one_row_and_takes_the_new_data"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_upsert_task_record_other_period_key_is_a_new_row")],
    },
    ("store.py", "make_event_id"): {
        "claims": ["幂等"],
        "cases": [("test_vma_phase3_claims_direct.py",
                   "test_make_event_id_is_a_stable_sha1_for_the_same_input"),
                  ("test_vma_phase3_claims_direct.py",
                   "test_make_event_id_keeps_events_that_differ_only_by_subsecond_timestamp")],
    },
}

#: 首批基线：声明在场、但还没配到"真断言那条语义"的用例。只准减，上限见 tests 里的 BASELINE_CAP。
#: 每条写清"缺的是哪一半"，补用例时就地移进 REGISTRY 并从这里删除。
UNVERIFIED = {
    ("activity_inference.py", "mine_process"): "幂等只写在文案里，现有用例走整链而非重复喂同一批",
    ("activity_inference.py", "mine_drift"): "同上：漂移挖掘的重复喂入没有直接断言",
    ("api.py", "_closed_ok"): "门面辅助函数，只被上层用例间接覆盖",
    ("api.py", "anomaly_report"): "fail-closed 分支没有单独用例（异常面本身有用例）",
    ("app.py", "metrics_ingest_endpoint"): "幂等需重复 POST 断言，现无",
    ("behavior_routes.py", "behaviors_feedback_pack"): "路由面 fail-closed 由 feedback_pack 的用例承担，路由本体无",
    ("behavior_routes.py", "behaviors_bad_case_export"): "路由本体的「拒了就不写盘」无直接断言，出境面白名单在 feedback_pack 那一侧有用例",
    ("insights_legacy.py", "data_quality_issues"): "声明词指向计数口径，现有用例只锁键位（不是同一条语义）",
    ("mcp_server.py", "get_member_daily_pattern"): "门面转发，fail-closed 断言在 summary_queries 那一侧",
    ("mcp_server.py", "trigger_incremental_collection"): "幂等需连续两次触发的断言，现无",
    ("mcp_server.py", "_idem_result"): "幂等缓存的内部件，断言在外层 c1 用例",
    ("mcp_server.py", "_idem_reserve"): "内部预留件：重复预留同一键的行为只被外层 c1 并发用例间接断到，本体无直接断言",
    ("perception_ingest.py", "from_ha_event"): "现有 test_from_ha_event 只断形状，不断重复喂同事件的幂等",
    ("perception_ingest.py", "gate_promote_to_behavior"): "晋升面的自增/幂等待重复晋升不叠加的用例",
    ("store.py", "save_arena_snapshot"): "递增（seq/版本）无直接断言",
    ("store.py", "insert_behavior_event"): "自增 id 断言依赖上层用例，本体无",
    ("store.py", "get_behavior_event"): "声明词与现有用例不同条语义",
    ("store.py", "_idem_now"): "时钟辅助件，无独立断言必要但登记以封住新增",
}


def _docstrings(path):
    try:
        tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return [], []
    docs, funcs = [], set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
            docs.append((node.name, node.lineno, ast.get_docstring(node) or ""))
    return docs, funcs


def scan_decls(src_root):
    """({(文件名, 函数名): [(行号, 声明词)]}, {(文件名, 函数名): 定义处数})"""
    decls, all_funcs = {}, {}
    for dirpath, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            docs, funcs = _docstrings(os.path.join(dirpath, fn))
            for name in funcs:
                all_funcs[(fn, name)] = all_funcs.get((fn, name), 0) + 1
            for name, line, doc in docs:
                hits = [w for w in CLAIM_WORDS if w in doc]
                if hits:
                    decls.setdefault((fn, name), []).append((line, hits))
    return decls, all_funcs


def _test_defs(tests_root):
    out = {}
    if not os.path.isdir(tests_root):
        return out
    for dirpath, dirs, files in os.walk(tests_root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            text = io.open(os.path.join(dirpath, fn), encoding="utf-8",
                           errors="replace").read()
            # 口径两处必须放宽，否则真用例存在却报 CASE_NOT_FOUND（假红）：
            #   ①本仓大量用例写在 `class Test…(unittest.TestCase)` 里，def 是缩进的；
            #   ②用例名允许中文（如 test_list_agent_memories_public档不谎称_all），不能只匹配 ASCII。
            out.setdefault(fn, set()).update(
                re.findall(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test[^\s(]+)[ \t]*\(", text, re.M))
    return out


def check(decls, all_funcs, tests_root, registry=None, unverified=None):
    registry = REGISTRY if registry is None else registry
    unverified = UNVERIFIED if unverified is None else unverified
    probs = []
    tdefs = _test_defs(tests_root)

    def declared_words(key):
        return {w for _l, hits in decls.get(key, []) for w in hits}

    for key in sorted(decls):
        if key in registry or key in unverified:
            continue
        line, hits = decls[key][0]
        probs.append("DECLARED_UNREGISTERED %s:%d %s 声明=%s ⇒ 必须进 REGISTRY（配用例）或 UNVERIFIED（写缺哪一半）"
                     % (key[0], line, key[1], "/".join(hits)))

    for key in sorted(registry):
        fn, name = key
        entry = registry[key]
        if (fn, name) not in all_funcs:
            probs.append("CONTRACT_ORPHANED %s::%s 源码里已找不到该函数" % (fn, name))
            continue
        present = declared_words(key)
        if not present:
            probs.append("CLAIM_NOT_DECLARED %s::%s 函数在，但 docstring 已不声明任何登记词" % (fn, name))
        for c in entry.get("claims", []):
            if c not in present:
                probs.append("CLAIM_NOT_DECLARED %s::%s 登记声明「%s」，docstring 里没有" % (fn, name, c))
        cases = entry.get("cases", [])
        if not cases:
            probs.append("REGISTRY_WITHOUT_CASE %s::%s 登记了契约却没写验证用例（两件套缺半）" % (fn, name))
        for tfile, tname in cases:
            if tfile not in tdefs:
                probs.append("CASE_NOT_FOUND %s::%s 的测试文件不存在：%s" % (fn, name, tfile))
            elif tname not in tdefs[tfile]:
                probs.append("CASE_NOT_FOUND %s::%s 登记的用例 %s 不在 %s 里" % (fn, name, tname, tfile))

    for key in sorted(unverified):
        fn, name = key
        if (fn, name) not in all_funcs:
            probs.append("UNVERIFIED_ORPHANED %s::%s 基线里的函数已不存在 ⇒ 必须删除该行（基线只准减）"
                         % (fn, name))
        elif not declared_words(key):
            probs.append("UNVERIFIED_UNDECLARED %s::%s 基线在册但 docstring 已不声明任何声明词 ⇒ 删除该行"
                         % (fn, name))
    return probs


def run(src_root, tests_root, registry=None, unverified=None):
    decls, all_funcs = scan_decls(src_root)
    return decls, check(decls, all_funcs, tests_root, registry, unverified)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--tests", default=TESTS_DEFAULT)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    decls, probs = run(args.src, args.tests)
    print("SRC=%s DECLARED=%d REGISTERED=%d CASES=%d UNVERIFIED=%d PROBLEM=%d"
          % (args.src, len(decls), len(REGISTRY),
             sum(len(e.get("cases", [])) for e in REGISTRY.values()),
             len(UNVERIFIED), len(probs)))
    if args.verbose:
        for key in sorted(decls):
            if key in REGISTRY:
                mark = "登记"
                info = len(REGISTRY[key].get("cases", []))
            elif key in UNVERIFIED:
                mark = "基线"
                info = UNVERIFIED[key]
            else:
                mark = "漏册"
                info = ""
            line, hits = decls[key][0]
            print("  %s %-24s:%-5d %-32s 声明=%-18s %s"
                  % (mark, key[0], line, key[1], "/".join(hits), info))
    for p in probs:
        print(p)
    if probs:
        print("SCAN_RC=1 判红 %d 条" % len(probs))
        return 1
    print("SCAN_RC=0 每条声明语义都在台账内（登记带用例，或基线写明缺哪一半）")
    return 0


FIXTURE_SRC = {
    "f_declared_unregistered.py": (
        "def bump(a):\n"
        "    \"\"\"version 自增。\"\"\"\n"
        "    return a + 1\n"
    ),
    "f_claim_retracted.py": (
        "def close_it(a):\n"
        "    \"\"\"按开关关闭，出错时也照样返回结果。\"\"\"\n"
        "    return a\n"
    ),
    "f_nocase.py": (
        "def cache_it(a):\n"
        "    \"\"\"幂等返回。\"\"\"\n"
        "    return a\n"
    ),
    "f_orphan.py": (
        "def still_here(a):\n"
        "    \"\"\"幂等返回。\"\"\"\n"
        "    return a\n"
    ),
    "f_reworded.py": (
        "def plain_result(a):\n"
        "    \"\"\"直接返回。\"\"\"\n"
        "    return a\n"
    ),
}
FIXTURE_REGISTRY = {
    ("f_claim_retracted.py", "close_it"): {"claims": ["fail-closed"],
                                           "cases": [("test_present.py", "test_real_case"),
                                                     ("test_present.py", "test_缩进的中文用例名")]},
    ("f_gone.py", "vanished"): {"claims": ["幂等"], "cases": []},
    ("f_nocase.py", "cache_it"): {"claims": ["幂等"], "cases": []},
}
FIXTURE_UNVERIFIED = {
    ("f_orphan.py", "still_here"): "基线在册且声明在场（正例，不该判红）",
    ("f_reworded.py", "plain_result"): "基线在册，但文案改写后已不声明任何声明词 ⇒ 该删行",
    ("f_baseline_gone.py", "removed"): "基线条目对应的函数已删 ⇒ 该判红",
}


def _self_test():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        tests = os.path.join(tmp, "tests")
        os.makedirs(src)
        os.makedirs(tests)
        for name, body in FIXTURE_SRC.items():
            with io.open(os.path.join(src, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        with io.open(os.path.join(tests, "test_present.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("def test_real_case():\n    assert True\n\n\n"
                     "class TestShape:\n"
                     "    def test_缩进的中文用例名(self):\n"
                     "        assert True\n")
        decls, probs = run(src, tests, FIXTURE_REGISTRY, FIXTURE_UNVERIFIED)
        text = "\n".join(probs)
        want = {
            "DECLARED_UNREGISTERED": "f_declared_unregistered 有声明却没进任何台账",
            "CLAIM_NOT_DECLARED": "close_it 的 docstring 不再声明 fail-closed",
            "CONTRACT_ORPHANED": "登记的 f_gone::vanished 在源码里已不存在",
            "REGISTRY_WITHOUT_CASE": "cache_it 登记了契约却没有用例",
            "UNVERIFIED_ORPHANED": "基线里的 f_baseline_gone::removed 函数已删",
            "UNVERIFIED_UNDECLARED": "plain_result 文案改写后不再声明，基线该删行",
        }
        for code, label in want.items():
            if code in text:
                print("SELFTEST_HIT %s（%s）" % (code, label))
            else:
                print("SELFTEST_MISS %s 没抓到（%s）⇒ 这一类漂移会漏" % (code, label))
                ok = False
        # 改写文案要判红，但"函数在、声明也在"的基线正例绝不能一起被判红（否则减基线永远减不动）
        # 用例名放宽口径必须见效：类内缩进 + 中文名字的用例都在册，不该判成假登记
        false_missing = [ln for ln in probs if "CASE_NOT_FOUND" in ln]
        if false_missing:
            print("SELFTEST_FALSE CASE_NOT_FOUND 误伤在册用例（缩进/中文名口径没放宽）：%s"
                  % false_missing[0])
            ok = False
        else:
            print("SELFTEST_CLEAN 在册用例全部现读得到（含类内缩进与中文名）")
        bad = [ln for ln in probs
               if "UNVERIFIED_UNDECLARED" in ln and "still_here" in ln]
        if bad:
            print("SELFTEST_FALSE UNVERIFIED_UNDECLARED 误伤正例 still_here：%s" % bad[0])
            ok = False
        else:
            print("SELFTEST_CLEAN 基线正例不误报")
        if len(decls) != 3:
            print("SELFTEST_FALSE 候选数=%d 期望=3（docstring 口径漏收或多收）" % len(decls))
            ok = False
        else:
            print("SELFTEST_CLEAN 候选=%d（注释不计，只算 docstring）" % len(decls))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
