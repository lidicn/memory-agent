# -*- coding: utf-8 -*-
"""运行时探针对（只读仓库，临时库落在 %TEMP%）：门面 vs legacy 的实际返回**顶层键**。

为什么要跑真的：静态量具（tmp-c72-keydiff.py）看不见 `return self.core.x(tr)` 里面
产出了什么，于是把"改名/新增"和"丢键"混成了同一格可疑（14 条里绝大多数是前者）。
只有把两边都真调用一遍，键集合才是证据。

判据口径（与 #72 同形才算缺陷）：
  - legacy 有、门面没有，**且**该键被 ToolSpec/文档字符串/调用点承诺或读取 ⇒ 丢键缺陷。
  - 门面有、legacy 没有 ⇒ 新增（MA-裁5 的 Page 信封、窗口回显即属此类，不是缺陷）。
本脚本只打印，不改任何仓库文件。
"""
import io
import os
import sys
import tempfile
from datetime import datetime, timedelta

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent.config import Config            # noqa: E402
from memory_agent.insights import InsightService   # noqa: E402
from memory_agent.insights_legacy import InsightService as Legacy  # noqa: E402
from memory_agent.store import Store               # noqa: E402

CASES = [
    ("data_coverage", dict(days=7)),
    ("infer_activities", dict(days=7)),
    ("get_user_persona", dict(days=14)),
    ("get_behavior_insights", dict(compare_days=7)),
    ("get_data_quality", dict(days=30)),
    ("device_usage", dict(days=7)),
    ("anomaly_report", dict(days=7)),
    ("climate_sessions", dict(days=7)),
    ("entity_catalog", dict(days=7)),
    ("search_events", dict(query="灯", days=7)),
    ("query_behavior_events", dict(days=7)),
    ("plan_question", dict(question="客厅灯为什么开着", days=7)),
]

DOMAINS = [
    ("light.study_desk_light", "light", "on", "书房"),
    ("binary_sensor.study_door_contact", "binary_sensor", "on", "书房"),
    ("climate.study_thermostat", "climate", "heat", "书房"),
    ("sensor.kitchen_power_meter", "sensor", "1200", "厨房"),
    ("switch.tv_room_power", "switch", "on", "客厅"),
]


def seed(store):
    now = datetime.now().replace(microsecond=0)
    rows = []
    for d in range(6):
        day = now - timedelta(days=d)
        for h in (7, 12, 18, 22):
            ts = day.replace(hour=h, minute=15)
            for idx, (eid, dom, st, room) in enumerate(DOMAINS):
                rows.append({
                    "entity_id": "%s_%d" % (eid, idx),
                    "ts": ts.isoformat(),
                    "room": room,
                    "domain": dom,
                    "new_state": st,
                    "old_state": "off" if st != "1200" else "1100",
                })
    store.insert_events(rows)


def keys_of(result):
    if isinstance(result, dict):
        return set(result.keys())
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                return set(d.keys())
        except Exception:
            pass
    return {"<NON-DICT:%s>" % type(result).__name__}


def main():
    tmp = tempfile.mkdtemp(prefix="c72probe-")
    path = os.path.join(tmp, "probe.db")
    store = Store(path, tz_offset_hours=0.0)
    store.init_schema()
    seed(store)
    cfg = Config()
    fac = InsightService(store, cfg)
    leg = Legacy(cfg, store)          # legacy 形参是 (config, store)，与门面相反；写反了会满屏假丢键

    lines = []
    for name, kwargs in CASES:
        leg_keys = fac_keys = None
        leg_err = fac_err = ""
        try:
            leg_keys = keys_of(getattr(leg, name)(**kwargs))
        except Exception as exc:
            leg_err = "%s: %s" % (type(exc).__name__, str(exc)[:60])
        try:
            fac_keys = keys_of(getattr(fac, name)(**kwargs))
        except Exception as exc:
            fac_err = "%s: %s" % (type(exc).__name__, str(exc)[:60])
        lost = sorted((leg_keys or set()) - (fac_keys or set()))
        new = sorted((fac_keys or set()) - (leg_keys or set()))
        lines.append("== %s" % name)
        lines.append("   legacy=%s%s" % (
            ",".join(sorted(leg_keys)) if leg_keys else "-",
            " ERR[" + leg_err + "]" if leg_err else ""))
        lines.append("   facade=%s%s" % (
            ",".join(sorted(fac_keys)) if fac_keys else "-",
            " ERR[" + fac_err + "]" if fac_err else ""))
        lines.append("   LOST=%s | NEW=%s" % (
            ",".join(lost) if lost else "-", ",".join(new) if new else "-"))

    with io.open(os.path.join(os.path.dirname(__file__),
                              "tmp-c72-probe.out"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
