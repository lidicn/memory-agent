"""行为洞察模板系统 - 分析历史数据，生成可执行的行为洞察"""

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field, replace
from typing import Any
from datetime import datetime

from .identity import looks_like_entity_id


@dataclass
class EntityQuery:
    """实体查询条件"""
    entity_id: str
    attribute: str  # state, attribute.xxx, etc.
    pattern: str    # 匹配模式：exact, range, contains, regex
    value: Any      # 期望值
    time_range: str = ""  # 时间范围：HH:MM-HH:MM, 或 relative:now-2h
    metric: str = "duration"  # 指标：duration | count | numeric_sum | state_share
    logical_id: str = ""  # 逻辑设备引用（v0.2 身份层）：优先于 entity_id 解析，
    #                      解析失败时回落 entity_id，兼容存量模板


@dataclass
class BehaviorInsight:
    """行为洞察 - 描述从历史数据中发现的用户行为模式"""
    id: str
    name: str
    description: str
    category: str  # sleep, media, lighting, climate, etc.
    entities: list[EntityQuery] = field(default_factory=list)
    pattern: str = ""  # 行为模式描述
    confidence: float = 0.0  # 置信度 0-1
    sample_days: int = 0  # 样本天数
    nr_condition: str = ""  # NR 中实现此条件的伪代码/逻辑描述
    nr_action: str = ""  # NR 中执行的动作描述
    default_days: int = 7  # 模板默认时间窗天数（run_template 兜底）
    interpretation: str = ""  # 给 Agent 的解读话术模板，{window}/{total_human}/{count}/{total_l} 可占位
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "attribute": e.attribute,
                    "pattern": e.pattern,
                    "value": e.value,
                    "time_range": e.time_range,
                    "metric": e.metric,
                    "logical_id": getattr(e, "logical_id", "") or ""
                }
                for e in self.entities
            ],
            "pattern": self.pattern,
            "confidence": self.confidence,
            "sample_days": self.sample_days,
            "nr_condition": self.nr_condition,
            "nr_action": self.nr_action,
            "default_days": self.default_days,
            "interpretation": self.interpretation,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BehaviorInsight":
        entities = [
            EntityQuery(
                entity_id=e["entity_id"],
                attribute=e["attribute"],
                pattern=e["pattern"],
                value=e["value"],
                time_range=e.get("time_range", ""),
                metric=e.get("metric", "duration"),
                logical_id=e.get("logical_id", "") or ""
            )
            for e in data.get("entities", [])
        ]
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            category=data["category"],
            entities=entities,
            pattern=data.get("pattern", ""),
            confidence=data.get("confidence", 0.0),
            sample_days=data.get("sample_days", 0),
            nr_condition=data.get("nr_condition", ""),
            nr_action=data.get("nr_action", ""),
            default_days=int(data.get("default_days", 0) or 0),
            interpretation=data.get("interpretation", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", "")
        )


