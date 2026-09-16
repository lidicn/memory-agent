"""系统 / 在线更新（从 GitHub 拉取最新代码并自重启）

设计要点
--------
* 容器通过 docker-compose 把宿主机仓库根挂载到 ``/repo``（见 docker-compose.yml），
  因此容器内 ``git`` 操作作用在宿主机仓库（含被 bind 挂载的 ``./src``），更新即时生效。
* 安全约束：
  - 仅 ``git pull --ff-only``（fast-forward），绝不 ``--force`` / 合并，避免覆盖本地改动。
  - 工作树 dirty（有未提交改动）时拒绝更新，提示先 ``git stash`` / ``commit``。
  - 不触碰 ``/data`` 等持久化数据卷；配置以原子写、容错读为前提，升级不破坏。
  - 写操作（apply_update）需管理员。
* 重启策略：若配置了 ``restart_cmd`` 则执行它（如 ``docker compose restart`` 或
  ``systemctl restart``）；否则回退为读取 ``/proc/1/cmdline`` 通过 ``os.execv``
  重启当前进程（docker 绑定挂载下即加载新代码）。
"""
from __future__ import annotations

import asyncio
import logging
import os

from memory_agent import __version__ as __app_version__
import shlex
import subprocess
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..config import get_config
from .deps import require_admin

_LOG = logging.getLogger("system.update")

# docker-compose 挂载的宿主机仓库根；可用环境变量覆盖以适配非标准部署。
REPO_DIR = os.getenv("REPO_DIR", "/repo").rstrip("/") or "/repo"

DEFAULT_REPO = "https://github.com/lidicn/memory-agent.git"
DEFAULT_BRANCH = "main"


async def _git(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """在仓库目录执行 git，转线程避免阻塞事件循环。"""
    return await asyncio.to_thread(
        subprocess.run,
        args,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


async def get_version(request: Request):
    """返回当前版本信息：commit / branch / tag / dirty / 更新源。"""
    info: dict = {
        "version": __app_version__,
        "repo_dir": REPO_DIR,
        "commit": "", "branch": "", "tag": "", "dirty": None,
        "update_repo_url": "", "update_branch": "",
    }
    try:
        cfg = get_config()
        info["update_repo_url"] = cfg.update_repo_url or DEFAULT_REPO
        info["update_branch"] = cfg.update_branch or DEFAULT_BRANCH
    except Exception:
        pass
    try:
        info["commit"] = (await _git(["git", "rev-parse", "--short", "HEAD"])).stdout.strip()
        info["branch"] = (await _git(["git", "rev-parse", "--abbrev-ref", "HEAD"])).stdout.strip()
        info["tag"] = (await _git(["git", "describe", "--tags", "--always"])).stdout.strip()
        info["dirty"] = bool((await _git(["git", "status", "--porcelain"])).stdout.strip())
    except Exception as exc:  # 非 git 仓库 / 无 git 时优雅降级
        info["error"] = str(exc)
    return JSONResponse(info)


async def check_update(request: Request):
    """比对本地 HEAD 与远端分支，返回是否有更新。"""
    try:
        cfg = get_config()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    repo = (cfg.update_repo_url or DEFAULT_REPO).strip()
    branch = (cfg.update_branch or DEFAULT_BRANCH).strip()
    try:
        local = (await _git(["git", "rev-parse", "HEAD"])).stdout.strip()
        remote_raw = (await _git(["git", "ls-remote", repo, f"refs/heads/{branch}"])).stdout.strip()
        if not remote_raw:
            return JSONResponse(
                {"ok": False, "error": "无法获取远端引用，检查仓库地址或网络连通性"}
            )
        remote_commit = remote_raw.split()[0]
        return JSONResponse({
            "ok": True,
            "has_update": local != remote_commit,
            "local_commit": local[:12],
            "latest_commit": remote_commit[:12],
            "branch": branch,
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


async def apply_update(request: Request):
    """拉取最新代码并重启（管理员）。"""
    user, err = require_admin(request)
    if err:
        return err
    try:
        cfg = get_config()
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    repo = (cfg.update_repo_url or DEFAULT_REPO).strip()
    branch = (cfg.update_branch or DEFAULT_BRANCH).strip()

    try:
        # 1) dirty 检测：有未提交改动则拒绝，避免更新后状态混乱
        status = (await _git(["git", "status", "--porcelain"])).stdout.strip()
        if status:
            return JSONResponse({
                "ok": False,
                "dirty": True,
                "error": "工作树有未提交改动，已拒绝更新。请先在宿主机执行 git stash / commit，或使用 restart_cmd 托管的重启流程。",
            })

        # 2) fetch + fast-forward pull（绝不 force / merge）
        await _git(["git", "fetch", "origin", branch])
        pull = await _git(["git", "pull", "--ff-only", "origin", branch])
        if pull.returncode != 0:
            return JSONResponse({
                "ok": False,
                "error": "git pull --ff-only 失败（本地分支可能已分叉，需先 rebase）",
                "detail": (pull.stderr or pull.stdout).strip()[:500],
            })

        # 3) 重启：优先 restart_cmd，否则 re-exec 当前进程
        restart_cmd = (cfg.restart_cmd or "").strip()
        if restart_cmd:
            # 安全（审计 O2）：移除 shell=True，避免 restart_cmd（可经配置修改）
            # 造成命令注入。用 shlex 拆分后直接 exec，不再经过 shell 解释，
            # 因此不支持 &&/|/重定向 等 shell 语法；复合命令请写成脚本再由本字段调用。
            try:
                cmd_argv = shlex.split(restart_cmd)
            except ValueError as exc:
                return JSONResponse({
                    "ok": False,
                    "error": f"restart_cmd 解析失败（检查引号是否闭合）：{exc}",
                })
            if not cmd_argv:
                return JSONResponse({"ok": False, "error": "restart_cmd 为空"})
            subprocess.Popen(
                cmd_argv, cwd=REPO_DIR,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return JSONResponse({
                "ok": True, "restarting": True, "via": "restart_cmd",
                "message": (pull.stdout or "").strip()[:500],
            })

        await _reexec()
        return JSONResponse({
            "ok": True, "restarting": True, "via": "reexec",
            "message": (pull.stdout or "").strip()[:500],
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)})


async def _reexec() -> None:
    """通过 /proc/1/cmdline 重启当前进程（加载绑定挂载的新代码）。

    先让当前 HTTP 响应返回，再用 call_later 延迟触发 execv，避免响应未发出即被杀。
    """
    try:
        with open("/proc/1/cmdline", "rb") as f:
            parts = f.read().split(b"\x00")
        cmd = [p.decode("utf-8", "replace") for p in parts if p]
        if not cmd:
            return
        loop = asyncio.get_event_loop()
        loop.call_later(0.6, lambda: os.execv(cmd[0], cmd))
    except Exception as exc:  # 无 /proc（如 Windows 开发环境）时静默跳过
        _LOG.warning("reexec 不可用：%s", exc)


async def breakers_status(request: Request):
    """v0.9 离线降级：各依赖断路器状态（LLM / embedding …）。

    ``degraded`` 列出当前非 closed 的断路器名，便于判断「是否在降级运行」。
    """
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
]


__all__ = ["ROUTES"]
