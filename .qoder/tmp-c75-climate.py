r"""#75 短名单最后一行的运行时对账：`get_climate_sessions` 文案许诺的温度到底走不走得通。

静态扫描把 docstring 里的 `current_temperature` 记成"键只存在于 legacy"，但那是**输入侧**：
`insights_legacy.py:1794` 从事件 attrs 取 `current_temperature`，落到会话的
`room_temp_c`（`insights/utils.py:316`）。文案从没许诺过载荷里有 `current_temperature` 这个键。

所以这一行要判的是两件事，都得真跑：
1. 载荷里确实**没有** `current_temperature` 键（ ⇒ 文案不构成凭空许诺）；
2. attrs 的 `current_temperature` / `temperature` 确实被解析进 `room_temp_c` / `setpoint_c`
   （ ⇒ 文案那句"直接解析"是真的，该响的时刻真响过）。

时间戳全部相对真机 now 生成，避免窗口把样本挡掉。只读，不改任何东西。

    python .qoder/tmp-c75-climate.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

_SRC = os.path.join(".", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config
from memory_agent.insights import InsightService
from memory_agent.insights.models import EntityInfo
from memory_agent.store import Store

AC = "climate.master_ac"
CATALOG = [EntityInfo(entity_id=AC, friendly_name="主卧空调", room="主卧", domain="climate")]

# (相对分钟, new_state, attrs 温度对) —— on/属性/attribute-only/off 都要有，
# 才能同时踩到 OPEN、非 OPEN 非 CLOSED（attrs 温度仍采信）与 CLOSED 三条分支。
LEG = [
    (0,    "off",  {}),
    (10,   "cool", {"temperature": 26.0, "current_temperature": 29.5}),
    (40,   "cool", {"temperature": 25.5, "current_temperature": 28.0}),
    (70,   "unknown", {"temperature": 25.0, "current_temperature": 26.5}),
    (100,  "off",  {"temperature": 25.0, "current_temperature": 26.0}),
    (130,  "heat", {"temperature": 22.0}),          # 只有设定温度，无室温
    (160,  "off",  {"temperature": 22.0}),
]


def _rows():
    now = datetime.now()
    out = []
    for minute, state, extra in LEG:
        ts = (now - timedelta(minutes=240 - minute)).strftime("%Y-%m-%dT%H:%M:%S")
        attrs = {"friendly_name": "主卧空调", "hvac_action": state}
        attrs.update(extra)
        out.append({"entity_id": AC, "ts": ts, "room": "主卧", "domain": "climate",
                    "new_state": state, "old_state": "x", "attrs": attrs})
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="ma_c75c_")
    store = Store(os.path.join(tmp, "c75c.db"), tz_offset_hours=0.0)
    store.init_schema()
    inserted = store.insert_events(_rows())
    svc = InsightService(store, Config())
    svc.resolver.refresh(CATALOG)

    out = svc.climate_sessions(days=7)
    keys = sorted(out)
    print("RC_INSERT=%s" % (inserted,))
    print("CLIMATE_TOP=%s" % keys)
    print("HAS_current_temperature_in_TOP=%s" % ("current_temperature" in out))
    print("HAS_temperature=%s HAS_setpoint=%s" % ("temperature" in out, "setpoint" in out))
    print("TOTAL_SESSIONS=%s SCANNED=%s IGNORED=%s WITH_TEMP=%s"
          % (out.get("total_sessions"), out.get("events_scanned"),
             out.get("events_state_unknown"), out.get("sessions_with_temperature")))
    print("NOTE=%s" % out.get("temperature_note"))
    sessions = out.get("sessions") or []
    if sessions:
        print("sessions[]ITEM=%s" % sorted(sessions[0]))
    for i, s in enumerate(sessions):
        print("  sess[%d] state=%s sp=%s rt=%s rng=%s samples=%s dur=%s still_on=%s notes=%s"
              % (i, s.get("hvac_actions"), s.get("setpoint_c"), s.get("room_temp_c"),
                 s.get("room_temp_range_c"), s.get("samples"), s.get("duration_minutes"),
                 s.get("still_on"), s.get("notes")))
        bad = [k for k in ("current_temperature", "temperature") if k in s]
        if bad:
            print("  sess[%d] >>> 会话里出现输入侧键名 %s" % (i, bad))

    q = svc.climate_sessions(query="主卧空调", days=7)
    print("CLIMATE_QUERY(total_sessions)=%s ids=%s"
          % (q.get("total_sessions"), [s.get("entity_id") for s in (q.get("sessions") or [])]))
    agg = None
    try:
        agg = svc.aggregate_climate_sessions(start="", end="")
    except Exception as exc:  # noqa: BLE001 - 附属面读不到就如实报，不硬判
        print("AGG_ERR=%s" % exc)
    if isinstance(agg, dict):
        print("AGG_TOP=%s" % sorted(agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
