import os
import sys

sys.path.insert(0, os.path.abspath("src"))
from memory_agent import intent_inference as imod  # noqa: E402

RULE = {
    "intent": "exercise", "label": "想运动", "triggers": ["客厅清空"],
    "secondary_triggers": [], "scene_graph_triggers": [], "confidence": 0.5,
    "suggestion": "s", "room": "客厅",
}
imod.INTENT_RULES = [RULE]


def ev(ts):
    return {"server_ts": ts, "action": "客厅清空", "scene": "", "room": "客厅",
            "persons_json": "[]"}


HIST = "2026-03-05T21:00:00"  # 远早于真实 now ⇒ 一旦兜到墙钟必然落空

cases = {
    "A_clean_hist_only": [ev(HIST)],
    "B_hist_plus_zero_date": [ev(HIST), ev("0000-00-00 00:00:00")],
    "C_hist_plus_garbage": [ev("not-a-ts"), ev(HIST)],
    "D_hist_plus_missing": [ev(None), ev(HIST), ev("")],
    "E_int_ts": [ev(1700000000)],
}
for name, events in cases.items():
    try:
        out = imod.infer_intent(events, window_min=10)
        print(f"{name}: {'HIT' if out else 'NONE'}"
              + (f" conf={out['confidence']} in_win={out['events_in_window']}" if out else ""))
    except Exception as exc:  # noqa: BLE001
        print(f"{name}: RAISED {type(exc).__name__}: {exc}")
