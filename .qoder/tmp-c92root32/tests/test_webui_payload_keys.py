"""回归锁 #77：WebUI 前端**读的键** ↔ HTTP 路由**实际返回的键**。

**钉的是什么**：`static/js/pages/*.js` 里 `res.foo` 读的键，必须在对应路由 handler 的载荷顶层真的存在。
这一族缺陷（"实现改了、消费方没跟着改"）在**给人看的界面**上最阴：JS 读不存在的键不抛异常，
只是 `undefined`，于是那块面板静默变空白/`—`，日志里一个字都没有——用户看到的是"功能没了"。
本轮用尺扫过 100 个 api.js 方法 / 208 条路由 / 67 个 JS 读点，**今天没有出货缺陷**（HARD=0），
12 条"只有走通 helper 边才判绿"的读键逐条按 `file:line` 抽查过（台账 §五十）。
这条锁把今天的绿钉住：日后任何一侧改键名而另一侧没跟，这里就红。

**为什么不解析 stdout**：`scripts/scan_webui_payload_keys.py:main()` 是给人看的报表，
`analyse()` 才是判据（返回分格明细）。首版证据字符串在 `analyse` 里就 `[:3]` 截断了，
那会让"某条边是否被钉住"取决于同路由上别名的边有几条 ⇒ 截断挪到打印，锁读全量元组。

**每条锁的对偶**：
- 第 1 条防止"尺扫了个空却写假读数"（面数地板 + `unparsed==0`）；§四十九 六 那一格的老毛病。
- 第 2 条是缺陷门本身：HARD / SHAPE / NESTED_MISS / ORPHAN 四格归零。
- 第 3 条把 12 条 helper 边的**生产方**按 `文件#类.方法` 钉住（不钉行号：行号会在无关改动里漂，
  而"键名/符号改名"才是这条锁要抓的事）。
- 第 4 条让尺自己也被咬：五条控制腿必须在 pytest 里真跑过（L5 = 把生产方键名改掉 ⇒ 必须响）。
- 第 5 条钉尺的**口径前提**：`deps.py` 的 `ok()`/`error()` 信封语义。信封一改，本尺的解包规则就作废，
  锁先红一次，逼着重新校准，而不是悄悄把新语义当旧语义判绿。
- 第 6 条钉"承认看不清"的那 5 个面：新增盲区必须显式登记，不许混进绿灯。

**响过的证据**（副本树里打补丁、真树不动）：把 `agent_memory.py:702` 的 `"sweep"` 改名成 `"sweep_moved"` 再跑尺
⇒ `agentMemorySweep` 那条 helper 边判绿消失（green 行 0 条）、同一条读键落进 HARD
⇒ 第 2 条与第 3 条同时会红。把 `deps.py:ok()` 的 `**data` 展开摘掉再跑第 5 条 ⇒ `spread==[]`，锁也红。
"""

import ast
import importlib.util
import os

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location(
    "scan_webui_payload_keys", os.path.join(_REPO, "scripts", "scan_webui_payload_keys.py"))
assert _spec and _spec.loader
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

_DEPS = os.path.join(_REPO, "src", "memory_agent", "api", "deps.py")


@pytest.fixture(scope="module")
def faces():
    return scan.analyse()


def _edge(evidence, rel, symbol):
    """证据串形如 `src/memory_agent/store.py:4996#Store.member_insight_feedback`，行号不参与比对。"""
    for e in evidence:
        head, _, tag = e.partition("#")
        path = head.rsplit(":", 1)[0]
        if path == rel and tag in (symbol, symbol + "(嵌套)"):
            return True
    return False


def test_faces_are_present(faces):
    """尺必须真的读到了树：面数为 0 ⇒ 取数根坏了，而"什么都读不到"会伪装成"一切正常"。"""
    assert faces["unparsed"] == 0, "api.js 里有方法解不出 verb/path：%d 条" % faces["unparsed"]
    assert faces["api_methods"] >= 90, faces["api_methods"]
    assert faces["route_entries"] >= 180, faces["route_entries"]
    assert faces["js_callsites"] >= 55, faces["js_callsites"]
    assert faces["defs_indexed"] >= 1200, faces["defs_indexed"]
    assert faces["handlers_with_helper_edge"] >= 60, faces["handlers_with_helper_edge"]


def test_no_page_reads_a_key_the_route_does_not_return(faces):
    """缺陷门：前端读的键，既解不到顶层、也不在 handler 函数体宇宙里 ⇒ 面板静默空白。"""
    assert faces["hard"] == [], "HARD（首要嫌疑）：%s" % (faces["hard"],)
    assert faces["shape"] == [], "SHAPE（嵌套/分支形状待判）：%s" % (faces["shape"],)
    assert faces["nested_miss"] == [], "NESTED_MISS（列表元素读键）：%s" % (faces["nested_miss"],)
    assert faces["orphan_callsites"] == [], "ORPHAN（页面调了 api.js 没有的方法）：%s" % (
        [(r["meth"], r["file"], r["line"]) for r in faces["orphan_callsites"]],)
    unmatched = [x for x in faces["unknown_chain"] if x[2].startswith("路径")]
    assert unmatched == [], "读点在路由表里找不到对应路由：%s" % (unmatched,)


