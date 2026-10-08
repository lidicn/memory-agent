"""修复 utils.py U-2: summarize_events 数值 ts 崩溃 + new_state/state 键不一致"""
from datetime import datetime
path = r'E:\NAS\memory-agent\src\memory_agent\insights\utils.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '''        slot["changes"] += 1
        st = str(r.get("new_state", ""))
        slot["states"][st] = slot["states"].get(st, 0) + 1
        ts = r.get("ts", "")
        if ts:
            slot["first_ts"] = min(slot["first_ts"] or ts, ts)
            slot["last_ts"] = max(slot["last_ts"] or ts, ts)
            try:
                by_hour[int(ts[11:13])] += 1
            except (ValueError, IndexError):
                pass'''

new = '''        slot["changes"] += 1
        # 兼容 new_state（旧）和 state（新 EventRecord.to_dict）两种键
        st = str(r.get("new_state", r.get("state", "")))
        slot["states"][st] = slot["states"].get(st, 0) + 1
        ts = r.get("ts", "")
        if ts:
            # 归一化 ts：float 时间戳或 ISO 字符串都能处理
            # 之前 ts[11:13] 对 float 抛 TypeError，且 except 只捕获 ValueError/IndexError
            try:
                if isinstance(ts, (int, float)):
                    ts_val = float(ts)
                    hour = datetime.fromtimestamp(ts_val).hour
                elif isinstance(ts, str) and len(ts) >= 13:
                    hour = int(ts[11:13])
                    ts_val = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
                else:
                    ts_val = None
                    hour = None
                if ts_val is not None:
                    slot["first_ts"] = ts_val if slot["first_ts"] in (None, "") else min(float(slot["first_ts"]), ts_val)
                    slot["last_ts"] = ts_val if slot["last_ts"] in (None, "") else max(float(slot["last_ts"]), ts_val)
                if hour is not None:
                    by_hour[hour] += 1
            except (ValueError, IndexError, TypeError, OSError):
                pass'''

assert old in src, "U-2 old pattern not found"
src = src.replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print("U-2 (summarize_events ts type + state key) fixed successfully")
