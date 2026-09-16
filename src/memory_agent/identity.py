"""实体身份与健康层（v0.2）

为什么需要它
------------
HA 的 ``entity_id`` 会因集成重登（小米 miot 退出重登）、双集成并存
（``xiaomi home`` + ``xiaomi miot`` 同时接入同一台电视）、设备替换而**漂移或倍增**，
而行为洞察模板原本写死 ``entity_id``，一旦漂移就静默查不到数据（hdmi3 事故根因）。

本模块把「物理设备」抽象为 **LogicalDevice（逻辑设备）**，对外以稳定的
``stable_id`` / ``display_name`` 为契约。``IdentityReconciler`` 定期用
``ha_client.discover_entities()`` 拿到的实体注册表做对账，完成三件事：

* **A1 重登漂移自愈**：原实体消失、但出现同名同类新实体 → 自动改指主实体。
* **A2 双集成冗余全自动合并**：同 area + 同 domain + 名称高相似 → 合并为一个
  逻辑设备并按规则选主（查询不重复计数、用户无感）。跨 area 的高相似**只建议
  不自动合并**，降低误并率。
* **A3 失效 / 离线清单**：长期不可见的实体标记 ``stale``，供上层告警与展示，
  不再静默产出错误洞察。

解析入口 ``IdentityService.resolve()`` **兼容裸 ``entity_id``**，保证存量模板
在灰度过渡期不受影响；解析失败时返回原因（``unresolved`` / ``stale``），
由调用方显式处理，绝不静默。

对账是**后台低频只读任务**，不阻塞在线查询；解析结果走内存缓存 + TTL。
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .store import now_local

# 名称相似度阈值：≥ 此值且同 area + 同 domain 才判定为同一物理设备
SIMILARITY_THRESHOLD = 0.85
# 加固：同 room 同 domain 的实体，若归一化名称共享公共前缀 ≥ 此字符数，也判定为同一
# 物理设备并合并。用于同一台电视在 HA 里被拆成多个 media_player 实体的场景
# （如「lidicn的电视电视」与「lidicn的电视播放控制」）。值设较大以抑制误并。
PREFIX_MERGE_MIN = 6
# 超过该天数未在 HA 注册表出现 → 标记 stale
STALE_DAYS = 30
# 解析结果缓存 TTL（秒）
CACHE_TTL_SECONDS = 300
# 视为「离线/不可信」的 HA 状态
OFFLINE_STATES = {"unavailable", "unknown", "none", ""}
# 设备状态字段里代表「当前在线」之外的取值（off 也是设备存在，仅 unavailable 才算失联）

_CJK = r"\u4e00-\u9fff"


# ── 归一化与相似度 ──────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """归一化设备名：小写、去掉空白与标点，仅保留字母数字与中日韩字符。"""
    s = (name or "").strip().lower()
    return re.sub(rf"[^0-9a-z{_CJK}]+", "", s)


def slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(rf"[^0-9a-z{_CJK}]+", "_", s)
    return s.strip("_")


def make_stable_id(domain: str, friendly_name: str) -> str:
    """逻辑设备稳定主键：``域名__归一化名``（永不因 entity_id 漂移而改变）。"""
    n = normalize_name(friendly_name)
    return f"{domain}__{n}" if n else domain


def similarity(a: str, b: str) -> float:
    """归一化后的名称相似度（0~1），用于 A1 重匹配与 A2 冗余合并。"""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _common_prefix_len(a: str, b: str) -> int:
    """两个字符串的最长公共前缀长度（按字符计，用于加固合并判定）。"""
    n = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            n += 1
        else:
            break
    return n


def looks_like_entity_id(ref: str) -> bool:
    """是否形如 ``domain.object_id`` 的裸实体 ID（兼容路径，不经身份层）。"""
    s = (ref or "").strip()
    return "." in s and not s.startswith(".")


def _parse_iso(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


# ── 数据模型 ────────────────────────────────────────────────────────────────

@dataclass
class LogicalDevice:
    """逻辑设备：一个物理设备 ↔ 多个可能漂移的 HA 实体。"""

    stable_id: str
    display_name: str = ""
    device_class: str = ""          # 归一化设备类（此处用 HA domain）
    primary_entity: str = ""        # 当前选主结果，查询解析用
    candidates: list[dict] = field(default_factory=list)
    provenance: str = "discovered"  # discovered|auto-merged|auto-remapped|user-pinned
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "stable_id": self.stable_id,
            "display_name": self.display_name,
            "device_class": self.device_class,
            "primary_entity": self.primary_entity,
            "candidates": self.candidates,
            "provenance": self.provenance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> "LogicalDevice":
        return cls(
            stable_id=row.get("stable_id") or "",
            display_name=row.get("display_name") or "",
            device_class=row.get("device_class") or "",
            primary_entity=row.get("primary_entity") or "",
            candidates=row.get("candidates") or [],
            provenance=row.get("provenance") or "discovered",
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
        )


# ── 解析服务 ────────────────────────────────────────────────────────────────

class IdentityService:
    """逻辑设备名 → 当前 entity_id 的唯一解析入口。"""

    def __init__(self, store, config: Any = None, tz_offset_hours: float = 8.0):
        self.store = store
        self.config = config
        self.tz_offset_hours = tz_offset_hours
        self._cache: dict[str, tuple[float, list[str], str | None]] = {}

    # ── 解析 ────────────────────────────────────────────────────────────────

    def resolve(self, ref: str) -> tuple[list[str], str | None]:
        """把逻辑引用解析为按优先级排序的 entity_id 列表。

        返回 ``(entity_ids, None)`` 成功；``([], reason)`` 失败，
        ``reason`` ∈ ``empty`` / ``unresolved`` / ``stale``。

        兼容：``ref`` 形如 ``domain.object`` 时原样返回，不经身份层，
        保证存量的裸 ``entity_id`` 模板继续可用（灰度过渡）。
        """
        ref = (ref or "").strip()
        if not ref:
            return [], "empty"
        if looks_like_entity_id(ref):
            return [ref], None

        cached = self._cache_get(ref)
        if cached is not None:
            return cached

        dev = self._find_device(ref)
        if not dev:
            self._cache_put(ref, [], "unresolved")
            return [], "unresolved"

        usable: list[str] = []
        primary = (dev.get("primary_entity") or "").strip()
        if primary and self._health_allows(primary):
            usable.append(primary)
        for cand in dev.get("candidates") or []:
            eid = (cand.get("entity_id") or "").strip()
            if not eid or eid in usable:
                continue
            if cand.get("state") == "disabled":
                continue
            if self._health_allows(eid):
                usable.append(eid)

        if not usable:
            self._cache_put(ref, [], "stale")
            return [], "stale"
        self._cache_put(ref, usable, None)
        return usable, None

    def resolve_one(self, ref: str) -> str | None:
        """取主实体；不可解析返回 None。"""
        eids, _reason = self.resolve(ref)
        return eids[0] if eids else None

    def _health_allows(self, entity_id: str) -> bool:
        """只有「最近一次对账确认在线」的实体参与解析。

        ``unknown``（短暂失联）与 ``stale``（长期失效）都不参与，避免把已下线的
        实体当作故障转移候选而算出空/旧数据。无健康记录（尚未对账过）时放行，
        保证身份层未就绪时不阻断既有行为。
        """
        try:
            row = self.store.get_device_health(entity_id)
        except Exception:
            return True
        if not row:
            return True
        return (row.get("state") or "active") == "active"

    def _find_device(self, ref: str) -> dict | None:
        try:
            dev = self.store.get_logical_device(ref)
        except Exception:
            dev = None
        if dev:
            return dev
        target = normalize_name(ref)
        if not target:
            return None
        try:
            devices = self.store.list_logical_devices()
        except Exception:
            return None
        for d in devices:
            if normalize_name(d.get("display_name") or "") == target:
                return d
            sid = d.get("stable_id") or ""
            if normalize_name(sid) == target:
                return d
            # 加固：stable_id 形如 ``domain__<human-suffix>``（例
            # ``media_player__lidicn的电视电视``），去掉 ``domain__`` 前缀后也能当作
            # 人类友好的逻辑名匹配。模板用 logical_id="lidicn的电视电视" 引用合并后的电视，
            # 但真实 stable_id 带 domain 前缀，原名不匹配导致解析失败、自愈静默失效
            # （P11：旧「hdmi3 又挂了」根因之一）。只做精确全后缀匹配，避免「电视」等
            # 过短词误命中多设备。
            if "__" in sid:
                suffix = sid.split("__", 1)[1]
                if normalize_name(suffix) == target:
                    return d
        return None

    # ── 缓存 ────────────────────────────────────────────────────────────────

    def _cache_get(self, ref: str) -> tuple[list[str], str | None] | None:
        hit = self._cache.get(ref)
        if not hit:
            return None
        ts, eids, reason = hit
        if time.time() - ts > CACHE_TTL_SECONDS:
            self._cache.pop(ref, None)
            return None
        return eids, reason

    def _cache_put(self, ref: str, eids: list[str], reason: str | None) -> None:
        self._cache[ref] = (time.time(), eids, reason)

    def invalidate_cache(self) -> None:
        """对账写库后调用，避免解析继续命中旧映射。"""
        self._cache.clear()

    # ── 查询辅助 ────────────────────────────────────────────────────────────

    def list_devices(self) -> list[dict]:
        return self.store.list_logical_devices()

    def list_unhealthy(self, state: str = "stale") -> list[dict]:
        return self.store.list_device_health(state)


# ── 对账 ────────────────────────────────────────────────────────────────────

class IdentityReconciler:
    """用 HA 实体注册表对账，维护逻辑设备映射与健康状态。"""

    def __init__(
        self,
        service: IdentityService,
        ha_getter: Callable[[], Any],
        templates_getter: Callable[[], Any] | None = None,
        stale_days: int = STALE_DAYS,
        threshold: float = SIMILARITY_THRESHOLD,
        tz_offset_hours: float = 8.0,
    ):
        self.service = service
        self.store = service.store
        self.ha_getter = ha_getter
        self.templates_getter = templates_getter
        self.stale_days = stale_days
        self.threshold = threshold
        self.tz_offset_hours = tz_offset_hours
        self._last_seen: set[str] = set()

    # ── 主流程 ──────────────────────────────────────────────────────────────

    def reconcile(self, bind_templates: bool = True) -> dict:
        """执行一次对账。返回统计字典。

        HA 不可达时不写库、不误判失效（只返回 ``ha_unreachable``），
        避免把全网设备误标 stale。
        """
        ha = self.ha_getter() if self.ha_getter else None
        if ha is None:
            return {"ok": False, "error": "HA 客户端不可用", "ha_unreachable": True}
        try:
            catalog = ha.discover_entities()
        except Exception as exc:
            return {"ok": False, "error": f"读取 HA 实体失败: {exc}", "ha_unreachable": True}
        if not isinstance(catalog, dict) or not catalog.get("ok"):
            return {
                "ok": False,
                "error": f"HA 实体发现失败: {(catalog or {}).get('error', '未知')}",
                "ha_unreachable": True,
            }

        entities = self._flatten(catalog)
        seen = {e["entity_id"] for e in entities}
        self._last_seen = seen
        references, ref_counts = self._collect_references()
        now_iso = now_local(self.tz_offset_hours).isoformat()

        # 1) 刷新在线实体的健康记录
        for e in entities:
            self.store.upsert_device_health(
                e["entity_id"],
                state="active",
                last_seen=now_iso,
                referenced=1 if e["entity_id"] in references else 0,
            )

        # 2) A2 全自动合并：同 room + 同 domain + 名称高相似聚为一类
        clusters = self._cluster(entities)
        devices = 0
        merged = 0
        for cl in clusters:
            stable_id = self._stable_id_for(cl)
            dev = self._build_device(stable_id, cl, ref_counts)
            if len(cl["members"]) > 1:
                dev["provenance"] = "auto-merged"
                merged += 1
            self.store.upsert_logical_device(dev)
            for m in cl["members"]:
                self.store.upsert_device_health(m["entity_id"], stable_id=stable_id)
            devices += 1

        # 3) A1 重匹配：未被本轮聚类覆盖的存量设备
        remapped = self._remap_orphans(entities, seen, clusters, ref_counts)

        # 4) A3 失效标记（返回的是「本轮发生变化」的实体，供推送/告警）
        changes = self._mark_stale(seen)
        stale = len([c for c in changes if c["to"] == "stale"])

        # 5) 模板自愈：写死 entity_id 但该实体已失效 → 改写为逻辑引用
        bound = self.bind_stale_templates() if bind_templates else 0

        self.service.invalidate_cache()
        return {
            "ok": True,
            "entities": len(entities),
            "devices": devices,
            "merged": merged,
            "remapped": remapped,
            "stale": stale,
            "health_changes": changes,
            "bound": bound,
        }

    # ── 步骤实现 ────────────────────────────────────────────────────────────

    @staticmethod
    def _flatten(catalog: dict) -> list[dict]:
        """把 discover_entities 的 rooms 结构摊平为实体列表。"""
        out: list[dict] = []
        for room, payload in (catalog.get("rooms") or {}).items():
            if not isinstance(payload, dict):
                continue
            for entity_id, info in (payload.get("entities") or {}).items():
                if not isinstance(info, dict):
                    continue
                name = info.get("name") or entity_id
                out.append(
                    {
                        "entity_id": entity_id,
                        "name": name,
                        "domain": (info.get("domain") or entity_id.split(".")[0]),
                        "room": room,
                        "state": str(info.get("state") or "").lower(),
                    }
                )
        return out

    def _cluster(self, entities: list[dict]) -> list[dict]:
        """按 (room, domain) 分组后再按名称相似度聚类（跨 room 不合并）。

        加固：同 room 同 domain 的实体，若归一化名称共享较长公共前缀
        （≥ ``PREFIX_MERGE_MIN`` 字符，如「lidicn的电视电视」与「lidicn的电视播放控制」
        实为同一台电视的多个 HA 实体），也并入同一逻辑设备，避免同一物理设备的
        多个 HA 实体被拆成多个设备、导致按设备查询漏算或模板指向错误实体。
        """
        clusters: list[dict] = []
        for e in sorted(entities, key=lambda x: (x["room"], x["domain"], x["entity_id"])):
            merged = False
            for cl in clusters:
                if (
                    cl["room"] == e["room"]
                    and cl["domain"] == e["domain"]
                    and similarity(cl["name"], e["name"]) >= self.threshold
                ):
                    cl["members"].append(e)
                    merged = True
                    break
            if not merged:
                for cl in clusters:
                    if (
                        cl["room"] == e["room"]
                        and cl["domain"] == e["domain"]
                        and _common_prefix_len(
                            normalize_name(cl["name"]), normalize_name(e["name"])
                        )
                        >= PREFIX_MERGE_MIN
                    ):
                        cl["members"].append(e)
                        merged = True
                        break
            if not merged:
                clusters.append(
                    {
                        "room": e["room"],
                        "domain": e["domain"],
                        "name": e["name"],
                        "members": [e],
                    }
                )
        return clusters

    def _match_existing_device(self, cluster: dict) -> dict | None:
        """找与当前聚类同一物理设备的存量逻辑设备。

        判定条件：同 domain + 同 room + 名称高相似。这是 A1「重登后 friendly_name
        微调」的主路径——复用原 ``stable_id``，避免每次对账都新造一个逻辑设备。
        **跨 room 一律不匹配**，防止把两个房间的同名设备误并。
        """
        best: dict | None = None
        best_score = 0.0
        for dev in self.store.list_logical_devices():
            if (dev.get("device_class") or "") != cluster["domain"]:
                continue
            rooms = {
                (c.get("room") or "")
                for c in (dev.get("candidates") or [])
                if isinstance(c, dict)
            }
            if rooms and cluster["room"] not in rooms:
                continue
            score = similarity(dev.get("display_name") or "", cluster["name"])
            for cand in dev.get("candidates") or []:
                if isinstance(cand, dict):
                    score = max(score, similarity(cand.get("name") or "", cluster["name"]))
            if score >= self.threshold and score > best_score:
                best, best_score = dev, score
        return best

    def _stable_id_for(self, cluster: dict) -> str:
        """稳定主键：优先复用已存在的同类设备，否则新建；不同房间的同名设备追加 room 后缀。"""
        matched = self._match_existing_device(cluster)
        if matched:
            return matched.get("stable_id") or ""
        base = make_stable_id(cluster["domain"], cluster["name"])
        existing = self.store.get_logical_device(base)
        if not existing:
            return base
        rooms = {
            (c.get("room") or "")
            for c in (existing.get("candidates") or [])
            if isinstance(c, dict)
        }
        if rooms and cluster["room"] not in rooms:
            return f"{base}__{slugify(cluster['room'])}"
        return base

    def _build_device(self, stable_id: str, cluster: dict, ref_counts: dict) -> dict:
        existing = self.store.get_logical_device(stable_id) or {}
        known = {
            (c.get("entity_id") or ""): c
            for c in (existing.get("candidates") or [])
            if isinstance(c, dict)
        }
        now_iso = now_local(self.tz_offset_hours).isoformat()
        candidates: list[dict] = []
        for m in cluster["members"]:
            prev = known.get(m["entity_id"]) or {}
            candidates.append(
                {
                    "entity_id": m["entity_id"],
                    "name": m["name"],
                    "room": m["room"],
                    "domain": m["domain"],
                    "priority": int(prev.get("priority") or 0),
                    "state": "active" if m["state"] not in OFFLINE_STATES else "offline",
                    "last_seen": now_iso,
                    "last_data_ts": prev.get("last_data_ts") or "",
                    "provenance": prev.get("provenance") or "discovered",
                }
            )
        # 保留历史候选（已下线）以便审计，并让「模板自愈」能定位到旧的 entity_id
        member_ids = {m["entity_id"] for m in cluster["members"]}
        for prev_id, prev in known.items():
            if prev_id in member_ids or not isinstance(prev, dict):
                continue
            kept = dict(prev)
            kept["state"] = "offline"
            candidates.append(kept)
        # 用户钉选优先：不覆盖 pinned 的主实体
        pinned = [
            c
            for c in (existing.get("candidates") or [])
            if isinstance(c, dict) and c.get("provenance") == "user-pinned"
        ]
        if pinned:
            primary = pinned[0].get("entity_id") or ""
        else:
            primary = self._pick_primary(candidates, ref_counts)
        return {
            "stable_id": stable_id,
            # display_name 一旦确定就不再跟随 HA 改名，保证模板/查询引用的名称稳定
            "display_name": existing.get("display_name") or cluster["name"],
            "device_class": cluster["domain"],
            "primary_entity": primary,
            "candidates": candidates,
            "provenance": existing.get("provenance") or "discovered",
            "created_at": existing.get("created_at") or now_iso,
        }

    @staticmethod
    def _pick_primary(candidates: list[dict], ref_counts: dict) -> str:
        """选主：在线优先 → 被模板引用频次 → 显式优先级 → entity_id 字典序。"""

        def score(c: dict) -> tuple:
            online = 0 if (c.get("state") or "") == "offline" else 1
            refs = int(ref_counts.get(c.get("entity_id") or "", 0))
            prio = int(c.get("priority") or 0)
            return (-online, -refs, -prio, c.get("entity_id") or "")

        if not candidates:
            return ""
        return sorted(candidates, key=score)[0].get("entity_id") or ""

    def _remap_orphans(
        self,
        entities: list[dict],
        seen: set[str],
        clusters: list[dict],
        ref_counts: dict,
    ) -> int:
        """A1：存量设备的主实体失效时改指；整体消失时按同名同类找新实体。"""
        touched = {self._stable_id_for(cl) for cl in clusters}
        now_iso = now_local(self.tz_offset_hours).isoformat()
        remapped = 0
        for row in self.store.list_logical_devices():
            stable_id = row.get("stable_id") or ""
            if stable_id in touched:
                continue
            cands = [c for c in (row.get("candidates") or []) if isinstance(c, dict)]
            if not cands:
                continue
            live = [c for c in cands if c.get("entity_id") in seen]
            primary = row.get("primary_entity") or ""

            if live:
                if primary and primary not in seen:
                    new_primary = self._pick_primary(live, ref_counts)
                    if new_primary and new_primary != primary:
                        row["primary_entity"] = new_primary
                        row["provenance"] = "auto-remapped"
                        self.store.upsert_logical_device(row)
                        remapped += 1
                continue

            # 全部候选都消失 → 找同名同类的新实体（重登漂移的典型形态）
            domain = row.get("device_class") or ""
            best: dict | None = None
            best_score = 0.0
            for e in entities:
                if domain and e["domain"] != domain:
                    continue
                score = similarity(row.get("display_name") or "", e["name"])
                for c in cands:
                    score = max(score, similarity(c.get("name") or "", e["name"]))
                if score >= self.threshold and score > best_score:
                    best, best_score = e, score
            if best is None:
                continue
            row["candidates"] = cands + [
                {
                    "entity_id": best["entity_id"],
                    "name": best["name"],
                    "room": best["room"],
                    "domain": best["domain"],
                    "priority": 0,
                    "state": "active"
                    if best["state"] not in OFFLINE_STATES
                    else "offline",
                    "last_seen": now_iso,
                    "last_data_ts": "",
                    "provenance": "auto-remapped",
                }
            ]
            row["primary_entity"] = best["entity_id"]
            row["provenance"] = "auto-remapped"
            self.store.upsert_logical_device(row)
            remapped += 1
        return remapped

    def _mark_stale(self, seen: set[str]) -> list[dict]:
        """A3：长期不可见 → stale；短暂失联 → unknown（不误判）。

        返回本轮**发生状态变化**的实体 ``[{entity_id, from, to}]``，
        供上层做 MQTT 推送 / 告警（只推变化，不刷屏）。
        """
        now = now_local(self.tz_offset_hours)
        changes: list[dict] = []
        for row in self.store.list_device_health():
            eid = row.get("entity_id") or ""
            if eid in seen:
                continue
            prev = row.get("state") or "active"
            last = _parse_iso(row.get("last_seen") or "")
            if last is None:
                # 从未真正见过：不判定失效，只标 unknown
                if prev != "unknown":
                    self.store.upsert_device_health(eid, state="unknown")
                    changes.append({"entity_id": eid, "from": prev, "to": "unknown"})
                continue
            if (now - last).days > self.stale_days:
                if prev != "stale":
                    self.store.upsert_device_health(
                        eid, state="stale", note="对账期间未在 HA 注册表出现"
                    )
                if prev != "stale":
                    changes.append({"entity_id": eid, "from": prev, "to": "stale"})
            elif prev != "unknown":
                # 短暂失联：降级为 unknown，不再算「确认在线」，
                # 这样解析不会把它当故障转移候选（恢复后下次对账会自动升回 active）
                self.store.upsert_device_health(
                    eid, state="unknown", note="本轮对账未出现（短暂失联）"
                )
                changes.append({"entity_id": eid, "from": prev, "to": "unknown"})
        return changes

    # ── 模板自愈 ────────────────────────────────────────────────────────────

    def bind_stale_templates(self) -> int:
        """把「写死 entity_id 但该实体已失效、身份层已找到新实体」的模板改为逻辑引用。

        原 ``entity_id`` **保留**作为解析失败时的兜底，因此是灰度且可回退的。
        返回改写的模板条目数。
        """
        tm = self.templates_getter() if self.templates_getter else None
        if tm is None:
            return 0
        try:
            templates = tm.list_all()
        except Exception:
            return 0
        bound = 0
        for tpl in templates:
            changed = False
            for eq in getattr(tpl, "entities", None) or []:
                old = (getattr(eq, "entity_id", "") or "").strip()
                if not old or getattr(eq, "logical_id", ""):
                    continue
                if old in self._last_seen:
                    continue
                for dev in self.store.list_logical_devices():
                    ids = {
                        (c.get("entity_id") or "")
                        for c in (dev.get("candidates") or [])
                        if isinstance(c, dict)
                    }
                    if old not in ids:
                        continue
                    primary = (dev.get("primary_entity") or "").strip()
                    if not primary or primary == old or primary not in self._last_seen:
                        continue
                    eq.logical_id = dev.get("stable_id") or ""
                    changed = True
                    break
            if changed:
                try:
                    tm.save(tpl)
                    bound += 1
                except Exception:
                    continue
        return bound

    # ── 引用统计 ────────────────────────────────────────────────────────────

    def _collect_references(self) -> tuple[set[str], dict[str, int]]:
        """统计模板引用到的 entity_id 及次数（选主与 referenced 标记用）。"""
        counts: dict[str, int] = {}
        tm = self.templates_getter() if self.templates_getter else None
        if tm is None:
            return set(), counts
        try:
            for tpl in tm.list_all():
                for eq in getattr(tpl, "entities", None) or []:
                    eid = (getattr(eq, "entity_id", "") or "").strip()
                    if eid:
                        counts[eid] = counts.get(eid, 0) + 1
        except Exception:
            return set(counts), counts
        return set(counts), counts
