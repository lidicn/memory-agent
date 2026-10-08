"""生产库 `events.ts` 的**字符串形状**：定长到秒，还是带小数秒？

这决定乙′ 夹紧窗口右边界能不能用「end_iso + 1 秒」的半开上界替掉 `ts <= end_iso`
而不多给/少给任何一行。`_to_iso` 侧是 `timespec="seconds"`（定长 19），但库里存的
是别的写入方给的串，必须实测，不能按 `_to_iso` 推。
"""
import sqlite3

con = sqlite3.connect("/data/memory_agent.db")
print("LEN_GROUPS=", con.execute(
    "SELECT length(ts), COUNT(*) FROM events GROUP BY 1 ORDER BY 1").fetchall())
print("HAS_DOT=", con.execute(
    "SELECT COUNT(*) FROM events WHERE ts LIKE '%.%'").fetchone()[0])
print("SAMPLE_DESC=", [r[0] for r in con.execute(
    "SELECT ts FROM events ORDER BY ts DESC LIMIT 3").fetchall()])
print("SAMPLE_ASC=", [r[0] for r in con.execute(
    "SELECT ts FROM events ORDER BY ts ASC LIMIT 3").fetchall()])
print("DAY_NE_TS=", con.execute(
    "SELECT COUNT(*) FROM events WHERE day != substr(ts,1,10)").fetchone()[0])
con.close()
print("SHAPE_DONE")
