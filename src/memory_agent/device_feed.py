"""设备事件 feed —— DCD 2026-10-01 §二（Q1=B / Q2 三项 / Q3=(i)）裁定落地。

背景：引擎的词汇表只有感知 kind（face_known/human/…），23 条设备序列候选全部被
``rule_lifecycle.build_condition`` 的 ``engine_feed_gap`` 如实拒绝。本模块按 **Q1=B**
建「独立批量扫描器」：按周期读 ``events`` 表，把设备状态变化聚成引擎可见的
``kind="device"`` 事件再喂给 ``match_event``——不压在感知链路同进程，对在线服务
零压力；代价是响应滞后一个轮询周期（裁定已接受：候选规则是「建议」不是「实时触发器」）。

Q2 三项降噪门槛（**裁定值，不是开发拍的**）：
1. 按 **domain** 放行 ``binary_sensor/switch/light/cover/climate/media_player``，
   排除占 ≈91% 流量的 ``sensor``；
2. 只接受**二元状态变化**（on/off、open/closed），数值型 sensor 不进 feed；
3. count 触发保持 ``window_seconds=60 / min_count=3``——本模块把这两个值作为
   设备类候选晋升时的触发策略写进规则（此前 ``trigger`` 从不入库，见
   ``rule_engine`` 的 ``trigger_json``），否则「保持默认值」只是一句空话。

Q3=(i)「通道建好、推进等人」：反馈面为 0 时通道结构性停在 dry_run，所以
总开关默认关（``device_feed_enabled``），开启后首轮亦默认 dry_run
（``device_feed_dry_run``）——照常匹配并记录触发历史（``dry_run=1``），不派发副作用。

保留期（裁定 §Q3.1）：``rule_trigger_history`` 保留 7 天 + 上限 10 万行，由
``purge_trigger_history`` 裁剪；裁的是本服务自己写入的观测记录，不是用户数据。
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from .insights.utils import TAG_RULES, tags_of
from .rule_engine import DEVICE_EVENT_KIND
from .store import now_local

logger = logging.getLogger("memory_agent.device_feed")

# ── Q2 裁定值 ──────────────────────────────────────────────────────────────

# 裁定 1：domain 白名单（排除 sensor）
FEED_DOMAINS: tuple[str, ...] = (
    "binary_sensor", "switch", "light", "cover", "climate", "media_player",
)
# 裁定 2：只收二元状态变化。HA 里成对出现的二值态就这么几组，其余（温度、湿度、
# "playing"、属性变化）是「读数」不是「翻转」，不进 feed。
BINARY_STATES: frozenset[str] = frozenset({"on", "off", "open", "closed"})
# 裁定 3：count 触发默认窗口。写进晋升规则的 trigger，而不是在 feed 里另判一次。
WINDOW_SECONDS = 60
MIN_COUNT = 3
DEVICE_TRIGGER: dict[str, Any] = {
    "type": "count",
    "window_seconds": WINDOW_SECONDS,
    "min_count": MIN_COUNT,
}

# feed 能产出的 tag 词表 = TAG_RULES 全集（与 insights 的标签口径同源，不另立词表）。
# 每个 tag 由哪个白名单域产出（排查「候选晋升了但永不触发」时对照）：
#   presence  ← binary_sensor(motion/occupancy/presence)
#   door      ← binary_sensor(contact/door/window)
#   light     ← light / media_player / switch（命名含「灯」）
#   cover     ← cover
#   climate   ← climate
#   appliance ← switch/socket/plug
#   media     ← media_player
#   computer  ← switch/light（entity_id 含 pc/computer/nas 等）
# 「词表里有」不等于「本家白名单流量里出现过」——后者由 ``observed_tags`` 摊开成读数。
FEED_TAGS: frozenset[str] = frozenset(TAG_RULES)

# 裁定 §Q3.1：触发历史保留期
TRIGGER_RETENTION_DAYS = 7
TRIGGER_MAX_ROWS = 100_000

# 单次扫描最多读多少事件：query_events 的 SQL 上限就是 5000。白名单生效后 ≈2k/天
# （9% 有效流量）→ 小时级窗口远低于此；触顶时如实报 truncated，不静默截断。
QUERY_LIMIT = 5000

_feed: Optional["DeviceEventFeed"] = None


def parse_wall(text: Any) -> Optional[datetime]:
    """events 表的 ts（家庭墙钟 ISO 串）→ naive datetime。"""
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T")[:19])
    except ValueError:
        return None


def wall_to_epoch(wall: datetime, tz_offset_hours: float = 8.0) -> float:
    """家庭墙钟（naive）→ 真实 epoch 秒。

    ``store.now_local`` 的口径是「带 tz 的 now 抹掉 tzinfo」，所以逆运算必须走
    UTC 日历时再减偏移。用 ``wall.timestamp()`` 会按**机器时区**解释这个 naive 值：
    容器 tz 不是 +8 时所有事件的 epoch 整体平移，单条判定看不出来，
    但 count 窗口（60 秒）与跨批拼接的 ``_event_windows`` 会因此错判。
    """
    return calendar.timegm(wall.timetuple()) - float(tz_offset_hours) * 3600


def friendly_names(config: Any) -> dict[str, str]:
    """从 ``config.rooms`` 取 entity_id → friendly_name（标签推断的名称输入）。

    与 ``activity_inference`` 同源：events 表没有名称列，``attrs_json`` 是差分属性
    （``_diff_attrs`` 只留变化项），friendly_name 基本不在里面，所以口径只能来自 config。
    """
    out: dict[str, str] = {}
    rooms = getattr(config, "rooms", None) or {}
    for payload in rooms.values():
        ents = (payload or {}).get("entities") or {}
        for eid, info in ents.items():
            if isinstance(info, dict):
                out[eid] = info.get("friendly_name") or ""
    return out


def binary_change(row: dict) -> bool:
    """裁定 2 的判据：只有「二元态 ↔ 二元态」的真实翻转才算事件。

    三条都必过：两端都在 ``BINARY_STATES`` 内、两端不同（同值重报不是变化）、
    ``new_state`` 非空。任何一条不满足都**丢弃而不是放行**——白名单的意义就是少喂，
    判据含糊等于没判。
    """
    old = str(row.get("old_state") or "").strip().lower()
    new = str(row.get("new_state") or "").strip().lower()
    if not new or old == new:
        return False
    return old in BINARY_STATES and new in BINARY_STATES


class DeviceEventFeed:
    """设备事件批量扫描器（Q1=B）。"""

    def __init__(self, store: Any, engine: Any, config: Any = None, *,
                 domains: tuple[str, ...] = FEED_DOMAINS,
                 names: dict[str, str] | None = None):
        self.store = store
        self.engine = engine
        self.config = config
        self.domains = tuple(domains)
        self._names = names
        self.tz_offset = float(getattr(store, "tz_offset_hours", 8.0) or 8.0)
        # 增量扫描的水位线（墙钟 ISO）。首轮从 interval 前起算，不回捞历史。
        self._watermark = ""

    # ── 输入构造 ────────────────────────────────────────────────────────────

    def _name_map(self) -> dict[str, str]:
        if self._names is None:
            self._names = friendly_names(self.config) if self.config is not None else {}
        return self._names

    def to_event(self, row: dict, names: dict[str, str]) -> dict:
        """events 行 → 引擎事件字典。

        ``tags`` 必须是 **list**：``_log_trigger`` 把整个 event ``json.dumps`` 落库，
        ``set`` 会让它抛 TypeError 并只返回 False——触发历史静默写失败，观察期判据
        与误报计数就全没了。
        """
        eid = str(row.get("entity_id") or "")
        ts = str(row.get("ts") or "")
        return {
            "kind": DEVICE_EVENT_KIND,
            "device": True,
            "entity_id": eid,
            "domain": str(row.get("domain") or (eid.split(".")[0] if eid else "")),
            "state": str(row.get("new_state") or "").strip().lower(),
            "old_state": str(row.get("old_state") or "").strip().lower(),
            "action": str(row.get("action") or ""),
            "tags": sorted(tags_of(eid, names.get(eid, ""))),
            "room": str(row.get("room") or ""),
            "person": str(row.get("person") or ""),
            "ts": ts,
            "server_ts": ts,
            "confidence": 1.0,  # 设备状态是确定读数，不是概率推断
        }

    def observed_tags(self, start: str, end: str) -> dict:
        """窗口内白名单流量里**真实出现过**的 tag（只读）。

        用途：候选的 tag 在 FEED_TAGS 里、却从未在本家的白名单流量中出现时，
        规则会照常存在但永不命中。这条把「本家有没有这个信号」变成可查读数，
        而不是让通道假装它一定有料。
        """
        rows = self.store.query_events(
            start=start, end=end, domains=list(self.domains),
            order="asc", limit=QUERY_LIMIT,
        )
        names = self._name_map()
        seen: set[str] = set()
        for row in rows:
            if not binary_change(row):
                continue
            eid = str(row.get("entity_id") or "")
            seen |= set(tags_of(eid, names.get(eid, "")))
        return {
            "window": {"start": start, "end": end},
            "scanned": len(rows),
            "domains": list(self.domains),
            "tags": sorted(seen),
            # 读满上限说明窗口没扫全，标签集合可能缺项——把这件事显式报出来，
            # 而不是给一个恒真的 ok 让调用方以为"有料/没料"是可信的。
            "complete": len(rows) < QUERY_LIMIT,
        }

    # ── 扫描 ────────────────────────────────────────────────────────────────

    def window(self, start: str | None = None, end: str | None = None) -> tuple[str, str]:
        now = now_local(self.tz_offset)
        end_iso = end or now.isoformat(timespec="seconds", sep="T")
        start_iso = start or self._watermark or (
            now - timedelta(seconds=self.interval_seconds())
        ).isoformat(timespec="seconds", sep="T")
        return start_iso, end_iso

    def run_once(self, start: str | None = None, end: str | None = None,
                 dry_run: bool | None = None) -> dict:
        """跑一个窗口的「设备事件 → 引擎匹配」。

        默认窗口是 ``[上次水位, now]``：批量扫描器没有断点水位就必然二选一——
        要么漏事件，要么每轮重复喂同一批（重复喂会让 count 触发虚高，
        同一个事件被数两次就到 3 次/60 秒了）。
        """
        start_iso, end_iso = self.window(start, end)
        forced_dry = self.dry_run() if dry_run is None else bool(dry_run)

        rows = self.store.query_events(
            start=start_iso, end=end_iso, domains=list(self.domains),
            order="asc", limit=QUERY_LIMIT,
        )
        names = self._name_map()
        kept = [r for r in rows if binary_change(r)]
        stats: dict[str, Any] = {
            "window": {"start": start_iso, "end": end_iso},
            "dry_run": forced_dry,
            "scanned": len(rows),
            "kept": len(kept),
            "dropped_non_binary": len(rows) - len(kept),
            "matched": 0,
            "dispatched": 0,
            "logged_only": 0,
            "truncated": len(rows) >= QUERY_LIMIT,
            "errors": [],
        }
        for row in kept:
            event = self.to_event(row, names)
            dt = parse_wall(event["ts"])
            now_ts = wall_to_epoch(dt, self.tz_offset) if dt else None
            try:
                triggered = self.engine.match_event(event, now_ts=now_ts)
            except Exception as exc:  # noqa: BLE001 - 单条事件不得打断整轮扫描
                logger.error("[DeviceFeed] 匹配失败 %s: %s", event["entity_id"], exc)
                stats["errors"].append(f"{event['entity_id']}: {exc}")
                continue
            if not triggered:
                continue
            stats["matched"] += len(triggered)
            for rule in triggered:
                try:
                    res = self.engine.execute_action(rule, event, dry_run_override=forced_dry)
                except Exception as exc:  # noqa: BLE001
                    logger.error("[DeviceFeed] 动作失败 %s: %s", rule.get("rule_id"), exc)
                    stats["errors"].append(f"{rule.get('rule_id')}: {exc}")
                    continue
                if res.get("dry_run"):
                    stats["logged_only"] += 1
                elif res.get("ok"):
                    stats["dispatched"] += 1

        # ok 只能来自实测：本轮吞过异常，或读满上限被截断（窗口没读完），都不算成功。
        stats["ok"] = not stats["errors"] and not stats["truncated"]

        # ── 水位线：只往前推到「这一轮真的读完」的下一秒 ──────────────────
        # 两个都必须修：
        # 1) ``query_events`` 的 ``ts BETWEEN ? AND ?`` 两端都是**闭**区间，停在 ``end``
        #    上就等于下一轮的起点和这一轮的终点重合——正好落在边界那一秒的事件会被喂两遍。
        #    count 触发（60 秒 3 次）全靠事件计数活着，同一事件数两次就是凭空多一票，
        #    裁定 §Q2 的门槛被架空（本模块 docstring 自己写过这个风险，代码没跟上）。
        # 2) 读满 ``QUERY_LIMIT`` 被截断时，窗口尾部还有一整段没读，直接推到 ``end``
        #    就是静默丢事件；此时推到「最后读到的那条 + 1 秒」，下一轮接着读。
        # ts 是秒粒度（生产 1,028,140 行全部 len=19、无小数秒，只读实测），+1 秒不留缝隙。
        if stats["truncated"]:
            resume_at = parse_wall(str(rows[-1].get("ts") or "")) if rows else None
            stats["pending_tail"] = True
        else:
            resume_at = parse_wall(end_iso)
            stats["pending_tail"] = False
        if resume_at is not None:
            self._watermark = (resume_at + timedelta(seconds=1)).isoformat(
                timespec="seconds", sep="T")
        stats["watermark"] = self._watermark
        logger.info("[DeviceFeed] %s→%s：扫描 %s，二元变化 %s，命中 %s，派发 %s，仅记录 %s（dry_run=%s，水位 %s）",
                    start_iso, end_iso, stats["scanned"], stats["kept"], stats["matched"],
                    stats["dispatched"], stats["logged_only"], forced_dry, self._watermark)
        return stats

    # ── 配置读取（getattr 兜底：单测可传裸对象）────────────────────────────
    # 三个读法都是公开的：runtime 的周期任务要在**每轮**重读它们——改配置生效
    # 不该等一次重启，也不该让调用方伸手进私有方法。

    def interval_seconds(self) -> int:
        return max(60, int(getattr(self.config, "device_feed_interval_seconds", 3600) or 3600))

    def dry_run(self) -> bool:
        return bool(getattr(self.config, "device_feed_dry_run", True))

    def enabled(self) -> bool:
        return bool(getattr(self.config, "device_feed_enabled", False))

    # ── 保留期裁剪（裁定 §Q3.1：7 天 + 10 万行）────────────────────────────

    def purge_trigger_history(self) -> dict:
        """裁过期与超额的触发历史（口径在 ``store.purge_rule_triggers``）。

        两个上限都要执行：只按天数裁，一场误报风暴一天就能打满 10 万行，
        7 天保留期对它没有约束力；只按行数裁，老规则的低频历史会无限占盘。
        """
        cutoff = (now_local(self.tz_offset)
                  - timedelta(days=TRIGGER_RETENTION_DAYS)).isoformat(sep="T")
        res = self.store.purge_rule_triggers(cutoff, TRIGGER_MAX_ROWS)
        if not isinstance(res, dict):
            return {"ok": False, "error": f"store.purge_rule_triggers 返回异常: {res!r}",
                    "retention_days": TRIGGER_RETENTION_DAYS, "max_rows": TRIGGER_MAX_ROWS,
                    "cutoff": cutoff}
        res.setdefault("retention_days", TRIGGER_RETENTION_DAYS)
        res.setdefault("max_rows", TRIGGER_MAX_ROWS)
        # store 的返回体只给计数、没有 ok 字：成功口径由本包装统一补上。
        # 不补的话调用方判 ``res["ok"]`` 会把每一次成功读成失败（缺键即 falsy）。
        res.setdefault("ok", True)
        return res


def get_device_feed(store: Any, engine: Any, config: Any = None) -> DeviceEventFeed:
    """进程内单例：水位线跟着实例走，重复构造等于每轮都从头回捞同一批事件。"""
    global _feed
    if _feed is None or _feed.store is not store:
        _feed = DeviceEventFeed(store, engine, config)
    return _feed
