"""临时验证：P1.4 规则召回审计（真实库 + HTTP）。用完即删。"""
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

print("=== 1) audit_rule_recall（真实库 14 天）===")
res = svc.audit_rule_recall(days=14)
print("SUMMARY", json.dumps({k: v for k, v in res.items()
                             if k not in ("audit", "gaps")}, ensure_ascii=False))
for a in res.get("audit", []):
    print("  RULE %s | eligible=%s matched=%s near_miss=%s recall=%s" % (
        a["rule"], a["eligible_days"], a["matched_days"], a["near_miss_days"],
        a["estimated_recall"]))
    print("       blockers=", json.dumps(a["top_blockers"][:2], ensure_ascii=False),
          " examples=", a["examples"][:3])
print("GAPS", json.dumps(res.get("gaps"), ensure_ascii=False))

print("=== 2) 落库的放宽建议 ===")
rules = [r for r in store.list_candidate_rules() if r.get("source") == "recall_gap"]
print("DB_RECALL_GAP_RULES", len(rules))
for r in rules[:3]:
    print("  ", r["name"], [s.get("tag") for s in (r.get("steps") or [])],
          r.get("evidence"))

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
            return r.status, r.read().decode()[:220]
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "code", "ERR"), str(exc)[:200]


print("AUDIT_HTTP", call("/api/behaviors/audit-rule-recall", "POST",
                         {"days": 14, "persist": False}))
print("NOAUTH", call("/api/behaviors/audit-rule-recall", "POST", {"days": 1},
                     token=None))
print("VERIFY_P14_DONE")
