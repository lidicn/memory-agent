"""Insights 框架 · 数据访问层（StoreRepository）

严格对齐生产库真实 schema（docs/insights_schema_contract.py）：

events(id, ts, day, room, entity_id, domain, action, person,
       old_state, new_state, attrs_json)
behavior_events(server_ts, device_ts, day, room, camera_src, persons_json, count,
                action, scene, confidence, appearance_json, trigger,
                vlm_latency_ms, snapshot_path, raw_response, status)
perception_events(event_id, server_ts, day, source, kind, room, entity_id,
                  confidence, payload_json, raw_event_json)

必须遵守的生产事实：
1. 不存在 entities / entity_catalog 表 —— 实体清单只能来自 events 表聚合；
2. events 没有 state / attributes 列 —— 只能用 new_state / attrs_json；
3. ts / server_ts 是 ISO8601 字符串，day 是 "YYYY-MM-DD"，比较走字符串序；
4. 全部 SQL 经 self.store.db_query(sql, params)（qmark 占位符）执行，不碰 conn/cursor。

失败语义：除 health() 外一律 fail-closed（异常上抛），由 service 层决定如何兜底。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import EventRecord, house_tz

__all__ = ["StoreRepository"]

_LOG = logging.getLogger("insights.repository")

#: events 表真实列（顺序与 SELECT 一致）
EVENT_COLUMNS = ("id", "ts", "day", "room", "entity_id", "domain",
                 "action", "person", "old_state", "new_state", "attrs_json")


# --------------------------------------------------------------------- 工具
def _to_epoch(value: Any) -> float:
    """ISO8601 字符串 -> epoch float（生产数据为家庭墙钟 naive 字符串）。

    容器跑在 UTC，naive ISO 会被当 UTC 解析导致整体平移数小时。
    这里显式按 ``house_tz()``（Config.tz_offset_hours 注入）解释再转 epoch。
    解析失败 fail-open 返回 0.0。
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=house_tz())
        return dt.timestamp()
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            _LOG.warning("无法解析时间戳: %r", value)
            return 0.0


def _to_iso(value: float) -> str:
    """epoch float -> ISO8601 字符串（秒级，家庭墙钟口径，与生产数据对齐）。"""
    return datetime.fromtimestamp(float(value), tz=house_tz()).replace(tzinfo=None).isoformat(timespec="seconds")