# 内置行为洞察模板
BUILTIN_INSIGHTS: list[BehaviorInsight] = [
    BehaviorInsight(
        id="xbox_daily_usage",
        name="Xbox 每日游戏时长",
        description="统计用户每天使用 Xbox 游戏的时长，通过 HDMI 3 输入源判断",
        category="media",
        entities=[
            EntityQuery(
                entity_id="media_player.xiaomi_rmh1_6103_play_control",  # 兜底：身份层解析失败时用裸实体
                logical_id="lidicn的电视电视",  # 走身份层(v0.2)：指向合并后的电视逻辑设备(含主实体+play_control 两个候选)，HA 重登/换集成致 entity_id 漂移时自愈
                attribute="source",
                pattern="equals",
                value="HDMI 3",
                time_range="00:00-23:59",
                metric="duration"
            )
        ],
        pattern="当 media_player 的 source 属性为 'HDMI 3' 时，表示用户在使用 Xbox 游戏",
        confidence=0.95,
        sample_days=14,
        nr_condition="""
// 查询条件：统计今日 HDMI 3 使用时长
// 1. 获取 media_player.xiaomi_rmh1_6103_play_control 的历史状态
// 2. 筛选 source = "HDMI 3" 的时段
// 3. 计算总时长（playing + idle 状态都算）
""".strip(),
        nr_action="""
// 执行动作：
// 方案 A：记录到日志/统计
// 方案 B：如果超过阈值（如 2 小时），发送提醒
// 方案 C：联动灯光/窗帘营造游戏氛围
""".strip(),
        default_days=2,
        interpretation="在 {window} 内，Xbox（电视 HDMI 3 输入源）累计游戏约 {total_human}。",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    ),
    BehaviorInsight(
        id="water_purifier_daily",
        name="净水器每日饮水统计",
        description="统计每日饮水量、TDS 变化、滤芯衰减情况",
        category="appliance",
        entities=[
            EntityQuery(
                entity_id="event.chunmi_cn_334432105_600f2_water_out_finish_e_7_1",
                attribute="attributes.out_data",
                pattern="contains",
                value="start_ts-end_ts-volume_mL-tds_in,tds_out",
                time_range="00:00-23:59",
                metric="numeric_sum"
            )
        ],
        pattern="out_data 格式：start_ts-end_ts-volume_mL-tds_in,tds_out",
        confidence=0.98,
        sample_days=7,
        nr_condition="""
// 查询条件：获取今日净水器出水记录
// 1. 获取 event.xxx_water_out_finish 的历史
// 2. 解析 out_data 属性，提取 volume_mL 和 tds 值
// 3. 累加计算总饮水量
""".strip(),
        nr_action="""
// 执行动作：
// 方案 A：记录每日饮水量统计
// 方案 B：如果饮水量不足（< 1L），发送提醒
// 方案 C：TDS 超过阈值时提醒更换滤芯
""".strip(),
        default_days=7,
        interpretation="在 {window} 内，净水器共出水 {total_l} 升、{count} 次。",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    ),
    BehaviorInsight(
        id="sleep_time_pattern",
        name="就寝时间模式",
        description="通过卧室人体传感器和灯光状态判断用户就寝时间",
        category="sleep",
        entities=[
            EntityQuery(
                entity_id="binary_sensor.bedroom_motion",
                attribute="state",
                pattern="equals",
                value="off",
                time_range="22:00-02:00",
                metric="duration"
            ),
            EntityQuery(
                entity_id="light.bedroom",
                attribute="state",
                pattern="equals",
                value="off",
                time_range="22:00-02:00",
                metric="duration"
            )
        ],
        pattern="当卧室人体传感器持续 10 分钟无检测 + 灯光关闭 = 用户已就寝",
        confidence=0.85,
        sample_days=14,
        nr_condition="""
// 查询条件：判断用户是否已就寝
// 1. 获取 binary_sensor.bedroom_motion 最近 10 分钟状态
// 2. 获取 light.bedroom 当前状态
// 3. 条件：motion=off 持续 10 分钟 AND light=off
""".strip(),
        nr_action="""
// 执行动作：
// 方案 A：记录就寝时间，统计睡眠时长
// 方案 B：触发全屋关灯 + 安防布防
// 方案 C：关闭窗帘，调整空调睡眠模式
""".strip(),
        default_days=14,
        interpretation="在 {window} 内，卧室人体传感器持续无人的时长约 {total_human}（近似就寝时段，v1 取首实体）。",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
]


class TemplateManager:
    """管理行为洞察模板"""

    BUILTIN_IDS = frozenset(bt.id for bt in BUILTIN_INSIGHTS)

    def __init__(self, data_dir: str = "/data"):
        self.data_dir = data_dir
        self.templates_file = os.path.join(data_dir, "templates.json")
        self._lock = threading.RLock()
        self._ensure_data_dir()
        self.templates: dict[str, BehaviorInsight] = {}
        self._load_templates()

    def _ensure_data_dir(self):
        os.makedirs(self.data_dir, exist_ok=True)

    def _load_templates(self):
        # 加载内置模板
        for t in BUILTIN_INSIGHTS:
            self.templates[t.id] = t

        # 加载自定义模板
        if os.path.exists(self.templates_file):
            try:
                with open(self.templates_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        t = BehaviorInsight.from_dict(item)
                        self.templates[t.id] = t
            except Exception as e:
                print(f"加载模板文件失败: {e}")

    def _save_custom_templates(self):
        """只保存非内置模板；原子写，避免并发保存写坏文件。"""
        custom = [
            t.to_dict() for t in self.templates.values()
            if t.id not in self.BUILTIN_IDS
        ]
        os.makedirs(self.data_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".templates-", suffix=".tmp", dir=self.data_dir
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(custom, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.templates_file)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    def list_all(self) -> list[BehaviorInsight]:
        """稳定排序：先按更新时间倒序，再按 id，保证前端列表不会随机跳动。"""
        with self._lock:
            items = list(self.templates.values())
        return sorted(items, key=lambda t: (t.updated_at or "", t.id), reverse=True)

    def get(self, template_id: str) -> BehaviorInsight | None:
        return self.templates.get(template_id)

    def is_builtin(self, template_id: str) -> bool:
        return template_id in self.BUILTIN_IDS

    def save(self, template: BehaviorInsight) -> BehaviorInsight:
        with self._lock:
            if not template.created_at:
                template.created_at = datetime.now().isoformat()
            template.updated_at = datetime.now().isoformat()
            self.templates[template.id] = template
            self._save_custom_templates()
        return template

    def delete(self, template_id: str) -> bool:
        with self._lock:
            if template_id not in self.templates:
                return False
            # 不允许删除内置模板
            if template_id in self.BUILTIN_IDS:
                return False
            del self.templates[template_id]
            self._save_custom_templates()
            return True

    def export_insight(self, template_id: str) -> dict | None:
        """导出行为洞察 - 包含查询条件和 NR 执行逻辑"""
        template = self.get(template_id)
        if not template:
            return None

        return {
            "insight": {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "category": template.category,
                "pattern": template.pattern,
                "confidence": template.confidence,
                "sample_days": template.sample_days
            },
            "queries": [
                {
                    "entity_id": e.entity_id,
                    "attribute": e.attribute,
                    "pattern": e.pattern,
                    "value": e.value,
                    "time_range": e.time_range
                }
                for e in template.entities
            ],
            "implementation": {
                "nr_condition": template.nr_condition,
                "nr_action": template.nr_action
            },
            "metadata": {
                "created_at": template.created_at,
                "updated_at": template.updated_at,
                "exported_at": datetime.now().isoformat()
            }
        }

    def export_all_insights(self) -> list[dict]:
        """导出所有行为洞察"""
        return [self.export_insight(t.id) for t in self.templates.values()]


# ── 模板执行引擎 ────────────────────────────────────────────────────────────


def _safe_dict(d: dict):
    """缺失键返回空串的字典，供 interpretation 模板安全 format_map。"""
    from collections import defaultdict

    class _SD(defaultdict):
        def __missing__(self, key):
            return ""

    return _SD(None, d)


def _numeric_sum(rt, ins, eq, start_iso: str, end_iso: str) -> dict:
    """metric=numeric_sum：优先走净水器 HA 链路，其余按属性数值求和。"""
    from .insights import _parse_attrs, _as_float

    water_entity = getattr(ins, "_WATER_PURIFIER_ENTITY", "")
    # v0.7：属性名在 HA 里实为中文「出水数据」，两种写法都转发到净水器专用统计
    if (
        eq.entity_id == water_entity
        or "out_data" in (eq.attribute or "")
        or "出水数据" in (eq.attribute or "")
    ):
        data = ins.water_purifier_usage(start=start_iso, end=end_iso)
        if not data.get("ok"):
            return {"error": data.get("error", "净水器统计失败")}
        total_ml = data.get("total_volume_ml", 0)
        return {
            "entity_id": eq.entity_id,
            "unit": "mL",
            "total_value": total_ml,
            "total_liters": round(total_ml / 1000, 2),
            "count": data.get("total_count", 0),
            # 修复：water_purifier_usage 返回的 days 项键是 total_volume_l（升），
            # 原代码误用 total_volume_ml —— 此前因记录被全部跳过（days 恒为空）而未
            # 暴露，一旦有数据就抛 KeyError。此处转回 mL，与 total_value 单位一致。
            "by_day_value": {
                d["date"]: round((d.get("total_volume_l") or 0) * 1000, 1)
                for d in data.get("days", [])
            },
            "days": data.get("days", []),
        }
    attr_key = eq.attribute.split(".", 1)[1] if eq.attribute.startswith("attributes.") else eq.attribute
    rows = ins.store.query_events(start_iso, end_iso, entities=[eq.entity_id], limit=5000, order="asc")
    total = 0.0
    by_day: dict = {}
    for r in rows:
        attrs = _parse_attrs(r.get("attrs_json"))
        v = _as_float(attrs.get(attr_key))
        if v is None:
            continue
        total += v
        ts = ins._parse(r.get("ts", ""))
        if ts:
            day = ts.strftime("%Y-%m-%d")
            by_day[day] = round(by_day.get(day, 0.0) + v, 2)
    return {"entity_id": eq.entity_id, "unit": "", "total_value": round(total, 2), "by_day_value": by_day}


def _resolve_entities(identity, eq) -> list[str]:
    """把 EntityQuery 解析为按优先级排序的 entity_id 列表（主优先，其余为故障转移候选）。

    解析顺序：``logical_id`` → ``entity_id``（若它不像裸实体 ID 也当逻辑名试）→ 原值兜底。
    身份层未装配或解析失败时一律回落到 ``entity_id``，保证存量模板行为不变（灰度过渡）。
    """
    ref = (getattr(eq, "logical_id", "") or "").strip()
    fallback = (eq.entity_id or "").strip()
    if identity is not None:
        if ref:
            eids, _reason = identity.resolve(ref)
            if eids:
                return eids
        if fallback and not looks_like_entity_id(fallback):
            eids, _reason = identity.resolve(fallback)
            if eids:
                return eids
    return [fallback] if fallback else []


def _compute_entity(rt, ins, eq, metric: str, eid: str,
                    start_iso: str, end_iso: str, include_timeline: bool) -> dict:
    """按 metric 分发到既有算力；eid 为本次实际使用的实体（可能由身份层解析得出）。"""
    if metric == "duration":
        from .insights import DEFAULT_DEBOUNCE_SECONDS
        if eq.attribute in ("", "state", "new_state"):
            on_set = {str(eq.value).strip().lower()} if eq.value not in (None, "") else set()
            return ins._usage_one(eid, start_iso, end_iso, on_set, DEFAULT_DEBOUNCE_SECONDS, include_timeline, time_range=eq.time_range)
        return ins._usage_by_attr(eid, eq.attribute, eq.value, eq.pattern, start_iso, end_iso, include_timeline=include_timeline, time_range=eq.time_range)
    if metric == "count":
        return ins._count_by_filter(eid, eq.attribute, eq.value, eq.pattern, start_iso, end_iso)
    if metric == "numeric_sum":
        # _numeric_sum 内部按 eq.entity_id 取数；解析结果不同时用副本覆盖实体，
        # 保持 5 参调用形态以兼容既有 monkeypatch。
        eq_for = replace(eq, entity_id=eid) if eid != eq.entity_id else eq
        return _numeric_sum(rt, ins, eq_for, start_iso, end_iso)
    return {"error": f"不支持的 metric: {metric}"}


def _merge_attr_usage(a: dict, b: dict, end_iso: str) -> dict:
    """跨候选实体合并「属性值匹配」类时长结果：对两份 timeline 区间取并集。

    用于修复 Bug#5：当逻辑设备被身份层拆成多个候选实体（如客厅电视的 main
    实体 + 播放控制实体），而属性（source='HDMI 3'）的不同时段可能分别由不同
    候选实体上报，单取 primary 会漏算迁移到另一候选的时段。并集只在时间维度
    去重、不会重复计数；state 类时长走单实体分支、不进这里。
    """
    from .insights import fmt_duration
    try:
        wend = datetime.fromisoformat(end_iso)
    except Exception:
        wend = None

    def _ivs(r):
        out = []
        for seg in (r or {}).get("timeline", []) or []:
            try:
                s = datetime.fromisoformat(seg["start"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(seg["end"].replace("Z", "+00:00"))
            except Exception:
                continue
            if e > s:
                out.append((s, e))
        return out

    ivs = _ivs(a) + _ivs(b)
    ivs.sort(key=lambda x: x[0])
    merged = []
    for s, e in ivs:
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    durations = [(e - s).total_seconds() for s, e in merged]
    total = sum(durations)
    by_day: dict[str, float] = {}
    for s, e in merged:
        day = s.strftime("%Y-%m-%d")
        by_day[day] = round(by_day.get(day, 0.0) + (e - s).total_seconds(), 1)

    timeline = []
    for s, e in merged:
        dur = (e - s).total_seconds()
        timeline.append({
            "start": s.isoformat(sep="T"),
            "end": e.isoformat(sep="T"),
            "duration_seconds": round(dur, 1),
            "duration_human": fmt_duration(dur),
            "still_on": (wend is not None and e >= wend),
        })

    base = a if not a.get("error") else b
    out = dict(base)
    out["match_count"] = (a.get("match_count", 0) or 0) + (b.get("match_count", 0) or 0)
    out["sessions"] = len(durations)
    out["total_on_seconds"] = round(total, 1)
    out["total_on_human"] = fmt_duration(total)
    out["avg_session_seconds"] = round(total / len(durations), 1) if durations else 0
    out["avg_session_human"] = fmt_duration(total / len(durations)) if durations else "0秒"
    out["longest_session_human"] = fmt_duration(max(durations)) if durations else "0秒"
    out["by_day_seconds"] = by_day
    out["timeline"] = timeline[:200]
    return out


def _compute_for_entities(rt, ins, eq, metric, eids, start_iso, end_iso, include_timeline):
    """在解析出的多候选实体上计算一个指标，返回 (res, used)。

    - 属性值匹配类时长（metric=duration 且 attribute 非 state）：跨候选做时间区间
      并集（修复 Bug#5，避免 source 上报迁移到另一候选实体时漏算）。
    - 其余（state 时长 / count / numeric_sum）：主实体优先、命中即 break 单实体计算，
      避免双集成重复计数。
    """
    is_attr_match = metric == "duration" and eq.attribute not in ("", "state", "new_state")
    if is_attr_match:
        merged = None
        used = None
        last_err = None
        for eid in eids:
            try:
                cur = _compute_entity(rt, ins, eq, metric, eid, start_iso, end_iso, include_timeline)
            except Exception as exc:
                cur = {"error": f"计算失败: {exc}"}
            if cur.get("error"):
                last_err = cur
                if merged is None:
                    used = eid
                continue
            if merged is None:
                merged, used = cur, eid
            else:
                merged = _merge_attr_usage(merged, cur, end_iso)
        if merged is not None:
            merged["merged_from"] = eids
            return merged, (used or eids[0])
        return (last_err or {"error": "无可用候选实体"}), eids[0]

    res: dict = {}
    used = eids[0]
    for eid in eids:
        try:
            cur = _compute_entity(rt, ins, eq, metric, eid, start_iso, end_iso, include_timeline)
        except Exception as exc:  # 单实体失败不拖垮整体
            cur = {"error": f"计算失败: {exc}"}
        if not cur.get("error"):
            res, used = cur, eid
            break
        if not res:
            res, used = cur, eid
    return res, used


def run_template(rt, template_id: str, days: int = 0, start: str = "",
                 end: str = "", include_timeline: bool = True) -> dict:
    """按行为洞察模板在服务端算出结构化结果。

    根据模板声明的实体条件（entity/attribute/pattern/value）与指标（metric），
    复用既有算力（device_usage 的 segment 算法 / store 事件聚合）返回每实体累计值、
    分日明细、时间轴与解读话术，Agent 只需转述结论。
    """
    tpl = rt.templates.get(template_id)
    if not tpl:
        return {"ok": False, "error": f"模板不存在: {template_id}"}
    ins = rt.insights
    start_iso, end_iso, meta = ins.resolve_range(days or tpl.default_days or 7, start, end)
    window_label = meta.get("label") or f"{meta.get('start', '')} ~ {meta.get('end', '')}"
    names = ins.name_map()
    # 身份层可选：未装配（如单测桩）时完全走旧的 entity_id 路径，行为不变
    identity = getattr(rt, "identity", None)
    entities_out = []
    for eq in tpl.entities:
        metric = (eq.metric or "duration")
        eids = _resolve_entities(identity, eq)
        if not eids:
            # 逻辑引用解析不到 / 全部候选失效：显式报错，绝不静默返回空结果（A3）
            label = eq.entity_id or (getattr(eq, "logical_id", "") or "")
            nm = names.get(label, {}) or {}
            entities_out.append({
                "entity_id": label,
                "friendly_name": nm.get("friendly_name") or ins._fallback_name(label),
                "metric": metric,
                "result": {"error": "设备已失效或待重匹配，请检查实体身份层", "stale": True},
                "resolved": [],
                "stale": True,
            })
            continue
        # 属性值匹配类时长（metric=duration 且 attribute 非 state）跨候选做时间区间
        # 并集（修复 Bug#5，避免 source=HDMI 3 上报从 primary 迁移到另一候选实体时漏算）；
        # 其余（state 时长 / count / numeric_sum）仍主实体优先、命中即 break 单实体计算。
        res, used = _compute_for_entities(rt, ins, eq, metric, eids, start_iso, end_iso, include_timeline)
        nm = names.get(used, {}) or {}
        # v0.7：显式区分「实体有效但窗口内无数据」与「真的算出 0」，
        # 避免调用方把 Nodata 误判为「最近确实没有活动」（错误模型修正）。
        try:
            last_ts = (rt.store.entity_last_seen([used]) or {}).get(used, "") or ""
        except Exception:
            last_ts = ""
        entities_out.append({
            "entity_id": used,
            "friendly_name": nm.get("friendly_name") or ins._fallback_name(used),
            "metric": metric,
            "result": res,
            "resolved": eids,
            "stale": False,
            "has_records": bool(last_ts),
            "last_data_ts": last_ts,
            "no_data": not bool(last_ts),
        })

    summary = _build_summary(tpl, entities_out, window_label)
    return {
        "ok": True,
        "template": {
            "id": tpl.id,
            "name": tpl.name,
            "category": tpl.category,
            "interpretation": tpl.interpretation,
            "default_days": tpl.default_days,
        },
        "window": meta,
        "entities": entities_out,
        "summary_text": summary,
    }


def run_query(rt, entity_id: str = "", logical_id: str = "", attribute: str = "state",
              pattern: str = "equals", value: str = "", metric: str = "duration",
              days: int = 7, start: str = "", end: str = "",
              include_timeline: bool = True) -> dict:
    """即时查询：不依赖已保存模板，直接按「逻辑设备名 / entity_id」算一个指标。

    与 ``run_template`` 共用解析（``_resolve_entities``）与算力（``_compute_entity``），
    因此同样享受身份层的漂移自愈；解析失败时同样**显式报错**而非静默返回空。

    供 HTTP ``/api/insights/query`` 与 MCP 语义工具 ``query_device_usage`` 共用，
    避免两条对外通道各写一套实现而漂移。
    """
    if not (entity_id or logical_id):
        return {"ok": False, "error": "缺少 entity_id 或 logical_id"}

    eq = EntityQuery(
        entity_id=entity_id or "",
        attribute=attribute or "state",
        pattern=pattern or "equals",
        value=value or "",
        metric=metric or "duration",
        logical_id=logical_id or "",
    )
    ins = rt.insights
    start_iso, end_iso, meta = ins.resolve_range(days or 7, start, end)
    label = logical_id or entity_id

    eids = _resolve_entities(getattr(rt, "identity", None), eq)
    if not eids:
        return {
            "ok": True,
            "window": meta,
            "entities": [{
                "entity_id": entity_id or logical_id,
                "friendly_name": label,
                "metric": eq.metric,
                "result": {"error": "设备已失效或待重匹配，请检查实体身份层", "stale": True},
                "resolved": [],
                "stale": True,
            }],
            "summary_text": f"{label}：设备已失效或待重匹配",
        }

    res, used = _compute_for_entities(rt, ins, eq, eq.metric, eids, start_iso, end_iso, include_timeline)

    nm = ins.name_map().get(used, {}) or {}
    return {
        "ok": True,
        "window": meta,
        "entities": [{
            "entity_id": used,
            "friendly_name": nm.get("friendly_name") or ins._fallback_name(used),
            "metric": eq.metric,
            "result": res,
            "resolved": eids,
            "stale": False,
        }],
        "summary_text": f"{label}（{meta.get('label') or ''}）",
    }


def _build_summary(tpl, entities_out, window_label: str) -> str:
    parts = []
    ctx: dict = {"window": window_label, "name": tpl.name}
    for e in entities_out:
        res = e.get("result", {}) or {}
        if res.get("error"):
            parts.append(f"{e['friendly_name']}：{res['error']}")
            continue
        metric = e["metric"]
        if metric == "duration":
            parts.append(
                f"{e['friendly_name']} 累计 {res.get('total_on_human', '0秒')}"
                f"（{res.get('sessions', 0)} 次会话，日均 {res.get('daily_average_human', '0秒')}）"
            )
            ctx.setdefault("total_human", res.get("total_on_human", ""))
        elif metric == "count":
            parts.append(f"{e['friendly_name']} 命中 {res.get('match_count', 0)} 次")
            ctx.setdefault("count", res.get("match_count", 0))
        elif metric == "numeric_sum":
            if res.get("unit") == "mL":
                liters = res.get("total_liters", round(res.get("total_value", 0) / 1000, 2))
                cnt = res.get("count", 0)
                parts.append(f"{e['friendly_name']} 共 {liters} 升（{cnt} 次）")
                ctx.setdefault("total_l", liters)
                ctx.setdefault("count", cnt)
            else:
                parts.append(f"{e['friendly_name']} 累计数值 {res.get('total_value', 0)}")
                ctx.setdefault("total_value", res.get("total_value", 0))
    body = "；".join(parts)
    interp = (tpl.interpretation or "").strip()
    if interp:
        ctx["body"] = body
        try:
            return interp.format_map(_safe_dict(ctx))
        except Exception:
            return f"{tpl.name}（{window_label}）：{body}"
    return f"{tpl.name}（{window_label}）：{body}"
