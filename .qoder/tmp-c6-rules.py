"""生产库 activity_rules 的逐行读数（只读，用于解释 Q4 读数② 的枚举值来源）。"""
import sqlite3

from memory_agent.config import get_config

cfg = get_config()
conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
cols = [r["name"] for r in conn.execute("PRAGMA table_info(activity_rules)")]
print("columns:", cols)
rows = conn.execute("SELECT * FROM activity_rules ORDER BY rule_id").fetchall()
print("rows:", len(rows))
for r in rows:
    d = {k: r[k] for k in cols}
    # 只打印判定维度，note 可能含人写的自由文本，只报长度
    d["note"] = "<len=%s>" % (len(str(d.get("note") or "")))
    print(d)