def _load_attrs(raw: Any) -> Dict[str, Any]:
    """attrs_json -> dict；NULL/空 -> {}；解析失败 fail-open 返回 {} 并留日志。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        _LOG.warning("attrs_json 解析失败: %r", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


class StoreRepository:
    """events / behavior_events / perception_events 的只读访问层。

    所有查询只经 _execute() -> store.db_query()。除 health() 外失败即抛（fail-closed）。
    """

    #: load_events(behavior_only=True) 追加的过滤条件（交付说明 §一/B）
    BEHAVIOR_ONLY_SQL = "COALESCE(action, '') != ''"
    DEFAULT_MAX_SCAN = 30000

    def __init__(self, store: Any, config: Any) -> None:
        self.store = store
        self.config = config
        self.log = _LOG
        # 第三轮审计 P0-6：entity_catalog 每次全表 GROUP BY（82 万行实测 621ms），
        # 且在全局 RLock 内执行 → 慢查询转化为全系统写入阻塞。加 60s TTL 缓存，
        # key 含过滤参数，命中即跳过全表扫描。
        self._catalog_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._catalog_ttl = 60.0
        # DCD 20261004 裁6 Q6-2=A 按天分批 → DCD 20261005 §二.1 乙′ 天内再按小时分层。
        # 现判据是「某个小时格多要的那一行真的回来了」（LIMIT 配额+1 的探针行），
        # 不是「某天满额」更不是「总数 >= 上限」：后两者都会把"本来就这么少"读成截断。
        # 放线程局部：StoreRepository 是门面级共享实例（`build_repository` 一 facade 一个），
        # MCP/HTTP 并发与 runtime 周期任务会在不同线程同时调 load_events —— 放实例上
        # 会让 A 请求读到 B 请求的截断位（对外 `scan_truncated` 就此张冠李戴）。
        # 读侧与自己的 load_events 在同一调用栈同线程内，故线程局部足够。
        self._scan_state = threading.local()

    # ------------------------------------------------------------ 基础出口
    def _execute(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        """唯一 SQL 出口，强制走 store.db_query（qmark 占位符）。"""
        rows = self.store.db_query(sql, tuple(params))
        return list(rows) if rows else []

    def health(self) -> bool:
        """SELECT 1 能跑通即 True；任何异常都 fail-closed 成 False。"""
        try:
            rows = self._execute("SELECT 1 AS ok")
        except Exception as exc:
            self.log.warning("health 检查失败: %s", exc)
            return False
        return bool(rows) and rows[0].get("ok") == 1

    # ------------------------------------------------------------ 内部工具
    @staticmethod
    def _as_tuple(values: Any) -> Tuple[Any, ...]:
        """None/""/[] -> ()（表示不过滤）；字符串按逗号分割；其余按元素收集。"""
        if values is None:
            return ()
        if isinstance(values, str):
            return tuple(p.strip() for p in values.split(",") if p.strip())
        if isinstance(values, (list, tuple, set, frozenset)):
            return tuple(v for v in values if v not in (None, ""))
        return (values,)

    @classmethod
    def _add_in(cls, where: List[str], params: List[Any], column: str, values: Any) -> None:
        vals = cls._as_tuple(values)
        if not vals:
            return
        where.append(column + " IN (" + ",".join(["?"] * len(vals)) + ")")
        params.extend(vals)

    @classmethod
    def _add_not_in(cls, where: List[str], params: List[Any], column: str,
                    values: Any) -> None:
        """硬排除：`column NOT IN (...)`，空排除集不加任何条件（等于不过滤）。

        不补 `OR column IS NULL`：events 的 entity_id/room/domain 都是
        `NOT NULL DEFAULT ''`（store.py 的建表语句），缺值时入库已被折叠成 `''`，
        NULL 行不存在，加了是掩盖问题而不是防御。排除集同理由 `_as_tuple` 滤掉
        `None`/`""`，不会写出 `NOT IN (NULL)` 这种恒为 UNKNOWN 的条件。
        """
        vals = cls._as_tuple(values)
        if not vals:
            return
        where.append(column + " NOT IN (" + ",".join(["?"] * len(vals)) + ")")
        params.extend(vals)

    @staticmethod
    def _bounds(tr: Any) -> Tuple[str, str, str, str]:
        """(start_iso, end_iso, start_day, end_day)；tr 只要求 TimeRange 形状。"""
        start_iso = getattr(tr, "start_iso", None) or ""
        end_iso = getattr(tr, "end_iso", None) or ""
        if not start_iso:
            if not hasattr(tr, "start_ts"):
                raise TypeError("tr 缺少 start_iso / start_ts")
            start_iso = _to_iso(tr.start_ts)
        if not end_iso:
            if not hasattr(tr, "end_ts"):
                raise TypeError("tr 缺少 end_iso / end_ts")
            end_iso = _to_iso(tr.end_ts)
        return start_iso, end_iso, start_iso[:10], end_iso[:10]

    def _scan_limit(self) -> int:
        return int(getattr(self.config, "max_scan", 0) or self.DEFAULT_MAX_SCAN)

    @property
    def scan_limit(self) -> int:
        """对外暴露的扫描上限（DCD 20261004 MA-裁5 Q4=A）。

        调用方需要它才能把「`total` 是扫描上限还是全量」说清楚；上限本身仍是
        `load_events` 的 `LIMIT`，裁定明确**不提高**。"""
        return self._scan_limit()

    def _top_n(self, limit: Any, default: int = 50) -> int:
        ceiling = int(getattr(self.config, "max_limit", 0) or 5000)
        try:
            n = int(limit) if limit else int(default)
        except (TypeError, ValueError):
            n = int(default)
        return max(1, min(n, ceiling))

    def _event_where(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                     domains: Any = None, behavior_only: bool = False,
                     exclude_entity_ids: Any = None,
                     exclude_domains: Any = None,
                     states: Any = None,
                     include_window: bool = True) -> Tuple[List[str], List[Any]]:
        """事件查询的 WHERE 片段与参数。

        `include_window=False` 只要**非时间**那几位（实体/房间/域/状态/硬排除）。分层扫描
        的小时分支用它：窗口边界折进每个小时格的 `[lo, hi)` 里夹紧，分支里就只剩一段 ts
        区间。带上 `day BETWEEN` 或整窗 `ts BETWEEN` 会让规划器改选宽索引——生产库实测
        同一天 24 格：`day=?` 408ms、加整窗 ts 合取 6109ms、只留夹紧后的 ts 区间 18.5ms，
        **三者返回的是同一批行**（`day != substr(ts,1,10)` 全表 0 行）。
        """
        start_iso, end_iso, start_day, end_day = self._bounds(tr)
        where = ["day BETWEEN ? AND ?", "ts BETWEEN ? AND ?"] if include_window else []
        params: List[Any] = ([start_day, end_day, start_iso, end_iso]
                             if include_window else [])
        self._add_in(where, params, "entity_id", entity_ids)
        self._add_in(where, params, "room", rooms)
        self._add_in(where, params, "domain", domains)
        # 状态维（裁5 追加 Q-B / Q3-1 的 `state` 位）：门面收的 `state` 一直是被丢弃的
        # 六个位之一，落点就是这一列——与 legacy 的 `states` 参逐字同义（new_state 白名单）。
        self._add_in(where, params, "new_state", states)
        # 硬排除（DCD 20261004 MA-裁6 Q3=A）：signal_exclusions 里的实体必须从
        # 事件流和 activity_matrix 两侧同时剔除，只剔一侧会让「已排除」成为空话。
        self._add_not_in(where, params, "entity_id", exclude_entity_ids)
        self._add_not_in(where, params, "domain", exclude_domains)
        if behavior_only:
            where.append(self.BEHAVIOR_ONLY_SQL)
        return where, params

    def _behavior_where(self, tr: Any, rooms: Any = None) -> Tuple[List[str], List[Any]]:
        start_iso, end_iso, start_day, end_day = self._bounds(tr)
        where = ["day BETWEEN ? AND ?", "server_ts BETWEEN ? AND ?"]
        params: List[Any] = [start_day, end_day, start_iso, end_iso]
        self._add_in(where, params, "room", rooms)
        return where, params

    def _to_record(self, row: Dict[str, Any]) -> EventRecord:
        """events 行 -> EventRecord（契约 §1 的转换规则逐条对应）。"""
        attrs = _load_attrs(row.get("attrs_json"))
        entity_id = row.get("entity_id") or ""
        friendly = attrs.get("friendly_name") or entity_id
        unit = attrs.get("unit_of_measurement") or attrs.get("unit") or ""
        return EventRecord(
            ts=_to_epoch(row.get("ts")),
            entity_id=entity_id,
            state=row.get("new_state") or "",
            attributes=attrs,
            friendly_name=str(friendly),
            room=row.get("room") or "",
            domain=row.get("domain") or "",
            unit=str(unit),
        )

    # ------------------------------------------------------------ 事件读取
    #: 日内分层的小时格数（DCD 20261005 §二.1 乙′）
    SCAN_HOURS = 24
    #: 24 个小时格的字面量（`'00'..'23'`）。只在这里生成一次，不进任何外部输入。
    HOURS = tuple("%02d" % h for h in range(24))

    def load_events(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                    domains: Any = None, behavior_only: bool = False,
                    exclude_entity_ids: Any = None,
                    exclude_domains: Any = None,
                    states: Any = None, order: str = "asc") -> List[EventRecord]:
        """查 events 表，按「天 × 小时」分层扫描，返回 EventRecord 列表。

        两代裁定叠在这一个方法上，形状必须说清：

        * **裁6 Q6-2=A**（DCD 20261004）：不许一条 `LIMIT scan_limit` 打完 30 天窗
          （旧实现只覆盖窗口前 20 小时）。预算按**日**分摊，日配额
          `max(1, scan_limit // n_days)`，**不新增预算**（约束①）。
        * **DCD 20261005 §二.1 乙′**：日配额再按**小时**切——每天一条语句，内含 24 个
          各带 `LIMIT k` 的子查询 `UNION ALL`，`k = 日配额 // 24`（生产 967//24≈40）。
          **驳回甲**（按天倒序分摊：30 天窗退化成"看昨天"，趋势线失去历史）、
          **驳回丙**（扫描下推到聚合：超出本件射程）。

        为什么是真 `LIMIT` 而不是窗口函数：乙′ 明写"分层但不算窗口函数"，每个小时格
        自带 `LIMIT`，SQL 侧不必为当日全部行算排名。每格的过滤是 **ts 的半开区间**
        （`ts >= DTHH:00:00 AND ts < DT(HH+1):00:00`，并被窗口首尾那一截夹紧），这样它能走
        `idx_events_ts`，一天的内部扫描量回到「一天一遍」量级；用 `substr(ts,12,2) = 'HH'`
        走不了索引，一条语句就是「把当天扫 24 遍」——见 `_hour_bounds`。语句数仍是 1 条/天。

        为什么分支里**只**有这一段区间（不带 `day = ?`，也不带整窗 `ts BETWEEN`）：
        生产库同一天 24 格实测三种形状的耗时是 408ms / 6109ms / **18.5ms**，而三者取回的是
        同一批行——`day = ?` 让规划器选 `idx_events_day`（等于全天重扫 24 遍），整窗
        `ts BETWEEN` 把 `idx_events_ts` 的范围撑回整个窗口。等价性的地基是
        `day == substr(ts,1,10)`：现网 1,010,348 行里不一致的有 **0** 行。

        **小时配额是"第一波的公平份额"，不是天花板**（见 `_scan_day`）：轮转分配先把
        每个非空小时格各交一行（夜间因此必出场），再把日预算的余量喂给被砍断过的格。
        按配额整数切完就交回会让日预算没满时照样少给行——生产上流量集中在两三个小时是
        常态，那样一天只交回 160/967 条，门面分页也会交出半页。

        **口径（裁定 :43 认这一条）**：「总读取量硬上限」重述为「**返回行数**硬上限」——
        Python 侧收到的行数仍 ≤ `scan_limit`，但**引擎内部扫描行数不再受限**。
        这是乙′ 的必要代价，不是放宽；引用这一格必须带上口径。

        `states` / `order` 是裁5 追加 Q-B（Q3-1 六个过滤/排序位）里的两位：
        前者下推进 `new_state IN (...)`，后者决定**小时格内取哪一段**（`asc` 留该小时
        最早、`desc` 留最晚）；小时配额、日配额与总预算都不因此改变。
        """
        from datetime import date, timedelta

        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains, states)
        # 小时分支只带非时间那几位；窗口边界由 `_hour_bounds` 折进每个格夹紧（原因见
        # `_event_where` 的 `include_window`）。
        filt, filt_params = self._event_where(tr, entity_ids, rooms, domains,
                                              behavior_only, exclude_entity_ids,
                                              exclude_domains, states,
                                              include_window=False)
        limit = self._scan_limit()
        start_iso, end_iso, start_day, end_day = self._bounds(tr)
        direction = "DESC" if str(order or "").strip().lower() in ("desc", "descending") else "ASC"
        order_by = " ORDER BY ts " + direction + ", id " + direction

        base_sql = ("SELECT id, ts, day, room, entity_id, domain, action, person, "
                    "old_state, new_state, attrs_json FROM events WHERE ")

        # 窗口右端的**排他**上界：分支里写 `ts < window_hi`，替掉旧口径的
        # `day BETWEEN + ts <= end_iso`。`end_iso` 由 `_to_iso` 产出（timespec=seconds），
        # 生产库的 ts 也全部定长 19 位、无小数秒（实测 1,010,348 行 HAS_DOT=0）⇒
        # 两种写法在这一档数据上取到同一批行；朝外取整秒是"宁可多一档也不漏行"的方向。
        # 解析不出来（调用方塞了非 ISO 形状）就**不夹紧上界**：驱动循环只走
        # [start_day, end_day] 内的日键，最坏是多带回末日窗口尾之后的行，
        # 而分支仍只有一段 ts 区间——不会退回"整天重扫"那个慢形状。
        try:
            window_hi = (datetime.fromisoformat(end_iso)
                         + timedelta(seconds=1)).isoformat(timespec="seconds")
        except ValueError:
            window_hi = "9999-12-31T23:59:59"

        def _hour_bounds(day_str: str, hh: str) -> Tuple[str, str]:
            """一个小时格 → **半开** ts 区间，并且已被窗口边界夹紧。

            为什么不用 `substr(ts,12,2) = 'HH'`（第一版用的就是这个）：那种写法走不了索引，
            SQLite 只能用 `idx_events_day` 把**整天**扫一遍再按小时筛 ⇒ 一条语句 24 个分支
            就是「把当天扫 24 遍」。改成 ts 的半开区间后，每个分支只碰自己那一小时的行，
            一天的内部扫描量回到「一天一遍」量级。

            半开是正确性要求，不是风格：闭区间 `'...T09:59:59'` 会把带毫秒的
            `09:59:59.500` 挡在两格之外（09 格上界不含它、10 格下界也不含它），
            那种行会**静默消失**。

            夹紧（本批把窗口边界折进区间，而不是在分支里再合取 `day` / 整窗 `ts`）：
            * 下界取 `max(格起点, start_iso)`——首日 start_iso 之前的格会得到
              `lo > hi` 的空区间，一行不返，等价于旧口径的 `ts >= start_iso`；
            * 上界取 `min(格终点, end_iso 的下一跳)`——`ts <= end_iso` 与
              `ts < end_iso + 1s` 的差别只在 (end_iso, end_iso+1s) 这段；上界朝外取整，
              窗口内的行一条不漏（生产库 ts 全部定长 19 位、无小数秒，这段本来就空）。

            为什么值得为夹紧改形状：规划器在分支里看到 `day = ?` 就选了 `idx_events_day`
            （实测 408ms/天），看到整窗 `ts BETWEEN` 就把索引范围撑回整月（6109ms/天），
            只留夹紧后的单段 ts 区间才走 `idx_events_ts`（18.5ms/天）——三者行集相同。
            """
            lo = day_str + "T" + hh + ":00:00"
            if hh != "23":
                hi = day_str + "T" + "%02d" % (int(hh) + 1) + ":00:00"
            else:
                nxt = date.fromisoformat(day_str) + timedelta(days=1)
                hi = nxt.isoformat() + "T00:00:00"
            if lo < start_iso:
                lo = start_iso
            if hi > window_hi:
                hi = window_hi
            return lo, hi

        def _hour_statement(day_str: str, hours: List[str], probe_cap: int,
                            offsets: Dict[str, int]) -> Tuple[str, List[Any]]:
            """给定小时格集合 → 一条 `UNION ALL` 语句，每格自带 `LIMIT/OFFSET`。

            `hour_bucket` 是每格自带的字面量列，只给判据回读用（哪一格被砍满），
            `_to_record` 不认它、也不往 `EventRecord` 里带。

            每格 `LIMIT` 绑的都是 `配额 + 1`（多要一行）。**判据要的是证据不是猜测**：
            按配额整数取，"这一小时本来只有 k 条"和"这一小时被砍到 k 条"回来的是同一个
            形状，截断位就恒真（对照组锁抓到了这一次假红）。多要一行之后，
            真被砍的格子会回来 k+1 条，能分开。
            """
            branches: List[str] = []
            branch_params: List[Any] = []
            for hh in hours:
                lo, hi = _hour_bounds(day_str, hh)   # hh 只来自 HOURS 常量，不是外部输入
                # `SELECT * FROM (…) UNION ALL …`：SQLite 不接受把带 ORDER BY/LIMIT 的
                # 子查询**整体**加括号当作 compound 的一项（`near "(": syntax error`），
                # 必须包成 `FROM (...)`。这一格踩过一次，6 条测试同时炸。
                # 分支里**只有**夹紧后的那段 ts 区间是非时间的位之外唯一的时间谓词：
                # 多带 `day = ?` 会让规划器选 `idx_events_day`（全天重扫 24 遍），
                # 多带整窗 `ts BETWEEN` 会把索引范围撑回整窗——见 `_hour_bounds` 的实测三档。
                branches.append(
                    "SELECT * FROM (SELECT '" + hh + "' AS hour_bucket, id, ts, day, room, "
                    "entity_id, domain, action, person, old_state, new_state, attrs_json "
                    "FROM events WHERE "
                    + " AND ".join(filt + ["ts >= ?", "ts < ?"])
                    + order_by + " LIMIT ? OFFSET ?)")
                # 每个分支都自带完整 where，占位符必须按分支重复绑定（只在外层绑一次
                # 会撞 `Incorrect number of bindings`：语句 168 个、参数 76 个）。
                branch_params += filt_params + [lo, hi, probe_cap, offsets[hh]]
            return " UNION ALL ".join(branches), branch_params

        def _plain_day_rows(day_str: str, cap: int) -> List[Dict[str, Any]]:
            """一条不分层的当日查询（只给"日预算连 24 格都喂不满"的退路用）。"""
            return self._execute(
                base_sql + " AND ".join(where + ["day = ?"]) + order_by + " LIMIT ?",
                params + [day_str, cap])

        def _scan_day(day_str: str, day_limit: int) -> Tuple[List[Dict[str, Any]], bool]:
            """一天 = 轮转分配 +（只在预算没满时）一轮缺口补读。返回 (行, 当天被砍)。

            为什么小时格不是天花板：按 `hour_cap` 整数切完就交回，**日预算没满也会少给**。
            一天只集中在几个小时是常态（晚饭+看电视那两三小时能占掉大半流量），
            24 格各砍 40 条只交回 160 条，剩下 807 条预算白放着 —— 语义侧的证据凭空少
            6 倍，门面分页会交出「窗口里明明有 10 条、只回 1 条」的半页。
            所以小时配额是**第一波的公平份额**（保证 20:00–23:00 出场，裁定 :39 的现象），
            轮转分配把日预算用满，被砍断过的格再补读一轮。

            代价边界：补读只在"手上的行分完了、预算还没满"时发生一轮。生产日量下第一波
            就把日配额占满 ⇒ **一天仍是一条语句**，Python 侧仍是 `日配额 + 24` 行
            （裁定 :43 那一格）；只有偏稀疏、把流量挤在少数几小时的一天才多走那一条。
            """
            if day_limit < self.SCAN_HOURS:
                # 日预算连每小时一行都给不出 ⇒ 分层只会把「按 ts 的前缀」换成
                # 「按小时升序的前缀」，既不填满预算也不见夜间。退回整日一条查询。
                rows = _plain_day_rows(day_str, day_limit + 1)
                return rows[:day_limit], len(rows) > day_limit

            hour_cap = max(1, day_limit // self.SCAN_HOURS)

            def _pools(hours: List[str], cap: int,
                       offsets: Dict[str, int]) -> Dict[str, List[Dict[str, Any]]]:
                """一条语句取这些格，按小时格归位（不依赖 SQLite 的分支返回顺序）。"""
                sql, sql_params = _hour_statement(day_str, hours, cap, offsets)
                grouped: Dict[str, List[Dict[str, Any]]] = {}
                for row in self._execute(sql, sql_params):
                    grouped.setdefault(str(row.get("hour_bucket") or ""), []).append(row)
                return grouped

            pools = _pools(list(self.HOURS), hour_cap + 1, {hh: 0 for hh in self.HOURS})
            # `capped[hh]`：这一格上一次抓取刚好满额 ⇒ 后面**可能**还有行（尚未证明）。
            capped = {hh: len(rows) > hour_cap for hh, rows in pools.items()}
            consumed = {hh: 0 for hh in pools}
            day_rows: List[Dict[str, Any]] = []

            while len(day_rows) < day_limit:
                progressed = False
                for hh in sorted(pools):
                    i = consumed[hh]
                    if i < len(pools[hh]):
                        day_rows.append(pools[hh][i])
                        consumed[hh] = i + 1
                        progressed = True
                        if len(day_rows) >= day_limit:
                            break
                if progressed:
                    continue
                open_hours = [hh for hh in sorted(pools) if capped.get(hh)]
                if not open_hours:
                    break
                # 补读一轮：只问那些"上次满额"的格，各自从手上的行数之后接着取。
                # `OFFSET` 能这么用是因为分支里的 `ORDER BY ts, id` 同方向、是全序，
                # 同一格两次抓取不会错位（id 是主键，ts 相同的行也有稳定次序）。
                rest = day_limit - len(day_rows)
                got = _pools(open_hours, rest + 1, {hh: len(pools[hh]) for hh in open_hours})
                for hh in open_hours:
                    new_rows = got.get(hh, [])
                    pools[hh].extend(new_rows)
                    capped[hh] = len(new_rows) > rest
                # 一轮之后不再补：`rest + 1` 的格容量已够把剩下的预算填满，
                # 真填满到还想知道"后面有没有"的，是下面判据里 `capped` 那一支。

            # 判据只认**已证明**的丢失：手上还剩没交出的行，或有格仍是"满额未探底"。
            leftover = sum(len(pools[hh]) - consumed[hh] for hh in pools)
            return day_rows, bool(leftover > 0 or any(capped.values()))

        # 边界解析不出来时退回一条不分层的查询：宁可少覆盖，也不猜窗口。
        d0: Optional[date]
        try:
            d0 = date.fromisoformat(start_day)
            d1 = date.fromisoformat(end_day)
            n_days = max(1, (d1 - d0).days + 1)
        except (ValueError, TypeError):
            d0 = None
            n_days = 1

        daily_limit = max(1, limit // n_days)
        hour_cap = max(1, daily_limit // self.SCAN_HOURS)
        all_rows: List[Dict[str, Any]] = []
        truncated = False

        if d0 is None:
            rows = self._execute(base_sql + " AND ".join(where) + order_by + " LIMIT ?",
                                 params + [limit])
            all_rows = rows
            truncated = len(rows) >= limit
        else:
            current = d0
            while current <= d1 and len(all_rows) < limit:
                day_limit = min(daily_limit, limit - len(all_rows))
                day_rows, day_cut = _scan_day(current.isoformat(), day_limit)
                truncated = truncated or day_cut
                all_rows.extend(day_rows)
                current += timedelta(days=1)
            # 预算被前面的天用完、后面的天一天都没扫 ⇒ 这是已发生的事实，不是猜测。
            if current <= d1:
                truncated = True

        self._scan_state.truncated = truncated
        if truncated:
            self.log.warning(
                "load_events 分层扫描未读全：返回 %s/%s 行（日配额 %s，小时公平份额 %s，"
                "窗口 %s 天）——有小时格补读后仍未探底，或有天没扫到",
                len(all_rows), limit, daily_limit, hour_cap, n_days,
            )
        records = [self._to_record(row) for row in all_rows]
        records.sort(key=lambda e: e.ts, reverse=(direction == "DESC"))
        return records

    @property
    def last_scan_truncated(self) -> bool:
        """分层扫描下的截断判据（线程局部，见 `__init__` 注释）。

        四代判据的演进要连着读，否则这一格永远像"漏报"：旧判据
        `len(events) >= scan_limit` 在按天分摊下失效（总数远低于上限但某天已被砍）；
        裁6 的"任一天命中日配额"在乙′ 下又漏报一层（当天被砍表现为**某个小时格**被砍满，
        日总量可能仍低于日配额）；而乙′ 第一版把"小时格砍满"直接当成终判据，又犯了反方向的
        错——某格满额只说明**可能**还有行，实际可能整天都读完了（这正是 8 条数据在
        `max_scan=50` 下被读成 6 条、还标了截断的那一次）。
        **现判据：只认已证明的丢失** —— 手上抓回来却没交进结果集的行（补读之后仍有剩余），
        或某格补读一轮后依然满额未探底，或窗口里有天压根没轮到扫。
        还没跑过任何 `load_events` 的线程读到 False。
        """
        return bool(getattr(self._scan_state, "truncated", False))

    def count_events(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                     domains: Any = None, behavior_only: bool = False,
                     exclude_entity_ids: Any = None,
                     exclude_domains: Any = None,
                     states: Any = None) -> int:
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains, states)
        rows = self._execute("SELECT COUNT(*) AS c FROM events WHERE " + " AND ".join(where), params)
        return int((rows[0].get("c") if rows else 0) or 0)

    def day_counts(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                   domains: Any = None, behavior_only: bool = False,
                   exclude_entity_ids: Any = None,
                   exclude_domains: Any = None) -> Dict[str, int]:
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains)
        sql = "SELECT day, COUNT(*) AS c FROM events WHERE " + " AND ".join(where) + " GROUP BY day ORDER BY day"
        return {str(r.get("day") or ""): int(r.get("c") or 0) for r in self._execute(sql, params)}

    def activity_matrix(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                        domains: Any = None, behavior_only: bool = False,
                        exclude_entity_ids: Any = None,
                        exclude_domains: Any = None) -> List[Dict[str, Any]]:
        """(day, hour, domain) -> count；hour 由 substr(ts,12,2) 提取（0-23）。

        `exclude_entity_ids` 是裁6 Q3 的硬排除落点：时段启发式（兜底输出）就由这张
        表算出，不排除被点名的实体，「已排除 N 个实体」只会出现在返回体里而不生效。
        """
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only,
                                          exclude_entity_ids, exclude_domains)
        sql = ("SELECT day, CAST(substr(ts, 12, 2) AS INTEGER) AS hour_value, domain, COUNT(*) AS c "
               "FROM events WHERE " + " AND ".join(where) + " GROUP BY day, hour_value, domain")
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "day": str(r.get("day") or ""),
                "hour": int(r.get("hour_value") or 0),
                "domain": str(r.get("domain") or ""),
                "count": int(r.get("c") or 0),
            })
        return out

    def entity_stats(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                     domains: Any = None, behavior_only: bool = False) -> List[Dict[str, Any]]:
        """窗口内每个实体一行：count / first_ts / last_ts / active_days。

        room/domain 理论上实体恒定，用 MAX() 保证「一实体一行」。
        """
        where, params = self._event_where(tr, entity_ids, rooms, domains, behavior_only)
        sql = ("SELECT entity_id, MAX(room) AS room, MAX(domain) AS domain, COUNT(*) AS c, "
               "MIN(ts) AS first_ts, MAX(ts) AS last_ts, COUNT(DISTINCT day) AS active_days "
               "FROM events WHERE " + " AND ".join(where) +
               " GROUP BY entity_id ORDER BY c DESC, entity_id")
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "entity_id": str(r.get("entity_id") or ""),
                "room": str(r.get("room") or ""),
                "domain": str(r.get("domain") or ""),
                "count": int(r.get("c") or 0),
                "first_ts": str(r.get("first_ts") or ""),
                "last_ts": str(r.get("last_ts") or ""),
                "active_days": int(r.get("active_days") or 0),
            })
        return out

    def entity_catalog(self, rooms: Any = None, domains: Any = None,
                       limit: Any = None) -> List[Dict[str, Any]]:
        """全量已知实体清单（契约 §五：只能从 events 聚合，不能查 entities 表）。"""
        import time as _time
        cache_key = (
            tuple(sorted(rooms)) if isinstance(rooms, (list, tuple, set)) else (rooms or ""),
            tuple(sorted(domains)) if isinstance(domains, (list, tuple, set)) else (domains or ""),
            int(limit) if limit is not None else None,
        )
        now = _time.monotonic()
        cached = self._catalog_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < self._catalog_ttl:
            return list(cached[1])
        where: List[str] = []
        params: List[Any] = []
        self._add_in(where, params, "room", rooms)
        self._add_in(where, params, "domain", domains)
        sql = ("SELECT entity_id, MAX(room) AS room, MAX(domain) AS domain, "
               "MAX(ts) AS last_ts, COUNT(*) AS total FROM events")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY entity_id ORDER BY entity_id LIMIT ?"
        params.append(self._top_n(limit, 5000))
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "entity_id": str(r.get("entity_id") or ""),
                "room": str(r.get("room") or ""),
                "domain": str(r.get("domain") or ""),
                "last_ts": str(r.get("last_ts") or ""),
                "total": int(r.get("total") or 0),
            })
        self._catalog_cache[cache_key] = (now, out)
        return out

    def last_seen(self, tr: Any = None, entity_ids: Any = None,
                  rooms: Any = None) -> Dict[str, str]:
        """entity_id -> 最后一次出现的 ISO 时间（tr=None 表示全量）。"""
        where: List[str] = []
        params: List[Any] = []
        if tr is not None:
            _s, _e, start_day, end_day = self._bounds(tr)
            where += ["day BETWEEN ? AND ?", "ts BETWEEN ? AND ?"]
            params += [start_day, end_day, _s, _e]
        self._add_in(where, params, "entity_id", entity_ids)
        self._add_in(where, params, "room", rooms)
        sql = "SELECT entity_id, MAX(ts) AS last_ts FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY entity_id"
        return {str(r.get("entity_id") or ""): str(r.get("last_ts") or "")
                for r in self._execute(sql, params)}

    def state_counts(self, tr: Any, entity_id: str = None, rooms: Any = None,
                     domains: Any = None, limit: Any = None) -> List[Dict[str, Any]]:
        """new_state 的分布（注意：events 没有 state 列）。"""
        where, params = self._event_where(tr, entity_id, rooms, domains, False)
        sql = ("SELECT COALESCE(new_state, '') AS state_value, COUNT(*) AS c FROM events WHERE "
               + " AND ".join(where) + " GROUP BY state_value ORDER BY c DESC, state_value LIMIT ?")
        params.append(self._top_n(limit, 50))
        return [{"state": str(r.get("state_value") or ""), "count": int(r.get("c") or 0)}
                for r in self._execute(sql, params)]

    def action_counts(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                      domains: Any = None, limit: Any = None) -> List[Dict[str, Any]]:
        where, params = self._event_where(tr, entity_ids, rooms, domains, False)
        sql = ("SELECT COALESCE(action, '') AS action_value, COUNT(*) AS c FROM events WHERE "
               + " AND ".join(where) + " GROUP BY action_value ORDER BY c DESC, action_value LIMIT ?")
        params.append(self._top_n(limit, 50))
        return [{"action": str(r.get("action_value") or ""), "count": int(r.get("c") or 0)}
                for r in self._execute(sql, params)]

    def entity_action_counts(self, tr: Any, entity_ids: Any = None, rooms: Any = None,
                             domains: Any = None, limit: Any = None) -> List[Dict[str, Any]]:
        where, params = self._event_where(tr, entity_ids, rooms, domains, False)
        sql = ("SELECT entity_id, COALESCE(action, '') AS action_value, COUNT(*) AS c FROM events WHERE "
               + " AND ".join(where) + " GROUP BY entity_id, action_value LIMIT ?")
        params.append(self._top_n(limit, 5000))
        return [{"entity_id": str(r.get("entity_id") or ""),
                 "action": str(r.get("action_value") or ""),
                 "count": int(r.get("c") or 0)} for r in self._execute(sql, params)]

    def quality_counts(self, tr: Any, rooms: Any = None) -> Dict[str, int]:
        """字段完整性计数（一次聚合拿全）。"""
        where, params = self._event_where(tr, None, rooms, None, False)
        sql = ("SELECT COUNT(*) AS total, COUNT(DISTINCT day) AS active_days, "
               "SUM(CASE WHEN room IS NULL OR room = '' THEN 1 ELSE 0 END) AS empty_room, "
               "SUM(CASE WHEN domain IS NULL OR domain = '' THEN 1 ELSE 0 END) AS empty_domain, "
               "SUM(CASE WHEN entity_id IS NULL OR entity_id = '' THEN 1 ELSE 0 END) AS empty_entity, "
               "SUM(CASE WHEN new_state IS NULL OR new_state = '' THEN 1 ELSE 0 END) AS empty_state, "
               "SUM(CASE WHEN attrs_json IS NULL OR attrs_json = '' THEN 1 ELSE 0 END) AS empty_attrs, "
               "SUM(CASE WHEN action IS NULL OR action = '' THEN 1 ELSE 0 END) AS empty_action "
               "FROM events WHERE " + " AND ".join(where))
        rows = self._execute(sql, params)
        row = rows[0] if rows else {}
        keys = ("total", "active_days", "empty_room", "empty_domain",
                "empty_entity", "empty_state", "empty_attrs", "empty_action")
        return {k: int(row.get(k) or 0) for k in keys}

    def sample_rows(self, tr: Any, limit: Any = 500, rooms: Any = None) -> List[Dict[str, Any]]:
        """抽样原始行（供 attrs_json 可解析性检查）。"""
        where, params = self._event_where(tr, None, rooms, None, False)
        sql = ("SELECT ts, day, room, entity_id, domain, new_state, attrs_json FROM events WHERE "
               + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?")
        params.append(self._top_n(limit, 500))
        return self._execute(sql, params)

    # ------------------------------------------------- 旁挂依赖（规则表/排除表）
    def activity_rules(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """`activity_rules` 表（define_activity 的落库处，裁6 Q2=A 要求引擎真的读它）。

        表不存在/查询失败一律上抛（本层 fail-closed），由 service 层降级并在返回体的
        `rule_sources.activity_rules_error` 里留痕——静默回退成"没有规则"就是又一次
        把失败换成空结果。
        """
        sql = ("SELECT rule_id, name, room, tags_json, start_hour, end_hour, "
               "min_events, confidence, note, enabled FROM activity_rules")
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY name"
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql):
            tags = r.get("tags_json")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags or "[]")
                except (TypeError, ValueError) as exc:
                    # 规则仍按"无标签条件"运行（不把一次解析失败升级成整条查询外抛），
                    # 但标签条件静默消失必须留痕：否则一条规则的匹配面被改小却无人知晓。
                    self.log.warning("activity_rules rule_id=%s 的 tags_json 解析失败(%s)，"
                                     "本条规则按无标签运行",
                                     r.get("rule_id"), type(exc).__name__)
                    tags = []
            out.append({
                "rule_id": str(r.get("rule_id") or ""),
                "name": str(r.get("name") or ""),
                "room": str(r.get("room") or ""),
                "tags": [str(t) for t in (tags or []) if str(t).strip()],
                "start_hour": int(r.get("start_hour") or 0),
                "end_hour": int(r.get("end_hour") or 23),
                "min_events": int(r.get("min_events") or 1),
                "confidence": float(r.get("confidence") or 0.0),
                "note": str(r.get("note") or ""),
                "enabled": bool(r.get("enabled")),
            })
        return out

    def signal_exclusions(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        """`signal_exclusions` 表（学习策略 teach_signal kind='hard' 的落库处）。"""
        sql = ("SELECT exclusion_id, entity_id, scope, exclusion_type, reason, revoked "
               "FROM signal_exclusions")
        if not include_revoked:
            sql += " WHERE revoked=0"
        sql += " ORDER BY entity_id, scope"
        return [{
            "exclusion_id": str(r.get("exclusion_id") or ""),
            "entity_id": str(r.get("entity_id") or ""),
            "scope": str(r.get("scope") or "all"),
            "exclusion_type": str(r.get("exclusion_type") or "exclude"),
            "reason": str(r.get("reason") or ""),
            "revoked": bool(r.get("revoked")),
        } for r in self._execute(sql)]

    def excluded_entity_ids(self) -> Dict[str, Any]:
        """活动推断要硬排除的实体清单 + 来源计数（裁6 Q3 的「已排除 N 个实体」）。

        两个来源：
        1. `signal_exclusions` 里生效且 `exclusion_type='exclude'` 的行——
           `is_automation` / `not_automation` 是**分类标注**（告诉 agent 这实体是不是自动化），
           不是排除，误当排除会把正常设备从活动里抹掉；
        2. `config.excluded_entities`（采集侧的显式排除；注入的是原始 app Config 时可见）。
        """
        ids: List[str] = []
        sources: Dict[str, int] = {"signal_exclusions": 0, "config": 0}
        scopes: Dict[str, int] = {}
        try:
            for row in self.signal_exclusions():
                if row["exclusion_type"] != "exclude":
                    continue
                eid = row["entity_id"]
                if not eid or eid in ids:
                    continue
                ids.append(eid)
                sources["signal_exclusions"] += 1
                scopes[row["scope"]] = scopes.get(row["scope"], 0) + 1
        except Exception as exc:  # noqa: BLE001 - 排除表读不到不能让整条查询外抛
            self.log.warning("signal_exclusions 读取失败，本轮按无硬排除处理: %s", exc)
            sources["signal_exclusions_error"] = "%s: %s" % (type(exc).__name__, exc)
        for eid in (getattr(self.config, "excluded_entities", None) or []):
            text = str(eid or "").strip()
            if text and text not in ids:
                ids.append(text)
                sources["config"] += 1
        return {"entity_ids": sorted(ids), "count": len(ids),
                "sources": sources, "scopes": scopes}

    # ------------------------------------------------- behavior / perception
    def behavior_summary(self, tr: Any, rooms: Any = None) -> List[Dict[str, Any]]:
        """behavior_events 按 (room, trigger) 聚合。"""
        where, params = self._behavior_where(tr, rooms)
        sql = ("SELECT room, COALESCE(\"trigger\", '') AS trigger_value, COUNT(*) AS c, "
               "SUM(COALESCE(\"count\", 0)) AS person_total, "
               "AVG(confidence) AS avg_confidence, AVG(vlm_latency_ms) AS avg_latency, "
               "SUM(CASE WHEN status IS NULL OR status = 'ok' THEN 0 ELSE 1 END) AS error_count "
               "FROM behavior_events WHERE " + " AND ".join(where) +
               " GROUP BY room, trigger_value ORDER BY c DESC")
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "room": str(r.get("room") or ""),
                "trigger": str(r.get("trigger_value") or ""),
                "count": int(r.get("c") or 0),
                "person_total": int(r.get("person_total") or 0),
                "avg_confidence": float(r.get("avg_confidence") or 0.0),
                "avg_latency": float(r.get("avg_latency") or 0.0),
                "error_count": int(r.get("error_count") or 0),
            })
        return out

    def behavior_actions(self, tr: Any, rooms: Any = None, limit: Any = 20) -> List[Dict[str, Any]]:
        where, params = self._behavior_where(tr, rooms)
        sql = ("SELECT COALESCE(action, '') AS action_value, COUNT(*) AS c FROM behavior_events WHERE "
               + " AND ".join(where) + " GROUP BY action_value ORDER BY c DESC, action_value LIMIT ?")
        params.append(self._top_n(limit, 20))
        return [{"action": str(r.get("action_value") or ""), "count": int(r.get("c") or 0)}
                for r in self._execute(sql, params)]

    def behavior_hourly(self, tr: Any, rooms: Any = None) -> Dict[int, int]:
        where, params = self._behavior_where(tr, rooms)
        sql = ("SELECT CAST(substr(server_ts, 12, 2) AS INTEGER) AS hour_value, COUNT(*) AS c "
               "FROM behavior_events WHERE " + " AND ".join(where) + " GROUP BY hour_value")
        return {int(r.get("hour_value") or 0): int(r.get("c") or 0) for r in self._execute(sql, params)}

    def behavior_days(self, tr: Any, rooms: Any = None) -> Dict[str, int]:
        where, params = self._behavior_where(tr, rooms)
        sql = "SELECT day, COUNT(*) AS c FROM behavior_events WHERE " + " AND ".join(where) + " GROUP BY day ORDER BY day"
        return {str(r.get("day") or ""): int(r.get("c") or 0) for r in self._execute(sql, params)}

    def perception_summary(self, tr: Any, rooms: Any = None, kinds: Any = None,
                           limit: Any = 50) -> List[Dict[str, Any]]:
        """perception_events 按 (source, kind, room) 聚合。"""
        where, params = self._behavior_where(tr, rooms)
        self._add_in(where, params, "kind", kinds)
        sql = ("SELECT source, kind, COALESCE(room, '') AS room_value, COUNT(*) AS c, "
               "AVG(confidence) AS avg_confidence FROM perception_events WHERE "
               + " AND ".join(where) + " GROUP BY source, kind, room_value ORDER BY c DESC LIMIT ?")
        params.append(self._top_n(limit, 50))
        out: List[Dict[str, Any]] = []
        for r in self._execute(sql, params):
            out.append({
                "source": str(r.get("source") or ""),
                "kind": str(r.get("kind") or ""),
                "room": str(r.get("room_value") or ""),
                "count": int(r.get("c") or 0),
                "avg_confidence": float(r.get("avg_confidence") or 0.0),
            })
        return out
    # ------------------------------------------------------------------
    # api.py 兼容接口
    # ------------------------------------------------------------------
    def list_entities(self) -> List[Any]:
        """api.py 用此方法构建 EntityResolver。

        P2：之前直接复用 entity_catalog()，但它返回的字段只有
        entity_id/room/domain/last_ts/total，根本没有 friendly_name/category/unit，
        导致 resolver 里所有实体的友好名和单位全是空串。
        改为取每个实体最新一条事件的 attrs_json 解析 friendly_name / unit。

        性能修复：原写法用相关子查询（每行两次全表扫描），events 表达百万行时
        启动需几十分钟。改为 JOIN + GROUP BY，一次扫描完成分组聚合。
        """
        from .models import EntityInfo
        sql = ("SELECT e.entity_id, e.room, e.domain, e.attrs_json, c.total "
               "FROM events e "
               "JOIN (SELECT entity_id, MAX(rowid) AS max_rowid, COUNT(*) AS total "
               "      FROM events GROUP BY entity_id) c "
               "ON e.rowid = c.max_rowid "
               "ORDER BY e.entity_id LIMIT ?")
        rows = self._execute(sql, (self._top_n(None, 5000),))
        out = []
        for r in rows:
            attrs = _load_attrs(r.get("attrs_json"))
            entity_id = str(r.get("entity_id") or "")
            friendly = attrs.get("friendly_name") or entity_id
            unit = attrs.get("unit_of_measurement") or attrs.get("unit") or ""
            try:
                out.append(EntityInfo(
                    entity_id=entity_id,
                    friendly_name=str(friendly),
                    room=str(r.get("room") or ""),
                    domain=str(r.get("domain") or ""),
                    category="",  # category 由 resolver.resolve 按 domain 推，不在此落库
                    unit=str(unit),
                ))
            except Exception as exc:  # noqa: BLE001 - 单行构造失败不该掀掉整张目录
                # 但"少了一个实体"必须留痕：静默 continue 会让目录里凭空缺项，
                # 消费方只会看到实体变少，永远查不到是哪一行、为什么。
                self.log.warning("list_entities 跳过 entity_id=%s 的行：%s: %s",
                                 entity_id, type(exc).__name__, exc)
                continue
        return out

    def invalidate(self) -> None:
        """api.py 调用此方法使缓存失效。StoreRepository 无缓存，空操作。"""
        pass


# ----------------------------------------------------------------------
# 工厂与基类（api.py 兼容）
# ----------------------------------------------------------------------
class BaseRepository:
    """仓储基类（api.py 类型标注用）。"""
    pass


def build_repository(store: Any, config: Any) -> StoreRepository:
    """从生产 Store 构建 StoreRepository（api.py 工厂函数）。"""
    return StoreRepository(store, config)
