"""Memory Agent - 家庭行为记忆中枢"""

# 包版本：pyproject 与 pip 元数据用的内部实现细节。
__version__ = "1.0.0"

# 计划号：路线图/交接单对外的版本口径（vMA-x.y.z 里的 x.y.z）。
# DCD 裁定 20261002 Q7：`adm/*/caps.version` 报计划号而非包版本——DB/AF 读 caps 时
# 把包版本当成版本会以为 MA 停在 1.0，而各仓沟通用的是计划号。
# 每次按计划增量交付时与路线图同步上调这一处，caps/自检页同时跟随。
PLAN_VERSION = "1.2.3"
