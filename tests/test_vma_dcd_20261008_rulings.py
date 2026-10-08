"""DCD `20261008-AF两件与MA四回执-裁定.md` 三件落码锁 + 判例 2（入口对等性）门。

三件裁定的共同形状是：**门面/文案对调用方的承诺必须与代码实际做的一致**。
- MA-29 乙：五支未实现的动作桩不得再声称成功（`ok=False` + `not_implemented` + `dry_run=True`，历史行 `dry_run=1`），
  G1 的豁免表随裁定清空 ⇒ 今后任何"带 TODO 却回 ok"的桩直接判红。
- MA-32 甲：设置页上那个不存在的开关（`auto_discover_persona`）整条删除，不留悬空引用。
- MA-34 甲：三个挖掘入口默认**只算不落**，要写库的调用方必须显式 `persist=True`；仓内每个调用点都受同一把锁检查。
- 判例 2：`scripts/scan_session_owner_parity.py` 把"读 sessionId 的入口 ↔ 有属主闸"做成门禁，
  正例（真树干净）与负例（缺闸判红）都要跑，缺闸这一格在合成夹具上必须真的响。
"""
import ast
import dataclasses
import io
import os
import subprocess
import sys
import tempfile

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent import config as config_module  # noqa: E402
from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.api.config_routes import WRITABLE_FIELDS  # noqa: E402
from memory_agent.rule_engine import ActiveRuleEngine  # noqa: E402
from memory_agent.store import Store  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STUB_TYPES = ("alert", "webhook", "tts", "light", "camera")
MINING_METHODS = ("mine_process", "mine_drift", "audit_rule_recall")
PARITY_GATE = os.path.join(ROOT, "scripts", "scan_session_owner_parity.py")
ACP_SERVER = os.path.join(ROOT, "src", "memory_agent", "acp_server.py")


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _field_names(cls):
    if hasattr(cls, "model_fields"):
        return set(cls.model_fields)
    return {f.name for f in dataclasses.fields(cls)}


@pytest.fixture
def engine():
    tmp = tempfile.mkdtemp(prefix="ma_dcd08_")
    st = Store(os.path.join(tmp, "t.db"), tz_offset_hours=8.0)
    st.init_schema()
    return ActiveRuleEngine(st)


def _rule(action_type, **action):
    body = {"type": action_type}
    if action_type == "webhook" and "url" not in action:
        body["url"] = "http://127.0.0.1/hook"   # 无 url 是配置错误，未实现那条路得带 url 才走得到
    body.update(action)
    return {"rule_id": "r-%s" % action_type, "name": "规则-%s" % action_type,
            "mode": "live", "action": body}


def _dry_run_rows(engine):
    with engine.store.transaction() as conn:
        total = conn.execute("SELECT COUNT(1) FROM rule_trigger_history").fetchone()[0]
        flagged = conn.execute(
            "SELECT COUNT(1) FROM rule_trigger_history WHERE dry_run=1").fetchone()[0]
    return total, flagged


# ── MA-29 乙：未实现就是未实现 ─────────────────────────────────────────────

@pytest.mark.parametrize("action_type", STUB_TYPES)
def test_five_unimplemented_actions_do_not_claim_success(engine, action_type):
    res = engine.execute_action(_rule(action_type), {"kind": "device"})
    assert res["ok"] is False, "%s 未实现却报成功" % action_type
    assert res["not_implemented"] is True
    assert res["dry_run"] is True
    assert res["dispatched"] is False


@pytest.mark.parametrize("action_type", STUB_TYPES)
def test_five_unimplemented_actions_log_history_as_dry_run(engine, action_type):
    engine.execute_action(_rule(action_type), {"kind": "device"})
    total, flagged = _dry_run_rows(engine)
    assert total == 1 and flagged == 1, "%s 的触发历史必须记成 dry_run=1" % action_type


def test_log_action_is_real_work_and_still_reports_success(engine):
    res = engine.execute_action(_rule("log"), {"kind": "device"})
    assert res["ok"] is True and "not_implemented" not in res
    total, flagged = _dry_run_rows(engine)
    assert total == 1 and flagged == 0, "记录日志本身就是该动作的全部工作，不该冒充未实现"


def test_webhook_without_url_is_configuration_error(engine):
    res = engine.execute_action(_rule("webhook", url=""), {"kind": "device"})
    assert res["ok"] is False and res.get("error") and "not_implemented" not in res


def test_execute_action_docstring_names_the_five_stubs():
    doc = ActiveRuleEngine.execute_action.__doc__ or ""
    assert "MA-29" in doc and all(t in doc for t in STUB_TYPES)


def test_stub_claims_gate_reports_zero_stub_and_zero_exemption():
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts",
                                                         "scan_stub_claims_success.py")],
                          capture_output=True, text=True, cwd=ROOT)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("ROOT=")]
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(lines) == 1, lines
    assert "STUBS=0" in lines[0] and "EXEMPT_TOTAL=0" in lines[0] \
        and "PROBLEM=0" in lines[0], lines[0]


# ── MA-32 甲：面板上那个开关并不存在，整条删掉 ─────────────────────────────

