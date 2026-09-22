"""API 层：与旧版 ``InsightService`` 完全兼容的门面类。

兼容契约（调用方无需修改）：
- 方法名、参数名、参数顺序、默认值与旧版一致；
- 返回格式统一为「分页信封」：``{"<key>": [...], "total", "offset", "limit", "has_more"}``；
- 所有条目带 ``friendly_name`` / ``room``；
- 查询无数据返回空列表 + ``total=0``，不抛异常、不返回 None。
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from .activity import ActivityEngine
from .anomaly import AnomalyDetector
from .models import InsightConfig, Page, TimeRange
from .nlquery import NLQueryEngine
from .parser.entity import EntityResolver
from .parser.timeframe import resolve_range
from .persona import PersonaBuilder
from .report import ReportBuilder
from .repository import BaseRepository, build_repository
from .service import BehaviorService

LOG = logging.getLogger(__name__)

__all__ = ["InsightService"]


def _empty() -> Dict[str, Any]:
    return {"items": [], "total": 0, "offset": 0, "limit": 0, "has_more": False}


def _degrade(factory: Callable[[], Any]) -> Callable[[Callable], Callable]:
    """异常降级：记录日志并返回空结果（数据库错误不外抛）。"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except ValueError as exc:      # 参数错误：返回明确错误信息
                LOG.info("参数错误 %s: %s", func.__name__, exc)
                result = factory()
                if isinstance(result, dict):
                    result["error"] = str(exc)
                return result
            except Exception as exc:       # noqa: BLE001 - 其它错误一律降级
                LOG.exception("执行 %s 失败，返回降级结果", func.__name__)
                result = factory()
                if isinstance(result, dict):
                    result["error"] = str(exc)
                return result
        return wrapper
    return decorator


