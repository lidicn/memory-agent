import sqlite3
con = sqlite3.connect(":memory:")
for js in ('["甲乙"]', '[{"name":"甲乙"}]', '[123,"x",null,{"no":1}]'):
    print(js)
    for r in con.execute("SELECT key, type, value, quote(value) FROM json_each(?)", (js,)):
        print("   key=%s type=%s value=%r quoted=%s" % (r[0], r[1], r[2], r[3]))
print("match_text=%s" % con.execute(
    "SELECT COUNT(*) FROM json_each('[\"甲乙\"]') j WHERE j.type='text' AND j.value='甲乙'").fetchone()[0])
print("match_string=%s" % con.execute(
    "SELECT COUNT(*) FROM json_each('[\"甲乙\"]') j WHERE j.type='string' AND j.value='甲乙'").fetchone()[0])
