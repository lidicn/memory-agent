import sys
sys.path.insert(0, "/app/src")
from memory_agent.insights import InsightService
from memory_agent.insights.utils import tags_of

eid = "binary_sensor.study_door_contact"
print(f"direct tags_of({eid}) = {tags_of(eid, '')}")
try:
    r = InsightService._tags_of(eid, "")
    print(f"InsightService._tags_of({eid}) = {r}")
except Exception as e:
    print(f"InsightService._tags_of ERROR: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

print(f"\nInsightService type: {type(InsightService)}")
print(f"_tags_of type: {type(InsightService.__dict__.get('_tags_of'))}")
