"""negative_samples —— vMA-1.2.1 §5.1 负样本聚类 → 规则建议（§5.1.1~5.1.3）。

负样本口径（只用现有数据结构里**真实存在**的信号，不新建表、不发明数据源）：
1. ``candidate_rules`` 中 ``status='rejected'`` 的行——用户驳回的
   「如果 X 则 Y」序列规则，即显式的「预测为 Y 但用户说不是」负信号。
   三元组取法：entity_id=steps 中的实体（多个用逗号连接）、时间段=time_window、
   预测标签=infer。
2. ``agent_memories`` 中 ``feedback_down >= 1`` 的行——用户对记忆内容
   （推断结论/洞察）的 👎 纠正。三元组取法：entity_id=tags 中 ``member:``/
   ``entity:`` 前缀或 source_refs 中的 HA 实体 token（取不到则空）、
   时间段=observed_at 小时桶（6 小时一段）、预测标签=topic_key。

说明：behavior_predictor / intent_inference 的「预测为 A 但实际未发生」记录
目前不落库（纯内存推断），因此**不作为负样本源**；本模块以上面两类落库信号为准。

聚类与建议（§5.1.2 / §5.1.3）：
- 按 (entity_id, 时间段, 预测标签) 三元组聚类，同类 ≥3 次才产出建议；
- 每类生成一条语义为「如果 X 则**不是** Y」的候选规则，经
  ``Store.upsert_candidate_rule`` 写入（该路径状态恒为 staging、
  user_confirmed 缺省 0），复用现有候选规则状态体系，不建平行体系。

红线（§5.1.4）：``ActiveRuleEngine`` 只消费 ``active_rules`` 表（用户显式创建、
enabled=1），行为推断只消费内置序列规则；``candidate_rules`` 的 staging 行
**永不参与推断**。本模块所有写入都不触碰这两张表。
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "MIN_CLUSTER_COUNT",
    "sanitize_text",
    "collect_negative_samples",
    "cluster_negative_samples",
    "generate_rule_suggestions",
    "run_negative_sample_analysis",
]

#: 同类负样本达到该次数才产出规则建议（§5.1.2）
MIN_CLUSTER_COUNT = 3

_ID18_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_ID15_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]{1,2})[A-Za-z0-9._%+-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
_ENTITY_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*\.[A-Za-z0-9_.\-]+$")

_TIME_SLOTS = ((0, 6), (6, 12), (12, 18), (18, 24))


def _slot_of(moment: Any) -> str:
    """把时间戳归入 6 小时时间段桶（解析失败归 'any'）。"""
    if moment is None:
        return "any"
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return "any"
    if not isinstance(moment, datetime):
        return "any"
    for start, end in _TIME_SLOTS:
        if start <= moment.hour < end:
            return f"{start:02d}-{end:02d}"
    return "any"


def sanitize_text(text: str, member_names: Optional[Iterable[str]] = None) -> str:
    """PII 脱敏：成员姓名→成员N，手机号/邮箱/身份证打码，≥7 位连续数字兜底 ***。

    替换顺序：姓名 → 身份证 → 手机号 → 邮箱 → 长数字兜底（兜底放最后，
    避免把已打码结果里的残留数字再误伤；已打码串不再含 ≥7 位连续数字）。
    """
    if not text:
        return text or ""
    # 1) 成员姓名 → 成员N（按传入顺序稳定映射；长名优先防子串误替）
    names = [n for n in (member_names or []) if n and isinstance(n, str)]
    if names:
        mapping: dict[str, str] = {}
        counter = 0
        for name in sorted(set(names), key=len, reverse=True):
            counter += 1
            mapping[name] = f"成员{counter}"
        for name in sorted(mapping, key=len, reverse=True):
            text = text.replace(name, mapping[name])
    # 2) 身份证：前 6 + 后 4，中间打码
    text = _ID18_RE.sub(lambda m: m.group(0)[:6] + "*" * 8 + m.group(0)[-4:], text)
    text = _ID15_RE.sub(lambda m: m.group(0)[:6] + "*" * 6 + m.group(0)[-3:], text)
    # 3) 手机号：前 3 + 后 4，中间打码
    text = _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
    # 4) 邮箱：本地名保留前 2 位
    text = _EMAIL_RE.sub(lambda m: m.group(1) + "***@" + m.group(2), text)
    # 5) 兜底：其余 ≥7 位连续数字整体打码
    text = _LONG_DIGITS_RE.sub("***", text)
    return text


def _store_member_names(store: Any) -> list[str]:
    try:
        rows = store.list_members()
    except Exception:
        return []
    return [r.get("name") or "" for r in rows or [] if isinstance(r, dict)]


def _entity_from_memory(tags: list, refs: list) -> str:
    """从记忆 tags / source_refs 里提取实体 id（口径见模块 docstring）。"""
    for t in tags:
        if isinstance(t, str) and (t.startswith("member:") or t.startswith("entity:")):
            return t
    for r in refs:
        if not isinstance(r, str):
            continue
        token = r.split(":", 1)[1] if r.startswith("event:") else r
        if _ENTITY_TOKEN_RE.match(token):
            return token
    return ""


def collect_negative_samples(store: Any) -> list[dict]:
    """采集现有落库的负样本信号（§5.1.1），返回统一的三元组样本列表。"""
    samples: list[dict] = []

    # 1) 用户驳回的候选规则：「预测为 infer，但用户说不是」
    try:
        rejected = store.list_candidate_rules(status="rejected")
    except Exception as exc:
        logger.error("读取 rejected 候选规则失败: %s", exc)
        rejected = []
    for r in rejected or []:
        steps = r.get("steps") or []
        entities: list[str] = []
        for s in steps:
            eid = (s or {}).get("entity_id") or ""
            if eid and eid not in entities:
                entities.append(eid)
        samples.append({
            "entity_id": ",".join(entities),
            "time_slot": (r.get("time_window") or "any") or "any",
            "predicted_label": (r.get("infer") or "").strip() or "unknown",
            "source": "rejected_candidate_rule",
            "ref": f"candidate_rule:{r.get('rule_id')}",
            "detail": f"用户驳回规则「{r.get('name') or ''}」(预测 {r.get('infer') or ''})",
        })

    # 2) 负反馈记忆：用户对内容（推断结论/洞察）的 👎 纠正
    try:
        conn = store.connect()
        rows = conn.execute(
            "SELECT memory_id, text, topic_key, tags_json, source_refs_json, "
            "feedback_down, observed_at, created_at "
            "FROM agent_memories WHERE feedback_down >= 1"
        ).fetchall()
    except Exception as exc:
        logger.error("读取负反馈记忆失败: %s", exc)
        rows = []
    for row in rows or []:
        d = dict(row) if not isinstance(row, dict) else row
        try:
            tags = json.loads(d.get("tags_json") or "[]")
        except Exception:
            tags = []
        try:
            refs = json.loads(d.get("source_refs_json") or "[]")
        except Exception:
            refs = []
        topic = (d.get("topic_key") or "").strip()
        excerpt = (d.get("text") or "")[:80]
        samples.append({
            "entity_id": _entity_from_memory(tags if isinstance(tags, list) else [],
                                             refs if isinstance(refs, list) else []),
            "time_slot": _slot_of(d.get("observed_at") or d.get("created_at")),
            "predicted_label": topic or (tags[0] if tags and isinstance(tags[0], str) else "unknown"),
            "source": "memory_feedback_down",
            "ref": f"memory:{d.get('memory_id')}",
            "detail": f"记忆被负反馈 {int(d.get('feedback_down') or 0)} 次(topic={topic})：{excerpt}",
        })

    return samples


def cluster_negative_samples(
    samples: Iterable[dict], min_count: int = MIN_CLUSTER_COUNT,
) -> list[dict]:
    """按 (entity_id, 时间段, 预测标签) 三元组聚类（§5.1.2），≥min_count 才成簇。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in samples or []:
        key = (s.get("entity_id") or "", s.get("time_slot") or "any",
               s.get("predicted_label") or "unknown")
        groups[key].append(s)
    clusters = []
    for key, items in groups.items():
        if len(items) < min_count:
            continue
        entity_id, time_slot, label = key
        clusters.append({
            "entity_id": entity_id,
            "time_slot": time_slot,
            "predicted_label": label,
            "count": len(items),
            "samples": items,
        })
    clusters.sort(key=lambda c: (-c["count"], c["entity_id"]))
    return clusters


