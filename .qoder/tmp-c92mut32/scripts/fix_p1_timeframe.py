"""修复 timeframe.py 的 T-1（时区）和 T-2（中文数字 X十Y）"""
path = r'E:\NAS\memory-agent\src\memory_agent\insights\parser\timeframe.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

# T-2: 修复 _num() X十Y 中文数字
old_num = '''    if len(text) == 2 and text[1] == "十":
        return float(_CN_NUM.get(text[0], 0)) * 10
    return 1.0'''
new_num = '''    if len(text) == 2 and text[1] == "十":
        return float(_CN_NUM.get(text[0], 0)) * 10
    # 补全 "X十Y" 型（如"二十五"→25.0），之前恒返回 1.0
    if "十" in text and len(text) == 3:
        head, _, tail = text.partition("十")
        if head in _CN_NUM or head == "":
            tens = 10.0 if head in ("", "一") else float(_CN_NUM.get(head, 0)) * 10
            ones = float(_CN_NUM.get(tail, 0)) if tail else 0.0
            return tens + ones
    return 1.0'''
assert old_num in src, "T-2 old pattern not found"
src = src.replace(old_num, new_num)

# T-1: 修复 parse_time() 时区转换
old_iso = '''    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None'''
new_iso = '''    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        # 带时区的输入先转本地时区再去 tzinfo，之前直接丢弃偏移导致窗口偏移
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.replace(tzinfo=None, microsecond=0)
    except ValueError:
        return None'''
assert old_iso in src, "T-1 old pattern not found"
src = src.replace(old_iso, new_iso)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print("T-1 (timezone) and T-2 (Chinese numerals X十Y) fixed successfully")
