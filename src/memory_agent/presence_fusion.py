"""在场融合 + 身份消歧（v0.7.5 轻量版）。

输入：
- ``roster``：成员名册（``store.list_members()`` 的结构，含 ``name`` /
  ``rooms`` 关联房间 / ``appearance_json.typical_location`` 房间先验）。
- ``occupancy``：各房间近期最新一条行为事件的占用快照
  ``[{room, members:[已识别名字], count:房间人数(含未识别)}]``，
  来自 ``store.recent_occupancy()``。

输出：融合后的「家庭在场图」，核心是用**名册消除法**做确定性身份推断：
已知在某房间的成员 + 名册全集 → 缺席成员；若「各房间未识别人数之和」
恰好等于「缺席成员数」，则把每个未识别占位确定性地归给一名缺席成员
（按关联房间 / 典型位置加权）。否则只报告未识别人数，不编造身份。

设计原则（对齐 MA「规则提纯、LLM 只解释」）：本模块是纯确定性规则，
零 LLM 调用；消除法只在等式成立时硬推断，其余情况保守标「未消歧」。
"""

from __future__ import annotations

# 与 store._UNKNOWN_IDENTITIES 保持一致：「没认出是谁」的占位名不计入已知在场
_UNKNOWN_IDENTITIES = {"未识别", "未识别成员", "陌生人", "unknown", "stranger", "none"}


def _member_prior_room(m: dict) -> str | None:
    """成员最可能的房间先验：关联房间优先，其次外观档案 typical_location。"""
    rooms = m.get("rooms") or []
    if rooms:
        return str(rooms[0]).strip()
    ap = m.get("appearance_json")
    if isinstance(ap, dict):
        tl = ap.get("typical_location")
        if isinstance(tl, str) and tl.strip():
            return tl.strip()
    return None


def _recognized(persons: list) -> list[str]:
    """从占用快照的 persons 字段提取已识别成员名。

    兼容两种形态：VLM/TV 上报是 ``[{name, ...}]`` 对象列表；butler 富化事件与
    ``recent_occupancy`` 落库后回流的是纯字符串名字列表。``未识别``/``陌生人``
    等占位名一律剔除。
    """
    out: list[str] = []
    for p in persons or []:
        if isinstance(p, dict):
            name = str(p.get("name") or "").strip()
        elif isinstance(p, str):
            name = p.strip()
        else:
            continue
        if name and name not in _UNKNOWN_IDENTITIES:
            out.append(name)
    return out


def fuse_presence(roster: list[dict], occupancy: list[dict]) -> dict:
    """融合各房间占用快照，输出家庭在场图。

    返回结构::

        {
          "enabled": True,
          "method": "elimination" | "inconclusive" | "none",
          "home_count": int,
          "occupancy": {room: {"members": [...], "unknown": int, "count": int}},
          "inferred": [{"member", "room", "confidence", "method", "reason"}],
          "unresolved_unknown": int,
          "known_present": [...],
          "absent": [...]
        }
    """
    occ_map: dict[str, dict] = {}
    known_present: set[str] = set()
    unknown_slots: list[str] = []  # 每个未识别占位记一笔房间

    for o in occupancy or []:
        room = (o.get("room") or "").strip()
        if not room:
            continue
        rec = _recognized(o.get("persons") if "persons" in o else o.get("members"))
        cnt = int(o.get("count") or 0)
        if cnt <= 0:
            cnt = len(rec)
        unknown = max(0, cnt - len(rec))
        # 同房间取「最新一条」即可；多次出现累计无意义（占用是瞬时快照）
        if room not in occ_map:
            occ_map[room] = {"members": rec, "unknown": unknown, "count": cnt}
        for n in rec:
            known_present.add(n)
        for _ in range(unknown):
            unknown_slots.append(room)

    roster_names = {m.get("name") for m in roster if m.get("name")}
    absent = [m for m in roster if m.get("name") and m["name"] not in known_present]
    total_unknown = len(unknown_slots)

    inferred: list[dict] = []
    unresolved = 0
    method = "none"

    if total_unknown > 0 and total_unknown == len(absent) and absent:
        # ── 确定性消除法：未识别人数 == 缺席人数 → 一一归位 ──
        method = "elimination"
        remaining = list(absent)
        for slot_room in unknown_slots:
            best, best_score = None, -1
            for m in remaining:
                score = 0
                pr = _member_prior_room(m)
                if pr and pr == slot_room:
                    score += 2
                if score > best_score:
                    best_score, best = score, m
            if best is None:
                unresolved += 1
                continue
            remaining.remove(best)
            conf = 0.9 if best_score >= 2 else 0.7
            reason = (
                f"消除法：其余成员已确认在 {', '.join(sorted(known_present)) or '其他房间'}"
                f"；名册余 {best['name']}；{slot_room}未识别 {1} 人 = 缺席人数 → 推断在此"
            )
            if best_score < 2:
                reason += "（无房间先验匹配，置信较低）"
            inferred.append({
                "member": best["name"],
                "room": slot_room,
                "confidence": conf,
                "method": "elimination",
                "reason": reason,
            })
    elif total_unknown > 0:
        # ── 等式不成立：保守，不编造身份 ──
        method = "inconclusive"
        unresolved = total_unknown

    home_count = len(known_present) + len(inferred)
    return {
        "enabled": True,
        "method": method,
        "home_count": home_count,
        "occupancy": occ_map,
        "inferred": inferred,
        "unresolved_unknown": unresolved,
        "known_present": sorted(known_present),
        "absent": [m["name"] for m in absent],
    }
