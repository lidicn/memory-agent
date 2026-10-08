"""Patch store.py: filter empty memory_id in list_dirty_agent_mirrors."""
file_path = r"E:\NAS\memory-agent\src\memory_agent\store.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old = '"SELECT * FROM agent_memories WHERE mirror_dirty=1"'
new = '"SELECT * FROM agent_memories WHERE mirror_dirty=1 AND memory_id IS NOT NULL AND memory_id != \'\'"'

if old in content:
    content = content.replace(old, new)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("OK: list_dirty_agent_mirrors filter added")
else:
    print("ERROR: old string not found")
