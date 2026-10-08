import sqlite3
from memory_agent.config import get_config
con = sqlite3.connect("file:%s?mode=ro" % get_config().db_path, uri=True)
q = lambda s: con.execute(s).fetchone()[0]
print("总行数=%s" % q("SELECT COUNT(*) FROM behavior_events"))
print("persons 为空/NULL=%s" % q("SELECT COUNT(*) FROM behavior_events WHERE persons_json IS NULL OR persons_json='' OR persons_json='[]'"))
print("非法 JSON=%s" % q("SELECT COUNT(*) FROM behavior_events WHERE json_valid(persons_json)=0"))
print("合法但非数组=%s" % q("SELECT COUNT(*) FROM behavior_events WHERE json_valid(persons_json)=1 AND json_type(persons_json)<>'array'"))
print("元素=dict=%s" % q("SELECT COUNT(*) FROM behavior_events b,json_each(CASE WHEN json_valid(b.persons_json)=1 AND json_type(b.persons_json)='array' THEN b.persons_json ELSE '[]' END) j WHERE j.type='object'"))
print("元素=str=%s" % q("SELECT COUNT(*) FROM behavior_events b,json_each(CASE WHEN json_valid(b.persons_json)=1 AND json_type(b.persons_json)='array' THEN b.persons_json ELSE '[]' END) j WHERE j.type='text'"))
print("姓名去重数=%s" % q("SELECT COUNT(DISTINCT json_extract(j.value,'$.name')) FROM behavior_events b,json_each(CASE WHEN json_valid(b.persons_json)=1 AND json_type(b.persons_json)='array' THEN b.persons_json ELSE '[]' END) j WHERE j.type='object'"))
con.close()
