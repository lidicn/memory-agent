#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集服务

重构要点
--------
1. **任务化**：``trigger()`` / ``backfill()`` 只负责建任务并立刻返回 ``job_id``，
   真正的采集在后台 task 中跑，请求不会被网关掐断。
2. **单任务互斥**：``asyncio.Lock`` 保护「检查 + 启动」，重复触发返回正在运行的 job。
3. **首采不再空窗**：``last_poll_time`` 为空时回溯 ``first_run_lookback_hours``
   小时。重构前起点直接等于当前时刻，必然采到 0 条 —— 这是「采集失效」
   最直接的原因。
4. **回填按天分片**：把 ``end_time`` 真正传给 HA，逐日拉取，避免单次请求过大。
5. **批量入库**：500 条一批 ``executemany``，替代原先逐条写向量库。
6. **幂等**：事件主键为 ``sha1(entity_id|原始时间戳)``，重复回填同一区间安全。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Iterable

from .store import Store, make_event_id, now_local, parse_ts

# 这些状态不代表真实行为，计入会污染统计
SKIP_STATES = {"unknown", "unavailable", "none", ""}

# 属性差分时忽略的高频噪声字段
# 注意：unit_of_measurement 不在此列，改为在 _diff_attrs 里以 "_unit" 单独保留，
# 否则上层（用量/气候会话）会丢失单位信息。
NOISY_ATTRS = {
    "friendly_name", "icon", "supported_features", "device_class",
    "entity_picture", "attribution",
}


def _empty_progress() -> dict:
    return {
        "job_id": "",
        "phase": "idle",
        "current_room": "",
        "current_room_index": 0,
        "total_rooms": 0,
        "current_entity": "",
        "current_entity_index": 0,
        "total_entities_in_room": 0,
        "total_events": 0,
        "elapsed": 0.0,
        "source": "",  # 当前生效数据源：mariadb | rest | rest-fallback
        "message": "",
    }


