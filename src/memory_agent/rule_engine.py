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
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("memory_agent.rule_engine")


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
        # 索引是否需要重建
        self._index_dirty = True

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
            event.update(dict(zip(keys, combo)))
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
        conn = self.store.connect()
        # 查询最近 N 天的触发历史
        since = datetime.now().timestamp() - days * 86400
        rows = conn.execute(
            """
            SELECT * FROM rule_trigger_history
            WHERE rule_id = ? AND triggered_at >= ?
            """,
            (rule_id, datetime.fromtimestamp(since).isoformat())
        ).fetchall()

        trigger_count = len(rows)
        # TODO: 从触发历史中提取误触发和漏触发
        false_positive = 0
        false_negative = 0
        precision = round((trigger_count - false_positive) / trigger_count, 4) if trigger_count > 0 else 1.0
        recall = 1.0  # 暂时设为 1.0

        return {
            "rule_id": rule_id,
            "days": days,
            "trigger_count": trigger_count,
            "false_positive": false_positive,
            "false_negative": false_negative,
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
    ) -> dict:
        """添加规则。

        rule_type: static（确定性，无 VLM 快路径）/ dynamic（需 VLM 语义理解）
        """
        conn = self.store.connect()
        rule_id = f"rule_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        try:
            # 先尝试加列（如果不存在）
            try:
                conn.execute("ALTER TABLE active_rules ADD COLUMN rule_type TEXT NOT NULL DEFAULT 'static'")
            except Exception:
                pass  # 列已存在
            conn.execute(
                """
                INSERT INTO active_rules
                (rule_id, name, description, condition_json, action_json, enabled, cooldown_seconds, rule_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            self._index_dirty = True  # 标记索引为脏
            return {"ok": True, "rule_id": rule_id}
        except Exception as exc:
            logger.error(f"添加规则失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def list_rules(self, enabled_only: bool = False) -> list[dict]:
        """列出所有规则。"""
        conn = self.store.connect()
        sql = "SELECT * FROM active_rules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["condition"] = json.loads(d.pop("condition_json", "{}"))
            d["action"] = json.loads(d.pop("action_json", "{}"))
            d["enabled"] = bool(d.get("enabled"))
            d["rule_type"] = d.pop("rule_type", "static")
            result.append(d)
        return result

    def get_rule(self, rule_id: str) -> Optional[dict]:
        """获取单条规则。"""
        conn = self.store.connect()
        row = conn.execute(
            "SELECT * FROM active_rules WHERE rule_id = ?", (rule_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["condition"] = json.loads(d.pop("condition_json", "{}"))
        d["action"] = json.loads(d.pop("action_json", "{}"))
        d["enabled"] = bool(d.get("enabled"))
        return d

    def update_rule(self, rule_id: str, **kwargs) -> dict:
        """更新规则。"""
        conn = self.store.connect()
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
        if not updates:
            return {"ok": False, "error": "无更新字段"}
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(rule_id)
        try:
            conn.execute(
                f"UPDATE active_rules SET {', '.join(updates)} WHERE rule_id = ?",
                params,
            )
            conn.commit()
            self._index_dirty = True  # 标记索引为脏
            return {"ok": True}
        except Exception as exc:
            logger.error(f"更新规则失败: {exc}")
            return {"ok": False, "error": str(exc)}

    def delete_rule(self, rule_id: str) -> dict:
        """删除规则。"""
        conn = self.store.connect()
        try:
            conn.execute("DELETE FROM active_rules WHERE rule_id = ?", (rule_id,))
            conn.commit()
            self._index_dirty = True  # 标记索引为脏
            return {"ok": True}
        except Exception as exc:
            logger.error(f"删除规则失败: {exc}")
            return {"ok": False, "error": str(exc)}

    # ── 规则匹配 ──────────────────────────────────────────────────────

    def match_event(self, event: dict, rule_type: str | None = None) -> list[dict]:
        """匹配事件，返回触发的规则列表。

        event 结构：
        {
            "kind": "face_unknown",
            "room": "客厅",
            "person": "陌生人",
            "confidence": 0.8,
            "server_ts": "2026-09-22T12:00:00",
        }

        rule_type: 可选，只匹配指定类型的规则（static/dynamic）
        """
        # 使用倒排索引获取候选规则
        rules = self._get_candidate_rules(event.get("kind", ""))
        triggered = []
        now = time.time()
        for rule in rules:
            # 只匹配启用的规则
            if not rule.get("enabled", True):
                continue
            # 按类型过滤
            if rule_type and rule.get("rule_type", "static") != rule_type:
                continue
            if self._match_condition(rule["condition"], event):
                # 触发策略
                trigger = rule.get("trigger", {})
                trigger_type = trigger.get("type", "single")
                if trigger_type == "count":
                    # count：N 次才触发
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
                if self._check_cooldown(rule):
                    triggered.append(rule)
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
                # 获取当前时间
                now = datetime.now()
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

        # state 匹配（如 away_mode）
        if "state" in cond:
            # TODO: 检查当前状态（如离家模式）
            pass

        result = len(failures) == 0
        if trace:
            return result, {
                "op": "PRED",
                "result": result,
                "failures": failures,
                "condition": cond
            }
        return result, {}

    def _check_cooldown(self, rule: dict) -> bool:
        """检查冷却期。"""
        # TODO: 记录最后触发时间，检查是否在冷却期内
        return True

    # ── 执行动作 ──────────────────────────────────────────────────────

    def execute_action(self, rule: dict, event: dict) -> dict:
        """执行规则动作。"""
        action = rule.get("action", {})
        action_type = action.get("type", "log")
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
            else:
                logger.warning(f"未知动作类型: {action_type}")
                return {"ok": False, "error": f"未知动作类型: {action_type}"}
        except Exception as exc:
            logger.error(f"执行动作失败: {exc}")
            return {"ok": False, "error": str(exc)}

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

    def _log_trigger(self, rule_id: str, event: dict, action: dict) -> None:
        """记录规则触发历史。"""
        try:
            conn = self.store.connect()
            conn.execute(
                """
                INSERT INTO rule_trigger_history
                (rule_id, event_json, action_json, triggered_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    rule_id,
                    json.dumps(event, ensure_ascii=False),
                    json.dumps(action, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.error(f"记录规则触发历史失败: {exc}")


# 全局单例
_engine: Optional[ActiveRuleEngine] = None


def get_rule_engine(store: Any, alert_dispatcher: Any = None) -> ActiveRuleEngine:
    """获取全局规则引擎单例。"""
    global _engine
    if _engine is None:
        _engine = ActiveRuleEngine(store, alert_dispatcher)
    return _engine
