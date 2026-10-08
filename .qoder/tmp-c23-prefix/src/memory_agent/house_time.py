"""家庭墙钟的机制层对接口：主路径 `homesdk.time`，MA 自身小时偏移退化为 fallback。

依据：`ADM联动主题注册表与消息契约.md` §四（统一走 homesdk.time）+
`ADM联动执行计划-MA.md` 第 0 步 ②③ + DCD `20261001-AF-homesdk接入四问-裁定.md` 问题 2。

**为什么要有这一层**：MA 的「家庭墙钟」此前只有 `store.now_local(tz_offset_hours)` 一种形态——
固定小时偏移表达不了 DST，而契约裁定的口径是 IANA 名。这一层把两种形态收成一个接缝：
装了 `homesdk>=0.3.1` 且家里**按名字**声明了时区（`HOMESDK_TZ` / `TZ` / `AF_TZ`），就走
`homesdk.time`（IANA、DST 正确）；否则退回 MA 自己的偏移算法。

**门（`is_active`）**：homesdk 可得 ≠ homesdk 当家。只有时区是被显式命名的时候才交给它——
库里那个 `Asia/Shanghai` 默认值是一次猜测，不能因为多装了一个包就把已经跑着的、
`tz_offset_hours` 配成别的家庭的时间轴换掉。`TZ_OFFSET_HOURS` 是 MA 自己的旧口径，
按契约只作过渡别名，MA 照旧直接用它，不必绕一层。

**调用点一律不变**：`now_local(tz_offset_hours)` 的签名保留，偏移参数在 homesdk 当家时
只作 fallback 值使用——这是刻意的，见 `now_local` 的文档字符串。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from types import ModuleType
from typing import Any, Optional

__all__ = [
    "homesdk_time",
    "is_active",
    "reset_homesdk_probe",
    "set_fallback_hours",
    "house_tz",
    "utc_offset_hours",
    "now_local",
    "status",
]

#: ``None`` = 还没探测过；``False`` = 探测过且不可用；模块对象 = 可用。
_probe: Optional[Any] = None

#: MA 自己的小时偏移快照，由 `config.get_config()` 每次重建配置时刷新（见 `set_fallback_hours`）。
_fallback_hours_cache: Optional[float] = None

#: homesdk `house_tz_status()["source"]` 里属于「按 IANA 名声明」的那几个键。
#: ``env:TZ`` 覆盖裸 `TZ` 与 `HOMESDK_TZ`（homesdk 报键名时不带前缀），`env:AF_TZ` 同理。
_NAMED_SOURCES = ("env:TZ", "env:AF_TZ")


def homesdk_time() -> Optional[ModuleType]:
    """返回 `homesdk.time`，不可用时返回 ``None``。探测结果会缓存，热更新后需显式重置。"""
    global _probe
    if _probe is None:
        try:
            from homesdk import time as _homesdk_time  # 0.3.1 起才有 time 模块
        except ImportError:
            _probe = False
        else:
            _probe = _homesdk_time
    return _probe if _probe is not False else None


def reset_homesdk_probe() -> bool:
    """清除探测缓存，让下一次调用重新尝试导入。

    与第七轮 CRITICAL-1/2 同一形态的接缝：`pip install`/换 PYTHONPATH 之后进程里的
    旧「不可用」判定会永久生效，热更新必须能把它解锁。返回重置前是否处于已探测态。
    """
    global _probe, _fallback_hours_cache
    was_probed = _probe is not None
    _probe = None
    _fallback_hours_cache = None
    return was_probed


def is_active() -> bool:
    """当前主路径是不是 homesdk.time：可得 **且** 家庭时区是被显式命名的。

    判定 live 读环境变量（不快照），这样 `reload_config()` 之外改了键也立刻生效，
    与 insights 层注入值的口径一致。
    """
    hs = homesdk_time()
    if hs is None:
        return False
    try:
        raw = hs.house_tz_status()
    except Exception:  # noqa: BLE001 - 机制层探测失败一律退回 MA 自己的钟
        return False
    return bool(raw.get("resolved_by_name")) and str(raw.get("source") or "") in _NAMED_SOURCES


def house_tz() -> tzinfo:
    """家庭时区的 tzinfo：homesdk 当家时是 IANA  ZoneInfo，否则按 MA 偏移造固定偏移。"""
    hs = homesdk_time()
    if hs is not None and is_active():
        return hs.house_tz()
    return timezone(timedelta(hours=_fallback_hours()))


def hours_label(hours: float) -> str:
    """`UTC±HH[:MM]` 的后半段；负偏移由调用方的 `+`/`-` 前缀之外自行承担。"""
    whole = int(abs(hours))
    minutes = int(round((abs(hours) - whole) * 60))
    prefix = "-" if hours < 0 else "+"
    return f"{prefix}{whole:02d}:{minutes:02d}" if minutes else f"{prefix}{whole:02d}"


def utc_offset_hours() -> Optional[float]:
    """homesdk 当前解析出的家庭时区偏移（小时）；不当家时返回 ``None`` 交调用方兜底。"""
    if not is_active():
        return None
    offset = datetime.now(house_tz()).utcoffset()
    if offset is None:
        return None
    return offset / timedelta(hours=1)


def now_local(fallback_offset_hours: float) -> datetime:
    """家庭墙钟的 naive datetime。

    **签名里的偏移在 homesdk 当家时不参与换算**——这是契约 §四 的口径
    （键名 `HOMESDK_TZ` 优先，`TZ_OFFSET_HOURS` 只作过渡别名），不是笔误。
    之所以仍然把偏移传进来而不是改成无参：MA 有 50+ 个调用点从
    `config.tz_offset_hours`/`store.tz_offset_hours` 取值，未装 homesdk 的部署
    （以及所有历史环境）必须照旧工作，改无参就等于把 fallback 砍掉。
    """
    hs = homesdk_time()
    if hs is not None and is_active():
        return hs.house_now().replace(tzinfo=None, microsecond=0)
    return datetime.now(timezone(timedelta(hours=fallback_offset_hours))).replace(
        tzinfo=None, microsecond=0
    )


def set_fallback_hours(hours: Optional[float]) -> None:
    """由 `config.get_config()` 每次构建配置时刷新的小时偏移快照。

    不缓存就得在每次换算时调 `get_config()`，而那是读一遍 config.json 的量——
    时区偏移会出现在每条事件写入的路径上（第六轮长跑审计同类形态）。
    传 ``None`` 表示交回懒加载。
    """
    global _fallback_hours_cache
    _fallback_hours_cache = None if hours is None else float(hours)


def _fallback_hours() -> float:
    if _fallback_hours_cache is None:
        from .config import get_config

        set_fallback_hours(getattr(get_config(), "tz_offset_hours", 8.0))
    return _fallback_hours_cache if _fallback_hours_cache is not None else 8.0


def status() -> dict:
    """供 `/api/health` 类出口回显：这台机器现在按哪套钟、依据哪个键。"""
    available = homesdk_time() is not None
    if available and is_active():
        raw = homesdk_time().house_tz_status()  # type: ignore[union-attr]
        offset = raw.get("utc_offset")
        return {
            "backend": "homesdk.time",
            "homesdk_available": True,
            "tz_name": raw.get("tz_name"),
            "source": raw.get("source"),
            "resolved_by_name": raw.get("resolved_by_name"),
            "tzdata_available": raw.get("tzdata_available"),
            "utc_offset_hours": (
                offset / timedelta(hours=1) if isinstance(offset, timedelta) else None
            ),
        }
    hours = _fallback_hours()
    source = "TZ_OFFSET_HOURS"
    reason = None
    if available:
        # 装了库但没按名声明时区：必须让排障的人一眼看出"为什么没走主路径"。
        raw = homesdk_time().house_tz_status()  # type: ignore[union-attr]
        source = str(raw.get("source") or "default")
        reason = "homesdk 在场但家庭时区未按 IANA 名声明，沿用 MA 的 tz_offset_hours"
    return {
        "backend": "ma_fallback",
        "homesdk_available": available,
        "tz_name": f"UTC{hours_label(hours)}",
        "source": source,
        "resolved_by_name": False,
        "tzdata_available": False,
        "utc_offset_hours": hours,
        **({"reason": reason} if reason else {}),
    }
