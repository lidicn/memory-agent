"""第十二/十四/十七/十九轮「建议加门禁」的四把量具：绿 + 不是空转 + 基线只准减。

审计员在原句里点名的不是某一条 bug，而是「这一族会再来」：
  第十四轮 :213-214 ⇒ scripts/scan_stub_claims_success.py（带 TODO 的桩照样回 ok:True）
  第十七轮 :153-154 ⇒ scripts/scan_source_of_truth_sync.py（文案说「真源=最新版本」，代码只判文件在不在）
  第十九轮 :153-154 ⇒ scripts/scan_cleanup_scheduled.py（清理函数没被排期 ⇒ 存量永不缩）
  第十二轮 :201-202 ⇒ scripts/scan_claimed_semantics.py（docstring 承诺的语义要有「登记 + 断言」两件套）

锁的口径随仓规：**import 并调用量具，不解析 stdout**——读数行给人看，判定用返回值。
每把门都带「不是空转」的一半：先证明它在这一版树上真扫到了候选，再证明候选全过；
否则 PROBLEM=0 可能只是「一个都没看见」。

基线只准减：`BASELINE_CAP` 钉住 scan_claimed_semantics.UNVERIFIED 的条数。补了一条真断言
就把对应行从 UNVERIFIED 删掉、移进 REGISTRY，并把 CAP 一起下调；CAP 永不下调 = 这条锁在假装工作。
"""

import importlib.util
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPTS = os.path.join(_ROOT, "scripts")
_SRC = os.path.join(_ROOT, "src", "memory_agent")
_TESTS = os.path.join(_ROOT, "tests")

#: 现读 2026-10-08（本机，同一棵树上两次读数）：
#:   首批  DECLARED=41 REGISTERED=15 CASES=17 UNVERIFIED=26 PROBLEM=0
#:   减三格后 DECLARED=41 REGISTERED=18 CASES=23 UNVERIFIED=23 PROBLEM=0
#:   减五格后 DECLARED=41 REGISTERED=23 CASES=36 UNVERIFIED=18 PROBLEM=0
#: 只准改小。
BASELINE_CAP = 18

#: 2026-10-08 减掉的三格：它们不许静默退回 UNVERIFIED（退回=那条真断言被人删了而台账不说）。
REDUCED_20261008 = (("repository.py", "health"),
                   ("repository.py", "activity_rules"),
                   ("mcp_server.py", "retrieve_agent_memories"))

GAUGES = (
    "scan_stub_claims_success",
    "scan_source_of_truth_sync",
    "scan_cleanup_scheduled",
    "scan_claimed_semantics",
)


