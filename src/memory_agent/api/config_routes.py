"""配置与系统状态路由"""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from ..llm_client import LLMProvider, normalize_chat_url
from ..config import get_config
from .deps import error, json_body, mask_secret, ok, require_admin, require_user, runtime

# 允许通过 API 更新的字段白名单。
# 缺字段会导致前端提交被静默丢弃（重构前 llm_provider 就是这么丢的），
# 新增配置项时务必同步补齐。
WRITABLE_FIELDS = (
    "hass_server", "hass_token",
    "nr_url", "nr_user", "nr_pass",
    "redis_host", "redis_port",
    "chroma_host", "chroma_port", "chroma_mirror",
    "llm_provider", "llm_api_url", "llm_api_key", "llm_model",
    "llm_temperature", "llm_max_tokens", "llm_timeout", "llm_backends",
    "mcp_auth_token",
    "polling_enabled", "polling_mode", "polling_interval", "polling_time",
    "rooms", "excluded_entities",
    "data_retention_days", "first_run_lookback_hours",
    "tz_offset_hours",
    "ha_db_enabled", "ha_db_host", "ha_db_port", "ha_db_name", "ha_db_user",
    "ha_db_password", "ha_db_query_batch", "ha_db_query_timeout",
    "autoflow_acp_url", "autoflow_acp_token",
    "auto_discover_persona",
    "member_tag_agent_writeback",
    "mcp_response_max_bytes",
    # ── 豆包管家对接（外部服务窄接口令牌）────────────────────────────────
    "butler_token",
    # ── AutoFlow 竞技场对接（脱敏映射，可选；缺省按分区内稳定生成通用名）──
    "arena_desensitize",
    # ── 视觉识别（vision-behavior-spec）────────────────────────────────
    "vision_enabled", "vision_device_token",
    "go2rtc_base_url", "go2rtc_user", "go2rtc_pass",
    "vlm_base_url", "vlm_api_key", "vlm_model",
    "vlm_timeout_s", "vlm_max_retries", "vlm_endpoint_path",
    "vision_cameras",
    "vision_cooldown_s", "vision_max_per_hour", "vision_no_tv_interval_s",
    "vision_light_gate", "vision_snapshot_retention_days",
    # ── 电视截屏多模态（docs/电视截屏多模态识别功能_交接单.md）─────────
    "tv_media_player_entity", "tv_capture_timeout_s",
    "tv_mqtt_enabled", "tv_mqtt_host", "tv_mqtt_port",
    "tv_mqtt_user", "tv_mqtt_pass", "tv_mqtt_topic", "tv_mqtt_timeout_s",
)

SECRET_FIELDS = (
    "hass_token", "nr_pass", "llm_api_key", "mcp_auth_token", "jwt_secret",
    "ha_db_password", "autoflow_acp_token",
    "vision_device_token", "go2rtc_pass", "vlm_api_key",
    "butler_token", "tv_mqtt_pass",
)


def _is_masked(value) -> bool:
    """判断前端回传的是否是掩码占位值，是则不覆盖真实密钥。

    mask_secret 对短密钥(≤8 字符)输出整串 '*'，对长密钥输出 '<前4><8个*><后4>'。
    真实密钥几乎不会整串为星号、也不该包含 8 连星序列，据此稳健判定：
    - 整串星号（短密钥）→ 掩码；
    - 含 8 连星（长密钥 '<前4>********<后4>' 不论前 4 是否为明文）→ 掩码。
    此前还要求「以 * 开头」，对长密钥误判为非掩码，导致 re-save 时把真实密码
    覆盖成掩码占位（如 go2rtc_pass=longyin1003 → 掩码 long********in1003 被当
    成真值写回，下次取帧即 401）。现已放宽到「含 8 连星即可判定为掩码」。
    """
    if not isinstance(value, str) or not value:
        return False
    if set(value) == {"*"}:
        return True
    return "*" * 8 in value


def _mask_backends(backends) -> list:
    """对代理池中每条后端的 api_key 做掩码，避免明文返回前端。"""
    out = []
    for b in (backends or []):
        if not isinstance(b, dict):
            continue
        b = dict(b)
        if b.get("api_key"):
            b["api_key"] = mask_secret(b["api_key"])
        out.append(b)
    return out


def _restore_backend_keys(new_list, old_list) -> list:
    """前端回传的代理池里，未改动的 api_key 是掩码占位，需从原存储恢复真实值。"""
    old_list = old_list or []
    out = []
    for i, b in enumerate(new_list or []):
        if not isinstance(b, dict):
            continue
        b = dict(b)
        key = b.get("api_key", "")
        if _is_masked(key):
            if i < len(old_list) and isinstance(old_list[i], dict):
                b["api_key"] = old_list[i].get("api_key", "")
            else:
                b["api_key"] = ""
        out.append(b)
    return out


