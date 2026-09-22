"""行为洞察框架（Insights）。

绞杀者模式迁移中：
- 旧版实现：insights_legacy.py（3900+ 行屎山代码，保持向后兼容）
- 新版模块化实现：insights/ 包（MiMo 生成，逐步迁移中）

当前阶段：所有公开接口仍从 insights_legacy 导入，保持向后兼容。
新版模块化代码作为内部模块，可通过 insights.models / insights.service 等方式访问。
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

# 新版模块化代码的版本号
__version__ = "2.0.0-legacy"

# 迁移状态说明
MIGRATION_STATUS = {
    "phase": "legacy_compatible",
    "description": "所有公开接口仍从 insights_legacy 导入，新版模块化代码作为内部模块",
    "next_steps": [
        "1. 逐步把功能从 insights_legacy 迁移到新版模块化代码",
        "2. 每迁移一个功能，更新对应的导入",
        "3. 最终完全替换 insights_legacy",
    ],
}
