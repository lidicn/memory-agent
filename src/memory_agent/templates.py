"""行为洞察模板系统 - 分析历史数据，生成可执行的行为洞察"""

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime


@dataclass
class EntityQuery:
    """实体查询条件"""
    entity_id: str
    attribute: str  # state, attribute.xxx, etc.
    pattern: str    # 匹配模式：exact, range, contains, regex
    value: Any      # 期望值
    time_range: str = ""  # 时间范围：HH:MM-HH:MM, 或 relative:now-2h
    metric: str = "duration"  # 指标：duration | count | numeric_sum | state_share


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
                    "metric": e.metric
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
                time_range=e.get("time_range", "")
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
                entity_id="media_player.xiaomi_cn_481102538_rmh1",
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
// 1. 获取 media_player.xiaomi_cn_481102538_rmh1 的历史状态
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
    if eq.entity_id == water_entity or "out_data" in (eq.attribute or ""):
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
            "by_day_value": {
                d["date"]: round(d["total_volume_ml"], 1) for d in data.get("days", [])
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
    entities_out = []
    for eq in tpl.entities:
        metric = (eq.metric or "duration")
        eid = eq.entity_id
        nm = names.get(eid, {}) or {}
        friendly = nm.get("friendly_name") or ins._fallback_name(eid)
        try:
            if metric == "duration":
                from .insights import DEFAULT_DEBOUNCE_SECONDS
                if eq.attribute in ("", "state", "new_state"):
                    on_set = {str(eq.value).strip().lower()} if eq.value not in (None, "") else set()
                    res = ins._usage_one(eid, start_iso, end_iso, on_set, DEFAULT_DEBOUNCE_SECONDS, include_timeline)
                else:
                    res = ins._usage_by_attr(eid, eq.attribute, eq.value, eq.pattern, start_iso, end_iso, include_timeline=include_timeline)
            elif metric == "count":
                res = ins._count_by_filter(eid, eq.attribute, eq.value, eq.pattern, start_iso, end_iso)
            elif metric == "numeric_sum":
                res = _numeric_sum(rt, ins, eq, start_iso, end_iso)
            else:
                res = {"error": f"不支持的 metric: {metric}"}
        except Exception as exc:  # 单实体失败不拖垮整体
            res = {"error": f"计算失败: {exc}"}
        entities_out.append({
            "entity_id": eid,
            "friendly_name": friendly,
            "metric": metric,
            "result": res,
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