def test_config_has_no_auto_discover_persona_field():
    assert "auto_discover_persona" not in _field_names(config_module.Config)


def test_writable_fields_and_payload_no_longer_declare_the_key():
    assert "auto_discover_persona" not in set(WRITABLE_FIELDS)
    assert "auto_discover_persona" not in _read("src/memory_agent/api/config_routes.py")


def test_settings_page_has_no_dangling_switch():
    js = _read("src/memory_agent/static/js/pages/settings.js")
    assert "auto_discover_persona" not in js
    assert "家庭成员与行为画像" not in js, "删了开关却留着卡片 = 空壳面板"


def test_repo_has_no_lingering_reference_to_the_removed_key():
    """本文件自己写着这个键名（它就是锁），所以只允许它一处命中。"""
    hits = []
    for base in ("src", "tests", "scripts"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if not fn.endswith((".py", ".js", ".html")):
                    continue
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
                if rel == "tests/test_vma_dcd_20261008_rulings.py":
                    continue
                if "auto_discover_persona" in _read(rel):
                    hits.append(rel)
    assert hits == [], hits


# ── MA-34 甲：默认只算不落，写库要显式 ────────────────────────────────────

@pytest.mark.parametrize("method", MINING_METHODS)
def test_mining_entrypoints_default_to_no_write(method):
    import inspect
    params = inspect.signature(getattr(ActivityInferenceService, method)).parameters
    assert params["persist"].default is False, "%s 的 persist 默认仍在偷偷写库" % method


def test_behavior_routes_pass_persist_default_false_three_times():
    src = _read("src/memory_agent/api/behavior_routes.py")
    assert src.count('body.get("persist", False)') == 3
    assert 'body.get("persist", True)' not in src


def _calls_of(tree, attr_names):
    """返回 [(方法名, 该次调用的关键字名集合)]。

    两种写法都要认：直接 `rt.activity.mine_drift(...)`，以及卸载调用
    `asyncio.to_thread(rt.activity.mine_drift, ...)`（方法名出现在位置参数里）。
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        targets = []
        if isinstance(node.func, ast.Attribute) and node.func.attr in attr_names:
            targets.append(node.func.attr)
        for arg in node.args:
            if isinstance(arg, ast.Attribute) and arg.attr in attr_names:
                targets.append(arg.attr)
        for name in targets:
            out.append((name, kwargs))
    return out


def test_runtime_daily_tasks_opt_into_writing():
    tree = ast.parse(_read("src/memory_agent/runtime.py"))
    found = [name for name, kwargs in _calls_of(tree, set(MINING_METHODS))
             if "persist" in kwargs]
    assert sorted(found) == sorted(MINING_METHODS), \
        "日任务是要落库的调用方，三个都得显式写 persist=True：%s" % found


def test_every_in_repo_caller_states_persist_explicitly():
    """默认值翻了 ⇒ 任何"没写 persist"的调用点都是潜在静默改动，一把锁罩住全仓。"""
    offenders = []
    for rel in ("src/memory_agent/mcp_server.py", "src/memory_agent/runtime.py",
                "src/memory_agent/api/behavior_routes.py"):
        tree = ast.parse(_read(rel))
        for name, kwargs in _calls_of(tree, set(MINING_METHODS)):
            if "persist" not in kwargs:
                offenders.append("%s:%s" % (rel, name))
    assert offenders == [], offenders


# ── 判例 2：入口对等性门（正例 + 负例都要响）──────────────────────────────

def test_parity_gate_self_test_is_green():
    proc = subprocess.run([sys.executable, PARITY_GATE, "--self-test"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SELFTEST_MISS" not in proc.stdout and "SELFTEST_FALSE" not in proc.stdout


def test_parity_gate_live_tree_is_clean():
    proc = subprocess.run([sys.executable, PARITY_GATE, ACP_SERVER],
                          capture_output=True, text=True, cwd=ROOT)
    head = proc.stdout.strip().splitlines()[0]
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PROBLEM=0" in head and "STALE=0" in head, head
    assert int(head.split("READERS=")[1].split()[0]) >= 5, head


def test_parity_gate_flags_entry_that_lost_its_guard(tmp_path):
    """负例：把 M_CANCEL 的 check_owner 摘掉，门必须当场判红（而不是"名字对上了"就算过）。"""
    src = (
        "async def acp_handle(scope, params):\n"
        "    method = params.get('method')\n"
        "    if method == M_SESSION_NEW:\n"
        "        try:\n"
        "            return STORE.new(params.get('sessionId'))\n"
        "        except SessionOwnerConflict:\n"
        "            return 'occupied'\n"
        "    if method == M_CANCEL:\n"
        "        sid = params.get('sessionId')\n"
        "        return STORE.cancel(sid)\n"
    )
    path = tmp_path / "shim.py"
    path.write_text(src, encoding="utf-8", newline="\n")
    proc = subprocess.run([sys.executable, PARITY_GATE, str(path)],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "OWNER_GUARD_MISSING M_CANCEL" in proc.stdout, proc.stdout
