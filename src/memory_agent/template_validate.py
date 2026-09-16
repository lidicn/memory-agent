#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行为洞察模板校验（v0.7）。

目标：让模板「失效可检出、原因可解释、能修的自动修、不能修的明确拦下」。

状态机（多实体模板取**最严重**状态，同时保留逐实体明细）::

    ok        实体有效且窗口内有数据
    no_data   实体有效、窗口内无数据        -> 仅警告（可能真的没有活动）
    stale     身份层判定已失效（约 30 天未见）-> 硬阻止
    missing   数据仓库与身份层均查无此实体   -> 硬阻止
    disabled  用户已停用                    -> 不参与调用与校验

判定优先级：``disabled`` > ``missing`` > ``stale`` > ``no_data`` > ``ok``。

设计约束
--------
* **不重复造轮子**：实体解析与候选发现复用 ``identity`` 身份层（逻辑设备候选、
  名称相似度），本模块只做「编排 + 状态判定 + 缓存」。
* **不阻塞**：校验需查事件表与健康表，成本不低。所有校验结果落
  ``data/template_state.json`` 缓存，列表接口只读缓存；全量校验由后台任务或
  用户手动触发。
* **不破坏契约**：停用只影响列表与调用过滤，不改模板定义；修复保留原值可回退。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Any

from .store import now_local

try:  # 复用身份层的相似度（CJK 感知）；万一不可用则回退 difflib
    from .identity import similarity as _identity_similarity
except Exception:  # pragma: no cover
    _identity_similarity = None

STATE_PRIORITY = {"ok": 0, "no_data": 1, "stale": 2, "missing": 3, "disabled": 4}
#: 需要**硬阻止执行**的状态（不再返回可能误导的数值）
BLOCKING_STATES = frozenset({"missing", "stale", "disabled"})
#: 仅警告、可正常执行
WARN_STATES = frozenset({"no_data"})


def _tz(rt) -> float:
    return float(getattr(rt.config, "tz_offset_hours", 8.0) or 8.0)


def _now_iso(rt) -> str:
    return now_local(_tz(rt)).isoformat(timespec="seconds")


def _sim(a: str, b: str) -> float:
    if _identity_similarity is not None:
        try:
            return float(_identity_similarity(a or "", b or ""))
        except Exception:
            pass
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a or "", b or "").ratio()


# ── 持久化：停用集合 + 校验缓存 + 修复审计 ──────────────────────────────────


