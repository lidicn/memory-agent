"""DCD R3 生效通道：``candidate_rules(accepted)`` → ``active_rules`` → 试运行 → 转正 / 撤销。

裁定 20261001-DB六格与MA五题 · MA R3 批准建通道，但必须满足裁定 7 红线的扩展。
四条红线在这里各自的判据（都可测、可在审计里还原）：

1. **证据门槛** —— 候选必须 ``status='accepted'`` 且 ``user_confirmed=1``，
   并且证据跨越 ``MA_RULE_MIN_EVIDENCE``（默认 3）个**独立自然日**。
   同一天里的三次重复不算三条独立证据。
2. **观察期** —— 晋升进来的规则一律 ``mode='dry_run'``：照常匹配、照常写触发历史
   （``dry_run=1``），但不派发任何动作。满 ``MA_RULE_DRY_RUN_DAYS``（默认 3）天
   且观察期内零误报，才允许 ``advance_to_live()`` 转 ``live``。
3. **可回滚** —— ``revoke()`` 关闭规则并删除它经 ``infer_activity`` 产生的推断
   （按 ``detected_activities.source_rule_id`` 定位），回滚条数进审计。
4. **审计** —— 建议/确认/晋升/转正/撤销每一步都写 ``rule_lifecycle_audit``。

## 已知结构限制（不掩盖）

``active_rules`` 的实时事件源只有客厅感知链路（``livingroom_ai.py`` 把
``ev.kind`` 喂给引擎），词表就是感知总线那一组 kind；而挖掘出的候选是
``{"tag": "door"|"presence"|"computer", "state": ...}`` 的**设备序列**，
引擎既没有设备事件源、也没有序列语义。因此本通道对这类候选给
``engine_feed_gap`` 判红而不是塞进表里当死规则——通道通了，但设备序列候选
要真生效，还需要"设备事件进引擎"这条 feed（属独立改动，需另行裁决）。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

from .store import CANDIDATE_ACCEPTED, CANDIDATE_PROMOTED

logger = logging.getLogger("memory_agent.rule_lifecycle")

# 红线 1：晋升所需的最少独立证据日数
MIN_EVIDENCE = int(os.getenv("MA_RULE_MIN_EVIDENCE", "3"))
# 红线 2：试运行观察期天数
DRY_RUN_DAYS = float(os.getenv("MA_RULE_DRY_RUN_DAYS", "3"))

MODE_DRY_RUN = "dry_run"
MODE_LIVE = "live"
MODE_REVOKED = "revoked"

# 引擎实时 feed 能见到的 kind 词表（口径见 store.py 感知总线 perception_events.kind 注释）
ENGINE_FEED_KINDS = frozenset({
    "face_known", "face_unknown", "human", "pet", "cry", "gesture",
    "day_night", "fav_area", "no_human", "motion", "object",
})

_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 审计动作字面量。"机器建议"这一环的载体是 candidate_rules 行本身
# （source='inference' + created_at），审计行用 source_rule_id 回指它，
# 于是"机器建议 → 人工确认 → 生效 → 转正/撤销"整链可还原。
ACT_CONFIRM = "confirm"
ACT_REJECT = "reject"
ACT_PROMOTE = "promote"
ACT_ADVANCE = "advance_live"
ACT_REVOKE = "revoke"
ACT_FALSE_POSITIVE = "false_positive"


def log_confirmation(store: Any, candidate_id: str, status: str,
                     actor: str = "user", reason: str = "") -> None:
    """人工确认/拒绝候选规则这一步留审计（红线 4 的"人工确认"环节）。"""
    store.log_rule_lifecycle(
        candidate_id, ACT_CONFIRM if status == CANDIDATE_ACCEPTED else ACT_REJECT,
        source_rule_id=candidate_id, actor=actor, to_state=status, reason=reason)


def independent_evidence_days(evidence: list) -> tuple[int, str]:
    """证据跨越的独立自然日数。返回 ``(天数, 计数口径说明)``。"""
    days: set[str] = set()
    for item in evidence or []:
        raw = item if isinstance(item, str) else repr(item)
        found = _DAY_RE.search(raw)
        if found:
            days.add(found.group(0))
    if days:
        return len(days), "distinct_day"
    # 证据没带日期时退化为条目计数，并在审计里说明口径（不谎称跨天独立）
    return len(evidence or []), "entry_count"


def build_condition(candidate: dict) -> tuple[dict, list[str]]:
    """把候选规则翻译成引擎可匹配的原子条件。返回 ``(condition, blockers)``。

    引擎的 ``_match_atom`` 只认扁平原子（kind/room/person/identity/
    min_confidence/home_mode/time_range），没有序列语义，所以这里取**首个
    事件类型**作为触发子，时间窗映射为 ``time_range``；序列的其余步骤无法
    表达，作为 note 记录而不是假装保留了语义。
    """
    steps = candidate.get("steps") or []
    kinds: list[str] = []
    room = str(candidate.get("room") or "").strip()
    for step in steps:
        if not isinstance(step, dict):
            continue
        kind = str(step.get("kind") or "").strip()
        if not room:
            room = str(step.get("room") or "").strip()
        if kind:
            kinds.append(kind)
    if not kinds:
        tags = [str(s.get("tag") or "") for s in steps if isinstance(s, dict)]
        return {}, [f"engine_feed_gap: 候选步骤是设备序列 tag={tags[:5]}，"
                    f"引擎实时 feed 只认感知 kind（{sorted(ENGINE_FEED_KINDS)[:4]}…）"]
    unreachable = [k for k in kinds if k not in ENGINE_FEED_KINDS]
    if unreachable:
        return {}, [f"engine_feed_gap: 事件类型 {unreachable} 不在引擎实时 feed 词表内"]
    condition: dict[str, Any] = {"kind": kinds[0]}
    if room:
        condition["room"] = room
    window = str(candidate.get("time_window") or "").strip()
    if window:
        condition["time_range"] = window
    return condition, []


def _parse_ts(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T")[:19])
    except ValueError:
        return None


class RuleLifecycle:
    """候选规则 → 引擎的生效通道（四红线的执行者）。"""

    def __init__(self, store: Any, engine: Any, *,
                 min_evidence: Optional[int] = None,
                 dry_run_days: Optional[float] = None):
        self.store = store
        self.engine = engine
        self.min_evidence = int(min_evidence if min_evidence is not None else MIN_EVIDENCE)
        self.dry_run_days = float(dry_run_days if dry_run_days is not None else DRY_RUN_DAYS)

    def _now(self) -> datetime:
        return self.engine._now()

    # ── 红线 1：证据门槛（只读判定，不写库）──────────────────────────────

    def eligibility(self, candidate_id: str) -> dict:
        """返回候选能否晋升及其全部判据，不产生任何写入。"""
        cand = self.store.get_candidate_rule(candidate_id)
        if not cand:
            return {"ok": False, "candidate_id": candidate_id,
                    "eligible": False, "blockers": [f"候选规则 {candidate_id} 不存在"]}
        evidence_days, evidence_basis = independent_evidence_days(cand.get("evidence") or [])
        condition, blockers = build_condition(cand)
        if cand.get("status") == CANDIDATE_PROMOTED:
            existing = self._rule_by_source(candidate_id)
            if existing and existing.get("mode") != MODE_REVOKED:
                blockers.append(f"duplicate: 已晋升为 {existing['rule_id']}（{existing['mode']}）")
        if cand.get("status") != CANDIDATE_ACCEPTED:
            blockers.append(f"evidence_gate: 状态为 {cand.get('status')}，未经人工确认（accepted）的候选不生效")
        elif not int(cand.get("user_confirmed") or 0):
            blockers.append("evidence_gate: user_confirmed=0，红线位未置起")
        if evidence_days < self.min_evidence:
            blockers.append(
                f"evidence_gate: 独立证据 {evidence_days} 天（口径 {evidence_basis}）< 门槛 {self.min_evidence}")
        return {
            "ok": True,
            "candidate_id": candidate_id,
            "name": cand.get("name") or "",
            "status": cand.get("status"),
            "user_confirmed": int(cand.get("user_confirmed") or 0),
            "eligible": not blockers,
            "blockers": blockers,
            "evidence_days": evidence_days,
            "evidence_basis": evidence_basis,
            "min_evidence": self.min_evidence,
            "condition": condition,
        }

    # ── 晋升：accepted → active_rules(dry_run) ──────────────────────────

    def promote(self, candidate_id: str, actor: str = "user", reason: str = "") -> dict:
        gate = self.eligibility(candidate_id)
        if not gate.get("eligible"):
            self.store.log_rule_lifecycle(
                candidate_id, ACT_PROMOTE, source_rule_id=candidate_id, actor=actor,
                from_state=str(gate.get("status") or ""), to_state="rejected_by_gate",
                reason=reason or "；".join(gate.get("blockers") or []),
                detail={"blockers": gate.get("blockers") or []})
            return {"ok": False, "error": "门槛未过，未进入引擎",
                    "blockers": gate.get("blockers") or [], "gate": gate}

        cand = self.store.get_candidate_rule(candidate_id)
        condition = gate["condition"]
        infer = str(cand.get("infer") or "").strip()
        action = ({"type": "infer_activity", "activity": infer,
                   "confidence": float(cand.get("confidence") or 0.6)}
                  if infer else {"type": "log"})
        res = self.engine.add_rule(
            name=cand.get("name") or candidate_id,
            condition=condition,
            action=action,
            description=(cand.get("note") or f"候选晋升 {candidate_id}；"
                         f"序列步骤未全部表达（观察期 {self.dry_run_days} 天）"),
            enabled=True,
            rule_type="static",
            origin="candidate_promoted",
            source_rule_id=candidate_id,
            mode=MODE_DRY_RUN,
            evidence_count=gate["evidence_days"],
        )
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error"), "gate": gate}

        rule_id = res["rule_id"]
        self.store.set_candidate_rule_status(candidate_id, CANDIDATE_PROMOTED)
        self.store.log_rule_lifecycle(
            rule_id, ACT_PROMOTE, source_rule_id=candidate_id, actor=actor,
            from_state=CANDIDATE_ACCEPTED, to_state=MODE_DRY_RUN,
            reason=reason,
            detail={"evidence_days": gate["evidence_days"],
                    "evidence_basis": gate["evidence_basis"],
                    "condition": condition, "action": action,
                    "dry_run_days": self.dry_run_days,
                    "note": "序列步骤未全部表达，仅首个事件类型作为触发子"})
        logger.info("[RuleLifecycle] 晋升 %s → %s（试运行）", candidate_id, rule_id)
        return {"ok": True, "rule_id": rule_id, "mode": MODE_DRY_RUN,
                "candidate_id": candidate_id, "gate": gate}

    # ── 红线 2：观察期满 + 零误报 → live ────────────────────────────────

    def observation(self, rule_id: str) -> dict:
        rule = self.engine.get_rule(rule_id)
        if not rule:
            return {"ok": False, "error": f"规则 {rule_id} 不存在"}
        activated = _parse_ts(rule.get("activated_at") or rule.get("created_at"))
        observed_days = round((self._now() - activated).total_seconds() / 86400, 4) \
            if activated else 0.0
        fp = self.store.count_rule_false_positives(rule_id, since=str(
            activated.isoformat(sep="T") if activated else ""))
        triggers = self.store.list_rule_triggers(rule_id)
        dry_hits = sum(1 for t in triggers if t.get("dry_run"))
        return {
            "ok": True,
            "rule_id": rule_id,
            "mode": rule.get("mode") or MODE_LIVE,
            "origin": rule.get("origin") or "manual",
            "source_rule_id": rule.get("source_rule_id") or "",
            "observed_days": observed_days,
            "required_days": self.dry_run_days,
            "dry_run_hits": dry_hits,
            "false_positives": fp,
            "eligible_for_live": (
                (rule.get("mode") or MODE_LIVE) == MODE_DRY_RUN
                and observed_days >= self.dry_run_days and fp == 0),
        }

    def advance_to_live(self, rule_id: str, actor: str = "user", reason: str = "") -> dict:
        obs = self.observation(rule_id)
        if not obs.get("ok"):
            return obs
        if obs["mode"] == MODE_REVOKED:
            return {"ok": False, "error": "规则已撤销，不能直接转正", "observation": obs}
        if obs["mode"] == MODE_LIVE:
            return {"ok": True, "rule_id": rule_id, "mode": MODE_LIVE,
                    "already_live": True, "observation": obs}
        blockers = []
        if obs["observed_days"] < obs["required_days"]:
            blockers.append(f"观察期未满：{obs['observed_days']:.2f} 天 < {obs['required_days']} 天")
        if obs["false_positives"] > 0:
            blockers.append(f"观察期内有误报 {obs['false_positives']} 条")
        if blockers:
            self.store.log_rule_lifecycle(
                rule_id, ACT_ADVANCE, source_rule_id=obs["source_rule_id"], actor=actor,
                from_state=MODE_DRY_RUN, to_state="rejected_by_gate",
                reason=reason or "；".join(blockers), detail={"observation": obs})
            return {"ok": False, "error": "；".join(blockers), "blockers": blockers,
                    "observation": obs}
        if not self.store.set_rule_mode(rule_id, MODE_LIVE):
            return {"ok": False, "error": "规则状态更新失败"}
        self.engine._index_dirty = True
        self.store.log_rule_lifecycle(
            rule_id, ACT_ADVANCE, source_rule_id=obs["source_rule_id"], actor=actor,
            from_state=MODE_DRY_RUN, to_state=MODE_LIVE, reason=reason,
            detail={"observation": obs})
        logger.info("[RuleLifecycle] %s 试运行转正（观察 %.2f 天，误报 0）",
                    rule_id, obs["observed_days"])
        return {"ok": True, "rule_id": rule_id, "mode": MODE_LIVE, "observation": obs}

    # ── 红线 3：撤销 + 回滚它产生的推断 ─────────────────────────────────

    def revoke(self, rule_id: str, actor: str = "user", reason: str = "",
               rollback_inferences: bool = True) -> dict:
        rule = self.engine.get_rule(rule_id)
        if not rule:
            return {"ok": False, "error": f"规则 {rule_id} 不存在"}
        rolled_back = 0
        if rollback_inferences:
            rolled_back = self.store.rollback_detected_activities(rule_id)
        if not self.store.set_rule_mode(
                rule_id, MODE_REVOKED, enabled=False, revoked=True):
            return {"ok": False, "error": "规则状态更新失败"}
        self.engine._index_dirty = True
        self.store.log_rule_lifecycle(
            rule_id, ACT_REVOKE, source_rule_id=rule.get("source_rule_id") or "",
            actor=actor, from_state=rule.get("mode") or MODE_LIVE, to_state=MODE_REVOKED,
            reason=reason,
            detail={"rolled_back_inferences": rolled_back,
                    "rollback_inferences": rollback_inferences})
        logger.info("[RuleLifecycle] 撤销 %s，回滚推断 %d 条", rule_id, rolled_back)
        return {"ok": True, "rule_id": rule_id, "mode": MODE_REVOKED,
                "rolled_back_inferences": rolled_back}

    # ── 红线 2 的输入：把试运行命中判为误报 ─────────────────────────────

    def flag_false_positive(self, rule_id: str, trigger_id: int,
                            actor: str = "user", reason: str = "") -> dict:
        if not self.store.mark_rule_trigger_false_positive(trigger_id):
            return {"ok": False, "error": f"触发记录 {trigger_id} 不存在"}
        self.store.log_rule_lifecycle(
            rule_id, ACT_FALSE_POSITIVE, actor=actor, to_state="false_positive",
            reason=reason, detail={"trigger_id": int(trigger_id)})
        return {"ok": True, "rule_id": rule_id, "trigger_id": int(trigger_id)}

    # ── 读侧：通道全景 ─────────────────────────────────────────────────

    def _rule_by_source(self, candidate_id: str) -> Optional[dict]:
        for rule in self.engine.list_rules(enabled_only=False):
            if (rule.get("source_rule_id") or "") == candidate_id:
                return rule
        return None

    def pending_promotions(self, limit: int = 50) -> list[dict]:
        """accepted 候选的晋升预演清单（只读，逐条给门槛判据）。"""
        out = []
        for cand in self.store.list_candidate_rules(status=CANDIDATE_ACCEPTED, limit=limit):
            gate = self.eligibility(cand["rule_id"])
            out.append({
                "candidate_id": cand["rule_id"],
                "name": cand.get("name") or "",
                "eligible": gate["eligible"],
                "evidence_days": gate["evidence_days"],
                "blockers": gate["blockers"],
            })
        out.sort(key=lambda r: (not r["eligible"], -r["evidence_days"]))
        return out

    def channel_status(self, limit: int = 100) -> dict:
        """通道全景：试运行中 / 已转正 / 已撤销的晋升规则 + 各自观察读数。"""
        buckets: dict[str, list[dict]] = {
            MODE_DRY_RUN: [], MODE_LIVE: [], MODE_REVOKED: []}
        for rule in self.engine.list_rules(enabled_only=False):
            if (rule.get("origin") or "manual") != "candidate_promoted":
                continue
            mode = rule.get("mode") or MODE_LIVE
            buckets.setdefault(mode, []).append(self.observation(rule["rule_id"]))
        return {
            "ok": True,
            "min_evidence": self.min_evidence,
            "dry_run_days": self.dry_run_days,
            "promoted_rules": buckets,
            "audit": self.store.list_rule_lifecycle(limit=20),
        }


_lifecycle: Optional[RuleLifecycle] = None


def get_rule_lifecycle(store: Any, engine: Any) -> RuleLifecycle:
    """获取全局生效通道单例（与 get_rule_engine 同样的一对一装配）。"""
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = RuleLifecycle(store, engine)
    return _lifecycle


def reset_rule_lifecycle() -> None:
    global _lifecycle
    _lifecycle = None
