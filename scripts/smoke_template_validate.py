#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板校验与修复 冒烟（部署后运行）。

    docker exec -i memory-agent python3 - < scripts/smoke_template_validate.py

覆盖：
1. 全量校验的状态分档是否符合实测预期；
2. 净水器统计 bug 是否修好（有真实数据，不再恒为 0）；
3. 失效模板（实体不存在）执行被**硬阻止**（409）；
4. 停用/恢复生效，且停用期间执行被拒。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "/app/src")

import httpx  # noqa: E402

from memory_agent.config import get_config  # noqa: E402
from memory_agent.auth import AuthManager  # noqa: E402

BASE = "http://127.0.0.1:8000"
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("[✓] " if ok else "[✕] ") + name + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def wait_ready() -> None:
    for i in range(180):
        try:
            if httpx.get(f"{BASE}/health", timeout=3).status_code == 200:
                print(f"  服务就绪（等待 {i}s）")
                return
        except Exception:
            pass
        time.sleep(1)
    print("[FAIL] 180s 内 /health 无响应")
    sys.exit(1)


def main() -> None:
    wait_ready()

    cfg = get_config()
    auth = AuthManager(cfg)
    users = auth.list_users()
    admin = next(u["username"] for u in users if u.get("is_admin"))
    H = {"Authorization": "Bearer " + auth._create_token(admin, True)}

    # 1) 全量校验
    r = httpx.post(f"{BASE}/api/templates/validate", headers=H, json={}, timeout=180)
    check("全量校验接口可用", r.status_code == 200, f"HTTP {r.status_code}")
    data = r.json()
    by_id = {t["template_id"]: t for t in (data.get("templates") or [])}
    print(f"  状态汇总 = {data.get('summary')}")
    for tid, t in by_id.items():
        print(f"    - {tid}: {t.get('status')}")

    # 2) 净水器统计修复：应算出真实数据
    r = httpx.post(f"{BASE}/api/insights/query", headers=H,
                   json={"template_id": "water_purifier_daily"}, timeout=180)
    check("净水器模板可执行", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        ents = r.json().get("entities") or []
        got = ents[0].get("result") if ents else {}
        total_ml = (got or {}).get("total_value", 0) or 0
        check("净水器统计不再恒为 0", total_ml > 0,
              f"total_value={total_ml}mL, count={(got or {}).get('count')}")

    # 3) 失效模板被硬阻止
    st = (by_id.get("sleep_time_pattern") or {}).get("status")
    check("sleep_time_pattern 检出为失效", st in ("missing", "stale", "no_data"),
          f"status={st}")
    r = httpx.post(f"{BASE}/api/insights/query", headers=H,
                   json={"template_id": "sleep_time_pattern"}, timeout=60)
    if st in ("missing", "stale"):
        check("失效模板执行被硬阻止", r.status_code == 409,
              f"HTTP {r.status_code} {r.text[:120]}")
    else:
        print(f"  （该模板状态为 {st}，仅警告不阻止，跳过硬阻止断言）")

    # 4) 停用 / 恢复
    r = httpx.post(f"{BASE}/api/templates/disable", headers=H,
                   json={"template_id": "sleep_time_pattern", "disabled": True}, timeout=60)
    check("停用模板", r.status_code == 200, f"HTTP {r.status_code}")
    r = httpx.post(f"{BASE}/api/insights/query", headers=H,
                   json={"template_id": "sleep_time_pattern"}, timeout=60)
    check("停用后执行被拒", r.status_code == 409, f"HTTP {r.status_code}")

    r = httpx.get(f"{BASE}/api/insights", headers=H, timeout=60)
    listed = {t["id"]: t for t in (r.json().get("templates") or [])}
    check("停用状态出现在列表",
          listed.get("sleep_time_pattern", {}).get("disabled") is True,
          f"disabled={listed.get('sleep_time_pattern', {}).get('disabled')}")

    r = httpx.post(f"{BASE}/api/templates/disable", headers=H,
                   json={"template_id": "sleep_time_pattern", "disabled": False}, timeout=60)
    check("恢复模板", r.status_code == 200, f"HTTP {r.status_code}")

    print()
    if FAILED:
        print(f"[FAIL] 冒烟有 {len(FAILED)} 项未通过：{FAILED}")
        sys.exit(1)
    print("[OK] 模板校验冒烟全部通过。")


if __name__ == "__main__":
    main()
