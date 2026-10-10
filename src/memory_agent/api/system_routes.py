"""系统 / WebUI 自更新（宿主侧 sidecar 模式）

DCD 20261010 裁定：
- Q1=B：不开放容器内写宿主源码（P0-10 安全红线），改为宿主侧 sidecar
- Q4=B：admin JWT + 二次确认（输入当前版本号）

流程：
  1. admin 在 WebUI 点"检查更新" → GET /api/system/update/check
  2. admin 点"更新"，输入当前版本号确认 → POST /api/system/update
  3. 容器写标记文件 /data/.update_request.json（宿主机 data 卷可见）
  4. 宿主 cron 每分钟轮询标记文件，执行 git fetch → checkout → py_compile → docker restart
  5. 宿主脚本写结果到 /data/.update_result.json
  6. admin 可通过 GET /api/system/update/status 查看进度
"""
from __future__ import annotations

import json
import logging
import os
import time

from memory_agent import __version__ as __app_version__
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..config import get_config
from .deps import require_admin

_LOG = logging.getLogger("system.update")

DATA_DIR = os.getenv("MA_DATA_DIR", "/data").rstrip("/") or "/data"
MARKER_PATH = os.path.join(DATA_DIR, ".update_request.json")
RESULT_PATH = os.path.join(DATA_DIR, ".update_result.json")

DEFAULT_REPO = "https://github.com/lidicn/memory-agent.git"
DEFAULT_BRANCH = "main"


async def get_version(request: Request):
    """返回当前版本信息。admin only。"""
    user, err = require_admin(request)
    if err:
        return err
    info: dict = {
        "version": __app_version__,
        "commit": "", "branch": "", "tag": "", "dirty": None,
    }
    try:
        cfg = get_config()
        info["update_repo_url"] = cfg.update_repo_url or DEFAULT_REPO
        info["update_branch"] = cfg.update_branch or DEFAULT_BRANCH
    except Exception:
        pass
    return JSONResponse(info)


async def check_update(request: Request):
    """读取宿主预检脚本写的更新预览文件。admin only。"""
    user, err = require_admin(request)
    if err:
        return err
    preview_path = os.path.join(DATA_DIR, ".update_available.json")
    try:
        if not os.path.isfile(preview_path):
            return JSONResponse({
                "ok": True,
                "has_update": False,
                "message": "宿主预检脚本尚未运行，请等待 5 分钟后重试",
            })
        with open(preview_path, encoding="utf-8") as f:
            preview = json.load(f)
        return JSONResponse({
            "ok": True,
            "has_update": preview.get("has_update", False),
            "local_commit": preview.get("local_commit", ""),
            "local_tag": preview.get("local_tag", ""),
            "latest_commit": preview.get("remote_commit", ""),
            "changelog": preview.get("changelog", []),
            "checked_at": preview.get("checked_at", ""),
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


async def apply_update(request: Request):
    """提交更新请求（admin JWT + 版本号二次确认）。

    DCD Q4=B：必须在 body 里传 confirm_version 且等于当前版本号。
    容器不执行 git 操作，只写标记文件给宿主 sidecar。
    """
    user, err = require_admin(request)
    if err:
        return err

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    confirm_version = (body.get("confirm_version") or "").strip()
    if not confirm_version:
        return JSONResponse({
            "ok": False,
            "error": "需要确认版本号",
            "detail": f"请在 body 中传 confirm_version=\"{__app_version__}\"",
        }, status_code=400)

    if confirm_version != __app_version__:
        return JSONResponse({
            "ok": False,
            "error": "版本号不匹配，已拒绝更新",
            "expected": __app_version__,
            "received": confirm_version,
        }, status_code=400)

    # 检查是否已有进行中的更新请求
    if os.path.isfile(MARKER_PATH):
        return JSONResponse({
            "ok": False,
            "error": "已有更新请求进行中，请等待宿主脚本执行完成",
        }, status_code=409)

    try:
        cfg = get_config()
    except Exception:
        cfg = None
    branch = (getattr(cfg, "update_branch", None) or DEFAULT_BRANCH).strip()

    # 写标记文件（原子写：先写临时文件再 rename）
    marker = {
        "ref": branch,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "requested_by": user.get("username", "admin"),
        "version": __app_version__,
    }
    tmp_path = MARKER_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, MARKER_PATH)

    # 清除旧结果
    if os.path.isfile(RESULT_PATH):
        os.remove(RESULT_PATH)

    _LOG.info("更新请求已提交：ref=%s by=%s", branch, user.get("username"))
    return JSONResponse({
        "ok": True,
        "message": "更新请求已提交，宿主脚本将在约1分钟内执行 git pull + 重启",
        "marker": MARKER_PATH,
        "expected_delay_sec": 60,
    })


async def update_status(request: Request):
    """查看更新进度。admin only。"""
    user, err = require_admin(request)
    if err:
        return err

    pending = os.path.isfile(MARKER_PATH)
    result = None
    if os.path.isfile(RESULT_PATH):
        try:
            with open(RESULT_PATH, encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None

    return JSONResponse({
        "pending": pending,
        "result": result,
        "current_version": __app_version__,
    })


async def breakers_status(request: Request):
    """各依赖断路器状态。admin only。"""
    user, err = require_admin(request)
    if err:
        return err
    from ..circuit_breaker import all_states
    states = all_states()
    return JSONResponse({
        "breakers": states,
        "degraded": [b["name"] for b in states if b.get("state") != "closed"],
    })


ROUTES = [
    Route("/api/system/version", get_version, methods=["GET"]),
    Route("/api/system/breakers", breakers_status, methods=["GET"]),
    Route("/api/system/update/check", check_update, methods=["GET"]),
    Route("/api/system/update", apply_update, methods=["POST"]),
    Route("/api/system/update/status", update_status, methods=["GET"]),
]

__all__ = ["ROUTES"]
