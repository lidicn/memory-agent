"""修复 utils.py U-1: 两套 on/off 判定不一致，统一委托 parser.entity"""
path = r'E:\NAS\memory-agent\src\memory_agent\insights\utils.py'
with open(path, 'r', encoding='utf-8') as f:
    src = f.read()

# 在导入区添加从 parser.entity 导入（放在 typing 导入之后）
old_imports = '''from typing import Any, Dict, List, Optional, Tuple

#: 抖动阈值'''
new_imports = '''from typing import Any, Dict, List, Optional, Tuple

# 统一委托 parser.entity 的状态判定，避免新旧两套常量口径不一致（U-1）
from .parser.entity import (
    OFF_STATES as _ENTITY_OFF_STATES,
    CN_OFF_STATES as _ENTITY_CN_OFF_STATES,
    CN_ON_STATES as _ENTITY_CN_ON_STATES,
    is_off as _entity_is_off,
    is_on as _entity_is_on,
    category_of_domain as _entity_category_of_domain,
)

#: 抖动阈值'''
assert old_imports in src, "imports pattern not found"
src = src.replace(old_imports, new_imports)

# 替换模块级常量定义为别名（指向 parser.entity）
old_consts = '''#: 关闭状态集合（英文）
OFF_STATES = frozenset({
    "off", "closed", "not_home", "unavailable", "unknown",
    "standby", "idle", "paused", "stopped",
})

#: 关闭状态集合（中文）
CN_OFF_STATES = frozenset({"关", "关闭", "门关", "闭合", "断开", "无", "否", "0"})

#: 开启状态集合（中文）
CN_ON_STATES = frozenset({"开", "打开", "开启", "有", "是", "1", "on"})'''
new_consts = '''#: 关闭状态集合（英文）—— 统一委托 parser.entity，避免口径分裂
OFF_STATES = _ENTITY_OFF_STATES

#: 关闭状态集合（中文）—— 统一委托 parser.entity
CN_OFF_STATES = _ENTITY_CN_OFF_STATES

#: 开启状态集合（中文+英文）—— 统一委托 parser.entity
CN_ON_STATES = _ENTITY_CN_ON_STATES'''
assert old_consts in src, "constants pattern not found"
src = src.replace(old_consts, new_consts)

# 替换 state_is_off/state_is_on 函数
old_funcs = '''def state_is_off(state: Any) -> bool:
    """判断状态是否为关闭。"""
    s = normalize_text(state)
    return s in OFF_STATES or s in CN_OFF_STATES


def state_is_on(state: Any) -> bool:
    """判断状态是否为开启。"""
    s = normalize_text(state)
    return s in CN_ON_STATES'''
new_funcs = '''def state_is_off(state: Any) -> bool:
    """判断状态是否为关闭。统一委托 parser.entity.is_off。"""
    return _entity_is_off(state)


def state_is_on(state: Any) -> bool:
    """判断状态是否为开启。统一委托 parser.entity.is_on。"""
    return _entity_is_on(state)'''
assert old_funcs in src, "state functions pattern not found"
src = src.replace(old_funcs, new_funcs)

# 替换 category_of 函数（如果存在）
old_cat = '''def category_of(domain: str) -> str:'''
# 只检查是否存在，存在的话看它的实现
if old_cat in src:
    # 找到 category_of 的完整实现并替换
    import re
    # 找到函数定义到下一个 def 或文件末尾
    cat_start = src.find(old_cat)
    # 找到函数体结束（下一个顶层 def 或类定义）
    next_def = src.find("\ndef ", cat_start + 10)
    next_class = src.find("\nclass ", cat_start + 10)
    ends = [x for x in [next_def, next_class] if x > 0]
    cat_end = min(ends) if ends else len(src)
    old_cat_full = src[cat_start:cat_end]
    new_cat_full = '''def category_of(domain: str) -> str:
    """设备类别。统一委托 parser.entity.category_of_domain。"""
    return _entity_category_of_domain(domain)

'''
    src = src.replace(old_cat_full, new_cat_full)
    print("category_of also delegated")

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print("U-1 (unified on/off state判定) fixed successfully")