def _find_saved_backend(saved_list, incoming) -> dict | None:
    """按身份（name+model+api_url）在已保存配置里找到对应后端，而非依赖易错位的位置索引。

    前端列表顺序 / 重启后索引可能与服务端落盘不一致，用身份匹配可彻底规避「索引越界 → 发掩码 key → 401」。"""
    saved_list = saved_list or []
    if not isinstance(incoming, dict):
        return None
    inc = (incoming.get("name"), incoming.get("model"), incoming.get("api_url"))
    for b in saved_list:
        if (b.get("name"), b.get("model"), b.get("api_url")) == inc:
            return b
    # 退而求其次：仅按 model+api_url 匹配（允许显示名被改）
    for b in saved_list:
        if (b.get("model"), b.get("api_url")) == (incoming.get("model"), incoming.get("api_url")):
            return b
    return None


async def get_config_api(request: Request):
    _, err = require_user(request)
    if err:
        return err
    cfg = runtime(request).config
    return ok(
        {
            "hass_server": cfg.hass_server,
            "hass_token": mask_secret(cfg.hass_token),
            "nr_url": cfg.nr_url,
            "nr_user": cfg.nr_user,
            "nr_pass": mask_secret(cfg.nr_pass),
            "redis_host": cfg.redis_host,
            "redis_port": cfg.redis_port,
            "chroma_host": cfg.chroma_host,
            "chroma_port": cfg.chroma_port,
            "chroma_mirror": cfg.chroma_mirror,
            "llm_provider": cfg.llm_provider,
            "llm_api_url": cfg.llm_api_url,
            "llm_api_key": mask_secret(cfg.llm_api_key),
            "llm_model": cfg.llm_model,
            "llm_temperature": cfg.llm_temperature,
            "llm_max_tokens": cfg.llm_max_tokens,
            "llm_timeout": cfg.llm_timeout,
            "llm_backends": _mask_backends(cfg.llm_backends),
            "mcp_auth_token": mask_secret(cfg.mcp_auth_token),
            "polling_enabled": cfg.polling_enabled,
            "polling_mode": cfg.polling_mode,
            "polling_interval": cfg.polling_interval,
            "polling_time": cfg.polling_time,
            "rooms": cfg.rooms,
            "excluded_entities": cfg.excluded_entities,
            "data_retention_days": cfg.data_retention_days,
            "first_run_lookback_hours": cfg.first_run_lookback_hours,
            "tz_offset_hours": cfg.tz_offset_hours,
            "ha_db_enabled": cfg.ha_db_enabled,
            "ha_db_host": cfg.ha_db_host,
            "ha_db_port": cfg.ha_db_port,
            "ha_db_name": cfg.ha_db_name,
            "ha_db_user": cfg.ha_db_user,
            "ha_db_password": mask_secret(cfg.ha_db_password),
            "ha_db_query_batch": cfg.ha_db_query_batch,
            "ha_db_query_timeout": cfg.ha_db_query_timeout,
            "autoflow_acp_url": cfg.autoflow_acp_url,
            "autoflow_acp_token": mask_secret(cfg.autoflow_acp_token),
            "auto_discover_persona": cfg.auto_discover_persona,
            "butler_token": mask_secret(cfg.butler_token),
            "vision_enabled": cfg.vision_enabled,
            "vision_device_token": mask_secret(cfg.vision_device_token),
            "go2rtc_base_url": cfg.go2rtc_base_url,
            "go2rtc_user": cfg.go2rtc_user,
            "go2rtc_pass": mask_secret(cfg.go2rtc_pass),
            "vlm_base_url": cfg.vlm_base_url,
            "vlm_api_key": mask_secret(cfg.vlm_api_key),
            "vlm_model": cfg.vlm_model,
            "vlm_timeout_s": cfg.vlm_timeout_s,
            "vlm_max_retries": cfg.vlm_max_retries,
            "vlm_endpoint_path": cfg.vlm_endpoint_path,
            "vision_cameras": cfg.vision_cameras,
            "vision_cooldown_s": cfg.vision_cooldown_s,
            "vision_max_per_hour": cfg.vision_max_per_hour,
            "vision_no_tv_interval_s": cfg.vision_no_tv_interval_s,
            "vision_light_gate": cfg.vision_light_gate,
            "vision_snapshot_retention_days": cfg.vision_snapshot_retention_days,
            "db_path": cfg.db_path,
            "last_poll_time": cfg.last_poll_time,
            # 电视截屏多模态配置
            "tv_media_player_entity": cfg.tv_media_player_entity,
            "tv_capture_timeout_s": cfg.tv_capture_timeout_s,
            "tv_mqtt_enabled": cfg.tv_mqtt_enabled,
            "tv_mqtt_host": cfg.tv_mqtt_host,
            "tv_mqtt_port": cfg.tv_mqtt_port,
            "tv_mqtt_user": cfg.tv_mqtt_user,
            "tv_mqtt_pass": mask_secret(cfg.tv_mqtt_pass),
            "tv_mqtt_topic": cfg.tv_mqtt_topic,
            "tv_mqtt_timeout_s": cfg.tv_mqtt_timeout_s,
            "secrets_set": {f: bool(getattr(cfg, f, "")) for f in SECRET_FIELDS},
        }
    )


