import sqlite3
import os

db = sqlite3.connect('/data/memory_agent.db')

# 各表行数
for table in ['events', 'perception_events', 'behavior_events', 'detected_activities', 'agent_memories']:
    try:
        count = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f"{table}: {count:,} rows")
    except:
        print(f"{table}: N/A")

# 最早和最晚事件时间
try:
    min_ts = db.execute('SELECT MIN(ts) FROM events').fetchone()[0]
    max_ts = db.execute('SELECT MAX(ts) FROM events').fetchone()[0]
    print(f"\nevents 时间范围: {min_ts} ~ {max_ts}")
except:
    pass

# 数据库文件大小
size = os.path.getsize('/data/memory_agent.db')
print(f"\nDB 文件大小: {size/1024/1024:.1f} MB")

db.close()
