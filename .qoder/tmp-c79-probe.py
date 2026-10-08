"""#79 运行时探针：温控环比块在真库上到底响不响。

只读探针（不写树），落盘读数供判据对锚。
"""
import os
import sys
import tempfile
from datetime import timedelta

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from memory_agent.config import Config
from memory_agent.insights import InsightService
from memory_agent.insights_legacy import InsightService as Legacy
from memory_agent.insights.models import house_now
from memory_agent.store import Store

ROOMS = {
    "卧室": {"enabled": True, "entities": {
        "climate.bedroom_ac": {"name": "卧室空调", "domain": "climate"},
        "light.bedroom": {"name": "卧室灯", "domain": "light"},
    }},
}


def _iso(ts):
    return ts.isoformat(timespec="seconds")


def _rows():
    """当前窗口（-2 天）一组会话，前一窗口（-9 天）一组更长的会话。

    放在自然日中间，legacy 的对齐窗口与引擎的滚动窗口都包得住，读数才能逐字对账。
    """
    now = house_now()
    rows = []
    for offset, on_h, off_h, setpoint, room_temp in ((2, 10, 12, 24.0, 27.5),
                                                     (9, 10, 13, 22.0, 26.0)):
        day = (now - timedelta(days=offset))
        on_ts = day.replace(hour=on_h, minute=0, second=0, microsecond=0)
        off_ts = day.replace(hour=off_h, minute=0, second=0, microsecond=0)
        rows.append({"entity_id": "climate.bedroom_ac", "ts": _iso(on_ts),
                     "day": on_ts.isoformat()[:10], "room": "卧室",
                     "new_state": "cool", "old_state": "off",
                     "attrs_json": '{"temperature": %s, "current_temperature": %s}'
                                   % (setpoint, room_temp)})
        rows.append({"entity_id": "climate.bedroom_ac", "ts": _iso(off_ts),
                     "day": off_ts.isoformat()[:10], "room": "卧室",
                     "new_state": "off", "old_state": "cool",
                     "attrs_json": '{"temperature": %s, "current_temperature": %s}'
                                   % (setpoint, room_temp)})
        rows.append({"entity_id": "light.bedroom", "ts": _iso(on_ts),
                     "day": on_ts.isoformat()[:10], "room": "卧室",
                     "new_state": "on", "old_state": "off",
                     "attrs_json": '{"friendly_name": "卧室灯"}'})
    return rows


def main():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    st = Store(path, tz_offset_hours=8.0)
    st.init_schema()
    n = st.insert_events(_rows())
    cfg = Config()
    from dataclasses import replace
    cfg = replace(cfg, rooms=ROOMS)
    svc = InsightService(st, cfg)
    try:
        print("INSERTED=%d" % n)
        print("HAS_PROVIDER=%s" % (svc.core.climate_provider is not None))
        print("PROBE_PROVIDER=%s" % getattr(svc.core.climate_provider, "__name__", "?"))
        out = svc.core.compare_insights(compare_days=7)
        cc = out.get("climate_comparison")
        print("TOP_KEYS=%s" % sorted(out))
        print("CLIMATE_KEYS=%s" % sorted(cc or {}))
        print("CLIMATE=%r" % (cc,))
        print("MAIN_KEYS=%s total_events=%s" % (
            sorted(out.get("current") or {}), (out.get("current") or {}).get("total_events")))
        # 门面两条入口都要带块
        facade_cmp = svc.compare_insights(compare_days=7)
        print("API_COMPARE_HAS_BLOCK=%s" % ("climate_comparison" in facade_cmp))
        outer = svc.get_behavior_insights(compare_days=7)
        print("OUTER_HAS_BLOCK=%s block=%r" % ("climate_comparison" in outer,
                                               outer.get("climate_comparison")))
        # legacy 真身对照：同一份库、同一把钟
        lg = Legacy(cfg, st)
        lcc = lg.get_behavior_insights(compare_days=7).get("climate_comparison")
        print("LEGACY_CLIMATE=%r" % (lcc,))
        print("SAME_KEYS=%s" % (sorted(lcc or {}) == sorted(cc or {})))
        print("EQUAL=%s" % (lcc == cc))
        # 未注入 provider 的形状
        from memory_agent.insights.service import BehaviorService
        bare = BehaviorService(svc.repo, svc.resolver, svc.config)
        print("BARE=%r" % (bare.compare_insights(compare_days=7).get("climate_comparison"),))
        # provider 炸掉的形状（活动环比必须不受影响）
        boom = BehaviorService(svc.repo, svc.resolver, svc.config,
                               climate_provider=lambda s, e: (_ for _ in ()).throw(
                                   RuntimeError("legacy 挂了")))
        b = boom.compare_insights(compare_days=7)
        print("BOOM_BLOCK=%r BOOM_MAIN_OK=%s boom_ok=%s" % (
            b.get("climate_comparison"), bool(b.get("current")), b.get("ok")))
        # days<=0 的整页降级信封也要带块
        zero = svc.core.compare_insights(compare_days=0)
        print("ZERO_BLOCK=%r zero_ok=%s" % (zero.get("climate_comparison"), zero.get("ok")))
        # 两窗口分别取数：忽略入参的实现会让 current 与 previous 一样
        same = BehaviorService(svc.repo, svc.resolver, svc.config,
                              climate_provider=lambda s, e: svc.legacy.climate_sessions(
                                  start="2000-01-01T00:00:00", end="2099-01-01T00:00:00"
                              ).get("sessions", []))
        g = same.compare_insights(compare_days=7)["climate_comparison"]
        print("STALE_WINDOW_HOURS cur=%s prev=%s delta=%s" % (
            g["current"]["hours"], g["previous"]["hours"], g["delta_hours"]))
    finally:
        st.close()
        os.remove(path)


if __name__ == "__main__":
    main()
