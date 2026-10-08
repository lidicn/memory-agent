import json, sys
sys.path.insert(0, "/app/src")
from memory_agent import mcp_server as ms
from memory_agent.mcp_server import _apply_response_cap, _build_tool_result, _result_text

cap_cfg = ms._mcp_response_max_bytes()
print("PROD_mcp_response_max_bytes =", cap_cfg)

rows = [{"entity_id": f"sensor.entity_{i:05d}", "stable_id": f"sensor__设备名_{i}",
         "state": "active", "referenced": i % 2, "note": "本轮对账未出现（短暂失联）",
         "last_seen": "2026-10-03T10:00:06", "last_data_ts": "",
         "updated_at": "2026-10-03T10:03:33"} for i in range(4000)]
payload = json.dumps({"ok": True, "state": "all", "health": rows,
                      "total": len(rows), "logical_devices": 1267}, ensure_ascii=False)
print("synthetic_bytes =", len(payload.encode("utf-8")), "rows =", len(rows))

res = _build_tool_result(payload, is_error=False)
out = _apply_response_cap(res, "list_device_health", max_bytes=cap_cfg)
text = _result_text(out)
d = json.loads(text)   # 旧行为在这里会抛 JSONDecodeError
print("after_cap_bytes =", len(text.encode("utf-8")), "kept =", len(d["health"]))
print("truncated_notice =", json.dumps({k: v for k, v in d["_truncated"].items()
                                        if k not in ("fields", "hint")}, ensure_ascii=False))
print("fields =", d["_truncated"]["fields"])
print("scalar_fields_intact =", d["ok"], d["state"], d["total"], d["logical_devices"])
print("row_shape_kept =", sorted(d["health"][0].keys()) == sorted(rows[0].keys()))
