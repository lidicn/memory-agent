"""反馈闭环打包：Phase 4.3 误识别 bad-case 导出。

设计：
- build_feedback_pack: 把 VLM 快照 + trace 打包成 tar.gz。
- S3（出境口径）：手机号/邮箱/身份证/银行卡/IP/MAC 网络地址/凭据串替换为 <REDACTED>。
- 分层（DCD 20261004 MA-裁3 Q1=A）：包内 ``trace.txt`` 保持 S3 原样（收件方有名册时
  才能还原「谁在哪个房间做了什么」），**另附**一份 ``trace_anon.txt``——在 S3 的输出之上
  再走 S1 入库口径（``Store.sanitize_feedback_text``，成员姓名 → ``成员N``）。
  两份同时在场，选哪份出境由出境动作决定，MA 不预设收件方有没有名册。
- label 白名单（裁3 Q2，收紧入参、不改产物格式）：label 会成为文件名，只允许
  ``vlm_failed-{room}-{date}`` 这一类「类型词 + 房间/日期/序号/ASCII slug」结构。
- fail-closed：脱敏失败宁可丢 trace（"宁可缺 trace 也不泄 PII"）；S1 入口缺失或抛异常
  时**不产** ``trace_anon.txt``，绝不产一份"名字像 anon、里面却有姓名"的文件。
- 快照路径白名单：只允许 /data/ 下的快照，防止任意文件打包。
"""

from __future__ import annotations

import os
import re
import tarfile
import tempfile
from typing import Callable, Iterable


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


# ── label 白名单（DCD 20261004 MA-裁3 Q2）─────────────────────────────────
#: 允许出现在开头的类型词。前三枚是 ``behavior_events.status`` 的既有闭集值
#: （见 store.py 建表注释 ``ok | vlm_failed | low_confidence | skipped``，
#: bad-case 包本来就是从这些状态里导出的），``bad_case`` 是两个 HTTP 端点的
#: 历史默认 label——留着是为了不改已交付产物的文件名。
LABEL_KINDS: tuple[str, ...] = ("vlm_failed", "low_confidence", "skipped", "bad_case")

#: 类型词之后允许的段落：日期 / 纯数字（事件 id 一类）/ 小写 ASCII slug。
#: ``re.ASCII`` 是必需的而不是修饰：默认 Unicode 下 ``\d`` 认全角 ``１`` 和
#: 阿拉伯-印度数字，那样 ``vlm_failed-１２３`` 也能过关，白名单就只剩形状不管内容。
_LABEL_SEGMENT_RE = re.compile(
    r"(?:(?:19|20)\d{2}-?(?:0[1-9]|1[0-2])-?(?:0[1-9]|[12]\d|3[01]))"
    r"|(?:(?:\d{1,12}))"
    r"|(?:(?:[a-z][a-z0-9]{0,31}))",
    re.ASCII,
)
_LABEL_SEG_SEP = "-_"

#: 写进 meta.json，让收件方知道第二份走的是哪一口径，而不是 ``trace.txt`` 的副本。
ANON_SANITIZER_ID = "S1"


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

    ⚠️ 这一套（S3）**不认识成员姓名**，而且这是裁定后的口径：出境包的
    ``trace.txt`` 就是留姓名可读的那一份，匿名那一份另开文件走 S1
    （DCD 20261004 MA-裁3 Q1=A）。想在这里加姓名脱敏 = 把分层裁掉，先申请裁定。
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


def _match_label_kind(label: str) -> tuple[str, str]:
    """拆出开头的类型词，返回 ``(kind, 剩余部分)``；不匹配返回 ``("", "")``。"""
    for kind in sorted(LABEL_KINDS, key=len, reverse=True):
        if label == kind:
            return kind, ""
        if label.startswith(kind) and label[len(kind)] in _LABEL_SEG_SEP:
            return kind, label[len(kind) + 1:]
    return "", ""


def validate_label(label: str, *, known_rooms: Iterable[str] = (),
                   member_names: Iterable[str] = ()) -> tuple[bool, str, str]:
    """出境包 label 的白名单校验 → ``(通过?, 理由, 文件名主干)``。

    三条全过才算通过：
    1. 开头是 ``LABEL_KINDS`` 里的类型词；
    2. 其后每一段只能是「已知房间名」/ 日期 / 数字 / 小写 ASCII slug，段间用 ``-`` 或 ``_``；
    3. 整串不含成员名册里的任何姓名（长度 ≥2 的都做子串比对）。

    房间名必须点名（在 ``known_rooms`` 里）才认：中文段落裸放行等于把 label 退回自由文本，
    而「姓名和房间名在中文里长得一模一样」正是本校验要挡的那件事——所以 ``known_rooms``
    为空时中文一律不认，读不到名册只会让校验**更严**，不会更松。
    ``member_names`` 为空只可能出现在没有库的调用点；HTTP 面两处都带真名册进来
    （``api/behavior_routes.py`` 的 ``_label_rosters``），名册读失败是报错而不是放行。
    理由串里不回显命中的姓名——它会被写进 HTTP 响应。
    """
    raw = str(label or "").strip()
    if not raw:
        return False, "label 为空", ""
    if raw[-1] in _LABEL_SEG_SEP:
        # 尾分隔符会原样长进文件名（`vlm_failed-.tar.gz`），且它多半是拼接时漏了段落。
        return False, "label 不能以 - 或 _ 结尾", ""
    names = [str(name).strip() for name in member_names if str(name or "").strip()]
    hits = [name for name in names if len(name) >= 2 and name.lower() in raw.lower()]
    if hits:
        return False, f"label 含成员姓名（命中 {len(hits)} 处），出境面白名单禁止", ""
    kind, rest = _match_label_kind(raw)
    if not kind:
        return False, f"label 必须以白名单类型词开头：{'/'.join(sorted(LABEL_KINDS))}", ""
    rooms = sorted({str(room).strip() for room in known_rooms if str(room or "").strip()},
                   key=lambda n: (-len(n), n))
    cursor = rest
    while cursor:
        room = next((r for r in rooms if cursor.startswith(r)), "")
        if room:
            cursor = cursor[len(room):]
        else:
            seg = _LABEL_SEGMENT_RE.match(cursor)
            if not seg:
                return False, f"label 段落 '{cursor[:12]}' 不在白名单结构内", ""
            cursor = cursor[seg.end():]
        if not cursor:
            break
        # 分隔符只认**一枚**：`vlm_failed--书房` 里第二段会以 `-` 开头，落进段落匹配再判红。
        if cursor[0] not in _LABEL_SEG_SEP:
            return False, "label 段落之间只能用单个 - 或 _ 分隔", ""
        cursor = cursor[1:]
    return True, "", re.sub(r"[^\w\-]", "_", raw)


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


