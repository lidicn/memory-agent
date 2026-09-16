"""API 路由包

取代重构前的 ``webui.py`` 单文件。

顺带消除了一个隐患：``webui.py`` 与 ``webui/`` 目录同名并存，
仅靠「常规模块优先于命名空间包」的解析顺序侥幸工作 ——
一旦有人给 ``webui/`` 加上 ``__init__.py``，应用会立即启动失败。
"""

from starlette.routing import Route

from . import (
    acp_routes,
    agent_memory_routes,
    auth_routes,
    behavior_routes,
    collect_routes,
    config_routes,
    debug_routes,
    events_routes,
    face_routes,
    ha_routes,
    insight_routes,
    identity_routes,
    llm_routes,
    mcp_routes,
    nr_routes,
    member_routes,
    signal_routes,
    vision_routes,
    tv_routes,
    system_routes,
    arena_routes,
)

_MODULES = (
    auth_routes,
    behavior_routes,
    config_routes,
    collect_routes,
    mcp_routes,
    acp_routes,
    llm_routes,
    insight_routes,
    identity_routes,
    ha_routes,
    nr_routes,
    agent_memory_routes,
    member_routes,
    signal_routes,
    vision_routes,
    face_routes,
    tv_routes,
    debug_routes,
    events_routes,
    system_routes,
    arena_routes,
)


def get_routes() -> list[Route]:
    routes: list[Route] = []
    for module in _MODULES:
        routes.extend(module.ROUTES)
    return routes


__all__ = ["get_routes"]
