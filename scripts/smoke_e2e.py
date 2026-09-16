#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MA 部署后端到端冒烟（绕过 deploy_nas.sh）。

运行方式（容器内）：
    docker exec -i memory-agent python3 - < scripts/smoke_e2e.py

流程：等待 8000 就绪 → 取管理员 JWT → 建临时 app_token(butler) →
写记忆并断言来源被令牌派生（伪造 source=ma 落入 butler）→ 列表合并审计 →
吊销断言 401 → 清理测试数据。任一步失败 exit 1 并打印步骤名。
"""

from __future__ import annotations

import os
import sys
import time
import uuid

# ── 让脚本能 import memory_agent 包（容器内常见路径）──
for cand in ("/app/src", "/app", "/app/memory_agent", os.getcwd()):
    if cand and cand not in sys.path:
        sys.path.insert(0, cand)

import httpx  # noqa: E402

BASE = os.environ.get("MA_BASE_URL", "http://127.0.0.1:8000")
# 每次运行用唯一名，保证可重复执行（早期版本用固定名，失败退出会残留）
APP_TOKEN_NAME = f"smoke-butler-{uuid.uuid4().hex[:8]}"
LEGACY_TOKEN_NAME = "smoke-butler-core"  # 历史遗留名，运行开始时清理
MARKER = f"冒烟自测记忆-{uuid.uuid4().hex[:8]}"


def step(name: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✕"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        print(f"[FAIL] 冒烟在步骤「{name}」失败，退出。")
        sys.exit(1)


def wait_ready() -> None:
    """等待服务就绪。

    注意：uvicorn 在 startup 生命周期事件完成后才开始监听 8000，而 MA 的 startup
    会跑首轮 HA 采集 + 身份对账，实测约需 2 分钟。因此窗口必须 >120s，
    否则会在重启窗口内误报失败。
    """
    for i in range(180):
        try:
            r = httpx.get(f"{BASE}/health", timeout=3)
            if r.status_code == 200:
                print(f"  服务就绪（等待 {i}s）")
                return
        except Exception:
            pass
        if i and i % 30 == 0:
            print(f"  … 等待服务就绪 {i}s")
        time.sleep(1)
    step("等待服务 8000 就绪", False, "180s 内 /health 无响应")


def get_admin_token() -> str:
    from memory_agent.config import get_config
    from memory_agent.auth import AuthManager

    cfg = get_config()
    auth = AuthManager(cfg)
    users = auth.list_users()
    admin = next((u["username"] for u in users if u.get("is_admin")), None)
    if not admin:
        step("获取管理员令牌", False, "容器内无管理员账号，请先通过 WebUI 创建")
    return auth._create_token(admin, True)


def main() -> None:
    wait_ready()
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 0) 清理历史遗留令牌（早期脚本用固定名，失败退出会残留有效 butler 令牌）
    try:
        httpx.delete(
            f"{BASE}/api/config/app-tokens/{LEGACY_TOKEN_NAME}",
            headers=headers,
            timeout=10,
        )
    except Exception:
        pass

    # 1) 建临时 butler 应用令牌
    r = httpx.post(
        f"{BASE}/api/config/app-tokens",
        headers=headers,
        json={"name": APP_TOKEN_NAME, "source": "butler"},
        timeout=10,
    )
    step("创建 butler 应用令牌", r.status_code == 200,
         f"HTTP {r.status_code} body={r.text[:200]}" if r.status_code != 200 else "HTTP 200")
    app_token = (r.json().get("token") or "").strip()
    if not app_token:
        step("取得 butler 令牌明文", False, "响应无 token 字段")

    # 2) 用 butler 令牌写记忆，故意谎报 source=ma，应被派生为 butler
    r = httpx.post(
        f"{BASE}/api/agent/memories",
        headers={"Authorization": f"Bearer {app_token}"},
        # source_refs 为必填：参与式写回要求可溯源
        json={
            "text": MARKER,
            "source": "ma",
            "topic_key": "smoke_test",
            "source_refs": ["activity:smoke_e2e"],
        },
        timeout=30,
    )
    # 非 200 时把响应体带上，便于定位（去重/参数/向量库等问题都会返回 4xx 带 code）
    step("butler 令牌写入记忆", r.status_code == 200,
         f"HTTP {r.status_code} body={r.text[:300]}" if r.status_code != 200 else "HTTP 200")
    memory_id = (r.json().get("memory_id") or "").strip()

    step("取得写入返回 memory_id", bool(memory_id), memory_id or "响应无 memory_id")

    # 3) 断言来源派生：列表按 source=butler 过滤应包含该记忆。
    # 注意：list 接口返回的投影不含 text 字段（只有 memory_id/source/state 等），
    # 因此必须以 memory_id 断言，不能用文本。
    r = httpx.get(
        f"{BASE}/api/agent/memories",
        headers=headers,
        params={"source": "butler", "state": "all"},
        timeout=10,
    )
    step("列表查询(source=butler)", r.status_code == 200, f"HTTP {r.status_code}")
    rows = (r.json().get("memories") or [])
    hit = [m for m in rows if m.get("memory_id") == memory_id]
    step("来源派生为 butler（伪造 source=ma 被覆盖）",
         bool(hit) and hit[0].get("source") == "butler",
         f"命中 {len(hit)} 条，source={hit[0].get('source') if hit else '无'}")

    # 3b) 合并审计：全量列表也应包含该记忆（统一视图）
    r = httpx.get(
        f"{BASE}/api/agent/memories",
        headers=headers,
        params={"state": "all"},
        timeout=10,
    )
    all_rows = (r.json().get("memories") or [])
    step("合并审计（全量列表可见）",
         any(m.get("memory_id") == memory_id for m in all_rows),
         f"全量 {len(all_rows)} 条")

    # 3c) 反向断言：不应以 source=ma 出现（确认派生生效，未落入 ma）
    r = httpx.get(
        f"{BASE}/api/agent/memories",
        headers=headers,
        params={"source": "ma", "state": "all"},
        timeout=10,
    )
    ma_rows = (r.json().get("memories") or [])
    step("伪造来源未落入 ma",
         not any(m.get("memory_id") == memory_id for m in ma_rows),
         f"source=ma 下命中 {sum(1 for m in ma_rows if m.get('memory_id') == memory_id)} 条")

    # 4) 吊销 butler 令牌后，再次写入应被拒（401/403）
    r = httpx.delete(
        f"{BASE}/api/config/app-tokens/{APP_TOKEN_NAME}",
        headers=headers,
        timeout=10,
    )
    step("吊销 butler 令牌", r.status_code == 200, f"HTTP {r.status_code}")

    r = httpx.post(
        f"{BASE}/api/agent/memories",
        headers={"Authorization": f"Bearer {app_token}"},
        json={"text": MARKER + "-after-revoke", "source": "butler"},
        timeout=10,
    )
    step("吊销后写入被拒(401/403)",
         r.status_code in (401, 403),
         f"HTTP {r.status_code}")

    # 5) 清理测试记忆（置为 revoked，退出 live 检索）
    if memory_id:
        r = httpx.post(
            f"{BASE}/api/agent/memories/revoke",
            headers=headers,
            json={"memory_id": memory_id},
            timeout=10,
        )
        step("清理测试记忆(revoke)", r.status_code == 200, f"HTTP {r.status_code}")

    print("[OK] 端到端冒烟全部通过。")


if __name__ == "__main__":
    main()
