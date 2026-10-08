"""修复 activity.py AC-1: _evaluate() 必要/可选信号索引错配"""
path = r'E:\NAS\memory-agent\src\memory_agent\insights\activity.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

old = '''        req_idx = [i for i, s in enumerate(signals) if not s.optional][:len(rule.requires)]
        opt_idx = [i for i, s in enumerate(signals) if s.optional or i >= len(rule.requires)]
        if not all(ok(i) for i in req_idx):
            return None
        if opt_idx and not any(ok(i) for i in opt_idx):
            return None'''

new = '''        # 按位置切分：前 len(requires) 个是必要信号，之后是 any_of 可选信号
        # 之前用 optional 标志切分 + 切片，导致必要信号被漏检、可选被强制要求
        n_req = len(rule.requires)
        required = [i for i in range(n_req) if not signals[i].optional]
        optional = list(range(n_req, len(signals))) + \
                   [i for i in range(n_req) if signals[i].optional]
        if required and not all(ok(i) for i in required):
            return None
        if optional and not any(ok(i) for i in optional):
            return None'''

assert old in src, "AC-1 old pattern not found"
src = src.replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print("AC-1 (activity._evaluate signal index) fixed successfully")