class InsightService:
    """行为洞察服务（与旧版接口完全兼容）。

    用法::

        service = InsightService(store)          # 真实数据库（Store 类）
        service = InsightService()               # 无数据库，返回空结果
        service = InsightService(repository=repo)  # 注入自定义仓储（测试）
    """

    def __init__(self, store: Any = None, config: Optional[InsightConfig] = None,
                 repository: Optional[BaseRepository] = None) -> None:
        self.config = config or InsightConfig()
        self.repo: BaseRepository = repository or build_repository(store, self.config)
        self.resolver = EntityResolver(self._safe_entities())
        self.core = BehaviorService(self.repo, self.resolver, self.config)
        self.nl = NLQueryEngine(self.core, self.resolver, self.config)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _safe_entities(self) -> List[Any]:
        try:
            return self.repo.list_entities()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("加载实体目录失败: %s", exc)
            return []

    def refresh(self) -> None:
        """数据变更后刷新目录与缓存。"""
        self.repo.invalidate()
        self.resolver.refresh(self._safe_entities())

    def _tr(self, start: Any = "", end: Any = "", days: int = 0) -> TimeRange:
        return resolve_range(start, end, days=days, default_days=self.config.default_days)

    # ------------------------------------------------------------------
    # 5.1 实体目录与语义解析
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("entities"))
    def entity_catalog(self, room: str = "", category: str = "", query: str = "",
                       only_enabled: bool = True) -> Dict[str, Any]:
        """获取实体目录（room + category + query 语义定位）。"""
        items = self.resolver.resolve(room=room, category=category, query=query,
                                      only_enabled=only_enabled) \
            if (room or category or query) else self.resolver.all(only_enabled)
        return Page.build([e.to_dict() for e in items]).to_dict("entities")

    @_degrade(list)
    def room_names(self, only_enabled: bool = True) -> List[str]:
        """房间名称列表。"""
        return self.resolver.rooms(only_enabled)

    def resolve_room_in_text(self, text: str,
                             rooms: Optional[Sequence[str]] = None) -> Optional[str]:
        """从文本中解析房间。"""
        return self.resolver.resolve_room_in_text(text, rooms)

    def domains_for(self, category: str = "", domain: str = "",
                    query: str = "") -> List[str]:
        """根据类别 / 关键词解析 domain。"""
        from .parser.entity import domains_for as _domains_for
        return _domains_for(category=category, domain=domain, query=query)

    def resolve_entities(self, room: str = "", category: str = "",
                         query: str = "") -> List[Dict[str, Any]]:
        """解析实体列表（带 friendly_name / room）。"""
        return [e.to_dict() for e in self.resolver.resolve(
            room=room, category=category, query=query)]

    def split_room_from_query(self, room: str, query: str):
        """从查询中分离房间，返回 (room, query)。"""
        return self.resolver.split_room_from_query(room, query)

    # ------------------------------------------------------------------
    # 5.2 事件搜索与查询
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("events"))
    def search_events(self, start: str = "", end: str = "", entity_id: str = "",
                      room: str = "", category: str = "", query: str = "",
                      limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """搜索事件（含遥测，原始检索）。"""
        return self._search(start, end, entity_id, room, category, query,
                            limit, offset, behavior_only=False)

    @_degrade(lambda: Page.build([]).to_dict("events"))
    def query_behavior_events(self, start: str = "", end: str = "", room: str = "",
                              category: str = "", query: str = "", limit: int = 100,
                              offset: int = 0) -> Dict[str, Any]:
        """查询行为事件（默认排除功率/温湿度等纯遥测）。"""
        return self._search(start, end, "", room, category, query,
                            limit, offset, behavior_only=True)

    def _search(self, start: Any, end: Any, entity_id: str, room: str, category: str,
                query: str, limit: int, offset: int, behavior_only: bool) -> Dict[str, Any]:
        tr = self._tr(start, end)
        ids = [entity_id] if entity_id else None
        events = self.core.load_events(tr, entity_ids=ids, room=room,
                                       category=category, query=query,
                                       behavior_only=behavior_only)
        events.sort(key=lambda e: e.ts)
        return Page.build(events, offset=offset,
                          limit=self.config.clamp_limit(limit),
                          time_range=tr.to_dict()).to_dict("events")

    @_degrade(lambda: {"event": None, "total": 0, "offset": 0, "limit": 1,
                       "has_more": False})
    def get_last_event(self, entity_id: Optional[str] = None, domain: Optional[str] = None,
                       room: Optional[str] = None, transition: str = "off",
                       days: int = 30) -> Dict[str, Any]:
        """获取最后一个事件（可按 on/off 切换过滤）。"""
        tr = self._tr(days=days or 30)
        ids = [entity_id] if entity_id else self.resolver.resolve_ids(
            room=room or "", domain=domain or "")
        if entity_id is None and domain and not ids:
            ids = None
        events = self.core.load_events(tr, entity_ids=ids, behavior_only=False)
        from .parser.entity import normalize_state
        if transition and transition != "any":
            wanted = "on" if transition in ("on", "open", "开") else "off"
            events = [e for e in events if normalize_state(e.state) == wanted]
        events.sort(key=lambda e: e.ts)
        last = events[-1] if events else None
        return {"event": last.to_dict() if last else None, "total": 1 if last else 0,
                "offset": 0, "limit": 1, "has_more": False}

    # ------------------------------------------------------------------
    # 5.3 设备使用统计
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("items"))
    def device_usage(self, start: str = "", end: str = "", room: str = "",
                     category: str = "", query: str = "",
                     group_by: str = "entity") -> Dict[str, Any]:
        """设备使用统计（时长、开关次数均在服务端算好）。"""
        tr = self._tr(start, end)
        return self.core.usage(tr, room=room, category=category, query=query,
                               group_by=group_by)

    @_degrade(lambda: Page.build([]).to_dict("sessions"))
    def climate_sessions(self, query: str = "", room: str = "", days: int = 7,
                         start: str = "", end: str = "") -> Dict[str, Any]:
        """空调会话统计。"""
        tr = self._tr(start, end, days=days or 7)
        return self.core.climate_sessions(query=query, room=room, tr=tr, days=days)

    @_degrade(lambda: Page.build([]).to_dict("items"))
    def water_purifier_usage(self, start: Any, end: Any) -> Dict[str, Any]:
        """净水器使用统计。"""
        return self.core.water_purifier_usage(start, end)

    # ------------------------------------------------------------------
    # 5.4 行为洞察
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("insights"))
    def behavior_insights(self, start: str = "", end: str = "", room: str = "",
                          category: str = "", query: str = "") -> Dict[str, Any]:
        """行为洞察（单窗口）。"""
        tr = self._tr(start, end)
        return self.core.behavior_insights(tr, room=room, category=category, query=query)

    @_degrade(lambda: Page.build([]).to_dict("insights"))
    def get_behavior_insights(self, compare_days: int = 7) -> Dict[str, Any]:
        """行为洞察（当前窗口 vs 前一窗口，带环比）。"""
        return self.core.compare_insights(compare_days=compare_days)

    @_degrade(lambda: Page.build([]).to_dict("activities"))
    def infer_activities(self, days: int = 7, rooms: str = "", start: str = "",
                         end: str = "", activities: Any = None) -> Dict[str, Any]:
        """活动推断（洗澡/学习/看电视/睡眠/烹饪 + 自定义）。"""
        tr = self._tr(start, end, days=days or 7)
        return self.core.infer_activities(tr, rooms=rooms, activities=activities)

    @_degrade(lambda: {"ok": False, "error": "invalid rule", "activity": {}})
    def define_activity(self, name: str, rule: Any, tags: Any = None,
                        room: str = None) -> Dict[str, Any]:
        """定义自定义活动规则。"""
        return self.core.activities.define_activity(
            name, rule, tags=tags, room=room or "")

    # ------------------------------------------------------------------
    # 5.5 异常检测
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("anomalies"))
    def anomaly_report(self, start: str = "", end: str = "", room: str = "",
                       category: str = "", query: str = "") -> Dict[str, Any]:
        """异常报告（设备异常 + 数据质量 + 噪声源）。"""
        tr = self._tr(start, end)
        return self.core.anomaly_report(tr, room=room, category=category, query=query)

    @_degrade(lambda: Page.build([]).to_dict("devices"))
    def device_health(self, start: str = "", end: str = "", room: str = "",
                      category: str = "", query: str = "") -> Dict[str, Any]:
        """设备健康检查。"""
        tr = self._tr(start, end)
        return self.core.device_health(tr, room=room, category=category, query=query)

    @_degrade(lambda: Page.build([]).to_dict("issues"))
    def data_quality_issues(self, start: str = "", end: str = "",
                            limit: int = 30000) -> Dict[str, Any]:
        """数据质量问题（缺失 / 单位冲突 / 噪声源）。"""
        tr = self._tr(start, end)
        return self.core.data_quality_issues(tr, limit=limit)

    # ------------------------------------------------------------------
    # 5.6 用户画像
    # ------------------------------------------------------------------
    @_degrade(lambda: {"persona": {}, "total": 0, "offset": 0, "has_more": False})
    def get_user_persona(self, days: int = 14) -> Dict[str, Any]:
        """获取用户画像。"""
        return self.core.user_persona(days=days or 14)

    @_degrade(lambda: {"insight_id": "", "found": False, "total": 0, "offset": 0,
                       "has_more": False})
    def explain_insight(self, insight_id: str) -> Dict[str, Any]:
        """解释洞察（含证据链）。"""
        result = self.core.explain_insight(insight_id)
        if not result.get("found"):
            self.get_behavior_insights()          # 未命中时重新计算填充索引
            result = self.core.explain_insight(insight_id)
        return result

    # ------------------------------------------------------------------
    # 5.7 自然语言查询
    # ------------------------------------------------------------------
    @_degrade(lambda: {"question": "", "answer": "查询失败", "total": 0, "offset": 0,
                       "has_more": False})
    def ask_memory(self, question: str, days: int = 7, route: str = "auto",
                   return_hints: bool = False) -> Dict[str, Any]:
        """自然语言查询记忆。"""
        return self.nl.ask(question, days=days or 7, route=route or "auto",
                           return_hints=return_hints)

    @_degrade(lambda: {"question": "", "intent": "unknown", "total": 0, "offset": 0,
                       "has_more": False})
    def plan_question(self, question: str, days: int = 7) -> Dict[str, Any]:
        """问题规划（意图 / 实体 / 时间范围）。"""
        plan = self.nl.plan(question, days=days or 7)
        data = plan.to_dict()
        data.update({"total": 1, "offset": 0, "has_more": False})
        return data

    # ------------------------------------------------------------------
    # 5.8 数据质量
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("days"))
    def data_coverage(self, days: int = 7, start: str = "",
                      end: str = "") -> Dict[str, Any]:
        """数据覆盖率。"""
        tr = self._tr(start, end, days=days or 7)
        return self.core.coverage(tr)

    @_degrade(lambda: Page.build([]).to_dict("issues"))
    def get_data_quality(self, days: int = 30) -> Dict[str, Any]:
        """数据质量综合评分。"""
        tr = self._tr(days=days or 30)
        return self.core.data_quality(tr)

    # ------------------------------------------------------------------
    # 报告输出（Markdown / JSON）
    # ------------------------------------------------------------------
    def insight_report(self, fmt: str = "markdown", days: int = 7) -> str:
        return self.core.reports.insights_report(self.behavior_insights(days=days), fmt)

    def anomaly_report_text(self, fmt: str = "markdown", days: int = 7) -> str:
        return self.core.reports.anomaly_report(self.anomaly_report(days=days), fmt)

    def data_quality_report(self, fmt: str = "markdown", days: int = 30) -> str:
        return self.core.reports.quality_report(self.get_data_quality(days=days), fmt)

    def persona_report(self, fmt: str = "markdown", days: int = 14) -> str:
        return self.core.reports.persona_report(self.get_user_persona(days=days), fmt)
