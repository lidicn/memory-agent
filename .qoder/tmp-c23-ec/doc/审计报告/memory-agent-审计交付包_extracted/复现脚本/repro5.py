import os, sys, traceback
os.environ.update({"JWT_SECRET":"t"*40,"DB_PATH":"/data/workspace/madata/memory_worker.db"})
sys.path.insert(0,"/data/workspace/ma/src")
print("="*66)
print("1) away_mode.AwayMode 缺失（test_perception_ingest 报 ImportError）")
print("="*66)
import memory_agent.away_mode as am
print("   模块实际导出:", [n for n in dir(am) if n[0].isupper()][:12])
try:
    from memory_agent.away_mode import AwayMode
    print("   AwayMode: 存在")
except ImportError as e:
    print("   ✗ ImportError:", e)
print()
print("="*66)
print("2) InsightService._detect_activities 缺失（test_signal_learning）")
print("="*66)
from memory_agent.insights.api import InsightService
print("   InsightService 实际方法:", sorted(n for n in dir(InsightService) if 'detect' in n or 'activit' in n))
print("   _detect_activities 存在:", hasattr(InsightService, "_detect_activities"))
