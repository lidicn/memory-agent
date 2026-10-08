r"""#75 的**运行时**判据：三条嫌疑工具的载荷里到底有哪些键。

静态链在两种形状下是瞎的（本轮实测）：
* 门面把 agent 记忆块整个塞进 `out["agent_memory"]`，`mirror_dirty` 在另一模块的 `health()` 里；
* NL 面的键由 `QuestionPlan.to_dict()` 造，跨模块、跨名字。

所以 `data_quality_issues`/`semantic_hints`/`recommended_tool` 三处到底是不是"文案凭空许诺"，
只能把真引擎跑一遍、把键集打出来。只读，不改任何东西。

    python .qoder/tmp-c75-runtime.py
"""
import json
import os
import sys
import tempfile

_SRC = os.path.join(".", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config
from memory_agent.insights import InsightService
from memory_agent.insights.models import EntityInfo
from memory_agent.store import Store

DAY_1 = "2026-09-21"
DAY_2 = "2026-09-22"
PC = "binary_sensor.study_pc"
LIGHT = "light.study_lamp"
CATALOG = [
    EntityInfo(entity_id=PC, friendly_name="书房电脑", room="书房", domain="binary_sensor"),
    EntityInfo(entity_id=LIGHT, friendly_name="书房台灯", room="书房", domain="light"),
]


def _ev(entity_id, day, minute, state, domain):
    return {"entity_id": entity_id, "ts": "%sT10:%02d:00" % (day, minute), "room": "书房",
            "domain": domain, "new_state": state, "old_state": "x",
            "attrs": {"friendly_name": entity_id.split(".")[-1]}}


def _rows():
    rows = []
    for day in (DAY_1, DAY_2):
        rows += [_ev(PC, day, i, ("on" if i % 2 else "off"), "binary_sensor") for i in range(6)]
        rows += [_ev(LIGHT, day, i, "on", "light") for i in range(3)]
    return rows


def _shape(label, payload):
    keys = sorted(payload) if isinstance(payload, dict) else []
    print("%s TOP=%s" % (label, keys))
    if not isinstance(payload, dict):
        return
    for k in keys:
        v = payload[k]
        if isinstance(v, dict):
            print("%s   %s.NESTED=%s" % (label, k, sorted(v)))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            print("%s   %s[]ITEM=%s" % (label, k, sorted(v[0])))
    for suspect in ("data_quality_issues", "issues", "semantic_hints", "agent_memory_hints",
                    "hints", "recommended_tool", "mirror_dirty", "next_tool", "tool",
                    "suggested_tool", "steps"):
        if suspect in payload:
            print("%s   >>> 顶层命中 %s" % (label, suspect))


def main():
    tmp = tempfile.mkdtemp(prefix="ma_c75_")
    store = Store(os.path.join(tmp, "c75.db"), tz_offset_hours=0.0)
    store.init_schema()
    store.insert_events(_rows())
    svc = InsightService(store, Config())
    svc.resolver.refresh(CATALOG)

    out = svc.get_data_quality(days=7)
    _shape("get_data_quality", out)

    out = svc.ask_memory("书房电脑昨天用了多久", days=7, route="auto", return_hints=True)
    _shape("ask_memory(hints=True)", out)
    if isinstance(out.get("plan"), dict):
        print("ask_memory plan.NESTED=%s" % sorted(out["plan"]))
        print("ask_memory plan.JSON=%s" % json.dumps(out["plan"], ensure_ascii=False)[:400])

    out = svc.plan_question("书房电脑昨天用了多久")
    _shape("plan_question", out)
    print("plan_question.JSON=%s" % json.dumps(out, ensure_ascii=False)[:500])

    out = svc.ask_memory("书房电脑昨天用了多久", days=7, route="auto", return_hints=False)
    _shape("ask_memory(hints=False)", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
