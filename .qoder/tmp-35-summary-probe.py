"""路线图 §3.5 验收真跑：三个汇总查询工具在生产数据上可调用 + 返回结构化数据。

只读；参数从库里自选，避免凭空参被判成"无数据"。输出只打键名/计数，不打成员姓名等值。
"""
import asyncio, json, sqlite3, sys
sys.path.insert(0, "/app/src")

from memory_agent.config import get_config  # noqa: E402

DB = get_config().db_path
print("DB_PATH =", DB)
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def pick(sql, params=()):
    try:
        return con.execute(sql, params).fetchone()
    except Exception as exc:
        print("PICK_SKIP:", type(exc).__name__, str(exc)[:120])
        return None


room = pick(
    "SELECT room FROM behavior_events WHERE IFNULL(status,'')='ok' AND room<>'' "
    "GROUP BY room ORDER BY COUNT(*) DESC LIMIT 1"
)
entity = pick(
    "SELECT entity_id FROM events WHERE entity_id IS NOT NULL AND entity_id<>'' "
    "GROUP BY entity_id ORDER BY COUNT(*) DESC LIMIT 1"
)
member = pick("SELECT id, name FROM members ORDER BY created_at LIMIT 1")
member_id = member["id"] if member else ""
member_name = member["name"] if member else ""
# 挑该成员真有行为数据的一天，避免把"空时间线"误读成"工具不可用"
day = pick(
    "SELECT substr(ts,1,10) d FROM behavior_states WHERE member=? "
    "GROUP BY d ORDER BY MAX(ts) DESC LIMIT 1",
    (member_name,),
) if member_name else None
con.close()

ARGS = {
    "get_device_usage_summary": {"entity_id": entity["entity_id"] if entity else "", "days": 7},
    "get_room_behavior_summary": {"room": room["room"] if room else "", "days": 7},
    "get_member_daily_pattern": {
        "member_id": member_id,
        "date": day["d"] if day else "",
    },
}
print("PICKED =", {
    "has_entity": bool(entity), "has_room": bool(room),
    "has_member": bool(member_id), "day_with_member_data": day["d"] if day else "",
})

from memory_agent import mcp_server  # noqa: E402

server = mcp_server.mcp_server
print("PROD_LIST_TOOLS =", len(asyncio.run(server.list_tools())))

for tool, args in ARGS.items():
    try:
        res = asyncio.run(server.call_tool(tool, args))
        is_err = bool(getattr(res, "isError", False))
        txt = "\n".join(getattr(c, "text", None) or str(c) for c in (res.content or []))
        try:
            data = json.loads(txt)
            keys, parseable = sorted(data)[:9], True
        except Exception:
            keys, parseable = [txt[:120]], False
        rows = {k: len(v) for k, v in (data.items() if parseable else ()) if isinstance(v, list)}
        print(f"CALL {tool}: isError={is_err} parseable={parseable} "
              f"bytes={len(txt.encode('utf-8'))} keys={keys} list_counts={rows}")
    except Exception as e:
        print(f"CALL {tool}: EXC {type(e).__name__}: {str(e)[:160]}")
