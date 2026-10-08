import chromadb
from collections import defaultdict

client = chromadb.HttpClient(host="chroma", port=8000)
coll = client.get_collection("home_events")
total = coll.count()
print(f"Total events: {total}")

results = coll.get(include=["metadatas"], limit=5000)

rh = defaultdict(lambda: defaultdict(int))
ra = defaultdict(lambda: defaultdict(int))
entity_room = {}

for m in results["metadatas"]:
    ts = m.get("timestamp", "")
    room = m.get("room", "unknown")
    action = m.get("action", "unknown")
    entity = m.get("entity_id", "")
    if "T" in ts:
        h = int(ts.split("T")[1].split(":")[0])
        rh[room][h] += 1
        ra[room][action] += 1
        if entity:
            entity_room[entity] = room

print("\n=== Activity by hour per room ===")
for room in sorted(rh.keys()):
    print(f"\n--- {room} ---")
    for h in range(24):
        c = rh[room].get(h, 0)
        if c > 0:
            bar = "#" * min(c // 3, 40)
            print(f"  {h:02d}:00  {c:4d}  {bar}")
    print(f"  Actions: {dict(ra[room])}")

print("\n=== Per-entity activity (top 30) ===")
entity_counts = defaultdict(int)
entity_hour = defaultdict(lambda: defaultdict(int))
for m in results["metadatas"]:
    ts = m.get("timestamp", "")
    entity = m.get("entity_id", "")
    if "T" in ts and entity:
        h = int(ts.split("T")[1].split(":")[0])
        entity_counts[entity] += 1
        entity_hour[entity][h] += 1

for entity, count in sorted(entity_counts.items(), key=lambda x: -x[1])[:30]:
    room = entity_room.get(entity, "?")
    peak_h = max(entity_hour[entity].items(), key=lambda x: x[1])[0]
    print(f"  [{room}] {entity}: {count} events, peak at {peak_h:02d}:00")
