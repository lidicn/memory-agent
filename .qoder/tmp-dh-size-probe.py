"""只读探针：量 `list_device_health` 的 lean 投影在生产真数据上省多少字节。

不写库、不建 Store（只 sqlite mode=ro）、不打印任何 entity_id / 设备名——只给聚合读数。
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/tmp/vs18/src")
from memory_agent.mcp_server import project_device_health  # noqa: E402

DB = "file:/data/memory_agent.db?mode=ro"
conn = sqlite3.connect(DB, uri=True)
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute("SELECT * FROM device_health")]
conn.close()

lean = project_device_health(rows, "lean")


def size(items):
    return len(json.dumps(items, ensure_ascii=False).encode("utf-8"))


raw_bytes, lean_bytes = size(rows), size(lean)
ref = sum(1 for r in rows if int(r.get("referenced") or 0))
note_raw = sum(len(str(r.get("note") or "")) for r in rows)
note_lean = sum(len(str(r.get("note") or "")) for r in lean)
stable_raw = sum(len(str(r.get("stable_id") or "")) for r in rows)
stable_lean = sum(len(str(r.get("stable_id") or "")) for r in lean)
print(f"ROWS={len(rows)} REFERENCED={ref}")
print(f"RAW_BYTES={raw_bytes} LEAN_BYTES={lean_bytes} RATIO={lean_bytes / max(1, raw_bytes):.3f}")
print(f"STABLE_CHARS={stable_raw} -> {stable_lean}")
print(f"NOTE_CHARS={note_raw} -> {note_lean}")
print(f"MAX_ROW_RAW={max(size([r]) for r in rows)} MAX_ROW_LEAN={max(size([r]) for r in lean)}")
print(f"PAGE500_RAW={size(rows[:500])} PAGE500_LEAN={size(lean[:500])}")
print("PROBE_RC=0")
