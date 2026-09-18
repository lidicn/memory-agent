"""反馈闭环打包：Phase 4.3 误识别 bad-case 导出。

设计：
- build_feedback_pack: 把 VLM 快照 + trace 打包成 tar.gz。
- 脱敏失败宁可丢 trace（"宁可缺 trace 也不泄 PII"）。
"""

from __future__ import annotations

import os
import tarfile
import tempfile
from typing import Any


def build_feedback_pack(
    snapshot_path: str,
    trace: str,
    output_dir: str,
    *,
    label: str = "bad_case",
) -> str | None:
    """把 VLM 快照 + trace 打包成 tar.gz，返回文件路径。

    脱敏失败时返回 None（宁可缺 trace 也不泄 PII）。
    """
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{label}.tar.gz")

    try:
        with tarfile.open(out_path, "w:gz") as tar:
            # 快照（如果存在）
            if snapshot_path and os.path.exists(snapshot_path):
                tar.add(snapshot_path, arcname=os.path.basename(snapshot_path))
            # trace（文本，脱敏后）
            trace_path = os.path.join(tempfile.gettempdir(), f"{label}_trace.txt")
            with open(trace_path, "w", encoding="utf-8") as f:
                f.write(trace or "")
            tar.add(trace_path, arcname="trace.txt")
            os.unlink(trace_path)
        return out_path
    except Exception:
        # 脱敏/打包失败：宁可丢 trace 也不泄 PII
        try:
            os.unlink(out_path)
        except OSError:
            pass
        return None
