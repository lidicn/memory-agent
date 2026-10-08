#!/usr/bin/env python3
"""Add query_unified_events tool definition and handler to mcp_server.py"""
import sys

file_path = r"E:\NAS\memory-agent\src\memory_agent\mcp_server.py"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# 1. Add tool definition after search_events definition
# Find the line with search_events pitfall, then the closing },
tool_def = '''    {
        "name": "query_unified_events",
        "group": "洞察",
        "summary": "vMA-1.3 多模态统一查询：跨设备事件/视觉行为/感知事件三源统一只读查询（VIEW）",
        "params": {
            "person": "人名过滤（可选）",
            "room": "房间名过滤（可选）",
            "start": "起始时间 ISO（可选）",
            "end": "结束时间 ISO（可选）",
            "days": "窗口天数，默认 7（start/end 未指定时生效）",
            "source": "来源过滤：device|vision|perception（可选）",
            "limit": "返回条数，默认 200，上限 2000",
            "offset": "分页偏移",
        },
        "example": "query_unified_events(room='客厅', days=7)",
        "pitfall": "只读工具，read scope；event_type 语义：device=entity:action, vision=action, perception=kind",
    },
'''

# Find search_events pitfall line
insert_idx = None
for i, line in enumerate(lines):
    if '"pitfall": "返回里的 total/has_more/next_offset' in line:
        # Next line should be closing },
        insert_idx = i + 2  # after the closing },
        break

if insert_idx is None:
    print("ERROR: Could not find search_events pitfall")
    sys.exit(1)

lines.insert(insert_idx, tool_def)
print(f"OK: Tool definition inserted at line {insert_idx+1}")

# 2. Add handler after search_events handler
# Find the end of search_events handler (the closing ) of asyncio.to_thread)
handler = '''
    @mcp.tool()
    async def query_unified_events(
        person: str = "",
        room: str = "",
        start: str = "",
        end: str = "",
        days: int = 7,
        source: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        """vMA-1.3 多模态统一查询：跨设备事件/视觉行为/感知事件三源统一只读查询。

        基于 unified_events VIEW（三源 UNION ALL，只读）。
        event_type 语义：device=entity:action, vision=action, perception=kind。
        只读工具，read scope。
        """
        from datetime import datetime, timedelta, timezone

        rt = get_runtime()
        now = datetime.now(timezone.utc)
        if not start and not end:
            end_dt = now
            start_dt = end_dt - timedelta(days=days)
            start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
            end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

        def _query():
            conn = rt.store.connect()
            try:
                sql = "SELECT event_id, server_ts, day, room, source, event_type, person, entity_id, confidence FROM unified_events WHERE 1=1"
                params = []
                if person:
                    sql += " AND person LIKE ?"
                    params.append(f"%{person}%")
                if room:
                    sql += " AND room LIKE ?"
                    params.append(f"%{room}%")
                if source:
                    sql += " AND source = ?"
                    params.append(source)
                if start:
                    sql += " AND server_ts >= ?"
                    params.append(start)
                if end:
                    sql += " AND server_ts <= ?"
                    params.append(end)
                # count
                count_sql = sql.replace("SELECT event_id, server_ts, day, room, source, event_type, person, entity_id, confidence", "SELECT COUNT(*)")
                total = conn.execute(count_sql, params).fetchone()[0]
                # rows
                limit = min(max(limit, 1), 2000)
                sql += " ORDER BY server_ts DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                rows = conn.execute(sql, params).fetchall()
                events = [dict(r) for r in rows]
                return {
                    "total": total,
                    "count": len(events),
                    "offset": offset,
                    "limit": limit,
                    "has_more": (offset + len(events)) < total,
                    "next_offset": offset + len(events) if (offset + len(events)) < total else None,
                    "events": events,
                }
            finally:
                conn.close()

        return await asyncio.to_thread(_query)
'''

# Find the end of search_events handler
handler_insert_idx = None
for i, line in enumerate(lines):
    if 'async def search_events(' in line:
        # Find the closing of this function - look for next @mcp.tool() or async def
        for j in range(i + 1, min(i + 100, len(lines))):
            if lines[j].strip().startswith('@mcp.tool()') or (lines[j].strip().startswith('async def ') and 'search_events' not in lines[j]):
                handler_insert_idx = j
                break
        break

if handler_insert_idx is None:
    print("ERROR: Could not find search_events handler end")
    sys.exit(1)

lines.insert(handler_insert_idx, handler)
print(f"OK: Handler inserted at line {handler_insert_idx+1}")

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"OK: mcp_server.py updated. Total lines: {len(lines)}")