#: (api.js 方法, 前端读的键, 造这个键的生产方 `文件#类.方法`)。行号故意不钉。
#: 12 条全部走通 helper 边才判绿 ⇒ handler 函数体里从没写过这个键，只有把 `ok(helper(...))`
#: 解到底才看得到；这些边是最容易被"改名"打断的一族。
PINNED_HELPER_EDGES = [
    ("agentMemorySweep", "sweep", "src/memory_agent/agent_memory.py", "AgentMemoryService.sweep_and_reconcile"),
    ("createAppToken", "token", "src/memory_agent/app_tokens.py", "AppTokenStore.generate"),
    ("health", "collecting", "src/memory_agent/runtime.py", "AppRuntime.health"),
    ("memberInsightFeedback", "up", "src/memory_agent/store.py", "Store.member_insight_feedback"),
    ("memberInsightFeedback", "down", "src/memory_agent/store.py", "Store.member_insight_feedback"),
    ("memberInsightFeedback", "memories", "src/memory_agent/store.py", "Store.member_insight_feedback"),
    ("signalRules", "hard", "src/memory_agent/signal_learning.py", "SignalLearningService.list_rules"),
    ("signalRules", "soft", "src/memory_agent/signal_learning.py", "SignalLearningService.list_rules"),
    ("signalRules", "counts", "src/memory_agent/signal_learning.py", "SignalLearningService.list_rules"),
    ("validateTemplates", "summary", "src/memory_agent/template_validate.py", "validate_all"),
    ("visionAnalyze", "scene", "src/memory_agent/vision_service.py", "VisionService.analyze_room"),
    ("visionTestLlm", "answer", "src/memory_agent/vision_service.py", "VisionService.test_llm"),
]


@pytest.mark.parametrize("meth,key,rel,symbol", PINNED_HELPER_EDGES)
def test_helper_edge_making_that_read_green_is_pinned(faces, meth, key, rel, symbol):
    rows = [r for r in faces["green_by_helper"] if r[0] == meth and r[2] == key]
    assert rows, "%s 读 %s：不再靠 helper 边判绿 ⇒ 边断了、或改成了直返（那就是另一次改名，另判）" % (meth, key)
    hit = any(_edge(r[5], rel, symbol) for r in rows)
    assert hit, "%s.%s 的 helper 边不含 %s#%s：%s" % (meth, key, rel, symbol, [r[5] for r in rows])
    assert not any(r[2] == key for r in faces["hard"] if r[0] == meth)


def test_gauge_control_legs_pass():
    """尺自己也得被咬：L1 假键必响 / L2 真顶层键必沉默 / L3 helper 键判绿 / L4 删 helper 家必改红 /
    L5 只把生产方键名改掉必改红。全在一次性副本树里跑，跑完自删。"""
    assert scan.selftest() == 0


def test_envelope_semantics_the_gauge_assumes_still_hold():
    """本尺把 `ok(data)` 按 `{"ok": True, **data}` 展开、把 `error(..., extra)` 按 `{ok, error, **extra}` 展开。
    信封一改（比如 dict 载荷改成挂在 `data` 下），前端读法与这套解包规则同时作废 ⇒ 先在这里红，
    逼着重新校准量具，而不是拿旧口径给新形状判绿。"""
    tree = ast.parse(open(_DEPS, encoding="utf-8").read())
    fns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("ok", "error"):
            fns[node.name] = node
    assert set(fns) == {"ok", "error"}, "deps.py 的信封函数改名或移走了：%s" % (sorted(fns),)

    def _dicts(fn):
        return [n for n in ast.walk(fn) if isinstance(n, ast.Dict)]

    def _const_keys(d):
        return sorted([k.value for k in d.keys if isinstance(k, ast.Constant)])

    # ok：dict 载荷走 `{"ok": True, **data}`（有一枚 `**` 展开），非 dict 走 `{"ok": True, "data": data}`
    spread = [d for d in _dicts(fns["ok"]) if None in d.keys]
    assert spread, "ok() 不再展开 dict 载荷 ⇒ 本尺的顶层解包规则作废，需重新校准"
    assert any("data" in _const_keys(d) for d in _dicts(fns["ok"])), "ok() 的非 dict 分支不再是 `data` 键"
    # error：`{"ok": False, "error": msg}` + `payload.update(extra)`（extra 里的键会升到顶层）
    assert any(_const_keys(d) == ["error", "ok"] for d in _dicts(fns["error"])), \
        "error() 的顶层信封不再是 {ok, error}"
    updates = [n for n in ast.walk(fns["error"])
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "update"
               and any(isinstance(a, ast.Name) and a.id == "extra" for a in n.args)]
    assert updates, "error() 不再把 extra 并进顶层"


def test_acknowledged_blind_faces_do_not_grow_quietly(faces):
    """CHAIN_UNKNOWN = 尺承认解不动的面（动态派发、跨模块同名歧义、非 dict 载荷）。
    今天 5 条；新增盲区要显式加进这张表并说明为什么看不清，不许让它默默变多。"""
    seen = sorted({m for m, _p, _why in faces["unknown_chain"]})
    assert seen == ["arenaAnalytics", "createAppToken", "health", "listAppTokens",
                    "memberInsightFeedback"], seen
