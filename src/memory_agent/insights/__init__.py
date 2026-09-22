"""行为洞察框架（Insights）。

绞杀者模式迁移中：
- 旧版实现：insights_legacy.py（3900+ 行屎山代码，保持向后兼容）
- 新版模块化实现：insights/ 包（MiMo 生成，逐步迁移中）

迁移阶段：
- Phase 0（已完成）：备份旧版，引入新框架，保持向后兼容
- Phase 1（进行中）：模型层 + 解析器层可通过子模块访问，验证新版模块可用性
- Phase 2（计划中）：逐步替换旧版中的工具函数
- Phase 3（计划中）：完全替换旧版
"""

# 从旧版实现导入所有内容，保持向后兼容
from ..insights_legacy import *  # noqa: F401,F403
from ..insights_legacy import (  # noqa: F401
    InsightService,
    _parse_attrs,
    _as_float,
    DEFAULT_DEBOUNCE_SECONDS,
    fmt_duration,
)

# Phase 1: 新版模块化代码可通过子模块访问
# 注意：不直接覆盖旧版的同名类，避免兼容性问题
# 使用方式：from memory_agent.insights.models import EventRecord
#          from memory_agent.insights.parser.entity import EntityResolver
from . import models  # noqa: F401
from . import parser  # noqa: F401
from . import anomaly  # noqa: F401
from . import report  # noqa: F401

# 新版模块化代码的版本号
__version__ = "2.0.0-phase1"

# 迁移状态说明
MIGRATION_STATUS = {
    "phase": "phase1_models_and_parsers",
    "description": "模型层 + 解析器层可通过子模块访问，旧版接口保持不变",
    "completed": [
        "Phase 0: 备份旧版 insights.py 为 insights_legacy.py",
        "Phase 0: 引入新版模块化框架（13个文件）",
        "Phase 0: 保持向后兼容，所有公开接口从 insights_legacy 导入",
        "Phase 1: 新版 models/parser/anomaly/report 模块可通过子模块访问",
    ],
    "in_progress": [
        "Phase 1: 验证新版模块的可用性和正确性",
    ],
    "next_steps": [
        "Phase 2: 逐步替换旧版中的工具函数（如 _parse_attrs, fmt_duration 等）",
        "Phase 3: 迁移 Service 层业务逻辑",
        "Phase 4: 完全替换 insights_legacy.py",
    ],
}
