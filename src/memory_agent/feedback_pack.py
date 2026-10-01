"""反馈闭环打包：Phase 4.3 误识别 bad-case 导出。

设计：
- build_feedback_pack: 把 VLM 快照 + trace 打包成 tar.gz。
- PII 脱敏：手机号/邮箱/身份证/银行卡/地址等敏感信息替换为 <REDACTED>。
- fail-closed：脱敏失败宁可丢 trace（"宁可缺 trace 也不泄 PII"）。
- 快照路径白名单：只允许 /data/ 下的快照，防止任意文件打包。
"""

from __future__ import annotations

import os
import re
import tarfile
import tempfile


# ── PII 脱敏正则 ──────────────────────────────────────────────────────────
# 手机号（1xx-xxxx-xxxx）
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 邮箱
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# 身份证（18位，最后一位可为X）
_ID_CARD_RE = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
# 银行卡（16-19位数字）
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
# IPv4 地址
_IPV4_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
# MAC 地址
_MAC_RE = re.compile(r"(?i)(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")
# JWT token（eyJ...）
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
# API key（sk- 开头）
_API_KEY_RE = re.compile(r"sk-[A-Za-z0-9]{16,}")
# Bearer token
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}")


def sanitize_text(text: str) -> str:
    """对文本做 PII 脱敏，返回脱敏后的文本。

    脱敏规则：
    - 手机号 → <REDACTED-PHONE>
    - 邮箱 → <REDACTED-EMAIL>
    - 身份证 → <REDACTED-ID_CARD>
    - 银行卡 → <REDACTED-BANK_CARD>
    - IPv4 → <REDACTED-IP>
    - MAC → <REDACTED-MAC>
    - JWT → <REDACTED-JWT>
    - API key → <REDACTED-API_KEY>
    - Bearer token → <REDACTED-TOKEN>
    """
    if text is None:
        return None
    if not text:
        return ""
    result = text
    result = _JWT_RE.sub("<REDACTED-JWT>", result)
    result = _API_KEY_RE.sub("<REDACTED-API_KEY>", result)
    result = _BEARER_RE.sub("<REDACTED-TOKEN>", result)
    result = _ID_CARD_RE.sub("<REDACTED-ID_CARD>", result)
    result = _PHONE_RE.sub("<REDACTED-PHONE>", result)
    result = _BANK_CARD_RE.sub("<REDACTED-BANK_CARD>", result)
    result = _EMAIL_RE.sub("<REDACTED-EMAIL>", result)
    result = _MAC_RE.sub("<REDACTED-MAC>", result)
    result = _IPV4_RE.sub("<REDACTED-IP>", result)
    return result


def _is_safe_snapshot_path(path: str) -> bool:
    """检查快照路径是否在白名单内（只允许 /data/ 下的文件）。

    防止通过 snapshot_path 参数打包任意文件（如 /etc/passwd、配置文件等）。
    """
    if not path:
        return False
    # 规范化路径，解析 .. 和符号链接
    real_path = os.path.realpath(path)
    # 只允许 /data/ 下的文件
    return real_path.startswith("/data/")


def build_feedback_pack(
    snapshot_path: str,
    trace: str,
    output_dir: str,
    *,
    label: str = "bad_case",
    sanitize: bool = True,
) -> str | None:
    """把 VLM 快照 + trace 打包成 tar.gz，返回文件路径。

    Args:
        snapshot_path: VLM 快照路径（必须在 /data/ 白名单内）
        trace: trace 文本（会做 PII 脱敏）
        output_dir: 输出目录
        label: 包标签（默认 bad_case）
        sanitize: 是否对 trace 做 PII 脱敏（默认 True）

    Returns:
        打包后的文件路径，失败时返回 None（fail-closed）

    fail-closed 策略：
    - 快照路径不在白名单内 → 不打包快照，但仍打包 trace
    - trace 脱敏失败 → 不打包 trace，但仍打包快照
    - 整体打包失败 → 删除已生成的文件，返回 None
    """
    os.makedirs(output_dir, exist_ok=True)
    # 清理 label 中的路径分隔符，防止路径遍历
    safe_label = re.sub(r"[^\w\-]", "_", label) or "bad_case"
    out_path = os.path.join(output_dir, f"{safe_label}.tar.gz")

    snapshot_included = False
    trace_included = False
    trace_sanitized = ""

    # 1. 检查快照路径白名单
    if snapshot_path and _is_safe_snapshot_path(snapshot_path) and os.path.exists(snapshot_path):
        snapshot_included = True

    # 2. 对 trace 做 PII 脱敏
    if trace:
        try:
            trace_sanitized = sanitize_text(trace) if sanitize else trace
            trace_included = True
        except Exception:
            # 脱敏失败：fail-closed，不包含 trace
            trace_included = False

    # 3. 如果快照和 trace 都没有，直接返回 None
    if not snapshot_included and not trace_included:
        return None

    try:
        with tarfile.open(out_path, "w:gz") as tar:
            # 快照（已验证白名单）
            if snapshot_included:
                tar.add(snapshot_path, arcname=os.path.basename(snapshot_path))
            # trace（已脱敏）
            if trace_included:
                trace_path = os.path.join(tempfile.gettempdir(), f"{safe_label}_trace.txt")
                with open(trace_path, "w", encoding="utf-8") as f:
                    f.write(trace_sanitized)
                tar.add(trace_path, arcname="trace.txt")
                os.unlink(trace_path)
            # 元信息（不包含 PII）
            meta_path = os.path.join(tempfile.gettempdir(), f"{safe_label}_meta.json")
            import json
            meta = {
                "label": safe_label,
                "snapshot_included": snapshot_included,
                "trace_included": trace_included,
                "trace_sanitized": sanitize and trace_included,
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            tar.add(meta_path, arcname="meta.json")
            os.unlink(meta_path)
        return out_path
    except Exception:
        # 打包失败：fail-closed，删除已生成的文件
        try:
            if os.path.exists(out_path):
                os.unlink(out_path)
        except OSError:
            pass
        return None
