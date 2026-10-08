"""解析器子包：实体语义解析 + 时间范围解析。"""

from .entity import (
    CATEGORY_DOMAINS, KEYWORD_DOMAINS, OFF_STATES, CN_OFF_STATES, CN_ON_STATES,
    EntityResolver, category_of_domain, domain_of, domains_for, all_domains,
    is_off, is_on, is_telemetry_name, normalize_state,
)
from .timeframe import (
    parse_time, as_ts, parse_timeframe, resolve_range, strip_time_text,
    split_days, split_hours,
)

__all__ = [
    "CATEGORY_DOMAINS", "KEYWORD_DOMAINS", "OFF_STATES", "CN_OFF_STATES",
    "CN_ON_STATES", "EntityResolver", "category_of_domain", "domain_of",
    "domains_for", "all_domains", "is_off", "is_on", "is_telemetry_name",
    "normalize_state", "parse_time", "as_ts", "parse_timeframe",
    "resolve_range", "strip_time_text", "split_days", "split_hours",
]