async def update_config_api(request: Request):
    _, err = require_admin(request)
    if err:
        return err
    body = await json_body(request)
    rt = runtime(request)
    cfg = rt.config

    changed = []
    for key in WRITABLE_FIELDS:
        if key not in body:
            continue
        value = body[key]
        if key == "llm_backends":
            value = _restore_backend_keys(value, getattr(cfg, "llm_backends", []))
        if key in SECRET_FIELDS and _is_masked(value):
            continue  # 用户没改密钥，保持原值
        current = getattr(cfg, key)
        # 数值字段做一次类型收敛，避免前端传字符串污染配置
        try:
            if isinstance(current, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
            elif isinstance(current, int) and not isinstance(value, bool):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
        except (TypeError, ValueError):
            return error(f"字段 {key} 类型非法")
        if current != value:
            setattr(cfg, key, value)
            changed.append(key)

    if changed:
        cfg.save()
        rt.reload_config()
    return ok({"message": "配置已更新", "changed": changed})


async def reveal_secret(request: Request):
    """按需读取密钥明文。仅管理员，且必须显式指定字段。"""
    _, err = require_admin(request)
    if err:
        return err
    body = await json_body(request)
    field = body.get("field", "")
    if field == "llm_backend_key":
        # 按身份匹配已保存后端，避免位置索引错位（前端索引 vs 服务端落盘不一致）导致越界。
        saved = _find_saved_backend(getattr(get_config(), "llm_backends", []), body.get("backend"))
        if saved is None:
            idx = body.get("index", 0)
            backs = getattr(get_config(), "llm_backends", []) or []
            if isinstance(idx, int) and 0 <= idx < len(backs):
                saved = backs[idx]
        if saved is None:
            return error("未找到匹配的后端")
        return ok({"field": field, "value": saved.get("api_key", "")})
    if field not in SECRET_FIELDS:
        return error("不支持的字段")
    return ok({"field": field, "value": getattr(runtime(request).config, field, "")})


async def test_connection(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    conn_type = body.get("type", "")
    rt = runtime(request)
    cfg = rt.config

    try:
        if conn_type == "ha":
            status = await asyncio.to_thread(rt.ha.get_status)
            if status.get("connected"):
                return ok({"message": f"HA 连接成功（{status.get('url')}）"})
            return error(f"HA 连接失败: {status.get('error', '无响应')}")

        if conn_type == "nr":
            status = await asyncio.to_thread(rt._nr_status)
            if status.get("connected"):
                return ok({"message": f"Node-RED 连接成功（HTTP {status.get('status_code')}）"})
            return error(f"Node-RED 连接失败: {status.get('error', status.get('status_code'))}")

        if conn_type == "llm":
            result = await rt.llm.ping()
            backends = result.get("backends", [])
            ok_count = sum(1 for b in backends if b.get("connected"))
            if result.get("connected"):
                return ok(
                    {
                        "message": (
                            f"LLM 代理池可用：{ok_count}/{len(backends)} 个后端连通"
                            f"（主模型 {result.get('model')}）"
                        ),
                        "endpoint": result.get("endpoint"),
                        "backends": backends,
                    }
                )
            return error(
                f"LLM 代理池不可用：{ok_count}/{len(backends)} 个后端连通",
                extra={"backends": backends},
            )

        if conn_type == "llm_backend":
            backend = body.get("backend") or {}
            if not backend.get("model") or not backend.get("api_key"):
                return error("请填写 model 与 api_key 后再测试")
            # api_key 为掩码（未改动）时，按身份从已保存配置还原真实 key，
            # 不再依赖前端位置索引，避免陈旧索引导致「发掩码 key → 401」。
            if _is_masked(backend.get("api_key", "")):
                saved = _find_saved_backend(getattr(get_config(), "llm_backends", []), backend)
                if saved:
                    backend = {**backend, "api_key": saved.get("api_key", "")}
            probe = LLMProvider(backend, cfg)
            r = await probe.ping()
            if r.get("connected"):
                return ok(
                    {
                        "message": f"连接成功（{r.get('model')}）",
                        "endpoint": r.get("endpoint"),
                        "backend": r,
                    }
                )
            return error(r.get("error", "连接失败"), extra={"backend": r})

        if conn_type == "chroma":
            # 只 ping 一下说明不了「向量库真的在工作」，跑一次写入→检索→清理的往返
            # 优先用前端表单当前值（还没保存也能先测），否则用运行时配置。
            # chroma_selftest 内部用临时 client，不会被「首次失败被记住」的缓存卡死。
            host = body.get("chroma_host") or cfg.chroma_host
            port = body.get("chroma_port") or cfg.chroma_port
            result = await asyncio.to_thread(
                rt.history.chroma_selftest, host=host, port=port
            )
            payload = {
                "message": result.get("summary") or result.get("error", ""),
                "steps": result.get("steps", []),
                "detail": {
                    "host": result.get("host"),
                    "collection": result.get("collection"),
                    "documents": result.get("documents"),
                    "mirror_enabled": result.get("mirror_enabled"),
                },
            }
            if result.get("ok"):
                return ok(payload)
            return error(
                result.get("summary") or result.get("error") or "向量库自检失败",
                extra=payload,
            )

        if conn_type == "ha_db":
            from ..ha_db import HADBClient

            ha_db = rt.ha_db
            if ha_db is None:
                # 尚未启用时，用表单当前值临时构造以「保存前先测」
                ha_db = HADBClient(
                    host=body.get("ha_db_host") or cfg.ha_db_host,
                    port=int(body.get("ha_db_port") or cfg.ha_db_port),
                    user=body.get("ha_db_user") or cfg.ha_db_user,
                    password=body.get("ha_db_password") or cfg.ha_db_password,
                    db=body.get("ha_db_name") or cfg.ha_db_name,
                    query_batch=int(body.get("ha_db_query_batch") or cfg.ha_db_query_batch),
                    timeout=int(body.get("ha_db_query_timeout") or cfg.ha_db_query_timeout),
                    enabled=True,
                    tz_offset_hours=cfg.tz_offset_hours,
                )
            if ha_db is None:
                return error("未配置 HA 数据库（请填写 host/user/password）")
            result = await asyncio.to_thread(ha_db.ping)
            if result.get("ok"):
                return ok({"message": f"HA MariaDB 连接成功（schema={result.get('mode')}）"})
            return error("HA MariaDB 连接失败：" + str(result.get("error", "未知错误")))

        if conn_type == "db":
            stats = await asyncio.to_thread(rt.store.stats)
            return ok(
                {
                    "message": (
                        f"数据库正常，事件总量 {stats['total_events']}，"
                        f"覆盖 {stats.get('days', 0)} 天"
                    ),
                    "detail": stats,
                }
            )

        return error(f"未知连接类型: {conn_type}")
    except Exception as exc:
        return error(f"{type(exc).__name__}: {exc}", 500)


async def health(request: Request):
    _, err = require_user(request)
    if err:
        return err
    return ok(await runtime(request).health())


# ── 应用令牌（TVPilot / DeskPilot）多令牌管理（v0.6）─────────────────────────
from ..app_tokens import get_app_token_store  # noqa: E402


async def list_app_tokens(request: Request):
    _, err = require_user(request)
    if err:
        return err
    return ok({"tokens": get_app_token_store().list_tokens()})


async def create_app_token(request: Request):
    _, err = require_user(request)
    if err:
        return err
    body = await json_body(request)
    name = (body.get("name") or "").strip()
    source = (body.get("source") or "").strip()
    res = get_app_token_store().generate(name, source)
    if not res.get("ok"):
        return error(res.get("error", "生成失败"))
    return ok(res)


async def revoke_app_token(request: Request):
    _, err = require_user(request)
    if err:
        return err
    name = request.path_params.get("name", "")
    if not get_app_token_store().revoke(name):
        return error("令牌不存在", 404)
    return ok({"message": f"已吊销: {name}"})


ROUTES = [
    Route("/api/config", get_config_api, methods=["GET"]),
    Route("/api/config", update_config_api, methods=["POST"]),
    Route("/api/config/test", test_connection, methods=["POST"]),
    Route("/api/config/reveal", reveal_secret, methods=["POST"]),
    Route("/api/config/app-tokens", list_app_tokens, methods=["GET"]),
    Route("/api/config/app-tokens", create_app_token, methods=["POST"]),
    Route("/api/config/app-tokens/{name}", revoke_app_token, methods=["DELETE"]),
    Route("/api/health", health, methods=["GET"]),
]