def _add_text_member(tar: tarfile.TarFile, arcname: str, content: str, stem: str) -> None:
    """把文本先落成临时文件再入包。

    沿用原实现的文件式 ``tar.add``（而不是 ``addfile`` + BytesIO）：成员头来自那个临时
    文件，与已交付的老包同形状，"加一份产物"不该顺带改掉另一份的元数据。
    """
    path = os.path.join(tempfile.gettempdir(), f"{stem}_{arcname}")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        tar.add(path, arcname=arcname)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def build_feedback_pack(
    snapshot_path: str,
    trace: str,
    output_dir: str,
    *,
    label: str = "bad_case",
    sanitize: bool = True,
    anon_sanitizer: Callable[[str], str] | None = None,
    known_rooms: Iterable[str] = (),
    member_names: Iterable[str] = (),
) -> str | None:
    """把 VLM 快照 + trace 打包成 tar.gz，返回文件路径。

    Args:
        snapshot_path: VLM 快照路径（必须在 /data/ 白名单内）
        trace: trace 文本（会做 PII 脱敏）
        output_dir: 输出目录
        label: 包标签（默认 bad_case），必须过 ``validate_label`` 白名单
        sanitize: 是否对 trace 做 PII 脱敏（默认 True）
        anon_sanitizer: S1 入库口径的脱敏函数（生产传 ``store.sanitize_feedback_text``）。
            给了才产 ``trace_anon.txt``；不给/抛异常/返回空 → 不产这一份，meta 里如实记。
        known_rooms: 房间名名册（label 里的中文段落只认点过名的）
        member_names: 成员姓名名册（label 里出现任何一枚都拒）

    Returns:
        打包后的文件路径，失败时返回 None（fail-closed）

    fail-closed 策略：
    - label 不过白名单 → 不落任何文件（label 会成为文件名，姓名最容易从这一层出去）
    - 快照路径不在白名单内 → 不打包快照，但仍打包 trace
    - trace 脱敏失败 → 不打包 trace，但仍打包快照
    - 整体打包失败 → 删除已生成的文件，返回 None
    """
    os.makedirs(output_dir, exist_ok=True)
    passed, _reason, safe_label = validate_label(
        label, known_rooms=known_rooms, member_names=member_names)
    if not passed or not safe_label:
        return None
    out_path = os.path.join(output_dir, f"{safe_label}.tar.gz")

    snapshot_included = False
    trace_included = False
    trace_sanitized = ""
    trace_anon: str | None = None

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
        # 2b. 分层的第二份：S3 输出之上再走 S1（姓名 → 成员N）。
        #     没有 S1 入口就不产这一份，而不是拿 S3 的结果冒充——收件方是按文件名选口径的，
        #     一份「叫 anon 却没脱姓名」的文件比缺文件危险得多。
        if trace_included and anon_sanitizer is not None:
            try:
                anon_out = anon_sanitizer(trace_sanitized)
            except Exception:
                anon_out = None
            trace_anon = anon_out if isinstance(anon_out, str) and anon_out else None

    # 3. 如果快照和 trace 都没有，直接返回 None
    if not snapshot_included and not trace_included:
        return None

    try:
        with tarfile.open(out_path, "w:gz") as tar:
            # 快照（已验证白名单）
            if snapshot_included:
                tar.add(snapshot_path, arcname=os.path.basename(snapshot_path))
            # trace：出境口径 S3（姓名可读），与老包逐字节一致
            if trace_included:
                _add_text_member(tar, "trace.txt", trace_sanitized, safe_label)
            # trace_anon：S3 之后再过入库口径 S1（姓名 → 成员N）
            if trace_anon is not None:
                _add_text_member(tar, "trace_anon.txt", trace_anon, safe_label)
            # 元信息（不包含 PII）
            meta_path = os.path.join(tempfile.gettempdir(), f"{safe_label}_meta.json")
            import json
            meta = {
                "label": safe_label,
                "snapshot_included": snapshot_included,
                "trace_included": trace_included,
                "trace_sanitized": sanitize and trace_included,
                "trace_anon_included": trace_anon is not None,
                "trace_anon_sanitizer": ANON_SANITIZER_ID if trace_anon is not None else "",
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
