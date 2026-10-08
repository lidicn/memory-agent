"""行为推断层生产读数探针：只读取，不写入。"""
import sqlite3
from datetime import timedelta

from memory_agent.config import get_config
from memory_agent.store import now_local

cfg = get_config()
print("db_path:", cfg.db_path)
print("activity_inference_enabled:", getattr(cfg, "activity_inference_enabled", None))
print("activity_window_minutes:", getattr(cfg, "activity_window_minutes", None))
print("activity_interval_seconds:", getattr(cfg, "activity_interval_seconds", None))
print("activity_conf_threshold:", getattr(cfg, "activity_conf_threshold", None))
print("pir_debounce_sec:", getattr(cfg, "pir_debounce_sec", None))

conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
c = conn.cursor()


def q(sql, params=()):
    try:
        c.execute(sql, params)
        return c.fetchall()
    except Exception as exc:
        return [("ERR", str(exc))]


now = now_local(cfg.tz_offset_hours)
print("now_local:", now.isoformat(sep="T"))
print("behavior_states rows:", q("select count(*) from behavior_states"))
print("behavior_states by activity:", q("select activity, count(*) from behavior_states group by activity"))
print("behavior_states max ts:", q("select max(ts) from behavior_states"))
print("candidate_rules rows:", q("select count(*) from candidate_rules"))
print("events last 15min:", q("select count(*) from events where ts >= ?",
                              ((now - timedelta(minutes=15)).isoformat(sep="T"),)))
