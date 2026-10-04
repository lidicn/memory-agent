"""Phase 3.1 主动规则引擎：规则 CRUD + 匹配 + 告警动作。

设计：
- 规则存在数据库表 active_rules
- 事件流触发时，遍历所有启用的规则，匹配条件则执行动作
- STATIC 规则（确定性匹配）毫秒级，不走 LLM
- 动作支持：推送 Bark / 记录日志 / 调用 MCP 工具
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Optional

from .activity_inference import _is_on
from .store import now_local

logger = logging.getLogger("memory_agent.rule_engine")

# 设备事件的 kind 字面量：feed 与 build_condition 共用这一个值，倒排索引按它建桶。
DEVICE_EVENT_KIND = "device"


def state_matches(state: Any, want: Any) -> bool:
    """``cond["state"]`` 与事件状态的比对口径。

    空/``any`` 表示不限；``on``/``off`` 走二值归类（cover 的 closed、插座的 off
    都算 off），复用 ``activity_inference._is_on`` 而不是再抄一份状态字面量表——
    两套口径迟早分叉的话，会出现「同一条规则在推断层命中、在引擎层不命中」
    这种查不出来的分歧。
    """
    w = str(want if want is not None else "").strip().lower()
    if w in ("", "any", "none"):
        return True
    if w == "on":
        return _is_on(state)
    if w == "off":
        return not _is_on(state)
    return str(state or "").strip().lower() == w


class ActiveRuleEngine:
    """主动规则引擎。"""

    def __init__(self, store: Any, alert_dispatcher: Any = None):
        self.store = store
        self.alert_dispatcher = alert_dispatcher
        # Phase 3.3 滑窗去抖：rule_id -> deque[timestamp]
        self._event_windows: dict[str, deque] = defaultdict(deque)
        # 倒排索引：kind -> [rule_id]
        self._kind_index: dict[str, list[str]] = defaultdict(list)
        # 规则缓存
        self._rules_cache: dict[str, dict] = {}
        # 冷却窗口：rule_id -> 下一次允许触发的墙钟（naive）
        self._cooldown_until: dict[str, datetime] = {}
        # 索引是否需要重建
        self._index_dirty = True

    def _now(self) -> datetime:
        """家庭墙钟（naive）。事件 ts / store 时间戳同为墙钟口径，混用机器时区
        会让冷却期、观察期与 time_range 判定整体偏移（BUG-TZ1 同类缺陷）。"""
        return now_local(getattr(self.store, "tz_offset_hours", 8.0))

    def _event_now(self, event: dict) -> datetime:
        """事件自带时间的墙钟口径（解析不出来才退回当前墙钟）。

        ``time_range`` 判定用它：批量 feed 喂的是历史窗口里的事件，"现在"和
        「事件发生时刻」最多差一个轮询周期（默认 1 小时），足以把边界事件
        错分到窗口内外。
        """
        raw = str(event.get("ts") or event.get("server_ts") or "").strip()
        if raw:
            try:
                return datetime.fromisoformat(raw.replace(" ", "T")[:19])
            except ValueError:
                pass
        return self._now()


    # ── 倒排索引 ──────────────────────────────────────────────────────

    def _rebuild_index(self):
        """重建倒排索引。"""
        self._kind_index.clear()
        self._rules_cache.clear()
        rules = self.list_rules(enabled_only=False)
        for rule in rules:
            rule_id = rule["rule_id"]
            self._rules_cache[rule_id] = rule
            # 从条件中提取 kind
            kind = self._extract_kind(rule.get("condition", {}))
            if kind:
                if isinstance(kind, list):
                    for k in kind:
                        self._kind_index[k].append(rule_id)
                else:
                    self._kind_index[kind].append(rule_id)
            else:
                # 无 kind 约束的规则，放在通配桶
                self._kind_index["*"].append(rule_id)
        self._index_dirty = False
        logger.info(f"规则索引重建完成: {len(self._rules_cache)} 条规则, {len(self._kind_index)} 个桶")

    def _extract_kind(self, condition: dict) -> str | list[str] | None:
        """从条件中提取 kind 约束。"""
        if not isinstance(condition, dict):
            return None
        # 直接有 kind
        if "kind" in condition:
            return condition["kind"]
        # AND 中的 kind
        if "AND" in condition:
            for sub in condition["AND"]:
                kind = self._extract_kind(sub)
                if kind:
                    return kind
        # OR 中的 kind 无法静态确定（可能匹配任意一个）
        if "OR" in condition:
            return None
        # NOT 中的 kind 无法静态确定
        if "NOT" in condition:
            return None
        return None

    def _get_candidate_rules(self, event_kind: str) -> list[dict]:
        """根据事件类型获取候选规则。"""
        if self._index_dirty:
            self._rebuild_index()
        # 获取候选规则 ID
        rule_ids = set()
        # 通配桶
        rule_ids.update(self._kind_index.get("*", []))
        # 具体 kind 桶
        rule_ids.update(self._kind_index.get(event_kind, []))
        # 返回规则对象
        return [self._rules_cache[rid] for rid in rule_ids if rid in self._rules_cache]

    # ── 规则测试 ──────────────────────────────────────────────────────

    def test_rule(self, rule: dict, events: list[dict], ignore_trigger: bool = False) -> dict:
        """测试规则：用历史事件或模拟事件测试规则。

        返回：
        {
            "total": 总事件数,
            "matched": 匹配数,
            "false_positive": 误触发数,
            "false_negative": 漏触发数,
            "precision": 精确率,
            "recall": 召回率,
            "items": [测试详情]
        }
        """
        total = 0
        matched = 0
        false_positive = 0
        false_negative = 0
        items = []

        for event in events:
            total += 1
            expected = event.get("expected", "unknown")
            # 匹配条件（不检查触发策略，只测试条件本身）
            result, trace = self._match_condition(rule["condition"], event, trace=True)
            if result:
                matched += 1
            # 判断是否误触发或漏触发
            if expected == "positive" and not result:
                false_negative += 1
            elif expected == "negative" and result:
                false_positive += 1
            # 记录详情
            items.append({
                "event": event,
                "matched": result,
                "expected": expected,
                "verdict": "OK" if result == (expected == "positive") else ("FP" if expected == "negative" else "FN"),
                "trace": trace
            })

        # 计算 precision 和 recall
        precision = round((matched - false_positive) / matched, 4) if matched > 0 else 1.0
        recall = round((total - false_negative) / total, 4) if total > 0 else 1.0

        return {
            "total": total,
            "matched": matched,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": precision,
            "recall": recall,
            "items": items
        }

    def simulate_events(self, spec: dict) -> list[dict]:
        """模拟事件：根据规格生成模拟事件。

        spec 格式：
        {
            "base": {"kind": "face", "room": "livingroom"},
            "grid": {"person": ["Kevin", "stranger"], "confidence": [0.2, 0.5, 0.9]},
            "label_rule": "identity == 'stranger'"  # 真值标注表达式
        }
        """
        import itertools
        base = spec.get("base", {})
        grid = spec.get("grid", {})
        keys = list(grid.keys())
        events = []
        # 生成所有组合
        for combo in itertools.product(*[grid[k] for k in keys]):
            event = base.copy()
            # product 的维度就是 keys 的顺序，两者长度由构造相等；strict 是给
            # 将来 grid 与 keys 不同步时兜底的（P2-1 同类：错位会造出假事件）。
            event.update(dict(zip(keys, combo, strict=True)))
            # 根据 label_rule 标注真值
            # 简单实现：如果 person 是 stranger，就是 positive
            if "stranger" in str(event.get("person", "")):
                event["expected"] = "positive"
            else:
                event["expected"] = "negative"
            events.append(event)
        return events

    # ── 统计服务 ──────────────────────────────────────────────────────

    def get_rule_stats(self, rule_id: str, days: int = 7) -> dict:
        """获取规则统计。

        返回：
        {
            "trigger_count": 触发次数,
            "false_positive": 误触发次数,
            "false_negative": 漏触发次数,
            "precision": 精确率,
            "recall": 召回率,
            "avg_latency_ms": 平均延迟
        }
        """
        # 第六轮审计 CRITICAL-2：共享连接必须在 Store 的锁内使用（_db() 持锁并交连接），
        # 否则本线程的读会撞进别的线程尚未提交的事务里。
        with self.store._db() as conn:
            # 查询最近 N 天的触发历史（triggered_at 是墙钟 ISO 串，按字典序即可比较）
            since = (self._now() - timedelta(days=days)).isoformat(sep="T")
            rows = conn.execute(
                """
                SELECT * FROM rule_trigger_history
                WHERE rule_id = ? AND triggered_at >= ?
                """,
                (rule_id, since)
            ).fetchall()

        trigger_count = len(rows)
        false_positive = sum(1 for r in rows if r["false_positive"]) if rows else 0
        dry_run_count = sum(1 for r in rows if r["dry_run"]) if rows else 0
        # 漏触发需要负样本来源，目前仍无人工标注入口，保持 0（不虚构召回率）
        false_negative = 0
        precision = round((trigger_count - false_positive) / trigger_count, 4) if trigger_count > 0 else 1.0
        recall = 1.0  # 暂时设为 1.0

        return {
            "rule_id": rule_id,
            "days": days,
            "trigger_count": trigger_count,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "dry_run_count": dry_run_count,
            "precision": precision,
            "recall": recall,
            "effect": "good" if precision >= 0.8 else ("tune" if precision >= 0.5 else "review")
        }

    def get_overall_stats(self, days: int = 7) -> dict:
        """获取整体统计。"""
        rules = self.list_rules(enabled_only=True)
        total_rules = len(rules)
        active_rules = 0
        total_triggers = 0
        total_fp = 0
        total_fn = 0

        for rule in rules:
            stats = self.get_rule_stats(rule["rule_id"], days)
            if stats["trigger_count"] > 0:
                active_rules += 1
            total_triggers += stats["trigger_count"]
            total_fp += stats["false_positive"]
            total_fn += stats["false_negative"]

        return {
            "total_rules": total_rules,
            "active_rules": active_rules,
            "usage_rate": round(active_rules / total_rules, 4) if total_rules > 0 else 0.0,
            "total_triggers": total_triggers,
            "total_fp": total_fp,
            "total_fn": total_fn,
            "days": days
        }

    # ── 规则 CRUD ──────────────────────────────────────────────────────

    def add_rule(
        self,
        name: str,
        condition: dict,
        action: dict,
        description: str = "",
        enabled: bool = True,
        cooldown_seconds: int = 300,
        rule_type: str = "static",
        origin: str = "manual",
        source_rule_id: str = "",
        mode: str = "live",
        evidence_count: int = 0,
        trigger: dict | None = None,
    ) -> dict:
        """添加规则。

        rule_type: static（确定性，无 VLM 快路径）/ dynamic（需 VLM 语义理解）
        origin/ source_rule_id/ mode/ evidence_count 是 DCD R3 生效通道的载体：
        人工建的规则默认 ``manual`` + ``live``，候选晋升走 ``candidate_promoted``
        + ``dry_run``（观察期内只记录不触发）。

        ``trigger`` 是触发策略（``{"type": "count", "window_seconds": 60,
        "min_count": 3}``）。此前它只存在于 ``match_event`` 的读取端，
        ``active_rules`` 没有对应列，于是 ``rule.get("trigger")`` 恒为 ``{}``——
        count 分支是不可达代码，DCD Q2 裁定的「60 秒 3 次」也无从落地。
        """
        rule_id = f"rule_{self._now().strftime('%Y%m%d%H%M%S%f')}"
        now = self._now().isoformat(sep="T")
        try:
            with self.store.transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO active_rules
                    (rule_id, name, description, condition_json, action_json, enabled,
                     cooldown_seconds, rule_type, origin, source_rule_id, mode,
                     evidence_count, promoted_at, activated_at, created_at, updated_at,
                     trigger_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule_id,
                        name,
                        description,
                        json.dumps(condition, ensure_ascii=False),
                        json.dumps(action, ensure_ascii=False),
                        1 if enabled else 0,
                        cooldown_seconds,
                        rule_type,
                        origin,
                        source_rule_id,
                        mode,
                        int(evidence_count or 0),
                        now if origin == "candidate_promoted" else "",
                        now,
                        now,
                        now,
                        json.dumps(trigger or {}, ensure_ascii=False),
                    ),
                )
            inserted = cur.rowcount == 1
            if inserted:
                self._index_dirty = True  # 标记索引为脏
            return {"ok": inserted, "rule_id": rule_id if inserted else "", "mode": mode}
        except Exception as exc:
            logger.error(f"添加规则失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def list_rules(self, enabled_only: bool = False) -> list[dict]:
        """列出所有规则。

        单条规则的 JSON 列读不通时只跳过那一条并留 WARNING，不让整批规则消失
        （第七轮审计 · 一条脏记录打断整批）。
        """
        with self.store._db() as conn:
            sql = "SELECT * FROM active_rules"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql).fetchall()
        result = []
        for row in rows:
            d = self._row_to_rule(row)
            if d is not None:
                result.append(d)
        return result

    def get_rule(self, rule_id: str) -> Optional[dict]:
        """获取单条规则；列已损坏、无法安全求值的规则按「不存在」处理。"""
        with self.store._db() as conn:
            row = conn.execute(
                "SELECT * FROM active_rules WHERE rule_id = ?", (rule_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_rule(row)

    @staticmethod
    def _row_to_rule(row: Any) -> Optional[dict]:
        """DB 行 → 规则字典；解析不通返回 None（调用方跳过该条）。

        坏 condition 绝不能退化成 ``{}`` 继续入索引：``_extract_kind({})`` 取不到 kind，
        规则会被放进 ``*`` 通配桶、对每一条事件求值——一条读不出来的规则就此变成
        全屋规则。
        """
        d = dict(row)
        try:
            condition = json.loads(d.pop("condition_json", "{}"))
            action = json.loads(d.pop("action_json", "{}"))
            trigger = json.loads(d.pop("trigger_json", "") or "{}")
        except (TypeError, ValueError) as exc:
            logger.warning("规则 %s 的 JSON 列无法解析，跳过该条: %s",
                           d.get("rule_id", "?"), exc)
            return None
        if not isinstance(condition, dict) or not isinstance(action, dict) \
                or not isinstance(trigger, dict):
            logger.warning("规则 %s 的 condition/action/trigger 不是对象，跳过该条",
                           d.get("rule_id", "?"))
            return None
        d["condition"] = condition
        d["action"] = action
        d["trigger"] = trigger
        d["enabled"] = bool(d.get("enabled"))
        d["rule_type"] = d.get("rule_type", "static")
        return d

    def update_rule(self, rule_id: str, **kwargs) -> dict:
        """更新规则。"""
        updates = []
        params = []
        for k in ("name", "description", "enabled", "cooldown_seconds"):
            if k in kwargs:
                updates.append(f"{k} = ?")
                v = kwargs[k]
                if k == "enabled":
                    v = 1 if v else 0
                params.append(v)
        if "condition" in kwargs:
            updates.append("condition_json = ?")
            params.append(json.dumps(kwargs["condition"], ensure_ascii=False))
        if "action" in kwargs:
            updates.append("action_json = ?")
            params.append(json.dumps(kwargs["action"], ensure_ascii=False))
        if "trigger" in kwargs:
            updates.append("trigger_json = ?")
            params.append(json.dumps(kwargs["trigger"] or {}, ensure_ascii=False))
        if not updates:
            return {"ok": False, "error": "无更新字段"}
        updates.append("updated_at = ?")
        params.append(self._now().isoformat(sep="T"))
        params.append(rule_id)
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    f"UPDATE active_rules SET {', '.join(updates)} WHERE rule_id = ?",
                    params,
                )
            self._index_dirty = True  # 标记索引为脏
            return {"ok": True}
        except Exception as exc:
            logger.error(f"更新规则失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def delete_rule(self, rule_id: str) -> dict:
        """删除规则。"""
        try:
            with self.store.transaction() as conn:
                conn.execute("DELETE FROM active_rules WHERE rule_id = ?", (rule_id,))
            self._cooldown_until.pop(rule_id, None)
            self._index_dirty = True  # 标记索引为脏
            return {"ok": True}
        except Exception as exc:
            logger.error(f"删除规则失败: {exc}")
            return {"ok": False, "error": str(exc)}

    # ── 规则匹配 ──────────────────────────────────────────────────────

    def match_event(self, event: dict, rule_type: str | None = None, *,
                    now_ts: float | None = None) -> list[dict]:
        """匹配事件，返回触发的规则列表。

        event 结构（感知）::

            {
                "kind": "face_unknown",
                "room": "客厅",
                "person": "陌生人",
                "confidence": 0.8,
                "server_ts": "2026-09-22T12:00:00",
            }

        event 结构（设备，由 ``device_feed`` 产出）::

            {
                "kind": "device", "domain": "binary_sensor",
                "entity_id": "binary_sensor.door_front", "tags": ["door"],
                "state": "on", "old_state": "off", "action": "off->on",
                "room": "客厅", "ts": "2026-09-22T12:00:00",
            }

        rule_type: 可选，只匹配指定类型的规则（static/dynamic）
        now_ts: count 触发窗口的「当前时刻」（真实 epoch）。在线路径不传，
        用 ``time.time()``；批量扫描器**必须**传事件自身的时间——否则回放
        一小时的历史时，全部事件都被当成「同一瞬间」发生，``min_count``
        门槛形同不存在（这是 Q1=B「聚合再评估」的核心语义，不是精度优化）。

        冷却期（``cooldown_seconds``）与 count 窗口同一口径：实时路径按「现在」
        判定并会从 ``rule_trigger_history`` 续上重启前的窗口，批量回放按事件
        自身的墙钟判定。见 :meth:`_check_cooldown`。
        """
        # 使用倒排索引获取候选规则
        rules = self._get_candidate_rules(event.get("kind", ""))
        triggered = []
        now = time.time() if now_ts is None else float(now_ts)
        live = now_ts is None
        # 冷却判定的时钟口径与 count 窗口一致：实时路径按「现在」（告警是此刻发出去的），
        # 批量回放按事件自身的墙钟（否则一轮历史里的事件全被判成同一瞬间）。
        cd_now = self._now() if live else self._event_now(event)
        for rule in rules:
            # 只匹配启用的规则
            if not rule.get("enabled", True):
                continue
            # 按类型过滤
            if rule_type and rule.get("rule_type", "static") != rule_type:
                continue
            if self._match_condition(rule["condition"], event):
                # 触发策略
                trigger = rule.get("trigger") or {}
                trigger_type = trigger.get("type", "single")
                if trigger_type == "count":
                    # count：N 次才触发。60 秒 / 3 次是 DCD Q2 的**裁定值**，
                    # 不是开发拍的默认值，改动要回 DCD。
                    window_seconds = int(trigger.get("window_seconds", 60))
                    min_count = int(trigger.get("min_count", 3))
                    if not self._check_count_trigger(rule["rule_id"], now, window_seconds, min_count):
                        continue
                elif trigger_type == "absence":
                    # absence：长时间无人（由后台扫描器处理，这里不触发）
                    continue
                else:
                    # single：匹配即触发
                    pass

                # 检查冷却期
                if self._check_cooldown(rule, cd_now, live=live):
                    self._mark_cooldown(rule, cd_now)
                    triggered.append(rule)
                else:
                    logger.info("[RuleCooldown] 冷却期内抑制重复触发: %s", rule.get("name"))
        return triggered

    def _check_count_trigger(self, rule_id: str, now: float, window_seconds: int, min_count: int) -> bool:
        """检查 count 触发策略：窗口内是否达到最小次数。"""
        # 记录事件时间戳
        self._event_windows[rule_id].append(now)
        # 清理窗口外的旧事件
        while self._event_windows[rule_id] and self._event_windows[rule_id][0] < now - window_seconds:
            self._event_windows[rule_id].popleft()
        # 检查是否达到阈值
        return len(self._event_windows[rule_id]) >= min_count

    def _match_condition(self, condition: dict, event: dict, trace: bool = False) -> bool | tuple[bool, list]:
        """匹配条件（支持 AND/OR/NOT 嵌套）。

        trace: 是否返回匹配追踪信息
        返回：如果 trace=False，返回 bool；如果 trace=True，返回 (bool, trace_list)
        """
        result, trace_list = self._eval_condition(condition, event, trace)
        if trace:
            return result, trace_list
        return result

    def _eval_condition(self, cond: Any, event: dict, trace: bool = False) -> tuple[bool, list]:
        """递归求值条件 DSL。"""
        trace_list = []

        # 如果是列表，默认 AND
        if isinstance(cond, list):
            results = []
            for c in cond:
                r, t = self._eval_condition(c, event, trace)
                results.append(r)
                if trace:
                    trace_list.append(t)
            result = all(results)
            if trace:
                return result, {"op": "AND", "result": result, "children": trace_list}
            return result, []

        # 如果不是字典，无法匹配
        if not isinstance(cond, dict):
            if trace:
                return False, {"op": "PRED", "result": False, "error": "invalid condition"}
            return False, []

        # 逻辑运算符
        if "AND" in cond:
            results = []
            for c in cond["AND"]:
                r, t = self._eval_condition(c, event, trace)
                results.append(r)
                if trace:
                    trace_list.append(t)
            result = all(results)
            if trace:
                return result, {"op": "AND", "result": result, "children": trace_list}
            return result, []

        if "OR" in cond:
            results = []
            for c in cond["OR"]:
                r, t = self._eval_condition(c, event, trace)
                results.append(r)
                if trace:
                    trace_list.append(t)
            result = any(results)
            if trace:
                return result, {"op": "OR", "result": result, "children": trace_list}
            return result, []

        if "NOT" in cond:
            r, t = self._eval_condition(cond["NOT"], event, trace)
            result = not r
            if trace:
                return result, {"op": "NOT", "result": result, "child": t}
            return result, []

        # 原子条件
        result, atom_trace = self._match_atom(cond, event, trace)
        if trace:
            return result, atom_trace
        return result, []

    def _match_atom(self, cond: dict, event: dict, trace: bool = False) -> tuple[bool, dict]:
        """匹配原子条件（单个条件）。"""
        failures = []

        # kind 匹配
        if "kind" in cond:
            expected = cond["kind"]
            actual = event.get("kind", "")
            if isinstance(expected, list):
                if actual not in expected:
                    failures.append(f"kind: expected {expected}, got {actual}")
            else:
                if actual != expected:
                    failures.append(f"kind: expected {expected}, got {actual}")

        # room 匹配
        if "room" in cond:
            expected = cond["room"]
            actual = event.get("room", "")
            if isinstance(expected, list):
                if actual not in expected:
                    failures.append(f"room: expected {expected}, got {actual}")
            else:
                if actual != expected:
                    failures.append(f"room: expected {expected}, got {actual}")

        # person 匹配
        if "person" in cond:
            expected = cond["person"]
            actual = event.get("person", "")
            if isinstance(expected, list):
                if actual not in expected:
                    failures.append(f"person: expected {expected}, got {actual}")
            else:
                if actual != expected:
                    failures.append(f"person: expected {expected}, got {actual}")

        # identity 匹配
        if "identity" in cond:
            expected = cond["identity"]
            actual = event.get("identity", "unknown")
            if expected == "stranger":
                if actual != "stranger" and actual != "unknown":
                    failures.append(f"identity: expected stranger, got {actual}")
            elif expected == "known":
                if actual == "stranger" or actual == "unknown":
                    failures.append(f"identity: expected known, got {actual}")
            else:
                if actual != expected:
                    failures.append(f"identity: expected {expected}, got {actual}")

        # min_confidence 匹配
        if "min_confidence" in cond:
            actual_conf = float(event.get("confidence") or 0)
            expected_conf = float(cond["min_confidence"])
            if actual_conf < expected_conf:
                failures.append(f"min_confidence: expected >= {expected_conf}, got {actual_conf}")

        # home_mode 匹配
        if "home_mode" in cond:
            expected = cond["home_mode"]
            actual = event.get("home_mode", "home")
            if actual != expected:
                failures.append(f"home_mode: expected {expected}, got {actual}")

        # time_range 匹配（扩展条件）
        if "time_range" in cond:
            time_range = cond["time_range"]
            # 格式："19:00-22:00"
            if "-" in time_range:
                start_str, end_str = time_range.split("-")
                # 判定基准取**事件自身**的墙钟（无 ts 才退回当前墙钟）：
                # 批量扫描器喂的是上一窗口的历史事件，用「现在」判 time_range
                # 会把 18:50 的开门算进 "19:00-22:00" 这类规则里。
                now = self._event_now(event)
                current_minutes = now.hour * 60 + now.minute
                # 解析开始时间
                start_h, start_m = map(int, start_str.split(":"))
                start_minutes = start_h * 60 + start_m
                # 解析结束时间
                end_h, end_m = map(int, end_str.split(":"))
                end_minutes = end_h * 60 + end_m
                # 检查是否在时间范围内
                if not (start_minutes <= current_minutes <= end_minutes):
                    failures.append(f"time_range: expected {time_range}, current {now.strftime('%H:%M')}")

        # state 匹配（设备二值态，如门开/灯关）
        if "state" in cond:
            # 此前这里是 ``# TODO`` + ``pass``——带 state 原子的条件**恒真**，
            # 「门开着」的规则会在门关时也命中。feed 上线同时把语义补实，
            # 判红口径与推断层一致（``state_matches``）。
            actual_state = event.get("state")
            if not state_matches(actual_state, cond["state"]):
                failures.append(f"state: expected {cond['state']}, got {actual_state}")

        # old_state 匹配（状态迁移语义：与 state 组成「从 X 变成 Y」）
        # device_feed.to_event 会把 HA 的 old_state 一起喂进来；感知/视觉类事件没有
        # 这个字段，因此带 old_state 的规则对它们恒不命中——「不知道从哪来」不等于
        # 「确认从没来」。这里不能直接把缺失值丢给 state_matches：它把空值判成 off，
        # 于是「没有 old_state」会伪装成「从 off 变来」，凭空造出一次迁移。
        if "old_state" in cond:
            want_old = str(cond["old_state"] if cond["old_state"] is not None else "")
            want_old = want_old.strip().lower()
            actual_old = str(event.get("old_state") or "").strip()
            if want_old in ("", "any", "none"):
                pass                                  # 不限，与空值口径一致
            elif not actual_old:
                failures.append(f"old_state: expected {cond['old_state']}, event carries none")
            elif not state_matches(actual_old, cond["old_state"]):
                failures.append(f"old_state: expected {cond['old_state']}, got {actual_old}")

        # domain 匹配（HA 实体域，如 binary_sensor / climate）
        if "domain" in cond:
            expected = cond["domain"]
            actual_domain = event.get("domain", "")
            if isinstance(expected, list):
                if actual_domain not in expected:
                    failures.append(f"domain: expected {expected}, got {actual_domain}")
            elif actual_domain != expected:
                failures.append(f"domain: expected {expected}, got {actual_domain}")

        # entity_id 匹配（精确到某个实体）
        if "entity_id" in cond:
            expected = cond["entity_id"]
            actual_eid = event.get("entity_id", "")
            if isinstance(expected, list):
                if actual_eid not in expected:
                    failures.append(f"entity_id: expected {expected}, got {actual_eid}")
            elif actual_eid != expected:
                failures.append(f"entity_id: expected {expected}, got {actual_eid}")

        # tag 匹配（设备语义标签，词表与 device_feed.FEED_TAGS 同源）
        if "tag" in cond:
            expected = cond["tag"]
            event_tags = set(event.get("tags") or ())
            want = {expected} if isinstance(expected, str) else set(expected or ())
            if not (want & event_tags):
                failures.append(f"tag: expected {sorted(want)}, got {sorted(event_tags)}")

        result = len(failures) == 0
        if trace:
            return result, {
                "op": "PRED",
                "result": result,
                "failures": failures,
                "condition": cond
            }
        return result, {}

    def _check_cooldown(self, rule: dict, now_dt: datetime, *,
                        live: bool = False) -> bool:
        """冷却期判定。True = 允许触发，False = 仍在冷却窗口内。

        ``cooldown_seconds`` 此前只写进 ``active_rules`` 列、匹配端从不读它
        （这里曾是 ``# TODO / return True`` 桩）。结果是规则作者承诺的
        「5 分钟内不重复」完全不成立：门磁一分钟连开五次，家人就收到五条告警。

        时钟口径：由调用方给的家庭墙钟（实时路径 = 当前墙钟，批量回放 = 事件
        自身墙钟），不用 ``time.monotonic()``——单调时钟从进程 0 起算，冷却配得
        比 uptime 大就会永久吞掉首次触发（第一轮审计 P1-1 正是这一类）。墙钟没有
        uptime 依赖，且与 ``triggered_at`` 同框，重启后能从触发历史直接续上。

        ``live`` 只由实时路径置真（``match_event`` 不传 ``now_ts``）。批量 feed
        回放的是历史事件，而历史行的 ``triggered_at`` 落的是**当时**的真实墙钟
        （≥ 事件时刻），拿它判「事件时刻」会把整轮回放误判成冷却中。

        非法/负数 ``cooldown_seconds`` 一律按「不冷却」处理：数值坏了就抑制
        触发，等于把家里的告警静默丢掉，比多播一次危险。
        """
        seconds = self._cooldown_seconds(rule)
        if seconds <= 0:
            return True
        rule_id = rule.get("rule_id", "")
        until = self._cooldown_until.get(rule_id)
        if until is None and live:
            last = self._last_trigger_wall(rule_id)
            if last is not None:
                until = last + timedelta(seconds=seconds)
                self._cooldown_until[rule_id] = until
        if until is None:
            return True
        return now_dt >= until

    def _mark_cooldown(self, rule: dict, now_dt: datetime) -> None:
        """规则本次决定触发 -> 占用一个冷却窗口。

        在派发**之前**记账（与 ``perception_rules.RuleEngine`` 同口径）：动作执行
        失败不该让下一条事件立刻重播；试运行同样占用窗口，观察期的触发计数因此
        与转正后实际会发出的条数一致。
        """
        seconds = self._cooldown_seconds(rule)
        if seconds <= 0:
            return
        self._cooldown_until[rule.get("rule_id", "")] = now_dt + timedelta(seconds=seconds)

    @staticmethod
    def _cooldown_seconds(rule: dict) -> int:
        try:
            return max(0, int(float(rule.get("cooldown_seconds") or 0)))
        except (TypeError, ValueError):
            return 0

    def _last_trigger_wall(self, rule_id: str) -> Optional[datetime]:
        """该规则最后一次触发的墙钟；无历史/读失败返回 ``None``。

        含 ``dry_run`` 行：观察期同样不该重复吵人。
        """
        if not rule_id:
            return None
        try:
            # 第六轮审计 CRITICAL-2：共享连接必须在 Store 的锁内使用
            with self.store._db() as conn:
                row = conn.execute(
                    "SELECT MAX(triggered_at) AS last FROM rule_trigger_history"
                    " WHERE rule_id = ?",
                    (rule_id,),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 - 读失败退回内存口径，不得打断匹配
            logger.warning("读取规则 %s 的最后触发时间失败: %s", rule_id, exc)
            return None
        raw = str((row["last"] if row else "") or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace(" ", "T")[:19])
        except ValueError:
            return None

    # ── 执行动作 ──────────────────────────────────────────────────────

    def execute_action(self, rule: dict, event: dict, *,
                       dry_run_override: bool = False) -> dict:
        """执行规则动作。

        DCD R3 观察期红线：``mode == 'dry_run'`` 的规则照常匹配并记录触发历史
        （带 ``dry_run=1``），但**不派发任何副作用**。误报在试运行期被发现
        时不会真的吵到家里人。

        ``dry_run_override`` 让调用方在不改规则mode的情况下强制试运行——
        设备 feed 的首轮（DCD Q3=(i)「通道建好、推进等人」）用它保证
        「即便规则已是 live，新通道第一遍也只记录不吵人」。
        """
        action = rule.get("action", {})
        action_type = action.get("type", "log")
        dry_run = dry_run_override or str(rule.get("mode") or "live") == "dry_run"
        if dry_run:
            logged = self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},
                                       dry_run=True)
            logger.info(f"[RuleDryRun] 试运行命中（未派发）: {rule.get('name')} "
                        f"/ {action_type} / 事件 {event.get('kind')}")
            return {"ok": logged, "dry_run": True, "dispatched": False,
                    "action_type": action_type}
        try:
            if action_type == "alert":
                return self._action_alert(rule, event, action)
            elif action_type == "log":
                return self._action_log(rule, event)
            elif action_type == "webhook":
                return self._action_webhook(rule, event, action)
            elif action_type == "tts":
                return self._action_tts(rule, event, action)
            elif action_type == "light":
                return self._action_light(rule, event, action)
            elif action_type == "camera":
                return self._action_camera(rule, event, action)
            elif action_type == "infer_activity":
                return self._action_infer_activity(rule, event, action)
            else:
                logger.warning(f"未知动作类型: {action_type}")
                return {"ok": False, "error": f"未知动作类型: {action_type}"}
        except Exception as exc:
            logger.error(f"执行动作失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def _action_infer_activity(self, rule: dict, event: dict, action: dict) -> dict:
        """落地一条规则推断的活动（带 source_rule_id，供撤销时回滚）。"""
        activity = str(action.get("activity") or rule.get("name") or "").strip()
        if not activity:
            return {"ok": False, "error": "infer_activity 缺少 activity"}
        day = str(event.get("ts") or event.get("server_ts") or "")[:10] \
            or self._now().date().isoformat()
        rule_id = rule["rule_id"]
        row = {
            "activity_id": f"rule:{rule_id}:{day}:{activity}",
            "day": day,
            "activity": activity,
            "confidence": float(action.get("confidence") or 0.6),
            "evidence": [f"rule:{rule_id}", str(event.get("entity_id") or event.get("kind") or "")],
            "room": str(event.get("room") or action.get("room") or ""),
            "session_id": str(event.get("session_id") or ""),
            "created_at": self._now().isoformat(sep="T"),
            "source_rule_id": rule_id,
        }
        self.store.upsert_detected_activities([row])
        logged = self._log_trigger(rule_id, event, action)
        return {"ok": logged, "activity_id": row["activity_id"], "day": day}

    def _action_alert(self, rule: dict, event: dict, action: dict) -> dict:
        """推送告警动作。"""
        message = action.get("message", f"规则触发: {rule['name']}")
        channel = action.get("channel", "bark")
        # TODO: 调用 AlertDispatcher 或直接推送
        logger.info(f"[RuleAlert] {channel}: {message} (规则: {rule['name']}, 事件: {event.get('kind')})")
        # 记录触发历史
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "channel": channel, "message": message}

    def _action_log(self, rule: dict, event: dict) -> dict:
        """记录日志动作。"""
        logger.info(f"[RuleLog] 规则触发: {rule['name']}, 事件: {event.get('kind')}")
        self._log_trigger(rule["rule_id"], event, {"type": "log"})
        return {"ok": True}

    def _action_webhook(self, rule: dict, event: dict, action: dict) -> dict:
        """调用 webhook 动作。"""
        url = action.get("url")
        if not url:
            return {"ok": False, "error": "webhook url 未配置"}
        # TODO: 实际调用 webhook
        logger.info(f"[RuleWebhook] {url}: {rule['name']}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "url": url}

    def _action_tts(self, rule: dict, event: dict, action: dict) -> dict:
        """语音播报动作。"""
        text = action.get("text", f"规则触发: {rule['name']}")
        room = action.get("room", event.get("room", ""))
        # TODO: 实际调用 TTS
        logger.info(f"[RuleTTS] {room}: {text}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "room": room, "text": text}

    def _action_light(self, rule: dict, event: dict, action: dict) -> dict:
        """控制灯光动作。"""
        device = action.get("device", "livingroom_light")
        cmd = {k: v for k, v in action.items() if k in ("on", "scene", "brightness", "color")}
        # TODO: 实际控制灯光
        logger.info(f"[RuleLight] {device}: {cmd}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "device": device, "cmd": cmd}

    def _action_camera(self, rule: dict, event: dict, action: dict) -> dict:
        """控制摄像头动作。"""
        device = action.get("device", "livingroom_camera")
        cam_action = action.get("action", "snapshot")
        # TODO: 实际控制摄像头
        logger.info(f"[RuleCamera] {device}: {cam_action}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "device": device, "action": cam_action}

    def _log_trigger(self, rule_id: str, event: dict, action: dict,
                    dry_run: bool = False) -> bool:
        """记录规则触发历史（试运行命中带 dry_run=1，观察期判据靠这一位）。

        返回是否真的落库：观察期天数、误报计数都以这条行为准，写失败得让调用方知道。
        """
        try:
            with self.store.transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO rule_trigger_history
                    (rule_id, event_json, action_json, triggered_at, dry_run)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        rule_id,
                        json.dumps(event, ensure_ascii=False),
                        json.dumps(action, ensure_ascii=False),
                        self._now().isoformat(sep="T"),
                        1 if dry_run else 0,
                    ),
                )
            return cur.rowcount == 1
        except Exception as exc:
            logger.error(f"记录规则触发历史失败: {exc}")
            return False


# 全局单例
_engine: Optional[ActiveRuleEngine] = None


def get_rule_engine(store: Any, alert_dispatcher: Any = None) -> ActiveRuleEngine:
    """获取全局规则引擎单例。"""
    global _engine
    if _engine is None:
        _engine = ActiveRuleEngine(store, alert_dispatcher)
    return _engine
