"""只读探针：NEW-P2-2「被域过滤静默排除的实体」在本居有没有现役受害者。
mode=ro 打开生产库，不写数据、不建对象。"""
import re
import sqlite3

from memory_agent.store import TELEMETRY_DOMAINS

DB = "/data/memory_agent.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

ph = ",".join("?" * len(TELEMETRY_DOMAINS))
print("TELEMETRY_DOMAINS =", TELEMETRY_DOMAINS)

for r in conn.execute(
    f"SELECT domain, COUNT(*) c, COUNT(DISTINCT entity_id) e FROM events "
    f"WHERE domain IN ({ph}) GROUP BY domain ORDER BY c DESC",
    TELEMETRY_DOMAINS,
):
    print(f"  排除域 {r['domain']:<10} rows={r['c']:<10} entities={r['e']}")

rows = conn.execute(
    f"SELECT entity_id, domain, COUNT(*) c, GROUP_CONCAT(DISTINCT new_state) vals "
    f"FROM events WHERE domain IN ({ph}) GROUP BY entity_id",
    TELEMETRY_DOMAINS,
).fetchall()
print("\n被排除实体总数 =", len(rows))

BINARY = {"on", "off", "unknown", "unavailable", "home", "away", "night", None, ""}
NUM = re.compile(r"^-?\d+(\.\d+)?$")

binary_shaped, numeric, other = [], [], []
for r in rows:
    vals = {v for v in (r["vals"] or "").split(",")}
    if vals & {"on", "off", "home", "away", "night"} and all(v in BINARY for v in vals):
        binary_shaped.append(r)
    elif vals and all(NUM.match(v or "") for v in vals):
        numeric.append(r)
    else:
        other.append(r)

print(f"  数值型（过滤的正作用面）      = {len(numeric)}")
print(f"  二值/开关形状但域被排除（疑似误配）= {len(binary_shaped)}")
for r in sorted(binary_shaped, key=lambda x: -x["c"])[:20]:
    print(f"    {r['entity_id']:<46} domain={r['domain']:<8} rows={r['c']:<9} states={(r['vals'] or '')[:60]}")
print(f"  其他形状（文本/枚举）          = {len(other)}")
for r in sorted(other, key=lambda x: -x["c"])[:8]:
    print(f"    {r['entity_id']:<46} domain={r['domain']:<8} rows={r['c']:<9} states={(r['vals'] or '')[:60]}")

print("\nintegrity_check =", conn.execute("PRAGMA integrity_check").fetchone()[0])
conn.close()
