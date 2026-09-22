"""通用工具函数：从旧版 insights.py 迁移的工具函数集合。

这些函数都是无状态的纯函数，易于测试和复用。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

#: 抖动阈值：短于该秒数的开启片段视为误触，不计入时长统计。
DEFAULT_DEBOUNCE_SECONDS: int = 5

#: 关闭状态集合（英文）
OFF_STATES = frozenset({
    "off", "closed", "not_home", "unavailable", "unknown",
    "standby", "idle", "paused", "stopped",
})

#: 关闭状态集合（中文）
CN_OFF_STATES = frozenset({"关", "关闭", "门关", "闭合", "断开", "无", "否", "0"})

#: 开启状态集合（中文）
CN_ON_STATES = frozenset({"开", "打开", "开启", "有", "是", "1", "on"})


def normalize_text(text: Any) -> str:
    """标准化文本：转字符串、去空白、转小写。"""
    return str(text or "").strip().lower()


def tokenize(text: str) -> List[str]:
    """粗分词：中文按连续汉字块 + 英文按单词切。够用且零依赖。"""
    return [t for t in re.split(r"[\s,，、_./|-]+", normalize_text(text)) if t]


def state_is_off(state: Any) -> bool:
    """判断状态是否为关闭。"""
    s = normalize_text(state)
    return s in OFF_STATES or s in CN_OFF_STATES


def state_is_on(state: Any) -> bool:
    """判断状态是否为开启。"""
    s = normalize_text(state)
    return s in CN_ON_STATES


def fmt_duration(total_seconds: float) -> str:
    """格式化时长为人类可读字符串。

    示例：
        fmt_duration(3661) -> "1小时1分"
        fmt_duration(90061) -> "1天1小时1分"
        fmt_duration(45) -> "45秒"
    """
    total = max(0, int(total_seconds))
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}天{h}小时{m}分"
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def parse_attrs(raw: Any) -> Dict[str, Any]:
    """解析属性，支持 dict 和 JSON 字符串。

    解析失败时返回空 dict，不抛异常。
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


def as_float(v: Any) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "OFF_STATES",
    "CN_OFF_STATES",
    "CN_ON_STATES",
    "normalize_text",
    "tokenize",
    "state_is_off",
    "state_is_on",
    "fmt_duration",
    "parse_attrs",
    "as_float",
]
