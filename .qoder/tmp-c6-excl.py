"""signal_exclusions 的行数按 revoked / exclusion_type 分档（只读，用于核对口径漂移）。"""
import sqlite3

from memory_agent.config import get_config

cfg = get_config()
conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
cols = [r[1] for r in conn.execute("PRAGMA table_info(signal_exclusions)")]
print("columns:", cols)
print("total rows:", conn.execute("SELECT COUNT(*) FROM signal_exclusions").fetchone()[0])
print("by revoked:", conn.execute(
    "SELECT revoked, COUNT(*) FROM signal_exclusions GROUP BY revoked").fetchall())
print("by type:", conn.execute(
    "SELECT exclusion_type, revoked, COUNT(*) FROM signal_exclusions "
    "GROUP BY exclusion_type, revoked").fetchall())