def _load(stem):
    path = os.path.join(_SCRIPTS, stem + ".py")
    assert os.path.isfile(path), "量具不在盘上：%s" % path
    spec = importlib.util.spec_from_file_location(stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _test_names_in_tests():
    names = set()
    for dirpath, dirs, files in os.walk(_TESTS):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        names.update(f for f in files if f.endswith(".py"))
    return names


def test_four_gauges_on_disk_and_self_test_green():
    """四把门先在盘上，各自的 --self-test 全绿——自检抓的是「门自己会不会漏」。"""
    for stem in GAUGES:
        mod = _load(stem)
        assert hasattr(mod, "_self_test"), "%s 没有自检" % stem
        rc = mod._self_test()
        assert rc == 0, "%s 自检失败 rc=%s" % (stem, rc)


def test_stub_claims_gate_green_and_sees_the_stubs():
    gate = _load("scan_stub_claims_success")
    rows = gate.scan(_SRC)
    assert len(rows) >= 5, "只扫到 %d 个桩 = 量具失效，不是通过" % len(rows)
    assert gate.problems_of(rows) == []
    for r in rows:
        assert r["key"] in gate.EXEMPT, "带 TODO 却回 ok:True 的桩没有在册豁免：%s" % r["name"]
    stale = [k for k in gate.EXEMPT if k not in {r["key"] for r in rows}]
    assert not stale, "豁免名单里有已不存在的桩（基线只准减）：%s" % stale


def test_truth_gate_locks_seeded_version_compare():
    """MA-33 修好的那条：seed_builtin_skills 有跳过守卫，但守卫旁边必须有版本比较。

    这条是第十七轮 :153-154 的正身——一旦有人把 `disk_version >= bundled_version` 退回
    `if os.path.isfile(dst): return`，门会判红，而不需要有人再读一遍 docstring。
    """
    gate = _load("scan_source_of_truth_sync")
    rows = gate.scan(_SRC)
    assert len(rows) >= 9, "只扫到 %d 条真源声明 = 口径失效" % len(rows)
    assert gate.problems_of(rows) == []
    seed = [r for r in rows if r["name"] == "seed_builtin_skills"]
    assert seed, "seed_builtin_skills 已不在候选表：声明词被改写 ⇒ 该更新审计登记"
    assert seed[0]["guards"], "seed_builtin_skills 没有跳过守卫：那这条门就没在守它守的东西"
    assert seed[0]["version_compare"], "seed_builtin_skills 的跳过守卫又退化成「在就算新」"


def test_cleanup_gate_locks_resident_task_and_two_hop_chain():
    """第十九轮 :153-154：清理族必须落在周期形状里；两层传递也要认。

    `expire_overdue_agent_memories` 走的是两跳（store → sweep_promote_candidates →
    sweep_and_reconcile → 常驻循环），`purge_rule_triggers` 走一跳半。单跳口径曾把它们
    判成 NOT_SCHEDULED（真红误报）；这条锁钉住「按名字多层归因」这个能力本身。
    """
    gate = _load("scan_cleanup_scheduled")
    ranks, targets, by_name = gate.run_scan(_SRC)
    assert len(ranks) >= 9, "只认出 %d 个清理函数 = 前缀口径失效" % len(ranks)
    assert gate.problems_of(ranks, targets, by_name) == []
    assert ranks["_run_retention_cleanup"][0] == "RESIDENT_TASK", ranks["_run_retention_cleanup"]
    assert ranks["purge_old"][0] == "DIRECT_LOOP", ranks["purge_old"]
    assert ranks["expire_overdue_agent_memories"][0] == "VIA_CALLER"
    assert ranks["purge_rule_triggers"][0] == "VIA_CALLER"
    for name, (rank, _detail) in ranks.items():
        assert rank in ("DIRECT_LOOP", "RESIDENT_TASK", "VIA_CALLER") or name in gate.EXEMPT, name


def test_claim_gate_two_ledgers_cover_every_declaration():
    """第十二轮 :201-202：每条文案承诺都要落在「登记带用例」或「基线写明缺哪一半」。"""
    gate = _load("scan_claimed_semantics")
    decls, probs = gate.run(_SRC, _TESTS)
    assert probs == [], probs
    assert len(decls) >= 41, "只扫到 %d 条声明 = 候选口径失效" % len(decls)
    assert set(decls) == set(gate.REGISTRY) | set(gate.UNVERIFIED), (
        "有声明不在两本台账里，或台账里有条目对应不到声明（基线只准减）")
    listed = _test_names_in_tests()
    for key, entry in gate.REGISTRY.items():
        assert entry["cases"], "REGISTRY 条目两件套缺半：%s::%s" % key
        assert entry["claims"], "REGISTRY 条目没写它守哪条语义：%s::%s" % key
        for tfile, _tname in entry["cases"]:
            assert tfile in listed, "登记的测试文件不存在：%s" % tfile
    for key in gate.UNVERIFIED:
        assert key not in gate.REGISTRY, "同一函数同时进了两本台账：%s::%s" % key


def test_unverified_baseline_never_grows():
    """基线只准减：条数上限钉在 BASELINE_CAP，且每行都得写清「缺的是哪一半」。"""
    gate = _load("scan_claimed_semantics")
    assert len(gate.UNVERIFIED) <= BASELINE_CAP, (
        "UNVERIFIED 从 %d 涨到 %d：新增的声明要么补真断言进 REGISTRY，"
        "要么改写文案不再承诺该语义，不许加基线把 SCAN_RC 压绿" % (BASELINE_CAP, len(gate.UNVERIFIED)))
    for key, note in gate.UNVERIFIED.items():
        assert len(note) >= 12, "基线条目没写缺哪一半（空话=台账说谎）：%s::%s" % key


REDUCED_20261008_B = (("app.py", "_is_trusted_source"),
                      ("auth.py", "_trusted_proxy_networks"),
                      ("mcp_tokens.py", "migrate_legacy"),
                      ("task_record.py", "upsert_task_record"),
                      ("store.py", "make_event_id"))


def test_reductions_stay_registered_with_both_directions():
    """减下来的格子不许静默退回基线，也不许只剩单方向断言。

    单方向（只测"该红的时候红"）会让门形同虚设：`health` 改成恒 True、
    `activity_rules` 改成吞异常回 [] 都还能过一半用例。
    """
    gate = _load("scan_claimed_semantics")
    for key in tuple(REDUCED_20261008) + tuple(REDUCED_20261008_B):
        assert key in gate.REGISTRY, "%s 不在 REGISTRY：用例被删了却没人记账" % (key,)
        assert key not in gate.UNVERIFIED, "%s 又出现在基线：同一函数进了两本台账" % (key,)
        assert len(gate.REGISTRY[key]["cases"]) >= 2, (
            "%s 的用例数退到 %d：正例与对偶档必须成对" % (key, len(gate.REGISTRY[key]["cases"])))