class TemplateStateStore:
    """``data/template_state.json`` 的读写（原子写，与 templates.json 同目录）。"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir or "/data"
        self.path = os.path.join(self.data_dir, "template_state.json")
        self._lock = threading.RLock()
        self.disabled: list[str] = []
        self.validation: dict[str, dict] = {}
        self.fixes: list[dict] = []
        self._load()

    # ── 载入 / 保存 ──
    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                self.disabled = list(data.get("disabled") or [])
                self.validation = dict(data.get("validation") or {})
                self.fixes = list(data.get("fixes") or [])
        except Exception as exc:  # noqa: BLE001 - 状态文件损坏不应阻断服务
            print(f"[TemplateState] 加载失败（使用空状态）: {exc}")

    def _save(self) -> None:
        payload = {
            "disabled": self.disabled,
            "validation": self.validation,
            "fixes": self.fixes[-200:],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".tpl-state-", suffix=".tmp", dir=self.data_dir
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001
            print(f"[TemplateState] 保存失败: {exc}")

    # ── 停用 ──
    def is_disabled(self, template_id: str) -> bool:
        with self._lock:
            return template_id in self.disabled

    def set_disabled(self, template_id: str, disabled: bool) -> None:
        with self._lock:
            if disabled and template_id not in self.disabled:
                self.disabled.append(template_id)
            elif not disabled and template_id in self.disabled:
                self.disabled.remove(template_id)
            self._save()

    # ── 缓存 ──
    def get_validation(self, template_id: str) -> dict | None:
        with self._lock:
            item = self.validation.get(template_id)
            return dict(item) if isinstance(item, dict) else None

    def all_validations(self) -> dict[str, dict]:
        with self._lock:
            return dict(self.validation)

    def set_validation(self, template_id: str, data: dict) -> None:
        with self._lock:
            self.validation[template_id] = data
            self._save()

    def drop_validation(self, template_id: str) -> None:
        with self._lock:
            if template_id in self.validation:
                del self.validation[template_id]
                self._save()

    # ── 修复审计 ──
    def record_fix(self, template_id: str, index: int, old: str, new: str) -> None:
        with self._lock:
            self.fixes.append({
                "at": datetime.now().isoformat(timespec="seconds"),
                "template_id": template_id,
                "entity_index": index,
                "from": old,
                "to": new,
            })
            self._save()


_STORES: dict[str, TemplateStateStore] = {}
_STORES_LOCK = threading.RLock()


def get_state_store(rt) -> TemplateStateStore:
    data_dir = getattr(rt.config, "data_dir", "/data") or "/data"
    with _STORES_LOCK:
        st = _STORES.get(data_dir)
        if st is None:
            st = TemplateStateStore(data_dir)
            _STORES[data_dir] = st
        return st


# ── 候选发现（复用身份层）──────────────────────────────────────────────────


def _find_candidates(rt, ref: str) -> list[dict]:
    """为失效引用寻找可替代实体。

    优先级：
    1. 该引用所属**逻辑设备的其它候选实体**（同一物理设备，高置信 0.9）；
    2. 按名称/ID 相似度模糊匹配逻辑设备候选（中置信，需确认）。
    """
    out: list[dict] = []
    if not ref:
        return out
    try:
        devices = rt.store.list_logical_devices()
    except Exception:
        return out

    # 1) 精确：同一逻辑设备的其它候选
    for dev in devices:
        cands = dev.get("candidates") or []
        eids = [c.get("entity_id") or "" for c in cands]
        if ref in eids:
            for c in cands:
                eid = c.get("entity_id") or ""
                if eid and eid != ref:
                    out.append({
                        "ref": eid,
                        "confidence": 0.9,
                        "reason": "同一逻辑设备的其它候选实体（双集成/重登漂移）",
                        "source": "logical_device",
                    })
            return out

    # 2) 模糊：名称/ID 相似度
    scored: list[tuple[float, str, str]] = []
    for dev in devices:
        for c in (dev.get("candidates") or []):
            eid = c.get("entity_id") or ""
            if not eid:
                continue
            name = c.get("name") or eid
            score = max(_sim(ref, eid), _sim(ref, name))
            if score >= 0.5:
                scored.append((score, eid, name))
    scored.sort(key=lambda x: -x[0])
    for score, eid, name in scored[:3]:
        out.append({
            "ref": eid,
            "confidence": round(float(score), 2),
            "reason": f"名称相似（{name}）",
            "source": "similarity",
        })
    return out


# ── 校验 ────────────────────────────────────────────────────────────────────


def _within_window(last_ts: str, days: int, rt) -> bool:
    try:
        lt = datetime.fromisoformat(last_ts)
        now = now_local(_tz(rt)).replace(tzinfo=None)
        return (now - lt) <= timedelta(days=max(1, int(days)))
    except Exception:
        return True


def _validate_entity(rt, eq, index: int, window_days: int) -> dict:
    """校验单个 EntityQuery。"""
    ref = (getattr(eq, "entity_id", "") or getattr(eq, "logical_id", "") or "").strip()
    info: dict[str, Any] = {
        "index": index,
        "entity_id": ref,
        "attribute": getattr(eq, "attribute", ""),
        "metric": getattr(eq, "metric", ""),
        "state": "ok",
        "resolved": [],
        "resolved_reason": "",
        "last_data_ts": "",
        "candidates": [],
        "fixable": False,
        "needs_manual": False,
        "message": "",
    }
    if not ref:
        info.update(state="missing", needs_manual=True, message="模板未配置实体")
        return info

    # 1) 身份层解析
    identity = getattr(rt, "identity", None)
    resolved: list[str] = []
    reason = ""
    if identity is not None:
        try:
            resolved, reason = identity.resolve(ref)
            reason = reason or ""
        except Exception as exc:  # noqa: BLE001
            reason = f"resolve_error: {exc}"
    info["resolved"] = resolved or []
    info["resolved_reason"] = reason

    # 2) 健康记录
    try:
        health = rt.store.get_device_health(ref)
    except Exception:
        health = None

    # 3) 数据可得性（单实体 MAX(ts)，走 idx_events_entity_ts）
    last_ts = ""
    try:
        last_ts = (rt.store.entity_last_seen([ref]) or {}).get(ref, "") or ""
    except Exception:
        last_ts = ""
    info["last_data_ts"] = last_ts

    hstate = (health or {}).get("state") or ""
    if hstate == "stale":
        info["state"] = "stale"
        info["message"] = "身份层判定该实体已失效（长期未在 HA 出现）"
    elif hstate == "unknown":
        info["state"] = "no_data"
        info["message"] = "该实体本轮对账未出现（可能短暂失联）"
    elif not resolved and reason == "stale":
        info["state"] = "stale"
        info["message"] = "身份层无法解析到可用实体（候选均已失效）"
    elif not resolved and reason == "unresolved":
        info["state"] = "missing" if not last_ts else "no_data"
        info["message"] = (
            "身份层未收录该实体，且数据仓库无任何记录（疑似占位/示例名）"
            if not last_ts
            else "身份层未收录，但数据仓库有历史记录"
        )
    elif not health and not last_ts:
        info["state"] = "missing"
        info["message"] = "数据仓库与身份层均查无此实体（疑似占位/示例名）"
    elif last_ts and _within_window(last_ts, window_days, rt):
        info["state"] = "ok"
    elif last_ts:
        info["state"] = "no_data"
        info["message"] = f"最近 {window_days} 天内无数据（最后一条 {last_ts}）"
    else:
        info["state"] = "no_data"
        info["message"] = "该实体暂无数据"

    # 4) 失效 → 给修复建议
    if info["state"] in ("missing", "stale"):
        cands = _find_candidates(rt, ref)
        info["candidates"] = cands
        info["fixable"] = bool(cands)
        info["needs_manual"] = not cands
    return info


def _status_reason(cached: dict) -> str:
    """把模板级状态翻译成可读原因（取最严重的实体说明）。"""
    ents = cached.get("entities") or []
    worst, prio = None, -1
    for e in ents:
        p = STATE_PRIORITY.get(e.get("state") or "ok", 0)
        if p > prio:
            worst, prio = e, p
    if worst is None:
        return "模板引用的实体均不可用"
    return f"{worst.get('entity_id')}: {worst.get('message') or worst.get('state')}"


def validate_template(rt, tpl) -> dict:
    """校验单个模板并写缓存。"""
    store = get_state_store(rt)
    tid = tpl.id
    checked_at = _now_iso(rt)
    if store.is_disabled(tid):
        data = {
            "template_id": tid,
            "status": "disabled",
            "entities": [],
            "fixable": False,
            "needs_manual": False,
            "suggestions": [],
            "checked_at": checked_at,
            "builtin": bool(rt.templates.is_builtin(tid)),
        }
        store.set_validation(tid, data)
        return data

    window_days = int(getattr(tpl, "default_days", 0) or getattr(tpl, "sample_days", 0) or 7)
    ents = []
    worst = "ok"
    for i, eq in enumerate(tpl.entities or []):
        info = _validate_entity(rt, eq, i, window_days)
        ents.append(info)
        if STATE_PRIORITY.get(info["state"], 0) > STATE_PRIORITY.get(worst, 0):
            worst = info["state"]

    suggestions = []
    for e in ents:
        if e["state"] in ("missing", "stale") and e.get("candidates"):
            top = e["candidates"][0]
            suggestions.append({
                "entity_index": e["index"],
                "from": e["entity_id"],
                "to": top["ref"],
                "confidence": top["confidence"],
                "reason": top.get("reason", ""),
            })

    data = {
        "template_id": tid,
        "status": worst,
        "entities": ents,
        "fixable": any(e.get("fixable") for e in ents),
        "needs_manual": any(e.get("needs_manual") for e in ents),
        "suggestions": suggestions,
        "window_days": window_days,
        "checked_at": checked_at,
        "builtin": bool(rt.templates.is_builtin(tid)),
    }
    store.set_validation(tid, data)
    return data


def validate_all(rt, force: bool = False) -> dict:
    """全量校验所有模板（不含已停用者仍会写入 disabled 状态）。"""
    results = []
    for tpl in rt.templates.list_all():
        try:
            results.append(validate_template(rt, tpl))
        except Exception as exc:  # noqa: BLE001 - 单个模板失败不影响整体
            results.append({
                "template_id": getattr(tpl, "id", "?"),
                "status": "missing",
                "entities": [],
                "error": f"校验异常: {exc}",
                "checked_at": _now_iso(rt),
            })
    summary: dict[str, int] = {}
    for r in results:
        key = r.get("status") or "unknown"
        summary[key] = summary.get(key, 0) + 1
    return {
        "ok": True,
        "total": len(results),
        "summary": summary,
        "templates": results,
        "checked_at": _now_iso(rt),
    }


# ── 查询 / 执行闸门 ─────────────────────────────────────────────────────────


def get_cached(rt, template_id: str) -> dict | None:
    return get_state_store(rt).get_validation(template_id)


def check_executable(rt, template_id: str) -> dict:
    """执行前检查：``{allowed, status, reason?, warning?, suggestions?}``。

    * ``missing`` / ``stale`` / ``disabled`` → ``allowed=False``（硬阻止）；
    * ``no_data`` → ``allowed=True`` + ``warning``；
    * 未校验 → ``allowed=True``（不因未校验而阻断，仅提示）。
    """
    store = get_state_store(rt)
    if store.is_disabled(template_id):
        return {"allowed": False, "status": "disabled", "reason": "该模板已停用"}
    cached = store.get_validation(template_id)
    if cached is None:
        return {"allowed": True, "status": "unchecked",
                "warning": "该模板尚未校验，建议在「行为洞察」页点一次「重新校验」"}
    status = cached.get("status") or "ok"
    if status in BLOCKING_STATES:
        return {
            "allowed": False,
            "status": status,
            "reason": _status_reason(cached),
            "suggestions": cached.get("suggestions") or [],
        }
    if status in WARN_STATES:
        return {
            "allowed": True,
            "status": "no_data",
            "warning": _status_reason(cached),
        }
    return {"allowed": True, "status": status}


# ── 停用 / 修复 ─────────────────────────────────────────────────────────────


def set_disabled(rt, template_id: str, disabled: bool) -> dict:
    if rt.templates.get(template_id) is None:
        return {"ok": False, "error": f"模板不存在: {template_id}"}
    store = get_state_store(rt)
    store.set_disabled(template_id, bool(disabled))
    if disabled:
        validate_template(rt, rt.templates.get(template_id))  # 写入 disabled 缓存
        return {"ok": True, "template_id": template_id, "disabled": True,
                "message": "已停用（不出现在列表与调用路径，可随时恢复）"}
    result = validate_template(rt, rt.templates.get(template_id))
    return {"ok": True, "template_id": template_id, "disabled": False,
            "validation": result, "message": "已恢复并重新校验"}


def apply_fix(rt, template_id: str, entity_index: int,
              new_ref: str = "", auto: bool = False) -> dict:
    """应用修复：把某个实体的引用替换为可用实体/逻辑设备。

    保留原值仅在新值保存失败时回滚内存；成功则记入修复审计（可追溯）。
    """
    tpl = rt.templates.get(template_id)
    if tpl is None:
        return {"ok": False, "error": f"模板不存在: {template_id}"}
    ents = tpl.entities or []
    if entity_index < 0 or entity_index >= len(ents):
        return {"ok": False, "error": f"entity_index 越界: {entity_index}"}
    eq = ents[entity_index]
    old_ref = eq.entity_id

    target = (new_ref or "").strip()
    if not target and auto:
        cached = get_cached(rt, template_id) or {}
        for e in (cached.get("entities") or []):
            if e.get("index") == entity_index and e.get("candidates"):
                target = e["candidates"][0]["ref"]
                break
    if not target:
        return {"ok": False, "error": "未提供目标实体，且无可用候选（需人工指定）"}
    if target == old_ref:
        return {"ok": True, "message": "目标与当前一致，无需修复"}

    eq.entity_id = target
    try:
        rt.templates.save(tpl)
    except Exception as exc:  # noqa: BLE001
        eq.entity_id = old_ref
        return {"ok": False, "error": f"保存失败已回滚: {exc}"}

    get_state_store(rt).record_fix(template_id, entity_index, old_ref, target)
    print(f"[TemplateValidate] 修复 {template_id}[{entity_index}] {old_ref} -> {target}")
    result = validate_template(rt, tpl)
    return {
        "ok": True,
        "template_id": template_id,
        "entity_index": entity_index,
        "previous": old_ref,
        "applied": target,
        "revalidated": result.get("status"),
        "validation": result,
        "message": f"已修复为 {target}，复验状态：{result.get('status')}",
    }


__all__ = [
    "STATE_PRIORITY",
    "BLOCKING_STATES",
    "WARN_STATES",
    "TemplateStateStore",
    "get_state_store",
    "validate_template",
    "validate_all",
    "get_cached",
    "check_executable",
    "set_disabled",
    "apply_fix",
]
