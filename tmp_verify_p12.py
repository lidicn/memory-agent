"""临时验证：P1.2 在线异常 / 概念漂移（真实库 + HTTP）。用完即删。"""
import json
import sys

sys.path.insert(0, "/app/src")

from memory_agent.activity_inference import ActivityInferenceService  # noqa: E402
from memory_agent.config import get_config  # noqa: E402
from memory_agent.insights import InsightService  # noqa: E402
from memory_agent.store import Store  # noqa: E402

conf = get_config()
store = Store(db_path=conf.db_path, tz_offset_hours=conf.tz_offset_hours)
store.init_schema()
ins = InsightService(conf, store)
svc = ActivityInferenceService(type("RT", (), {"config": conf, "store": store,
                                               "insights": ins})())

from datetime import timedelta  # noqa: E402

from memory_agent.store import now_local  # noqa: E402

_tomorrow = (now_local(conf.tz_offset_hours) + timedelta(days=1)).strftime("%Y-%m-%d")
print("PURGED_OLD", store.purge_behavior_drifts(_tomorrow))

print("=== 1) mine_drift（真实库 30 天 / 1h 分桶）===")
res = svc.mine_drift(days=30)
print("SUMMARY", json.dumps({k: v for k, v in res.items()
                             if k not in ("drifts", "anomalies")}, ensure_ascii=False))
print("DRIFTS", json.dumps((res.get("drifts") or [])[:5], ensure_ascii=False))
print("ANOM", json.dumps((res.get("anomalies") or [])[:5], ensure_ascii=False))

print("=== 2) 落库结果 ===")
rows = store.list_behavior_drifts(limit=10)
print("DB_ROWS", len(rows))
for r in rows[:4]:
    print("  ", r["bucket_ts"], r["kind"], "density=", r["density"],
          "score=", r["score"], r["detail"])
print("KIND_COUNTS", {k: len(store.list_behavior_drifts(kind=k, limit=1000))
                       for k in ("drift", "anomaly")})

print("=== 3) HTTP 端点（带管理员 JWT）===")
import urllib.request  # noqa: E402

from memory_agent.auth import AuthManager  # noqa: E402

tok = AuthManager(conf)._create_token("admin", True)


def call(path, method="GET", body=None, token=tok):
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Content-Type": "application/json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:8000{path}", method=method,
                                 data=data, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read().decode()[:240]
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "code", "ERR"), str(exc)[:200]


print("DRIFTS_GET", call("/api/behaviors/drifts?limit=5"))
print("DRIFTS_LIVE", call("/api/behaviors/drifts?live=1&days=14&limit=3"))
print("MINE_POST", call("/api/behaviors/mine-drift", "POST",
                        {"days": 14, "persist": False}))
print("NOAUTH", call("/api/behaviors/drifts", token=None))
print("VERIFY_P12_DONE")
