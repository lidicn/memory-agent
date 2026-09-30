"""学习闭环 · 反馈收集。

脱敏策略与 feedback_pack 一致：fail-closed —— 脱敏不通过就丢掉整个 trace，
只保留结构化信号本身（宁可丢 trace，不可泄露家庭成员隐私）。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from learning_models import (
    DEFAULT_VALENCE,
    EPS,
    FeedbackKind,
    FeedbackSignal,
    ReasonCode,
    SubjectType,
    clamp,
)

REPEAT_COLLAPSE_WINDOW = timedelta(minutes=10)
HALF_LIFE_DAYS = 21.0  # 时间衰减半衰期（天），旧数据权重趋零


class RedactionError(ValueError):
    """脱敏失败：调用方必须丢弃 trace。"""


class NormalizationError(ValueError):
    """事件字段不合法。"""


_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"1[3-9]\d{9}"),                # 手机号
    re.compile(r"\d{17}[\dXx]"),               # 身份证
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),    # 邮箱
    re.compile(r"\b\d[\d\-\s]{7,}\d\b"),       # 固话/卡号等长数字串
)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")
_ENUM_PATTERN = re.compile(r"^[\w\u4e00-\u9fff\-]{1,32}$")

# context 键白名单：值类型 → 校验方式（未知键直接 RedactionError）
ALLOWED_CONTEXT_KEYS: Mapping[str, str] = {
    "trace_id": "id",
    "snapshot_id": "id",
    "memory_id": "id",
    "rule_id": "id",
    "query_hash": "id",
    "recall_ids": "ids",
    "room": "enum",
    "source": "enum",
    "latency_ms": "number",
    "rank": "number",
    "score": "number",
    "note": "text",   # 自由文本：只存哈希，原文不落库
}


def contains_pii(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _check_string(value: str, kind: str, key: str) -> str | None:
    """返回可安全落库的值；text 类型返回哈希；不合法抛 RedactionError。"""
    if contains_pii(value):
        raise RedactionError(f"context[{key}] 命中 PII 规则")
    if kind == "text":
        return _hash_text(value)
    if kind in ("id", "ids") and not _ID_PATTERN.match(value):
        raise RedactionError(f"context[{key}] id 格式不合法")
    if kind == "enum" and not _ENUM_PATTERN.match(value):
        raise RedactionError(f"context[{key}] 枚举值不合法")
    return value


def redact_context(raw: Mapping[str, Any]) -> dict[str, object]:
    """脱敏 trace 上下文；任何不合规输入都抛 RedactionError（fail-closed）。"""
    clean: dict[str, object] = {}
    for key, value in raw.items():
        kind = ALLOWED_CONTEXT_KEYS.get(key)
        if kind is None:
            raise RedactionError(f"未登记的 context 键: {key}")
        if value is None:
            continue
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RedactionError(f"context[{key}] 数值类型不合法")
            clean[key] = float(value)
        elif kind == "ids":
            if not isinstance(value, (list, tuple)):
                raise RedactionError(f"context[{key}] 应为列表")
            clean[key] = [_check_string(str(v), "id", key) for v in value]
        else:
            clean[key] = _check_string(str(value), kind, key)
    return clean


def _resolve_valence(kind: FeedbackKind, payload: Mapping[str, Any]) -> float:
    if "valence" in payload:
        valence = float(payload["valence"])
    elif "rating" in payload:
        rating = payload["rating"]
        valence = 1.0 if rating in (True, 1, "up", "like", "+1") else -1.0
    elif kind in DEFAULT_VALENCE:
        return DEFAULT_VALENCE[kind]
    else:
        raise NormalizationError(f"{kind.value} 必须带 valence 或 rating")
    if abs(valence) < 0.05:
        raise NormalizationError("中性反馈无信息量，丢弃")
    return clamp(valence, -1.0, 1.0)


def _resolve_time(payload: Mapping[str, Any], now: datetime) -> datetime:
    raw = payload.get("created_at", now)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    return datetime.fromisoformat(str(raw)).astimezone(timezone.utc)


def normalize_event(payload: Mapping[str, Any], now: datetime) -> FeedbackSignal:
    """把一条上报事件归一化为 FeedbackSignal（纯函数，字段不合法即抛错）。"""
    try:
        kind = FeedbackKind(str(payload["kind"]))
        subject_type = SubjectType(str(payload["subject_type"]))
        subject_id = str(payload["subject_id"])
        reason = ReasonCode(str(payload.get("reason", ReasonCode.OTHER.value)))
    except (KeyError, ValueError) as exc:
        raise NormalizationError(str(exc)) from exc

    if not subject_id:
        raise NormalizationError("subject_id 不能为空")
    confidence = clamp(float(payload.get("confidence", 1.0)), 0.0, 1.0)
    return FeedbackSignal(
        feedback_id=str(payload.get("feedback_id") or uuid.uuid4().hex[:16]),
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        valence=_resolve_valence(kind, payload),
        created_at=_resolve_time(payload, now),
        confidence=confidence,
        member_id=(str(payload["member_id"]) if payload.get("member_id") else None),
        room=(str(payload["room"]) if payload.get("room") else None),
        reason=reason,
        repeat_count=max(1, int(payload.get("repeat_count", 1))),
    )


def dedupe(signals: Sequence[FeedbackSignal]) -> tuple[FeedbackSignal, ...]:
    """按 feedback_id 去重，保留最早一条。"""
    seen: dict[str, FeedbackSignal] = {}
    for s in sorted(signals, key=lambda x: x.created_at):
        seen.setdefault(s.feedback_id, s)
    return tuple(seen.values())


def collapse_repeats(
    signals: Sequence[FeedbackSignal], window: timedelta = REPEAT_COLLAPSE_WINDOW
) -> tuple[FeedbackSignal, ...]:
    """折叠重复查询：同 (member, subject) 在窗口内的多条 repeat_query 合成一条。"""
    from dataclasses import replace

    out: list[FeedbackSignal] = []
    buckets: dict[tuple[str, str, str], list[FeedbackSignal]] = {}
    for s in signals:
        if s.kind is not FeedbackKind.REPEAT_QUERY:
            out.append(s)
            continue
        buckets.setdefault((s.member_id or "", s.subject_type.value, s.subject_id), []).append(s)

    for group in buckets.values():
        group.sort(key=lambda x: x.created_at)
        head = group[0]
        count = head.repeat_count
        for prev, cur in zip(group, group[1:]):
            if cur.created_at - prev.created_at <= window:
                count += 1
            else:
                out.append(replace(head, repeat_count=count))
                head, count = cur, cur.repeat_count
        out.append(replace(head, repeat_count=count))
    return tuple(sorted(out, key=lambda x: x.created_at))


@dataclass(frozen=True)
class CollectResult:
    accepted: tuple[FeedbackSignal, ...]
    dropped: tuple[tuple[str, str], ...]  # (原因, feedback_id/原始引用)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)


def collect_events(
    raw_events: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
    *,
    half_life_days: float = HALF_LIFE_DAYS,  # noqa: ARG001 - 预留：策略随配置下发
) -> CollectResult:
    """①收集：归一化 + fail-closed 脱敏 + 去重折叠（纯函数，落库交给 store）。"""
    now = now or datetime.now(timezone.utc)
    accepted: list[FeedbackSignal] = []
    dropped: list[tuple[str, str]] = []

    for raw in raw_events:
        ref = str(raw.get("feedback_id") or raw.get("subject_id") or "?")
        try:
            signal = normalize_event(raw, now)
        except NormalizationError as exc:
            dropped.append((f"归一化失败: {exc}", ref))
            continue

        context_dropped = False
        try:
            context = redact_context(raw.get("context") or {})
        except RedactionError:
            context, context_dropped = {}, True  # fail-closed：宁可丢 trace

        from dataclasses import replace

        accepted.append(replace(signal, context=context, context_dropped=context_dropped))

    return CollectResult(accepted=collapse_repeats(dedupe(accepted)), dropped=tuple(dropped))