class CollectService:
    """后台采集服务。"""

    FLUSH_SIZE = 500
    CHUNK_SIZE = 10          # 与 HAClient.HISTORY_BATCH_SIZE 对齐
    SCHEDULER_TICK = 30      # 调度器轮询间隔（秒）
    PROGRESS_PERSIST_INTERVAL = 2.0
    MIN_POLLING_INTERVAL = 900   # 最小采集间隔 15 分钟（直连 MariaDB 后支持高频同步）

    def __init__(self, config, ha, history, store: Store, ha_db=None):
        self.config = config
        self.ha = ha
        self.history = history
        self.store = store
        self.ha_db = ha_db

        self._start_lock = asyncio.Lock()
        self._job_task: asyncio.Task | None = None
        self._scheduler_task: asyncio.Task | None = None
        self._cancel_flag = False
        self.progress: dict = _empty_progress()
        self._last_source = ""
        self._last_persist = 0.0

    # ── 生命周期 ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            return
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        print(
            f"[Collect] 调度器已启动 "
            f"(enabled={self.config.polling_enabled}, mode={self.config.polling_mode})"
        )

    async def stop(self) -> None:
        self._cancel_flag = True
        for task in (self._scheduler_task, self._job_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._scheduler_task = None
        self._job_task = None

    def reconfigure(self, config, ha, ha_db=None) -> None:
        self.config = config
        self.ha = ha
        self.ha_db = ha_db

    # ── 状态 ─────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return bool(self._job_task and not self._job_task.done())

    def get_progress(self) -> dict:
        data = dict(self.progress)
        data["running"] = self.is_running
        data["enabled"] = self.config.polling_enabled
        data["mode"] = self.config.polling_mode
        data["last_poll_time"] = self.config.last_poll_time
        data["source"] = self.current_source()
        return data

    def current_source(self) -> str:
        """当前生效的数据源：mariadb（直连）/ rest（REST）。"""
        return "mariadb" if self.ha_db is not None else "rest"

    def next_run_time(self) -> str | None:
        """估算下次自动采集时间（ISO 字符串），手动模式或关闭时返回 None。"""
        cfg = self.config
        if not cfg.polling_enabled:
            return None
        tz = cfg.tz_offset_hours
        now = now_local(tz)
        last = parse_ts(cfg.last_poll_time, tz)
        mode = (cfg.polling_mode or "manual").lower()
        if mode == "interval":
            interval = max(self.MIN_POLLING_INTERVAL, int(cfg.polling_interval or 3600))
            base = last if last else now
            nxt = base + timedelta(seconds=interval)
            if nxt < now:
                nxt = now
            return nxt.isoformat()
        if mode == "scheduled":
            raw = (cfg.polling_time or "01:00").strip()
            try:
                hour, minute = (int(x) for x in raw.split(":")[:2])
            except ValueError:
                hour, minute = 1, 0
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target.isoformat()
        return None

    def request_cancel(self) -> bool:
        if not self.is_running:
            return False
        self._cancel_flag = True
        self._set_progress(message="正在取消…")
        return True

    # ── 任务入口 ─────────────────────────────────────────────────────────

    async def trigger(self, source: str = "manual") -> dict:
        """增量采集：从上次采集时间到现在。"""
        return await self.trigger_incremental(since_minutes=None, source=source)

    async def trigger_incremental(
        self, since_minutes: int | None = None, source: str = "mcp"
    ) -> dict:
        """增量采集（支持自定义回溯窗口）。

        since_minutes:
          - None  → 从上次采集时间 last_poll_time 到现在（标准增量）。
          - >0    → 从 ``now - since_minutes`` 分钟到现在（强制重采最近一段）。
        返回 ``{ok, job_id}``，任务在后台异步执行。
        """
        async with self._start_lock:
            if self.is_running:
                return {
                    "ok": False,
                    "error": "已有采集任务在运行",
                    "job_id": self.progress.get("job_id", ""),
                    "code": 409,
                }
            job_id = await asyncio.to_thread(
                self.store.create_job, "realtime", {"source": source}
            )
            self._cancel_flag = False
            self._job_task = asyncio.create_task(
                self._run_realtime(job_id, source, since_minutes=since_minutes)
            )
            return {"ok": True, "job_id": job_id}

    async def backfill(
        self, start_day: str, end_day: str, rooms: list[str] | None = None
    ) -> dict:
        """历史回填：按天分片补采指定区间。"""
        try:
            start_dt = datetime.strptime(start_day, "%Y-%m-%d")
            end_dt = datetime.strptime(end_day, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "日期格式应为 YYYY-MM-DD", "code": 400}
        if end_dt < start_dt:
            return {"ok": False, "error": "结束日期不能早于开始日期", "code": 400}
        if (end_dt - start_dt).days > 365:
            return {"ok": False, "error": "单次回填区间不得超过 365 天", "code": 400}

        async with self._start_lock:
            if self.is_running:
                return {
                    "ok": False,
                    "error": "已有采集任务在运行",
                    "job_id": self.progress.get("job_id", ""),
                    "code": 409,
                }
            params = {"start_day": start_day, "end_day": end_day, "rooms": rooms or []}
            job_id = await asyncio.to_thread(self.store.create_job, "backfill", params)
            self._cancel_flag = False
            self._job_task = asyncio.create_task(
                self._run_backfill(job_id, start_dt, end_dt, rooms)
            )
            return {"ok": True, "job_id": job_id}

    # ── 任务实现 ─────────────────────────────────────────────────────────

    async def _run_realtime(
        self, job_id: str, source: str, since_minutes: int | None = None
    ) -> None:
        tz = self.config.tz_offset_hours
        now = now_local(tz)
        if since_minutes and since_minutes > 0:
            last = now - timedelta(minutes=int(since_minutes))
            print(f"[Collect] 增量采集：回溯最近 {since_minutes} 分钟")
        else:
            last = parse_ts(self.config.last_poll_time, tz)
            if last is None:
                hours = max(1, int(self.config.first_run_lookback_hours or 24))
                last = now - timedelta(hours=hours)
                print(f"[Collect] 首次采集，回溯 {hours} 小时")
        # 留 1 分钟重叠，避免边界事件漏采（写入幂等，重复无害）
        start_dt = last - timedelta(minutes=1)

        await self._execute(
            job_id,
            "realtime",
            [(start_dt, now)],
            rooms_filter=None,
            on_success=self._update_last_poll_time,
        )

    async def _run_backfill(
        self,
        job_id: str,
        start_dt: datetime,
        end_dt: datetime,
        rooms: list[str] | None,
    ) -> None:
        windows: list[tuple[datetime, datetime]] = []
        cursor = start_dt
        while cursor <= end_dt:
            windows.append(
                (
                    cursor.replace(hour=0, minute=0, second=0, microsecond=0),
                    cursor.replace(hour=23, minute=59, second=59, microsecond=0),
                )
            )
            cursor += timedelta(days=1)
        await self._execute(job_id, "backfill", windows, rooms_filter=rooms)

    def _update_last_poll_time(self) -> None:
        self.config.last_poll_time = now_local(self.config.tz_offset_hours).isoformat()
        try:
            self.config.save()
        except Exception as exc:
            print(f"[Collect] 保存 last_poll_time 失败: {exc}")

    async def _execute(
        self,
        job_id: str,
        job_type: str,
        windows: list[tuple[datetime, datetime]],
        rooms_filter: list[str] | None = None,
        on_success=None,
    ) -> None:
        started = time.time()
        targets = self._enabled_targets(rooms_filter)
        total_entities = sum(len(v) for _, v in targets)

        self.progress = _empty_progress()
        self._set_progress(
            job_id=job_id,
            phase="polling",
            total_rooms=len(targets),
            message=f"{len(targets)} 个房间 / {total_entities} 个实体 / {len(windows)} 个时间窗",
        )
        await asyncio.to_thread(
            self.store.update_job, job_id, "running", self.progress
        )

        if not targets:
            await self._finish(
                job_id, "error", started,
                error="没有启用的房间或实体，请先在「数据采集 → 房间实体」中启用",
            )
            return

        buffer: list[dict] = []
        touched_days: set[str] = set()
        total_events = 0
        errors: list[str] = []

        try:
            for w_index, (w_start, w_end) in enumerate(windows, 1):
                if self._cancel_flag:
                    break
                window_label = (
                    w_start.strftime("%Y-%m-%d")
                    if len(windows) > 1
                    else f"{w_start:%m-%d %H:%M} ~ {w_end:%m-%d %H:%M}"
                )
                for r_index, (room, entity_ids) in enumerate(targets, 1):
                    if self._cancel_flag:
                        break
                    self._set_progress(
                        current_room=room,
                        current_room_index=r_index,
                        total_entities_in_room=len(entity_ids),
                        current_entity_index=0,
                        elapsed=round(time.time() - started, 1),
                        message=f"[{w_index}/{len(windows)}] {window_label}",
                    )

                    self._set_progress(
                        current_entity="MariaDB 批量查询中…" if self.ha_db else "REST 查询中…"
                    )

                    def _on_prog(eid, idx, total):
                        self._set_progress(
                            current_entity=eid,
                            current_entity_index=idx,
                            elapsed=round(time.time() - started, 1),
                        )

                    series, source, fetch_errors = await self._fetch_history(
                        entity_ids, w_start, w_end, on_progress=_on_prog
                    )
                    errors.extend(fetch_errors)
                    self._set_progress(source=source, elapsed=round(time.time() - started, 1))

                    for entity_id, records in (series or {}).items():
                        events = self._events_from_series(room, entity_id, records)
                        for ev in events:
                            touched_days.add(ev["day"])
                        buffer.extend(events)

                    if len(buffer) >= self.FLUSH_SIZE:
                        total_events += await self._flush(buffer)
                        buffer = []
                        self._set_progress(
                            total_events=total_events,
                            elapsed=round(time.time() - started, 1),
                        )
                    await self._persist_progress(job_id)
                    # 让出事件循环，避免采集把 WebUI 拖死
                    await asyncio.sleep(0)

            if buffer:
                self._set_progress(phase="flushing")
                total_events += await self._flush(buffer)
                buffer = []

            if self._cancel_flag:
                await self._finish(
                    job_id, "cancelled", started,
                    result={"events": total_events, "days": sorted(touched_days)},
                )
                return

            if touched_days and self.config.chroma_mirror:
                self._set_progress(phase="mirroring", message="生成语义摘要…")
                await asyncio.to_thread(self.history.mirror_days, sorted(touched_days))

            if on_success:
                on_success()

            await self._finish(
                job_id,
                "done",
                started,
                result={
                    "events": total_events,
                    "days": sorted(touched_days),
                    "rooms": len(targets),
                    "entities": total_entities,
                    "errors": errors[:20],
                },
            )
            print(
                f"[Collect] {job_type} 完成: {total_events} 条事件, "
                f"{len(touched_days)} 天, 耗时 {time.time() - started:.1f}s"
            )
        except asyncio.CancelledError:
            await self._finish(job_id, "cancelled", started, error="任务被取消")
            raise
        except Exception as exc:
            import traceback

            traceback.print_exc()
            await self._finish(job_id, "error", started, error=str(exc))

    async def _flush(self, buffer: list[dict]) -> int:
        if not buffer:
            return 0
        return await asyncio.to_thread(self.history.add_events, buffer)

    async def _fetch_history(
        self,
        entity_ids: list[str],
        w_start: datetime,
        w_end: datetime,
        on_progress=None,
    ) -> tuple[dict, str, list[str]]:
        """取某房间全部实体的状态历史。

        数据源优先级：HA MariaDB 直读（方案B，主）> HA REST（降级）。
        返回 ``(series, source, errors)``，``source`` ∈ {mariadb, rest, rest-fallback}。

        ``on_progress(eid, idx, total)`` 在 REST 降级逐批查询时回调，用于刷新进度。
        """
        w_start_iso = w_start.isoformat()
        w_end_iso = w_end.isoformat()
        series: dict = {}
        errors: list[str] = []

        if self.ha_db is not None:
            try:
                sub = await asyncio.to_thread(
                    self.ha_db.get_history, entity_ids, w_start_iso, w_end_iso
                )
                for k, v in (sub or {}).items():
                    series.setdefault(k, []).extend(v)
                self._last_source = "mariadb"
                return series, "mariadb", errors
            except Exception as exc:
                print(f"[Collect] MariaDB 取数失败，回退 REST: {exc}")
                self._last_source = "rest-fallback"
                # 继续走下方 REST 分支
        else:
            self._last_source = "rest"

        # REST 降级（保留原逐批语义）
        chunk = self.CHUNK_SIZE
        for offset in range(0, len(entity_ids), chunk):
            if self._cancel_flag:
                break
            part = entity_ids[offset : offset + chunk]
            if on_progress:
                on_progress(part[0], min(offset + len(part), len(entity_ids)), len(entity_ids))
            try:
                sub = await asyncio.to_thread(
                    self.ha.get_history, part, w_start_iso, w_end_iso
                )
            except Exception as exc:
                errors.append(f"{part[0]}: {exc}")
                continue
            for k, v in (sub or {}).items():
                series.setdefault(k, []).extend(v)
        return series, self._last_source, errors

    async def _finish(
        self,
        job_id: str,
        status: str,
        started: float,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        self._set_progress(
            phase=status,
            elapsed=round(time.time() - started, 1),
            message=error or (self.progress.get("message") or ""),
        )
        await asyncio.to_thread(
            self.store.update_job,
            job_id,
            status,
            self.progress,
            result,
            error,
            True,
        )

    # ── 进度 ─────────────────────────────────────────────────────────────

    def _set_progress(self, **kwargs) -> None:
        self.progress.update(kwargs)

    async def _persist_progress(self, job_id: str) -> None:
        now = time.time()
        if now - self._last_persist < self.PROGRESS_PERSIST_INTERVAL:
            return
        self._last_persist = now
        await asyncio.to_thread(
            self.store.update_job, job_id, None, dict(self.progress)
        )

    # ── 采集目标 ─────────────────────────────────────────────────────────

    def _enabled_targets(
        self, rooms_filter: Iterable[str] | None = None
    ) -> list[tuple[str, list[str]]]:
        """返回 [(房间, [实体ID])]，已应用房间/实体启停与排除名单。"""
        allow = set(rooms_filter) if rooms_filter else None
        excluded = set(self.config.excluded_entities or [])
        targets: list[tuple[str, list[str]]] = []
        for room, payload in (self.config.rooms or {}).items():
            if not isinstance(payload, dict):
                continue
            if allow is not None and room not in allow:
                continue
            if not payload.get("enabled", True):
                continue
            entity_ids = [
                entity_id
                for entity_id, info in (payload.get("entities") or {}).items()
                if isinstance(info, dict)
                and info.get("enabled", True)
                and entity_id not in excluded
            ]
            if entity_ids:
                targets.append((room, sorted(entity_ids)))
        return targets

    # ── 事件构造 ─────────────────────────────────────────────────────────

    def _events_from_series(
        self, room: str, entity_id: str, records: list[dict]
    ) -> list[dict]:
        """把 HA 的状态序列差分为「状态变化事件」。"""
        if not records:
            return []
        tz = self.config.tz_offset_hours
        domain = entity_id.split(".")[0]
        events: list[dict] = []
        prev_state: str | None = None
        prev_attrs: dict = {}

        for record in records:
            if not isinstance(record, dict):
                continue
            state = str(record.get("state", "")).strip()
            if state.lower() in SKIP_STATES:
                continue
            attrs = record.get("attributes") or {}
            if prev_state is not None and state != prev_state:
                raw_ts = record.get("last_changed") or record.get("last_updated")
                dt = parse_ts(raw_ts, tz)
                if dt is None:
                    prev_state, prev_attrs = state, attrs
                    continue
                events.append(
                    {
                        "id": make_event_id(entity_id, raw_ts),
                        "ts": dt.isoformat(sep="T"),
                        "day": dt.strftime("%Y-%m-%d"),
                        "room": room,
                        "entity_id": entity_id,
                        "domain": domain,
                        "action": f"{prev_state}->{state}",
                        "person": self._person_hint(),
                        "old_state": prev_state,
                        "new_state": state,
                        "attrs": self._diff_attrs(prev_attrs, attrs),
                    }
                )
                # climate 域：把温度 / 湿度 / 模式全量快照进事件，
                # 解决「只有 hvac_action、缺室温与设定温度」的痛点（get_climate_sessions 依赖）。
                # 用 setdefault 保证存在 attrs 字典，且无论 diff 是否已含都强制写入最新值。
                if domain == "climate" and events:
                    snap = events[-1].setdefault("attrs", {})
                    for k in (
                        "current_temperature",
                        "temperature",
                        "target_temp_high",
                        "target_temp_low",
                        "current_humidity",
                        "humidity",
                        "hvac_action",
                        "hvac_mode",
                        "fan_mode",
                    ):
                        if k in attrs:
                            snap[k] = attrs[k]
            prev_state, prev_attrs = state, attrs
        return events

    @staticmethod
    def _diff_attrs(old: dict, new: dict, limit: int = 12) -> dict:
        """只保留发生变化的标量属性，避免把整包属性写进库。"""
        if not isinstance(new, dict):
            return {}
        changed: dict[str, Any] = {}
        for key, value in new.items():
            if key in NOISY_ATTRS:
                continue
            # 单位单独存为 _unit，避免与状态属性混淆，也便于上层展示
            if key == "unit_of_measurement":
                changed["_unit"] = value
                continue
            if not isinstance(value, (str, int, float, bool)):
                continue
            if isinstance(old, dict) and old.get(key) == value:
                continue
            changed[key] = value
            if len(changed) >= limit:
                break
        return changed

    def _person_hint(self) -> str:
        """人员归属占位。

        HA 的设备状态变化本身不携带操作者信息，需要结合 ``person.*``
        实体的位置轨迹推断。当前留空（``get_person_history`` 用 all 查询），
        待人员实体纳入采集范围后在此关联。
        """
        return ""

    # ── 调度 ─────────────────────────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.SCHEDULER_TICK)
                await self._maybe_auto_collect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[Collect] 调度器异常: {exc}")

    async def _maybe_auto_collect(self) -> None:
        cfg = self.config
        if not cfg.polling_enabled or self.is_running:
            return
        tz = cfg.tz_offset_hours
        now = now_local(tz)
        last = parse_ts(cfg.last_poll_time, tz)
        mode = (cfg.polling_mode or "manual").lower()

        if mode == "interval":
            interval = max(self.MIN_POLLING_INTERVAL, int(cfg.polling_interval or 3600))
            if last is None or (now - last).total_seconds() >= interval:
                print(f"[Collect] 间隔触发采集（{interval}s）")
                await self.trigger("auto-interval")
        elif mode == "scheduled":
            raw = (cfg.polling_time or "01:00").strip()
            try:
                hour, minute = (int(x) for x in raw.split(":")[:2])
            except ValueError:
                hour, minute = 1, 0
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target and (last is None or last < target):
                print(f"[Collect] 定时触发采集（{raw}）")
                await self.trigger("auto-scheduled")
        # manual 模式不自动采集


# 兼容旧引用
BackgroundPoller = CollectService
