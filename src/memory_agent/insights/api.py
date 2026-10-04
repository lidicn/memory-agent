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

from .models import InsightConfig, Page, TimeRange
from .nlquery import NLQueryEngine
from .parser.entity import EntityResolver
from .parser.timeframe import resolve_range
from .repository import BaseRepository, build_repository
from .service import BehaviorService

LOG = logging.getLogger(__name__)

__all__ = ["InsightService", "LEGACY_CONTRACT_MEMBERS", "LEGACY_OUTWARD_METHODS"]


#: 生产代码仍在调用、但本门面未自行实现的成员（审计 P0-5，实测 11 个）。
#: 每一项由 ``InsightService`` 显式转发到 ``insights_legacy.InsightService``，
#: ``tests/test_insights_facade_contract.py`` 用「生产侧属性访问扫描」锁定这张表。
#: 新增成员必须同时补转发方法，否则契约测试红——不允许再靠 ``_degrade``/``hasattr``
#: 把缺失静默降级成空结果。
LEGACY_CONTRACT_MEMBERS = (
    "store",
    "resolve_range",
    "name_map",
    "decorate",
    "_parse",
    "_fallback_name",
    "_usage_one",
    "_usage_by_attr",
    "_count_by_filter",
    "_iter_all_events",
    "_tags_of",
)

#: 门面「签名按调用点保留、计算交回 legacy」的对外工具（审计 §十六，DCD 20261004 MA-裁5 Q1=A）。
#: 与上表的差别：上表是门面根本没有的成员（纯转发）；这里是门面**有**新实现，
#: 但 Phase 4 只换了引擎没重接调用点——MCP handler / HTTP 路由仍按 legacy 的参数形状
#: 传，且 category/query/domain/state/order/summarize/stale_days 等入参在新实现里
#: 无处可去。生产实测这 5 条工具线要么外抛、要么把过滤位静默丢掉。
#: `tests/test_vma_insights_callsite_binding.py` 逐个锁签名与调用点绑定。
#:
#: 后两项是裁定落地时按同一条判据补的（不是新裁量，是同一件事的两条漏网工具）：
#: - `query_behavior_events`：ToolSpec 声明 `member`/`days`，门面签名两个都不收，而
#:   `dispatch` 会把默认值非 None 的可选参一律补上 ⇒ **每次调用必抛 TypeError**，
#:   被 `_degrade` 收成空页；更要紧的是它查的是 `events`（HA 状态变化）而不是
#:   `behavior_events`（VLM 识别记录），工具说明写的「谁在哪个房间」在新引擎里无从实现。
#: - `get_last_event`：门面体调 `self.core.load_events`，而 `BehaviorService` 根本没有
#:   这个方法（实测 `hasattr(core, 'load_events') == False`）⇒ 永久降级成
#:   `{"event": None}`；legacy 有完整实现，MCP handler 的五个位置参本来就按 legacy 顺序传。
#:
#: 同一判据（门面体指向 `BehaviorService` 上不存在的成员）扫出的后四条：
#: `climate_sessions` / `explain_insight` 是对外 MCP 工具，`water_purifier_usage` 是
#: `templates.py:445` 净水器日报的消费点，三条在 legacy 都有完整实现；
#: `data_quality_issues` 无 legacy 同名可转（签名要传 store），改读新引擎自己的
#: `data_quality(tr)["issues"]`。静态扫描器见 `scripts/scan_insights_engine_attrs.py`。
LEGACY_OUTWARD_METHODS = (
    "entity_catalog",
    "search_events",
    "device_usage",
    "behavior_insights",
    "device_health",
    "define_activity",
    "query_behavior_events",
    "get_last_event",
    "climate_sessions",
    "explain_insight",
    "water_purifier_usage",
)


def _empty() -> Dict[str, Any]:
    return {"items": [], "total": 0, "offset": 0, "limit": 0, "has_more": False}


