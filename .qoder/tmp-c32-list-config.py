import os, sys
sys.path.insert(0, os.path.abspath("scripts"))
import scan_day_bounds as s
rows = s.scan_root("src/memory_agent")
for r in rows:
    if r["label"] in ("config",) or r["detail"].startswith(("writable-config", "config-list")):
        print(f"{r['label']:9s} {r['guard']:9s} {r['file']}:{r['line']} {r['detail']} | {r['expr']}")
print("WRITABLE_KEYS_TOTAL=%d" % len(s.WRITABLE_CONFIG_KEYS))
