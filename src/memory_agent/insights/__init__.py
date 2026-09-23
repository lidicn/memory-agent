"""行为洞察框架（Insights）。

绞杀者模式迁移中：
- 旧版实现：insights_legacy.py（3900+ 行屎山代码，保持向后兼容）
- 新版模块化实现：insights/ 包（MiMo 生成，逐步迁移中）

迁移阶段：
- Phase 0（已完成）：备份旧版，引入新框架，保持向后兼容
- Phase 1（已完成）：模型层 + 解析器层可通过子模块访问
- Phase 2（已完成）：工具函数迁移到新版 utils.py，旧版函数名保持兼容
- Phase 3（进行中）：迁移 Service 层业务逻辑（_rhythm, _num_stale, 时间处理等）
- Phase 4（计划中）：完全替换旧版
"""

# 从旧版实现导入所有内容，保持向后兼容
from ..insights_legacy import *  # noqa: F401,F403
from ..insights_legacy import (  # noqa: F401
    InsightService,
)

# Phase 2: 工具函数从新版 utils.py 导入
# 旧版函数名保持向后兼容（别名指向新版函数）
from .utils import (  # noqa: F401
    DEFAULT_DEBOUNCE_SECONDS,
    fmt_duration,
    parse_attrs,
    as_float,
    normalize_text,
    tokenize,
    state_is_off,
    state_is_on,
    num_stale,
    parse_time_range,
    split_by_day,
    clip_to_time_range,
    summarize_events,
    fallback_name,
    make_activity,
    CATEGORY_DOMAINS,
    category_of,
    finalize_climate_session,
)

# 旧版函数名别名（保持向后兼容）
_parse_attrs = parse_attrs  # noqa: F401
_as_float = as_float  # noqa: F401
_norm = normalize_text  # noqa: F401
_tokens = tokenize  # noqa: F401
_state_is_off = state_is_off  # noqa: F401
_state_is_on = state_is_on  # noqa: F401

# Phase 3: Service 层业务逻辑迁移
# 从新版 activity.py 导入 analyze_rhythm（作息节律分析）
from .activity import analyze_rhythm  # noqa: F401

# Phase 1: 新版模块化代码可通过子模块访问
# 注意：不直接覆盖旧版的同名类，避免兼容性问题
# 使用方式：from memory_agent.insights.models import EventRecord
#          from memory_agent.insights.parser.entity import EntityResolver
from . import models  # noqa: F401
from . import parser  # noqa: F401
from . import anomaly  # noqa: F401
from . import report  # noqa: F401
from . import utils  # noqa: F401
from . import activity  # noqa: F401

# 新版模块化代码的版本号
__version__ = "2.0.0-phase3"

# 迁移状态说明
MIGRATION_STATUS = {
    "phase": "phase3_service_migration",
    "description": "Service 层业务逻辑迁移（_rhythm, _num_stale, 时间处理等），旧版方法调用新版实现",
    "completed": [
        "Phase 0: 备份旧版 insights.py 为 insights_legacy.py",
        "Phase 0: 引入新版模块化框架（13个文件）",
        "Phase 0: 保持向后兼容，所有公开接口从 insights_legacy 导入",
        "Phase 1: 新版 models/parser/anomaly/report 模块可通过子模块访问",
        "Phase 2: 创建 utils.py 模块，迁移通用工具函数",
        "Phase 2: 工具函数从新版导入，旧版函数名保持兼容（别名）",
        "Phase 3: 迁移 _rhythm 作息节律分析到新版 activity.analyze_rhythm",
        "Phase 3: 旧版 InsightService._rhythm 改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 _num_stale 到新版 utils.num_stale",
        "Phase 3: 旧版 InsightService._num_stale 改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 _parse_time_range 到新版 utils.parse_time_range",
        "Phase 3: 迁移 _split_by_day 到新版 utils.split_by_day",
        "Phase 3: 迁移 _clip_to_time_range 到新版 utils.clip_to_time_range",
        "Phase 3: 旧版三个时间处理方法改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 _summarize 到新版 utils.summarize_events",
        "Phase 3: 旧版 InsightService._summarize 改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 _fallback_name 到新版 utils.fallback_name",
        "Phase 3: 旧版 InsightService._fallback_name 改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 _act 到新版 utils.make_activity",
        "Phase 3: 旧版 InsightService._act 改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 CATEGORY_DOMAINS 常量和 category_of 方法到新版 utils",
        "Phase 3: 旧版 InsightService.category_of 改为调用新版实现（兼容包装）",
        "Phase 3: 迁移 _finalize_climate_session 到新版 utils.finalize_climate_session",
        "Phase 3: 旧版 InsightService._finalize_climate_session 改为调用新版实现（兼容包装）",
    ],
    "in_progress": [
        "Phase 3: 验证 _finalize_climate_session 迁移后的行为一致性",
    ],
    "next_steps": [
        "Phase 3: 继续迁移其他 Service 层方法（如 data_coverage, _diagnose_empty 等）",
        "Phase 4: 完全替换 insights_legacy.py",
    ],
}