def generate_rule_suggestions(
    store: Any,
    clusters: Optional[list[dict]] = None,
    samples: Optional[list[dict]] = None,
    min_count: int = MIN_CLUSTER_COUNT,
) -> dict:
    """为每个负样本簇生成一条「如果 X 则不是 Y」候选规则建议（§5.1.3）。

    落点：``Store.upsert_candidate_rule``（状态 staging、user_confirmed=0，
    待人工确认后才可能被导出/回灌，红线 §5.1.4）。
    """
    if clusters is None:
        clusters = cluster_negative_samples(
            samples if samples is not None else collect_negative_samples(store),
            min_count,
        )
    member_names = _store_member_names(store)
    suggestions: list[dict] = []
    for c in clusters:
        entity_id = c["entity_id"]
        label = c["predicted_label"]
        slot = c["time_slot"]
        # 建议置信度：簇越大越高，下限保证过 upsert 的 0.3 质量闸门
        confidence = round(min(0.8, 0.2 + 0.1 * c["count"]), 3)
        evidence = [
            {"source": s["source"], "ref": s["ref"],
             "detail": sanitize_text(s.get("detail") or "", member_names)}
            for s in c["samples"][:10]
        ]
        steps = [{"entity_id": eid, "role": "negative_trigger"}
                 for eid in (entity_id.split(",") if entity_id else ["unknown"])]
        # 名称/推断也走脱敏：entity_id 可能是 member:<姓名>、label 可能含原文词
        name = sanitize_text(f"负样本建议:{entity_id or 'unknown'}|{slot}|{label}",
                             member_names)
        infer = sanitize_text(f"非{label}", member_names)
        try:
            rule_id, action = store.upsert_candidate_rule(
                name=name, steps=steps, time_window=slot, infer=infer,
                confidence=confidence, source="negative_cluster",
                evidence=evidence,
            )
        except Exception as exc:
            logger.error("写入负样本规则建议失败 (%s): %s", name, exc)
            suggestions.append({"name": name, "written": False,
                                "error": str(exc), "count": c["count"]})
            continue
        suggestions.append({
            "name": name,
            "rule_id": rule_id,
            "action": action,
            "written": bool(rule_id),
            "status": "staging",
            "user_confirmed": 0,
            "count": c["count"],
            "entity_id": entity_id,
            "time_slot": slot,
            "predicted_label": label,
            "suggested_infer": infer,
            "confidence": confidence,
        })
    return {"ok": True, "cluster_count": len(clusters), "suggestions": suggestions}


def run_negative_sample_analysis(store: Any, min_count: int = MIN_CLUSTER_COUNT) -> dict:
    """入口：采集 → 聚类 → 生成 staging 规则建议。返回统计摘要。

    挂载方式：手动触发端点 ``POST /api/behaviors/negative-samples/suggestions``，
    或在现有 ``_periodic_candidate_promotion`` 任务内低频轮转（不新建裸 task）。
    """
    samples = collect_negative_samples(store)
    clusters = cluster_negative_samples(samples, min_count)
    result = generate_rule_suggestions(store, clusters=clusters)
    written = [s for s in result["suggestions"] if s.get("written")]
    return {
        "ok": True,
        "total_negative": len(samples),
        "cluster_count": len(clusters),
        "suggestions": result["suggestions"],
        "written": len(written),
        "min_count": min_count,
    }
