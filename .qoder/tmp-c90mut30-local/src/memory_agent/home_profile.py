"""家庭档案（home_profile）：Phase 2.3 原子写 + 权重截断。

设计：
- ``write_profile_atomic``：临时文件 + rename，并发写不读半截。
- ``truncate_by_weight``：按 trust 降序，超长自动截断高权重优先。
- ``build_profile``：从 agent_memories 查 live 记忆，生成 profile.md。
"""

from __future__ import annotations

import os
import tempfile
from typing import Any


def write_profile_atomic(path: str, content: str) -> None:
    """原子写 profile.md：临时文件 + os.replace，并发写不读半截。"""
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # 原子替换
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def truncate_by_weight(memories: list[dict], max_chars: int) -> list[dict]:
    """按 trust 降序截断记忆列表，总字符数不超过 max_chars。

    高权重（trust 高）的记忆优先保留；低权重的超长记忆自动丢弃。
    """
    ranked = sorted(memories, key=lambda m: float(m.get("trust") or 0.0), reverse=True)
    kept: list[dict] = []
    total = 0
    for m in ranked:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        size = len(text)
        if total + size > max_chars:
            break  # 权重降序，低权重的直接丢
        kept.append(m)
        total += size
    return kept


def build_profile(store: Any, max_chars: int = 4000) -> str:
    """从 agent_memories 查 live 记忆，按 trust 降序截断，生成 profile.md 文本。"""
    mems = store.list_agent_memories(state="live", limit=500)
    habit_mems = [m for m in mems if (m.get("topic_key") or "").startswith("habit:")]
    kept = truncate_by_weight(habit_mems, max_chars)

    lines = ["# 家庭画像", ""]
    for m in kept:
        topic = m.get("topic_key") or ""
        text = (m.get("text") or "").strip()
        trust = float(m.get("trust") or 0.0)
        lines.append(f"## {topic}")
        lines.append(f"- {text}")
        lines.append(f"- 权重：{trust:.2f}")
        lines.append("")
    return "\n".join(lines)
