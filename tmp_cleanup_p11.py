"""临时清理：作废调参期间产生的旧行为异常（可重算），保留人工 confirmed。用完即删。"""
import sys
from datetime import timedelta

sys.path.insert(0, "/app/src")
from memory_agent.config import get_config  # noqa: E402
from memory_agent.store import Store, now_local  # noqa: E402

conf = get_config()
st = Store(conf.db_path, tz_offset_hours=conf.tz_offset_hours)
st.init_schema()
before = (now_local(conf.tz_offset_hours) + timedelta(days=1)).strftime("%Y-%m-%d")
print("PURGED", st.purge_behavior_anomalies(before))
print("LEFT", len(st.list_behavior_anomalies(limit=1000)))
