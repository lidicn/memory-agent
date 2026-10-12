#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入期环境变量读数的收口（稳定性/功能性审计 20261012 · D1）。

模块级的裸 `int(os.getenv(...))` 读到非数字（`MA_VOICE_CACHE_MAX=2000条`、误留引号、空串）
就在 **import 期** 抛 ValueError：`import memory_agent.store` 失败 → uvicorn 起不来 →
容器进入启动失败循环。0 与负值更隐蔽：不报错，却把护栏语义反转（上限 0 = 这条护栏整条关闭）。
所以坏配置一律**记 warning + 回落默认值**，不让一个手滑的 env 杀死进程。
"""
import logging
import os

_LOG = logging.getLogger("memory_agent.env")


def _read(name):
    try:
        raw = os.getenv(name)
    except Exception:  # noqa: BLE001 — os.getenv 只在解释器状态坏掉时才抛
        return None
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _cast(name, default, to_number, lo, hi):
    raw = _read(name)
    if raw is None:
        return default
    try:
        value = to_number(raw)
    except (TypeError, ValueError, OverflowError):
        _LOG.warning("[env] %s=%r 不是合法数字，回落默认值 %s（坏配置不杀启动链路）", name, raw, default)
        return default
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        _LOG.warning("[env] %s=%s 越界（允许区间 %s..%s），回落默认值 %s", name, value, lo, hi, default)
        return default
    return value


def env_int(name, default, lo=None, hi=None):
    """读整数型环境变量；`3.5` 这类越界/畸形值按默认值处理而不是崩进程。"""
    return _cast(name, default, lambda s: int(float(s)), lo, hi)


def env_float(name, default, lo=None, hi=None):
    return _cast(name, default, float, lo, hi)
