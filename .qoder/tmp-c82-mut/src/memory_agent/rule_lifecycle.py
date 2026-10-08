"""DCD R3 生效通道：``candidate_rules(accepted)`` → ``active_rules`` → 试运行 → 转正 / 撤销。

裁定 20261001-DB六格与MA五题 · MA R3 批准建通道，但必须满足裁定 7 红线的扩展。
四条红线在这里各自的判据（都可测、可在审计里还原）：

1. **证据门槛** —— 候选必须 ``status='accepted'`` 且 ``user_confirmed=1``，
   并且证据跨越 ``MA_RULE_MIN_EVIDENCE``（默认 3）个**独立自然日**。
   同一天里的三次重复不算三条独立证据。
   DCD 20261004 MA-裁1 Q1 在这条红线上加了一个数值判据：晋升必须带**冷却上限**
   （调用参数或候选行 ``cooldown_seconds`` 列），两者皆空即拒——300 不再由
   ``add_rule`` 的形参默认值替裁定说话。
2. **观察期** —— 晋升进来的规则一律 ``mode='dry_run'``：照常匹配、照常写触发历史
   （``dry_run=1``），但不派发任何动作。满 ``MA_RULE_DRY_RUN_DAYS``（默认 3）天
   且观察期内零误报，才允许 ``advance_to_live()`` 转 ``live``。
3. **可回滚** —— ``revoke()`` 关闭规则并删除它经 ``infer_activity`` 产生的推断
   （按 ``detected_activities.source_rule_id`` 定位），回滚条数进审计。
4. **审计** —— 建议/确认/晋升/转正/撤销每一步都写 ``rule_lifecycle_audit``。

## 人工录入（DCD 20261005 §二.2 Q1 = 乙 + 丁分两步）

写侧 CRUD 不直接写 ``active_rules``：人工录入走 ``manual_add`` / ``manual_edit`` /
``manual_withdraw``，落点是 ``candidate_rules``（``source='manual'``），此后与机器建议
同一条通道、同一套四红线。人工身份只改「建议是谁提的」，不改门槛——红线 1 要求
的独立证据日数对人工候选同样生效，证据不足就按 blocker 拒晋升（响应里当场给出
``gate``，不留到三个月后）。规则的下架仍然只有 ``revoke`` 一条路（红线 3）。

## 已知结构限制（不掩盖）

``active_rules`` 的实时事件源原先只有客厅感知链路（``livingroom_ai.py`` 把
``ev.kind`` 喂给引擎），词表就是感知总线那一组 kind；而挖掘出的候选是
``{"tag": "door"|"presence"|"computer", "state": ...}`` 的**设备序列**，
引擎既没有设备事件源、也没有序列语义，于是这类候选在这里被
``engine_feed_gap`` 如实拒绝——通道通了，但设备进不了引擎。

**该缺口已由 ``device_feed`` 补上**（DCD 裁定 20261001-MA-service_token与设备事件feed
§二 · Q1=B 独立批量扫描器）。因此本通道的判定改成两条可查的口径：

* 候选步骤带 ``kind`` → 感知路径，判据不变（不在 ``ENGINE_FEED_KINDS`` 里就拒）；
* 候选步骤带 ``tag`` → 设备路径，条件译成 ``{"kind": "device", "tag": …}``，
  由 ``device_feed`` 喂；tag 不在 feed 词表（``FEED_TAGS``）、或 state 不是
  二元翻转（``BINARY_STATES``，裁定 Q2 第 2 项）时**仍然拒**——放宽的是
  "引擎有没有这个字"，不是"这个候选能不能命中"。

仍未消除的限制，如实留在这里：

1. **序列语义只取首步**。引擎 ``_match_atom`` 没有跨事件的序列状态，晋升后的规则
   是「首步命中即触发」，序列的其余步骤作为 note 进审计，不假装保留了语义。
2. **设备路径响应滞后一个轮询周期**（Q1=B 的裁定代价），且总开关
   ``device_feed_enabled`` 默认关——晋升进来的设备规则在开关打开前不会收到事件；
   观察期读数会是恒 0 的 ``dry_run_hits``，那是"没喂"，不是"没命中"。
3. **``count`` 触发依赖 ``trigger_json`` 列**（Q2 第 3 项的载体）：列由
   ``Store.init_schema`` 的 ADD COLUMN 迁移建，未重启的在线进程里 ``rule["trigger"]``
   仍是 ``{}``，count 分支走不到——落库与否要在重启窗后复查，不在此处谎报。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

from .device_feed import BINARY_STATES, DEVICE_TRIGGER, FEED_TAGS
from .rule_engine import DEVICE_EVENT_KIND
from .store import (CANDIDATE_ACCEPTED, CANDIDATE_PRE_ENGINE, CANDIDATE_PROMOTED,
                    CANDIDATE_SOURCE_MANUAL)

logger = logging.getLogger("memory_agent.rule_lifecycle")

# 红线 1：晋升所需的最少独立证据日数
MIN_EVIDENCE = int(os.getenv("MA_RULE_MIN_EVIDENCE", "3"))
# 红线 2：试运行观察期天数
DRY_RUN_DAYS = float(os.getenv("MA_RULE_DRY_RUN_DAYS", "3"))

MODE_DRY_RUN = "dry_run"
MODE_LIVE = "live"
MODE_REVOKED = "revoked"

# 「这个入参没传」与「传了 None」在候选编辑上是两件事：前者不动那一列，后者是
# 主动撤回设定（冷却上限清空后，晋升端会重新按「缺省即拒」挡住）。用 None 当
# 缺省值就把这两件事压成一件，编辑接口因此需要一个独立的哨兵。
_KEEP = object()

# 引擎**感知** feed 能见到的 kind 词表（口径见 store.py 感知总线 perception_events.kind 注释）。
# 设备侧不在这张表里：它走 ``device_feed`` 的 ``kind="device"`` + tag 条件，
# 词表是 ``device_feed.FEED_TAGS``——两张表各管一条链路，合并成一张就看不出
# 「这个候选是被哪条链路挡在门外的」。
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
# 人工录入这条分支的三个动作（DCD 20261005 §二.2 Q1=乙+丁）。审计里必须和机器
# 建议分得开：「谁写的」决定出了问题该找哪条链路，混成一个 confirm 就查不出来了。
ACT_MANUAL_ADD = "manual_add"
ACT_MANUAL_EDIT = "manual_edit"
ACT_MANUAL_WITHDRAW = "manual_withdraw"

# DCD 20261004 MA-裁1 Q1（显式化）。``add_rule`` 的形参默认值 300 从此只服务人工建规则
# 的路径；晋升出去的每一条规则，它的「吵人上限」必须来自这次晋升自己给出的数值——
# 调用方显式传入，或候选行 ``cooldown_seconds`` 列已设定。两者都拿不到就**拒绝晋升**，
# 而不是悄悄吃一个从未被裁定的数（裁定原文：缺省即拒）。
#
# 数值本身不在这里定：Q2 已确认「count 60 秒 3 次」与「每 300 秒最多一条」是**叠加**
# 关系（单条设备类规则理论上限 12 条/小时），所以 300 是一个可写入的裁定值，
# 不是一个隐式默认。列刻意**不设 SQL 默认值**——有默认值就永远走不到拒绝分支。
COOLDOWN_UNSET_BLOCKER = (
    "cooldown_gate: 候选未设定 cooldown_seconds——晋升的每条规则都要有自己的吵人上限"
    "（DCD 20261004 MA-裁1 Q1：缺省即拒）")


def parse_cooldown(value: Any) -> tuple[Optional[int], str]:
    """显式冷却值 → ``(非负整数或 None, 拒因)``。

    「没给」与「给了但读不出数」分两种拒因：前者要的是*去设定一个值*，
    后者要的是*值写坏了*——混成一条会让人去补一个本来就写错的数。
    冷却语义由 ``rule_engine._cooldown_seconds`` 决定（非法/负数=不冷却），
    晋升端不接受这类值：一条会广播每一次命中的规则不该悄悄进引擎。
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, ""
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None, f"cooldown_gate: 冷却值 {value!r} 不是数"
    if seconds < 0:
        return None, f"cooldown_gate: 冷却值 {seconds} 是负数"
    return seconds, ""


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
    min_confidence/home_mode/time_range + 设备侧的 domain/entity_id/tag/state/old_state），
    没有跨事件的序列语义，所以这里取**首个事件**作为触发子，时间窗映射为 ``time_range``；
    序列的其余步骤无法表达，作为 note 记录而不是假装保留了语义。

    ``old_state`` 只能表达**单条事件内**的迁移（HA 一行 state_changed 自带两端，
    于是 ``{"state": "on", "old_state": "off"}`` 就是「关→开」）；跨事件的
    「先 A 后 B」仍然表达不了。本函数不会自行为候选生成 old_state——推断步骤里
    没有可信的"变更前状态"，凭 note 猜一个迁移方向会造出假真值。

    两条来源分别判：

    * 步骤带 ``kind`` —— 感知链路（``livingroom_ai`` 实时喂），词表是
      ``ENGINE_FEED_KINDS``；
    * 步骤带 ``tag`` —— 设备链路（``device_feed`` 批量喂），条件是
      ``{"kind": "device", "tag": …}``，词表是 ``FEED_TAGS``，state 只认
      ``BINARY_STATES``（DCD Q2 第 2 项：数值型读数不进 feed）。
    """
    steps = candidate.get("steps") or []
    kinds: list[str] = []
    room = str(candidate.get("room") or "").strip()
    first_tag = ""
    first_state = ""
    for step in steps:
        if not isinstance(step, dict):
            continue
        kind = str(step.get("kind") or "").strip()
        tag = str(step.get("tag") or "").strip()
        if not room:
            room = str(step.get("room") or "").strip()
        if kind:
            kinds.append(kind)
        elif tag and not first_tag:
            first_tag = tag
            first_state = str(step.get("state") or "").strip().lower()
    window = str(candidate.get("time_window") or "").strip()

    if kinds:
        unreachable = [k for k in kinds if k not in ENGINE_FEED_KINDS]
        if unreachable:
            return {}, [f"engine_feed_gap: 事件类型 {unreachable} 不在引擎实时 feed 词表内"]
        condition: dict[str, Any] = {"kind": kinds[0]}
        if room:
            condition["room"] = room
        if window:
            condition["time_range"] = window
        return condition, []

    if first_tag:
        # DCD 裁定 §二 Q1=B 之后，设备序列候选不再是「引擎没有这个字」——
        # 判据换成 feed 真能产出什么：词表 + 二元态。
        if first_tag not in FEED_TAGS:
            return {}, [f"device_feed_gap: tag={first_tag} 不在设备 feed 词表内"
                        f"（词表与 insights 标签口径同源：{sorted(FEED_TAGS)}）"]
        if first_state and first_state not in BINARY_STATES:
            return {}, [f"device_feed_gap: 状态 {first_state} 不是二元翻转"
                        f"（feed 只产出 {sorted(BINARY_STATES)}，数值读数按 Q2 第 2 项不进 feed）"]
        condition = {"kind": DEVICE_EVENT_KIND, "tag": first_tag}
        if first_state:
            condition["state"] = first_state
        if room:
            condition["room"] = room
        if window:
            condition["time_range"] = window
        return condition, []

    return {}, ["empty_steps: 候选没有带 kind 或 tag 的步骤，无法构造触发子"]


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

    def eligibility(self, candidate_id: str, cooldown_seconds: Any = None) -> dict:
        """返回候选能否晋升及其全部判据，不产生任何写入。

        ``cooldown_seconds`` 是本次晋升想采用的吵人上限；不传则回退到候选行
        ``cooldown_seconds`` 列（DCD 20261004 MA-裁1 Q1）。两者都空 ⇒ 判拒，
        缺口写在 blockers 里，让「这条候选还差一个数值」在预演清单上就看得见。
        """
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
        explicit, explicit_why = parse_cooldown(cooldown_seconds)
        stored, stored_why = parse_cooldown(cand.get("cooldown_seconds"))
        cooldown = explicit if explicit is not None else stored
        cooldown_source = "argument" if explicit is not None else ("candidate_column" if stored is not None else "")
        if explicit_why or stored_why:
            blockers.append(explicit_why or stored_why)
        elif cooldown is None:
            blockers.append(COOLDOWN_UNSET_BLOCKER)
        return {
            # ok = 「通道真的给出了可匹配的触发子」。构造不出条件的候选（device_feed_gap /
            # empty_steps）在这里 condition={}，于是 ok=False，缺口暴露在接口上
            # 而不是被 ok=True 掩盖。
            "ok": bool(condition),
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
            "cooldown_seconds": cooldown,
            "cooldown_source": cooldown_source,
        }

    # ── 晋升：accepted → active_rules(dry_run) ──────────────────────────

    def promote(self, candidate_id: str, actor: str = "user", reason: str = "",
                cooldown_seconds: Any = None) -> dict:
        gate = self.eligibility(candidate_id, cooldown_seconds)
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
        # DCD Q2 第 3 项：设备类候选的 count 触发（60 秒 3 次）是**裁定值**，
        # 必须随规则入库——此前 ``trigger`` 从不写库，``rule["trigger"]`` 恒为 ``{}``，
        # 「保持默认值」只是一句空话。感知类候选维持原语义（匹配即触发）。
        trigger = (dict(DEVICE_TRIGGER)
                   if condition.get("kind") == DEVICE_EVENT_KIND else None)
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
            trigger=trigger,
            # DCD 20261004 MA-裁1 Q1：冷却值由这次晋升显式给出（见 ``parse_cooldown``）。
            # ``gate`` 里没有数值时早已判拒，所以这里不可能拿到 None——一旦有人把
            # 这一行删掉，规则就退回吃 ``add_rule`` 的形参默认 300，那条锁会红。
            cooldown_seconds=gate["cooldown_seconds"],
        )
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error"), "gate": gate}

        rule_id = res.get("rule_id") or ""
        if not rule_id:
            return {"ok": False, "error": "引擎未返回 rule_id，晋升未落库", "gate": gate}
        self.store.set_candidate_rule_status(candidate_id, CANDIDATE_PROMOTED)
        # 数值来自本次调用参数时回写候选行：下次有人问「这条当初按多少冷却晋升的」，
        # 候选行自己就答得出来，不必再去翻 active_rules。列里本来就有值时不动它。
        if gate.get("cooldown_source") == "argument":
            self.store.set_candidate_rule_cooldown(candidate_id, gate["cooldown_seconds"])
        self.store.log_rule_lifecycle(
            rule_id, ACT_PROMOTE, source_rule_id=candidate_id, actor=actor,
            from_state=CANDIDATE_ACCEPTED, to_state=MODE_DRY_RUN,
            reason=reason,
            detail={"evidence_days": gate["evidence_days"],
                    "evidence_basis": gate["evidence_basis"],
                    "condition": condition, "action": action,
                    "trigger": trigger or {},
                    "cooldown_seconds": gate["cooldown_seconds"],
                    "cooldown_source": gate.get("cooldown_source") or "",
                    "dry_run_days": self.dry_run_days,
                    "note": "序列步骤未全部表达，仅首个事件作为触发子"})
        logger.info("[RuleLifecycle] 晋升 %s → %s（试运行）", candidate_id, rule_id)
        return {"ok": bool(rule_id), "rule_id": rule_id, "mode": MODE_DRY_RUN,
                "cooldown_seconds": gate["cooldown_seconds"],
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
            # 观察期判据全部挂在这条时间轴上：时间戳解析不出来就没有可信读数，
            # ok=False 会让 advance_to_live 直接退回（宁可挡住转正，不拿 observed_days=0 冒充证据）。
            "ok": activated is not None,
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
            return {"ok": bool(obs.get("ok")), "rule_id": rule_id, "mode": MODE_LIVE,
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
        moved = self.store.set_rule_mode(rule_id, MODE_LIVE)
        if not moved:
            return {"ok": False, "error": "规则状态更新失败"}
        self.engine._index_dirty = True
        self.store.log_rule_lifecycle(
            rule_id, ACT_ADVANCE, source_rule_id=obs["source_rule_id"], actor=actor,
            from_state=MODE_DRY_RUN, to_state=MODE_LIVE, reason=reason,
            detail={"observation": obs})
        logger.info("[RuleLifecycle] %s 试运行转正（观察 %.2f 天，误报 0）",
                    rule_id, obs["observed_days"])
        return {"ok": moved, "rule_id": rule_id, "mode": MODE_LIVE, "observation": obs}

    # ── 红线 3：撤销 + 回滚它产生的推断 ─────────────────────────────────

    def revoke(self, rule_id: str, actor: str = "user", reason: str = "",
               rollback_inferences: bool = True) -> dict:
        rule = self.engine.get_rule(rule_id)
        if not rule:
            return {"ok": False, "error": f"规则 {rule_id} 不存在"}
        rolled_back = 0
        if rollback_inferences:
            rolled_back = self.store.rollback_detected_activities(rule_id)
        moved = self.store.set_rule_mode(
            rule_id, MODE_REVOKED, enabled=False, revoked=True)
        if not moved:
            return {"ok": False, "error": "规则状态更新失败"}
        self.engine._index_dirty = True
        self.store.log_rule_lifecycle(
            rule_id, ACT_REVOKE, source_rule_id=rule.get("source_rule_id") or "",
            actor=actor, from_state=rule.get("mode") or MODE_LIVE, to_state=MODE_REVOKED,
            reason=reason,
            detail={"rolled_back_inferences": rolled_back,
                    "rollback_inferences": rollback_inferences})
        logger.info("[RuleLifecycle] 撤销 %s，回滚推断 %d 条", rule_id, rolled_back)
        return {"ok": moved, "rule_id": rule_id, "mode": MODE_REVOKED,
                "rolled_back_inferences": rolled_back}

    # ── 红线 2 的输入：把试运行命中判为误报 ─────────────────────────────

    def flag_false_positive(self, rule_id: str, trigger_id: int,
                            actor: str = "user", reason: str = "") -> dict:
        marked = self.store.mark_rule_trigger_false_positive(trigger_id)
        if not marked:
            return {"ok": False, "error": f"触发记录 {trigger_id} 不存在"}
        self.store.log_rule_lifecycle(
            rule_id, ACT_FALSE_POSITIVE, actor=actor, to_state="false_positive",
            reason=reason, detail={"trigger_id": int(trigger_id)})
        return {"ok": marked, "rule_id": rule_id, "trigger_id": int(trigger_id)}

    # ── 人工录入：写侧挂进通道（四条红线一条不放宽）─────────────────────

    def manual_add(self, *, name: str, steps: list, time_window: str = "",
                   infer: str = "", confidence: float = 0.6,
                   evidence: list | None = None,
                   cooldown_seconds: Any = _KEEP, actor: str = "user") -> dict:
        """人工录一条候选规则：落 ``candidate_rules``，**不落** ``active_rules``。

        DCD 20261005 §二.2 Q1 = 乙 + 丁分两步：只读面先挂上，写 CRUD 挂进 R3 通道。
        于是这条路径录进来的东西与机器建议同类同规——一样要 ``accepted`` +
        ``user_confirmed=1``、一样要跨 ``MIN_EVIDENCE`` 个独立自然日、一样先
        ``dry_run`` 满观察期、一样逐步进 ``rule_lifecycle_audit``。人工身份只改变
        「建议是谁提的」这一格，不换取红线豁免。

        返回值里永远带着 ``gate``（红线 1 的逐条判据）：录一条规则时最坑的不是
        被拒，而是「录成功了，三个月后才发现它永远晋升不了」。缺独立证据、
        缺冷却上限这类缺口在这里就摆在响应上。

        冷却上限沿用 DCD 20261004 MA-裁1 Q1 的口径：不传就等于不设定，晋升端
        按「缺省即拒」处理；这里不吃 ``add_rule`` 的形参默认 300。
        """
        if not isinstance(steps, list) or not steps:
            return {"ok": False, "error": "steps 必须是非空数组（通道按首个事件构造触发子）"}
        existing = self.store.get_candidate_rule_by_name(name)
        if existing and (existing.get("source") or "") != CANDIDATE_SOURCE_MANUAL:
            # ``upsert_candidate_rule`` 按 name 判重。不拦这一条，人工录一条同名规则
            # 就会把机器建议的 steps/evidence 原地洗掉，而审计里那条还是"机器建议"。
            return {"ok": False,
                    "error": f"同名候选已存在且来自机器建议（{existing['rule_id']}）——"
                             f"请改名录入，或先驳回那条建议"}
        seconds, why = parse_cooldown(None if cooldown_seconds is _KEEP else cooldown_seconds)
        if why:
            return {"ok": False, "error": why}
        rid, action = self.store.upsert_candidate_rule(
            name, steps, time_window=time_window, infer=infer,
            confidence=confidence, source=CANDIDATE_SOURCE_MANUAL,
            evidence=evidence or [])
        if not rid:
            return {"ok": False, "error": f"候选质量闸门拒绝录入: {action}"}
        if seconds is not None:
            self.store.set_candidate_rule_cooldown(rid, seconds)
        cand = self.store.get_candidate_rule(rid) or {}
        # ok 位来自回读，不是字面量（门禁 fake-ok-const）：写了没读回来就是没写。
        # 回读为空时连审计都不记——留痕里那条"已录入"会比真相更误导人。
        persisted = cand.get("rule_id") == rid
        if not persisted:
            return {"ok": False, "rule_id": rid,
                    "error": f"候选 {rid} 写入后回读为空（未落盘），未记审计"}
        self.store.log_rule_lifecycle(
            rid, ACT_MANUAL_ADD if action == "added" else ACT_MANUAL_EDIT,
            source_rule_id=rid, actor=actor,
            to_state=str(cand.get("status") or ""),
            reason=f"人工录入（{action}）",
            detail={"steps": steps, "time_window": time_window, "infer": infer,
                    "confidence": confidence, "cooldown_seconds": seconds,
                    "source": CANDIDATE_SOURCE_MANUAL})
        gate = self.eligibility(rid)
        return {"ok": persisted, "rule_id": rid, "candidate_action": action,
                "status": cand.get("status") or "", "gate": gate}

    def manual_edit(self, rule_id: str, *, steps: list | None = _KEEP,
                    time_window: str | None = _KEEP, infer: str | None = _KEEP,
                    confidence: float | None = _KEEP,
                    evidence: list | None = _KEEP,
                    cooldown_seconds: Any = _KEEP,
                    actor: str = "user", reason: str = "") -> dict:
        """改**自己录的**那条候选，且只在她还没进引擎之前。

        两道拒绝都不是保守，是链路的形状：

        * ``source != manual`` ——机器建议的 steps/evidence 是「机器看到了什么」的
          原始记录，人改了就再也不能拿它复盘建议质量；要否掉它走
          ``candidate-rules/update``（驳回/采纳），要自己那条走 ``manual_add``。
        * ``status == promoted`` ——候选已经译成 ``active_rules`` 里的一行，改候选
          行不会改引擎行，两头就此分叉。要改已晋升的规则：先 ``revoke`` 再重录重晋升。
        """
        cand = self.store.get_candidate_rule(rule_id)
        if not cand:
            return {"ok": False, "error": f"候选规则 {rule_id} 不存在"}
        if (cand.get("source") or "") != CANDIDATE_SOURCE_MANUAL:
            return {"ok": False,
                    "error": "只改人工录入的候选；机器建议请走驳回/采纳，不要改写它的 steps"}
        if cand.get("status") == CANDIDATE_PROMOTED:
            return {"ok": False,
                    "error": "该候选已晋升进引擎，请先撤销（revoke）再重新录入，"
                             "直接改候选会让候选行与引擎行分叉"}
        fields = {k: v for k, v in {
            "steps": steps, "time_window": time_window, "infer": infer,
            "confidence": confidence, "evidence": evidence}.items()
            if v is not _KEEP}
        seconds, why = parse_cooldown(None if cooldown_seconds is _KEEP else cooldown_seconds)
        if why:
            return {"ok": False, "error": why}
        if not fields and cooldown_seconds is _KEEP:
            return {"ok": False, "error": "没有要更新的字段"}
        if fields.get("steps") is not None and not fields["steps"]:
            return {"ok": False, "error": "steps 必须是非空数组"}
        if fields:
            if not self.store.update_candidate_rule(rule_id, **fields):
                return {"ok": False, "rule_id": rule_id,
                        "error": f"候选 {rule_id} 更新未命中行（可能已被撤回）"}
        if cooldown_seconds is not _KEEP:
            # 传 None 是**撤回设定**（列回到未设定），与"没传"是两件事：
            # 前者让人能把一个写错的数值清掉，让晋升端重新按缺省即拒处理。
            if not self.store.set_candidate_rule_cooldown(rule_id, seconds):
                return {"ok": False, "rule_id": rule_id,
                        "error": f"候选 {rule_id} 冷却上限未命中行（可能已被撤回）"}
        updated = self.store.get_candidate_rule(rule_id) or {}
        persisted = updated.get("rule_id") == rule_id
        if not persisted:
            return {"ok": False, "rule_id": rule_id,
                    "error": f"候选 {rule_id} 更新后回读为空，未记审计"}
        self.store.log_rule_lifecycle(
            rule_id, ACT_MANUAL_EDIT, source_rule_id=rule_id, actor=actor,
            from_state=str(cand.get("status") or ""), to_state=str(updated.get("status") or ""),
            reason=reason,
            detail={"changed": sorted(fields), "cooldown_seconds": seconds})
        return {"ok": persisted, "rule_id": rule_id, "gate": self.eligibility(rule_id)}

    def manual_withdraw(self, rule_id: str, *, actor: str = "user",
                        reason: str = "") -> dict:
        """撤回并删除**自己录的、还没进引擎的**候选。

        已晋升的不在这一格的射程内：那条候选是引擎里那行的上游依据，删了它
        「谁建议的、按什么证据晋升」就查不回来了；下架规则本身请走 ``revoke``
        （红线 3：连带回滚它产生的推断，并留审计）。
        """
        cand = self.store.get_candidate_rule(rule_id)
        if not cand:
            return {"ok": False, "error": f"候选规则 {rule_id} 不存在"}
        if (cand.get("source") or "") != CANDIDATE_SOURCE_MANUAL:
            return {"ok": False, "error": "只能撤回人工录入的候选；"
                                          "机器建议请驳回（rejected），保留原始记录"}
        if cand.get("status") == CANDIDATE_PROMOTED:
            return {"ok": False, "error": "已晋升的候选不能删——请撤销规则（revoke），"
                                          "它会关闭引擎里的规则并回滚推断"}
        deleted = self.store.delete_candidate_rule(rule_id, CANDIDATE_PRE_ENGINE)
        if not deleted:
            return {"ok": False, "error": f"候选状态 {cand.get('status')} 不在可删词表内"}
        # 删没删掉由回读说了算，不写字面量（门禁 fake-ok-const）：
        # 留痕里那条 withdrawn 会长期充当"这条曾经存在过"的依据。
        gone = self.store.get_candidate_rule(rule_id) is None
        if not gone:
            return {"ok": False, "rule_id": rule_id,
                    "error": f"候选 {rule_id} 删除后仍能读回，未记审计"}
        self.store.log_rule_lifecycle(
            rule_id, ACT_MANUAL_WITHDRAW, source_rule_id=rule_id, actor=actor,
            from_state=str(cand.get("status") or ""), to_state="withdrawn",
            reason=reason, detail={"name": cand.get("name") or ""})
        return {"ok": gone, "rule_id": rule_id, "withdrawn": gone}

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
                # 预演清单里给出冷却上限及其来源：缺数值是这条候选被挡住的常见原因，
                # 只显示"不 eligible"的话，人得逐条点开才知道差一个数。
                "cooldown_seconds": gate.get("cooldown_seconds"),
                "cooldown_source": gate.get("cooldown_source") or "",
            })
        out.sort(key=lambda r: (not r["eligible"], -r["evidence_days"]))
        return out

    def channel_status(self, limit: int = 100) -> dict:
        """通道全景：试运行中 / 已转正 / 已撤销的晋升规则 + 各自观察读数。"""
        buckets: dict[str, list[dict]] = {
            MODE_DRY_RUN: [], MODE_LIVE: [], MODE_REVOKED: []}
        unreadable: list[str] = []
        for rule in self.engine.list_rules(enabled_only=False):
            if (rule.get("origin") or "manual") != "candidate_promoted":
                continue
            mode = rule.get("mode") or MODE_LIVE
            readout = self.observation(rule["rule_id"])
            if not readout.get("ok"):
                unreadable.append(rule["rule_id"])
            buckets.setdefault(mode, []).append(readout)
        return {
            # 全景里任何一条晋升规则读不出观察数据都不算取到可信快照
            "ok": not unreadable,
            "unreadable_rules": unreadable,
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
