"""统一日志配置（Phase 3 可维护性）

背景（真实缺陷）
--------------
项目里有 16+ 个模块使用 ``logging.getLogger(__name__).info()/warning()``，
但**从未调用过 ``basicConfig`` / ``dictConfig``**。Python 的 root logger 默认
级别是 ``WARNING``，且没有 handler 时只走 ``logging.lastResort``（级别同样是
WARNING），于是：

* 所有 ``logger.info(...)`` 被**静默丢弃**——线上一句都看不到
  （排查时只能靠散落的 ``print``，而 print 恰好不受影响，造成"日志在但 info 不在"的错觉）
* 只有 ``warning`` 及以上能侥幸输出

因此本模块在**应用导入期**就配好 root logger：级别由 ``MA_LOG_LEVEL`` 控制
（默认 INFO），输出到 stdout（``docker logs`` 可见），并给第三方库降噪。
这也是后续把 ``print`` 迁移到 ``logging`` 的**前置条件**——否则一迁移就等于删日志。
"""
from __future__ import annotations

import logging
import os
import sys

DEFAULT_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# 默认抬到 WARNING 的吵闹第三方 logger
_NOISY_LOGGERS = ("httpx", "httpcore", "chromadb", "uvicorn.access", "apscheduler")

_configured = False


def configure_logging(force: bool = False) -> str:
    """配置根 logger，返回实际生效的级别名（如 ``"INFO"``）。

    :param force: True 时重建 handler（测试/重新配置用）
    只添加一次 handler，避免与 uvicorn 等外部配置重复叠加。
    """
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        return logging.getLevelName(root.level)

    level_name = (os.getenv("MA_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level, level_name = logging.INFO, "INFO"

    if force:
        for h in list(root.handlers):
            root.removeHandler(h)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(DEFAULT_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
        root.addHandler(handler)
    root.setLevel(level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    _configured = True
    return level_name
