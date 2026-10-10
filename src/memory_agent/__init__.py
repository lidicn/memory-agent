"""Memory Agent - 家庭行为记忆中枢"""

# 计划号：路线图/交接单对外的版本口径（vMA-x.y.z 里的 x.y.z）。
# DCD 裁定 20261002 Q7：`adm/*/caps.version` 报计划号而非包版本——DB/AF 读 caps 时
# 把包版本当成版本会以为 MA 停在 1.0，而各仓沟通用的是计划号。
# 每次按计划增量交付时与路线图同步上调这一处，caps/自检页同时跟随。
PLAN_VERSION = "1.2.3"


def _resolve_version() -> str:
    import json
    import os

    data_dir = os.getenv("MA_DATA_DIR", "/data")
    preview = os.path.join(data_dir, ".update_available.json")
    try:
        if os.path.isfile(preview):
            with open(preview, encoding="utf-8") as f:
                d = json.load(f)
            commit = (d.get("local_commit") or "")[:7]
            if commit:
                return f"{PLAN_VERSION}+{commit}"
    except Exception:
        pass
    return PLAN_VERSION


__version__ = _resolve_version()
del _resolve_version
