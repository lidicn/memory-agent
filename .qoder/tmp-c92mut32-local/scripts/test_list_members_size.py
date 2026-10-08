"""Test list_members response size and content."""
import sys
sys.path.insert(0, "/app/src")
from memory_agent.store import Store
import json

store = Store("/data/memory_agent.db")
members = store.list_members()
print(f"Total members: {len(members)}")
for i, m in enumerate(members):
    # Count fields and sizes
    devices = m.get("devices", [])
    tags = m.get("tags", [])
    print(f"\nMember {i}: {m.get('name', '?')}")
    print(f"  Fields: {list(m.keys())}")
    print(f"  rooms: {m.get('rooms', [])}")
    print(f"  devices count: {len(devices) if isinstance(devices, list) else 'N/A'}")
    print(f"  tags count: {len(tags) if isinstance(tags, list) else 'N/A'}")
    # Size of this member's JSON
    member_json = json.dumps(m, ensure_ascii=False)
    print(f"  raw JSON size: {len(member_json)} bytes")

# Total response size (simulating MCP _SKIP + tags slim)
_SKIP = {"face_photo", "avatar_url", "embedding", "face_feature",
         "profile_json", "appearance_json"}
slim_members = []
for m in members:
    slim = {k: v for k, v in m.items() if k not in _SKIP}
    if isinstance(slim.get("tags"), list):
        slim["tags"] = [
            {"label": t.get("label") or t.get("name", ""),
             "category": t.get("category", ""),
             "confidence": t.get("confidence", 0)}
            for t in slim["tags"] if isinstance(t, dict)
        ]
    slim_members.append(slim)

response = {"ok": True, "members": slim_members, "total": len(slim_members)}
response_json = json.dumps(response, ensure_ascii=False)
print(f"\n=== MCP response size (with _SKIP + tags slim): {len(response_json)} bytes ===")

# Without devices
slim_no_devices = []
for m in slim_members:
    s = {k: v for k, v in m.items() if k != "devices"}
    slim_no_devices.append(s)
response2 = {"ok": True, "members": slim_no_devices, "total": len(slim_no_devices)}
response2_json = json.dumps(response2, {"ok": True, "members": slim_no_devices, "total": len(slim_no_devices)}, ensure_ascii=False)
print(f"Without devices field: {len(json.dumps(response2, ensure_ascii=False))} bytes")
