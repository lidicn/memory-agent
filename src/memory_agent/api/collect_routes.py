"""数据采集路由

新端点统一在 ``/api/collect/*``；旧的 ``/api/poller/*`` 保留为薄转发别名，
避免任何未知调用方（脚本、旧页面缓存）失效。
"""

from __future__ import annotations

import asyncio
import calendar as _calendar
from datetime import datetime, timedelta

from starlette.requests import Request
from starlette.routing import Route

from ..store import now_local
from .deps import error, json_body, ok, require_user, runtime


# ── 触发与进度 ─────────────────────────────────────────────────────────────

async def collect_trigger(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    result = await rt.collector.trigger("manual")
    if not result.get("ok"):
        return error(result.get("error", "触发失败"), result.get("code", 400))
    # 202：任务已受理，真正的执行在后台，前端轮询进度
    return ok({"job_id": result["job_id"], "message": "采集任务已启动"}, status_code=202)


async def collect_backfill(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    start_day = (body.get("start_day") or body.get("start") or "").strip()
    end_day = (body.get("end_day") or body.get("end") or "").strip()
    if not start_day or not end_day:
        return error("缺少 start_day / end_day")
    rooms = body.get("rooms") if isinstance(body.get("rooms"), list) else None
    rt = runtime(request)
    result = await rt.collector.backfill(start_day, end_day, rooms)
    if not result.get("ok"):
        return error(result.get("error", "回填失败"), result.get("code", 400))
    return ok(
        {"job_id": result["job_id"], "message": f"回填任务已启动：{start_day} ~ {end_day}"},
        status_code=202,
    )


async def collect_progress(request: Request):
    _, err = require_user(request)
    if err:
        return err
    return ok({"progress": runtime(request).collector.get_progress()})


async def collect_cancel(request: Request):
    _, err = require_user(request)
    if err:
        return err
    if not runtime(request).collector.request_cancel():
        return error("当前没有运行中的采集任务", 409)
    return ok({"message": "已请求取消，正在收尾"})


async def collect_jobs(request: Request):
    _, err = require_user(request)
    if err:
        return err
    try:
        limit = int(request.query_params.get("limit", 20))
    except ValueError:
        limit = 20
    jobs = await asyncio.to_thread(runtime(request).store.list_jobs, max(1, min(limit, 100)))
    return ok({"jobs": jobs})


async def collect_job_detail(request: Request):
    _, err = require_user(request)
    if err:
        return err
    job_id = request.path_params.get("job_id", "")
    job = await asyncio.to_thread(runtime(request).store.get_job, job_id)
    if not job:
        return error("任务不存在", 404)
    return ok({"job": job})


# ── 配置 ───────────────────────────────────────────────────────────────────

async def collect_status(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    cfg = rt.config
    stats = await asyncio.to_thread(rt.store.stats)
    return ok(
        {
            "enabled": cfg.polling_enabled,
            "mode": cfg.polling_mode,
            "interval": cfg.polling_interval,
            "interval_minutes": round((cfg.polling_interval or 0) / 60, 2),
            "time": cfg.polling_time,
            "last_poll_time": cfg.last_poll_time,
            "next_run": rt.collector.next_run_time(),
            "source": rt.collector.current_source(),
            "ha_db_enabled": bool(cfg.ha_db_enabled and cfg.ha_db_password),
            "running": rt.collector.is_running,
            "progress": rt.collector.get_progress(),
            "stats": stats,
        }
    )


async def collect_enable(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    enabled = body.get("enabled")
    if enabled is None:
        return error("缺少 enabled")
    rt = runtime(request)
    rt.config.polling_enabled = bool(enabled)
    rt.config.save()
    rt.reload_config()
    return ok({"message": "采集已开启" if enabled else "采集已关闭", "enabled": bool(enabled)})


async def collect_config(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    cfg = rt.config

    if "polling_enabled" in body:
        cfg.polling_enabled = bool(body["polling_enabled"])
    if body.get("polling_mode") in ("interval", "scheduled", "manual"):
        cfg.polling_mode = body["polling_mode"]
    if "polling_time" in body:
        cfg.polling_time = str(body["polling_time"])[:5]
    # 前端以「分钟」为单位展示，这里统一换算为秒存储，避免单位歧义
    if "interval_minutes" in body:
        try:
            minutes = int(float(body["interval_minutes"]))
            cfg.polling_interval = max(15, minutes) * 60  # 最小 15 分钟
        except (TypeError, ValueError):
            return error("interval_minutes 必须是数字（最小 15）")
    elif "polling_interval" in body:
        try:
            cfg.polling_interval = max(900, int(body["polling_interval"]))  # 最小 900 秒
        except (TypeError, ValueError):
            return error("polling_interval 必须是整数秒（最小 900）")
    if "data_retention_days" in body:
        try:
            cfg.data_retention_days = max(0, int(body["data_retention_days"]))
        except (TypeError, ValueError):
            return error("data_retention_days 必须是整数")
    if isinstance(body.get("rooms"), dict):
        cfg.rooms = body["rooms"]
    if isinstance(body.get("excluded_entities"), list):
        cfg.excluded_entities = body["excluded_entities"]

    cfg.save()
    rt.reload_config()
    return ok(
        {
            "message": "采集配置已更新",
            "enabled": cfg.polling_enabled,
            "mode": cfg.polling_mode,
            "interval": cfg.polling_interval,
            "time": cfg.polling_time,
        }
    )


# ── 统计与日历 ─────────────────────────────────────────────────────────────

async def collect_calendar(request: Request):
    """月视图热力图。走 collect_days 索引，绝不全表扫描。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    month = request.query_params.get("month") or now_local(
        rt.config.tz_offset_hours
    ).strftime("%Y-%m")
    try:
        year, mon = (int(x) for x in month.split("-")[:2])
        last_day = _calendar.monthrange(year, mon)[1]
    except (ValueError, _calendar.IllegalMonthError):
        return error("month 格式应为 YYYY-MM")

    start_day = f"{year:04d}-{mon:02d}-01"
    end_day = f"{year:04d}-{mon:02d}-{last_day:02d}"
    counts = await asyncio.to_thread(rt.store.day_counts, start_day, end_day)
    days = [
        {"day": f"{year:04d}-{mon:02d}-{d:02d}", "events": counts.get(f"{year:04d}-{mon:02d}-{d:02d}", 0)}
        for d in range(1, last_day + 1)
    ]
    values = [d["events"] for d in days if d["events"] > 0]
    return ok(
        {
            "month": f"{year:04d}-{mon:02d}",
            "days": days,
            "max": max(values) if values else 0,
            "total": sum(values),
            "covered_days": len(values),
        }
    )


async def collect_stats(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    stats = await asyncio.to_thread(rt.store.stats)
    end = now_local(rt.config.tz_offset_hours)
    start = end - timedelta(days=29)
    trend_counts = await asyncio.to_thread(
        rt.store.day_counts, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    )
    trend = []
    for i in range(30):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        trend.append({"day": day, "events": trend_counts.get(day, 0)})
    rooms = await asyncio.to_thread(rt.store.distinct_rooms)
    return ok({"stats": stats, "trend": trend, "rooms": rooms})


async def collect_events(request: Request):
    """事件明细查询，供概览与洞察页取样。"""
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    params = request.query_params
    try:
        limit = max(1, min(int(params.get("limit", 100)), 1000))
        offset = max(0, int(params.get("offset", 0)))
    except ValueError:
        return error("limit / offset 必须是整数")
    rooms = [r for r in (params.get("rooms") or "").split(",") if r]
    rows = await asyncio.to_thread(
        rt.store.query_events,
        params.get("start") or None,
        params.get("end") or None,
        rooms or None,
        None,
        None,
        params.get("person") or None,
        limit,
        offset,
        params.get("order", "desc"),
    )
    return ok({"events": rows, "count": len(rows)})


# ── 旧端点别名（薄转发，契约不变）───────────────────────────────────────────

async def poller_status(request: Request):
    _, err = require_user(request)
    if err:
        return err
    cfg = runtime(request).config
    return ok(
        {
            "enabled": cfg.polling_enabled,
            "mode": cfg.polling_mode,
            "interval": cfg.polling_interval,
            "time": cfg.polling_time,
            "last_poll_time": cfg.last_poll_time,
        }
    )


async def poller_trigger(request: Request):
    response = await collect_trigger(request)
    return response


async def poller_history(request: Request):
    _, err = require_user(request)
    if err:
        return err
    jobs = await asyncio.to_thread(runtime(request).store.list_jobs, 20)
    return ok({"history": jobs})


async def poller_history_status(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    stats = await asyncio.to_thread(rt.store.stats)
    collected = await asyncio.to_thread(rt.store.collected_days)
    persons = await asyncio.to_thread(rt.store.distinct_persons)
    return ok(
        {
            "status": "running" if rt.collector.is_running else "idle",
            "total_events": stats.get("total_events", 0),
            "persons": persons,
            "collected_dates": collected,
        }
    )


async def poller_calendar(request: Request):
    _, err = require_user(request)
    if err:
        return err
    rt = runtime(request)
    collected = await asyncio.to_thread(rt.store.collected_days)
    if not collected:
        return ok({"calendar": []})
    counts = await asyncio.to_thread(
        rt.store.day_counts, collected[0], collected[-1]
    )
    return ok({"calendar": [{"date": d, "events": c} for d, c in counts.items()]})


ROUTES = [
    Route("/api/collect/trigger", collect_trigger, methods=["POST"]),
    Route("/api/collect/backfill", collect_backfill, methods=["POST"]),
    Route("/api/collect/progress", collect_progress, methods=["GET"]),
    Route("/api/collect/cancel", collect_cancel, methods=["POST"]),
    Route("/api/collect/jobs", collect_jobs, methods=["GET"]),
    Route("/api/collect/jobs/{job_id}", collect_job_detail, methods=["GET"]),
    Route("/api/collect/status", collect_status, methods=["GET"]),
    Route("/api/collect/enable", collect_enable, methods=["POST"]),
    Route("/api/collect/config", collect_config, methods=["POST"]),
    Route("/api/collect/calendar", collect_calendar, methods=["GET"]),
    Route("/api/collect/stats", collect_stats, methods=["GET"]),
    Route("/api/collect/events", collect_events, methods=["GET"]),
    # 旧端点别名
    Route("/api/poller/status", poller_status, methods=["GET"]),
    Route("/api/poller/trigger", poller_trigger, methods=["POST"]),
    Route("/api/poller/config", collect_config, methods=["POST"]),
    Route("/api/poller/enable", collect_enable, methods=["POST"]),
    Route("/api/poller/history", poller_history, methods=["POST", "GET"]),
    Route("/api/poller/history/status", poller_history_status, methods=["GET"]),
    Route("/api/poller/calendar", poller_calendar, methods=["GET"]),
]
