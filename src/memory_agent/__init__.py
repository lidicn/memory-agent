"""Memory Agent - 家庭行为记忆中枢"""

# 包版本：pyproject 与 pip 元数据用的内部实现细节。
# 运行时从宿主预检脚本写的 .update_available.json 读取当前 git commit，
# 拼成 1.0.0+<short_hash> 形式，使 WebUI 能直观看到真实部署版本。
_BASE_VERSION = "1.0.0"


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
                return f"{_BASE_VERSION}+{commit}"
    except Exception:
        pass
    return _BASE_VERSION


__version__ = _resolve_version()
del _resolve_version

# 计划号：路线图/交接单对外的版本口径（vMA-x.y.z 里的 x.y.z）。
# DCD 裁定 20261002 Q7：`adm/*/caps.version` 报计划号而非包版本——DB/AF 读 caps 时
# 把包版本当成版本会以为 MA 停在 1.0，而各仓沟通用的是计划号。
# 每次按计划增量交付时与路线图同步上调这一处，caps/自检页同时跟随。
PLAN_VERSION = "1.2.3"