def annotate_scan(payload: Dict[str, Any], scanned: int, scan_limit: int) -> Dict[str, Any]:
    """把「扫描上限有没有命中」写进返回体（DCD 20261004 MA-裁5 **Q4=A**）。

    `total` 来自 `repo.load_events`，而它的 `LIMIT` 就是 `scan_limit`：命中上限时
    `total` 是**扫描到的行数**而不是匹配总数——生产实测 30 天窗口 legacy 报 87,404 条，
    门面报 30,000 条，用户读到的是「30 天只有 3 万条」。裁定选择**如实上报**而不是提高上限。

    边界要说清楚：命中判据是 `scanned >= scan_limit`，**恰好等于上限**的那一类无法与
    「真的只有这么多个体」区分开，所以这里给的是 `total_exact: false`（不保证是全量），
    而不是谎称「一定被截了」。`scan_limit` 为 0 表示注入的仓储没有上限。
    """
    truncated = bool(scan_limit) and int(scanned) >= int(scan_limit)
    payload["truncated"] = truncated
    payload["scan_limit"] = int(scan_limit or 0)
    payload["total_exact"] = not truncated
    return payload


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
        # 审计 P0-5：Phase 4 把门面切到本包后，legacy 上仍被生产代码调用的成员
        # 凭空消失（templates / activity_inference / mcp_server / llm_routes 共 11 个），
        # 且被 ``_degrade``、``hasattr`` + 空 ``set()`` 这类降级静默吞掉，
        # 表现为"行为推断/模板分析/实体名兜底/缓存"四条功能线无声返回空。
        # 修复策略是**组合 + 显式转发**（不整体换回 legacy 门面：那会改掉
        # 本类 ~17 个共享公开方法的返回形状，而生产真正依赖的只有下面这张表）。
        self.store: Any = store
        self.raw_config = config  # legacy 读的是原始 app Config（rooms / tz_offset_hours / …）
        #: 实体目录加载状态。`_safe_entities()` 失败时仍返回空表（契约要求查询不外抛），
        #: 但状态会被记在这里，由 `/api/health` 的 `insights` 段如实暴露。
        self.entities_loaded: bool = False
        self.entities_error: str = ""
        self.config = self._normalize_config(config)
        self._injected_repo = repository is not None
        self.repo: BaseRepository = repository or build_repository(store, self.config)
        self.resolver = EntityResolver(self._safe_entities())
        self.core = BehaviorService(self.repo, self.resolver, self.config)
        self.nl = NLQueryEngine(self.core, self.resolver, self.config)
        # 延迟导入：insights_legacy 体积大且反向依赖本包 utils，仅在实例化时取。
        from ..insights_legacy import InsightService as LegacyInsightService
        self.legacy = LegacyInsightService(self.raw_config, store)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_config(config: Any) -> InsightConfig:
        """把任意来源的 Config 归一化成 InsightConfig。

        兼容生产 Config：非 InsightConfig 时用默认值包装，
        避免 'Config' object has no attribute 'cache_ttl'/'default_days'。
        """
        if config is None or isinstance(config, InsightConfig):
            return config or InsightConfig()
        target = InsightConfig()
        for attr in ("tz_offset_hours", "default_days", "default_limit"):
            val = getattr(config, attr, None)
            if val is not None:
                setattr(target, attr, val)
        return target

    def _days_to_range(self, days: int, start: str, end: str):
        """days 兼容：days>0 且 start/end 为空时计算时间范围。"""
        if days and not start and not end:
            from datetime import timedelta
            from .models import house_now
            _end = house_now()
            _start = _end - timedelta(days=days)
            start = _start.isoformat(timespec="seconds")
            end = _end.isoformat(timespec="seconds")
        return start, end

    def _safe_entities(self) -> List[Any]:
        try:
            entities = self.repo.list_entities()
            self.entities_loaded = True
            self.entities_error = ""
            return entities
        except Exception as exc:  # noqa: BLE001
            # P0-1 教训：之前只 LOG.warning 一行消息，参数顺序错位这种致命 bug
            # 被静默降级成"实体目录为空"，用户完全无感。改 LOG.exception 留完整 traceback。
            LOG.exception("加载实体目录失败，InsightService 将以空实体表运行")
            # 契约要求查询不外抛，所以这里仍返回空表；但失败必须**可见**——
            # 状态记在 self.entities_* 上，由 runtime.health() 的 insights 段暴露。
            self.entities_loaded = False
            self.entities_error = f"{type(exc).__name__}: {exc}"
            return []

    def reload_config(self, config: Any) -> None:
        """配置热更新：归一化后**重建**依赖该配置的下游对象。

        审计报告 20261002 · 新发现 1：runtime 原先只做
        `self.insights.config = self.config`，而 repo / core / nl / legacy 早在构造时
        就把旧 config 抓在自己手里（`cache_ttl`、`default_days` 等），热更新后洞察
        仍按旧参数查询，且面板显示的是新值——状态与行为不一致。
        """
        self.raw_config = config
        self.config = self._normalize_config(config)
        # 注入了自定义仓储的门面（测试用）不重建：那个 repo 不由本对象拥有。
        if not self._injected_repo:
            self.repo = build_repository(self.store, self.config)
        self.resolver.refresh(self._safe_entities())
        self.core = BehaviorService(self.repo, self.resolver, self.config)
        self.nl = NLQueryEngine(self.core, self.resolver, self.config)
        # legacy 在构造时把 insight_cache_ttl 读进 _CACHE_TTL，只换 .config 不生效；
        # 重建一次，缓存 TTL 与热更新一致（代价：丢弃进程内结果缓存）。
        from ..insights_legacy import InsightService as LegacyInsightService
        self.legacy = LegacyInsightService(config, self.store)

    def status(self) -> Dict[str, Any]:
        """洞察链路自检（纯内存读数，不查库）。"""
        return {
            "entities_loaded": self.entities_loaded,
            "entities_error": self.entities_error,
            "cache_ttl": getattr(self.config, "cache_ttl", None),
            "default_days": getattr(self.config, "default_days", None),
        }

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
    def entity_catalog(self, room: str = "", category: str = "", domain: str = "",
                       query: str = "", only_enabled: bool = True,
                       days: int = 7) -> Dict[str, Any]:
        """设备目录（room + category + domain + query 语义定位）。

        签名按调用点的既有形状收（mcp_server.py:1116/3044 与 ToolSpec 登记的都是这个顺序）。
        Phase 4 换的新实现只认 room/category/query，且生产实测 `room` 过滤位被丢掉
        （传 room=客厅 拿到全屋 501 条，见审计报告 §十六），所以计算交回 legacy；
        legacy 的载荷按 `rooms` 分组，这里摊平成 `entities` 一并给出，两代键都在，
        消费方不必做大爆炸切换。
        """
        out = self.legacy.entity_catalog(room=room, category=category, domain=domain,
                                         query=query, only_enabled=only_enabled, days=days)
        if isinstance(out, dict) and "entities" not in out:
            rooms = out.get("rooms")
            flat: list = []
            if isinstance(rooms, dict):
                for room_name, payload in rooms.items():
                    entities = payload.get("entities") if isinstance(payload, dict) else payload
                    for it in entities or []:
                        if isinstance(it, dict):
                            flat.append({**it, "room": it.get("room") or room_name})
            out["entities"] = flat
            out.setdefault("total", len(flat))
            out.setdefault("has_more", False)
        return out

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
    def search_events(self, room: str = "", category: str = "", domain: str = "",
                      query: str = "", entity_id: str = "", state: str = "",
                      days: int = 7, start: str = "", end: str = "", limit: int = 200,
                      offset: int = 0, order: str = "desc", behavior_only: bool = True,
                      summarize: bool = False) -> Dict[str, Any]:
        """语义化事件搜索（room/category/domain/query/entity_id/state + 分页 + 摘要）。

        新框架的 `repo.load_events` 只认 entity_ids/rooms/behavior_only，category/query/
        domain/state/order/summarize 六个声明过的过滤位无处可去（生产实测四个过滤位
        拿到同一个 30000 上限值，见审计报告 §十六）。这些能力在 legacy 里是完整的，
        故交回 legacy；同时补上门面 Page 的 `limit`/`time_range` 键，保持向后兼容。
        """
        out = self.legacy.search_events(
            room=room, category=category, domain=domain, query=query, entity_id=entity_id,
            state=state, days=days, start=start, end=end, limit=limit, offset=offset,
            order=order, behavior_only=behavior_only, summarize=summarize)
        if isinstance(out, dict):
            out.setdefault("limit", limit)
            if "time_range" not in out and out.get("window"):
                out["time_range"] = out["window"]
        return out

    @_degrade(lambda: {"ok": False, "count": 0, "events": [],
                       "offset": 0, "limit": 0, "has_more": False})
    def query_behavior_events(self, room: str = "", member: str = "", days: int = 7,
                              start: str = "", end: str = "", limit: int = 50) -> Dict[str, Any]:
        """查 VLM 多模态识别记录（谁在哪个房间、什么时间）——计算跑在 legacy 上（裁5 Q1=A）。

        两件新引擎给不了的事：数据源是 `behavior_events` 表（`load_events` 读的是
        `events`，HA 状态变化表），而 `member` 过滤要解 `persons_json`。生产实测
        `events` 表 989,240 行里 `person` 非空 **0 行**，`behavior_events` 4,023 行里
        3,595 行带人员——按门面原先的实现，这个工具永远答不出「有谁在书房」。
        形参与 legacy 逐字一致（ToolSpec 的 `member`/`days` 原先一个都不收，而
        `dispatch` 会把默认值非 None 的可选参全部补上 ⇒ 每次调用必抛 TypeError 后被
        `_degrade` 收成空页）。

        键集按裁5 Q2=A 并存：legacy 键（`ok/window/room/member_filter/count/events`）是
        长期承诺键，这里补门面代分页键 `offset`/`limit`/`has_more`/`time_range`。
        **不补 `total`**：legacy 侧一次只取一页（`limit` 上限 200），库里没有
        `behavior_events` 的全量计数口径，把 `count` 改名成 `total` 会把「本页条数」
        谎报成「匹配总数」。取全与否由 `has_more` 如实回答。
        """
        page_limit = max(1, min(int(limit or 50), 200))
        out = self.legacy.query_behavior_events(room=room, member=member, days=days,
                                                start=start, end=end, limit=page_limit)
        if isinstance(out, dict):
            count = int(out.get("count") or 0)
            out.setdefault("offset", 0)
            out.setdefault("limit", page_limit)
            out.setdefault("has_more", count >= page_limit)
            if "time_range" not in out and out.get("window"):
                out["time_range"] = out["window"]
        return out

    def _search(self, start: Any, end: Any, entity_id: str, room: str, category: str,
                query: str, limit: int, offset: int, behavior_only: bool) -> Dict[str, Any]:
        tr = self._tr(start, end)
        ids = [entity_id] if entity_id else None
        events = self.repo.load_events(tr, entity_ids=ids, rooms=[room] if room else None,
                                       behavior_only=behavior_only)
        events.sort(key=lambda e: e.ts)
        out = Page.build(events, offset=offset,
                         limit=self.config.clamp_limit(limit),
                         time_range=tr.to_dict()).to_dict("events")
        # `total` 直接来自上面那次扫描，命中 `max_scan` 时它是扫描行数而非匹配总数（Q4=A）。
        return annotate_scan(out, len(events), getattr(self.repo, "scan_limit", 0))

    @_degrade(lambda: {"ok": False, "event": None, "total": 0,
                       "offset": 0, "limit": 1, "has_more": False})
    def get_last_event(self, entity_id: Optional[str] = None, domain: Optional[str] = None,
                       room: Optional[str] = None, transition: str = "off",
                       days: int = 30) -> Dict[str, Any]:
        """某实体/某类设备最近一次状态变化——计算跑在 legacy 上（裁5 Q1=A）。

        门面原先的实现调 `self.core.load_events`，而 `BehaviorService` 没有这个方法
        （实测 `hasattr(core, "load_events") == False`），于是这个工具从上线第一天起
        每次调用都被 `_degrade` 收成 `{"event": None}`。legacy 有完整实现，MCP handler
        的五个位置参本来就是按 legacy 顺序传的。形参与 legacy 逐字一致；键集按 Q2=A
        并存：legacy 的 `ok/entity_id/friendly_name/ts/old_state/new_state/transition`
        为承诺键，门面代分页键 `event/total/offset/limit/has_more` 在此补上。
        """
        out = self.legacy.get_last_event(entity_id=entity_id, domain=domain, room=room,
                                         transition=transition, days=days)
        if isinstance(out, dict):
            found = bool(out.get("ok"))
            out.setdefault("event", dict(out) if found else None)
            out.setdefault("total", 1 if found else 0)
            out.setdefault("offset", 0)
            out.setdefault("limit", 1)
            out.setdefault("has_more", False)
        return out

    # ------------------------------------------------------------------
    # 5.3 设备使用统计
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("items"))
    def device_usage(self, entity_id: str = "", room: str = "", category: str = "",
                     query: str = "", days: int = 7, start: str = "", end: str = "",
                     on_states: str = "", debounce_seconds: Optional[int] = None,
                     include_timeline: bool = True) -> Dict[str, Any]:
        """设备用量（状态配对 / 跨窗口截断 / 去抖 / 时间线均在服务端算好）。

        门面原先的 7 参形状不收 entity_id/on_states/debounce_seconds/include_timeline，
        而 mcp_server.py:1164 与 llm_routes.py:539/544/549 都按 legacy 的 10 参形状调用：
        前者直接 TypeError（工具整天返回 error），后者把 entity_id 绑进了 `days`
        （timedelta 收到字符串）。能力在 legacy 侧完整，交回 legacy，并补 Page 键。
        """
        if debounce_seconds is None:
            from ..insights_legacy import DEFAULT_DEBOUNCE_SECONDS
            debounce_seconds = DEFAULT_DEBOUNCE_SECONDS
        out = self.legacy.device_usage(
            entity_id=entity_id, room=room, category=category, query=query, days=days,
            start=start, end=end, on_states=on_states,
            debounce_seconds=debounce_seconds, include_timeline=include_timeline)
        if isinstance(out, dict):
            out.setdefault("items", out.get("devices") or [])
            out.setdefault("total", out.get("device_count") or 0)
        return out

    @_degrade(lambda: {"sessions": [], "total": 0, "offset": 0,
                       "limit": 0, "has_more": False})
    def climate_sessions(self, query: str = "", room: str = "", days: int = 7,
                         start: str = "", end: str = "") -> Dict[str, Any]:
        """空调/暖气会话（设定温度 + 室温 + 运行时长）——计算跑在 legacy 上（裁5 Q1=A）。

        门面原先调 `self.core.climate_sessions`，`BehaviorService` 没有这个方法
        （静态扫描实测）⇒ 每次都 AttributeError 被 `_degrade` 收成空 `sessions`。
        形参与 legacy 逐字一致（MCP handler 的五个位置参本来就按这个顺序传）；
        legacy 键（`ok/window/total_sessions/events_scanned/…/sessions`）为承诺键，
        这里补门面代分页键。
        """
        out = self.legacy.climate_sessions(query=query, room=room, days=days,
                                           start=start, end=end)
        if isinstance(out, dict):
            sessions = out.get("sessions") or []
            out.setdefault("total", out.get("total_sessions", len(sessions)))
            out.setdefault("offset", 0)
            out.setdefault("limit", len(sessions))
            out.setdefault("has_more", False)
            if "time_range" not in out and out.get("window"):
                out["time_range"] = out["window"]
        return out

    @_degrade(lambda: {"ok": False, "entity": "", "total_volume_ml": 0,
                       "total_count": 0, "days": []})
    def water_purifier_usage(self, start: Any, end: Any) -> Dict[str, Any]:
        """净水器用量——计算跑在 legacy 上（裁5 Q1=A）。

        这一条不是 MCP 工具，消费者是仓内的 `templates.py:445`（净水器日报模板），
        它按 legacy 的 `ok/total_volume_ml/total_count/days[].total_volume_l` 取值。
        门面原先同样调 `self.core.water_purifier_usage`（不存在）⇒ 模板拿到的永远是空表。
        """
        return self.legacy.water_purifier_usage(start, end)

    # ------------------------------------------------------------------
    # 5.4 行为洞察
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("insights"))
    def behavior_insights(self, days: int = 7, rooms: str = "", behavior_only: bool = True,
                          start: str = "", end: str = "") -> Dict[str, Any]:
        """行为洞察（单窗口）：作息节律、房间活跃、跨设备转移、每日量与异常。

        门面原先的签名是 `(days, start, end, room, category, query)`，而两个调用点
        （mcp_server.py:1137 位置传参、researcher.py:115 关键字 `rooms=`）按 legacy 的
        `(days, rooms, behavior_only, start, end)` 传：前者的 `rooms` 落进 `start`、
        `behavior_only` 落进 `end`（本机实测连默认参数都会让窗口解析抛 OSError，生产则
        "成功"返回一份口径错位的报告），后者压根不认识 `rooms`。报告字段
        （daily_rhythm / room_transitions / anomalies / daily_totals）在新实现里也没有
        对应物，所以整条交回 legacy。
        """
        return self.legacy.behavior_insights(days=days, rooms=rooms,
                                             behavior_only=behavior_only,
                                             start=start, end=end)

    @_degrade(lambda: Page.build([]).to_dict("insights"))
    def get_behavior_insights(self, compare_days: int = 7) -> Dict[str, Any]:
        """行为洞察（当前窗口 vs 前一窗口，带环比）。"""
        return self.core.compare_insights(compare_days=compare_days)

    @_degrade(lambda: Page.build([]).to_dict("activities"))
    def infer_activities(self, days: int = 7, rooms: str = "", start: str = "",
                         end: str = "", activities: Any = None) -> Dict[str, Any]:
        """活动推断（洗澡/学习/看电视/睡眠/烹饪 + 自定义）。

        `activities` 是 ToolSpec 声明的入参，原先收进来就丢——调用方缩小范围的意图
        被静默吞掉，拿到的仍是全量。
        """
        tr = self._tr(start, end, days=days or 7)
        out = self.core.infer_activities(tr, rooms=rooms)
        if isinstance(activities, str):
            allow = {p.strip() for p in activities.split(",") if p.strip()}
        else:
            allow = {str(v).strip() for v in (activities or []) if str(v).strip()}
        if allow and isinstance(out, dict) and isinstance(out.get("activities"), list):
            out["activities"] = [
                a for a in out["activities"]
                if str(a.get("activity") or a.get("name") or "") in allow
            ]
        return out

    @_degrade(lambda: {"ok": False, "error": "invalid rule", "activity": {}})
    def define_activity(self, name: str, room: str = "", tags: Any = None,
                        start_hour: int = 0, end_hour: int = 23, min_events: int = 1,
                        confidence: float = 0.6, note: str = "") -> Dict[str, Any]:
        """定义自定义活动规则。

        门面原先的签名是 `(name, rule, tags, room)`：MCP 按 ToolSpec 登记的 8 个参数
        位置传参进来先撞 TypeError，签名对上后又撞 `BehaviorService` 没有 `activities`
        属性——两层异常都被 `_degrade` 吞成 `ok:false`，于是这个工具从来没成功过
        （`activity_rules` 里一条都没落）。改回与 legacy 一致的签名并显式转发，
        同 `LEGACY_CONTRACT_MEMBERS` 的处理路子。
        规则的**套用**落在语义引擎那一侧，口径待 DCD 裁定。
        """
        out = self.legacy.define_activity(
            name, room=room, tags=tags or [], start_hour=start_hour, end_hour=end_hour,
            min_events=min_events, confidence=confidence, note=note,
        )
        # legacy 的回执写着"下次 infer_activities 自动套用"，那是语义引擎时代的话。
        # 现行门面用的是时段启发式，还没有规则入口——不能把"注册成功"回成"将要生效"，
        # 那等于把一次失败换成一次静默的过度承诺（套用口径见 DCD 20261003 申请）。
        if isinstance(out, dict) and out.get("ok"):
            out["message"] = (
                "规则已注册进 activity_rules（rule_id=%s）；"
                "当前活动推断走时段启发式，尚未套用自定义规则，"
                "套用口径待 DCD 裁定后接通" % (out.get("rule", {}).get("rule_id") or "")
            )
        return out

    # ------------------------------------------------------------------
    # 5.5 异常检测
    # ------------------------------------------------------------------
    @_degrade(lambda: Page.build([]).to_dict("anomalies"))
    def anomaly_report(self, days: int = 0, start: str = "", end: str = "", room: str = "",
                       category: str = "", query: str = "") -> Dict[str, Any]:
        """异常报告（设备异常 + 数据质量 + 噪声源）。"""
        tr = self._tr(start, end)
        start, end = self._days_to_range(days, start, end)
        return self.core.anomaly_report(tr, room=room, category=category)

    @_degrade(lambda: Page.build([]).to_dict("devices"))
    def device_health(self, room: str = "", category: str = "", query: str = "",
                      days: int = 7, stale_days: int = 3,
                      only_enabled: bool = True) -> Dict[str, Any]:
        """设备健康探测（失联 / 没电 / 长期静默）。

        mcp_server.py:1446 按 legacy 的 `(room, category, query, days, stale_days,
        only_enabled)` 位置传参，门面签名却是 `(days, start, end, room, category, query)`：
        生产读数里 `房间名` 落进 `days`、`3`（stale_days）落进 `category`，报
        `'int' object has no attribute 'strip'`——工具自切换那天起就没成功返回过。
        stale_days / only_enabled 两个入参在新实现里根本没有对应物，交回 legacy。
        """
        return self.legacy.device_health(room=room, category=category, query=query,
                                         days=days, stale_days=stale_days,
                                         only_enabled=only_enabled)

    @_degrade(lambda: Page.build([]).to_dict("issues"))
    def data_quality_issues(self, start: str = "", end: str = "",
                            limit: int = 30000) -> Dict[str, Any]:
        """数据质量问题（缺失 / 单位冲突 / 噪声源）。

        原先调 `self.core.data_quality_issues`——`BehaviorService` 没有这个方法，
        而它自己的质量问题在 `data_quality(tr)` 的 `issues` 里（`get_data_quality`
        这条对外工具用的就是它）。这里改成读同一份产出，不再指向不存在的成员。
        """
        tr = self._tr(start, end)
        issues = (self.core.data_quality(tr) or {}).get("issues") or []
        return Page.build(issues, offset=0, limit=max(0, int(limit or 0))).to_dict("issues")

    # ------------------------------------------------------------------
    # 5.6 用户画像
    # ------------------------------------------------------------------
    @_degrade(lambda: {"persona": {}, "total": 0, "offset": 0, "has_more": False})
    def get_user_persona(self, days: int = 14) -> Dict[str, Any]:
        """获取用户画像。"""
        return self.core.user_persona(days=days or 14)

    @_degrade(lambda: {"insight_id": "", "found": False, "ok": False, "total": 0,
                       "offset": 0, "has_more": False})
    def explain_insight(self, insight_id: str) -> Dict[str, Any]:
        """证据溯源（洞察 id / agent 记忆 id → 底层事件与 source_refs）——跑在 legacy 上（裁5 Q1=A）。

        门面原先调 `self.core.explain_insight`（不存在）⇒ 恒为 `found: False`。
        新引擎这一侧确实有实现（`PersonaBuilder.explain`），但它要一份
        `insight_id -> Insight` 索引，而**没有任何地方生产这份索引**，
        `infer_activities` 也不落 `detected_activity` 行——那正是裁5 Q3 验收单第 5 条
        要补的东西，补齐之前不切。legacy 走的是库里真实存在的两类 id：
        `detected_activity.activity_id` 与 agent 记忆 id。
        legacy 键（`ok/type/activity_id/memory_id/events/resolved`）为承诺键，
        门面代键 `found/insight_id/total/offset/has_more` 在此补上。
        """
        out = self.legacy.explain_insight(insight_id)
        if isinstance(out, dict):
            out.setdefault("insight_id", insight_id)
            out.setdefault("found", bool(out.get("ok")))
            out.setdefault("total", 1 if out.get("ok") else 0)
            out.setdefault("offset", 0)
            out.setdefault("has_more", False)
        return out

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
    @_degrade(lambda: {"events": [], "total": 0, "offset": 0, "limit": 0, "has_more": False})
    def get_events(self, days: int = 7, start: str = "", end: str = "",
                   entity_id: str = "", room: str = "", limit: int = 50,
                   offset: int = 0) -> Dict[str, Any]:
        """事件列表（新引擎扫描路径，命中 `max_scan` 时带 truncated/scan_limit/total_exact）。

        原先的实现在切片之后才数 `total`（所以 `total` 永远等于本页条数，翻页翻不出
        全量），并把未序列化的 `EventRecord` 对象直接放进返回体；两者都交给 `_search`
        统一处理，降级信封的键名也从误抄的 `days` 改回 `events`。
        """
        start, end = self._days_to_range(days, start, end)
        return self._search(start, end, entity_id, room, "", "", limit, offset,
                            behavior_only=False)

    def compare_insights(self, compare_days: int = 7, base_days: int = 7) -> Dict[str, Any]:
        """对比洞察（兼容 legacy）。"""
        try:
            return self.core.compare_insights(compare_days=compare_days)
        except Exception as exc:
            LOG.exception("compare_insights 失败")
            return {"periods": [], "changes": [], "error": str(exc)}

    def insights_report(self, days: int = 7, fmt: str = "json") -> Any:
        """洞察报告（兼容 legacy）。"""
        insights = self.behavior_insights(days=days)
        try:
            from .report import ReportBuilder
            return ReportBuilder().insights_report(insights, fmt)
        except Exception as exc:
            LOG.exception("insights_report 失败")
            return {"error": str(exc)}

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

    # ------------------------------------------------------------------
    # 审计 P0-5：legacy 契约成员显式转发（清单见 LEGACY_CONTRACT_MEMBERS）
    # 这些方法**不套 ``_degrade``**：调用失败必须抛出真异常，而不是静默变成空结果。
    # ------------------------------------------------------------------
    def resolve_range(self, *args: Any, **kwargs: Any):
        return self.legacy.resolve_range(*args, **kwargs)

    def name_map(self) -> Dict[str, Any]:
        return self.legacy.name_map()

    def decorate(self, rows: Any) -> List[dict]:
        return self.legacy.decorate(rows)

    def _parse(self, raw: str):
        return self.legacy._parse(raw)

    def _fallback_name(self, entity_id: str) -> str:
        return self.legacy._fallback_name(entity_id)

    def _usage_one(self, *args: Any, **kwargs: Any):
        return self.legacy._usage_one(*args, **kwargs)

    def _usage_by_attr(self, *args: Any, **kwargs: Any):
        return self.legacy._usage_by_attr(*args, **kwargs)

    def _count_by_filter(self, *args: Any, **kwargs: Any):
        return self.legacy._count_by_filter(*args, **kwargs)

    def _iter_all_events(self, *args: Any, **kwargs: Any):
        return self.legacy._iter_all_events(*args, **kwargs)

    def _tags_of(self, eid: str, name: str) -> set:
        return self.legacy._tags_of(eid, name)
