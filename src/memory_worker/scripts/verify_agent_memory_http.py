"""通过 Web API 做 Agent 记忆端到端验收（本地 Python，调用容器 HTTP 端口）。

不依赖本地 chromadb：业务逻辑（含真实 chroma 检索）都在容器内执行。
- 临时注册测试账号用于登录；产生的记忆最后按 session 回滚，无生产副作用。
- 需要容器已加载最新代码（改动 agent_memory_routes 后需 restart）。

运行（在能访问容器 HTTP 端口的机器上）：
    python -m memory_worker.scripts.verify_agent_memory_http
环境变量：
    MW_BASE  容器地址，默认 http://192.168.2.200:8086
"""

import json
import os
import secrets
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("MW_BASE", "http://192.168.2.200:8086")
passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name} {extra}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def req(method, path, body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"ok": False, "error": e.reason}
    except Exception as e:
        return -1, {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    print(f"== Agent 记忆 Web API 验收 @ {BASE} ==")
    user = "verify_" + secrets.token_hex(4)
    pw = "verify123456"

    st, d = req("POST", "/api/auth/register", {"username": user, "password": pw})
    check("注册测试账号", st in (200, 201) or (st == 400 and "已存在" in str(d.get("error", ""))),
          f"(status={st})")

    st, d = req("POST", "/api/auth/login", {"username": user, "password": pw})
    token = d.get("access_token") or d.get("token")
    if st != 200 or not token:
        check("登录获取 token", False, f"(status={st}, {d})")
        print(f"\n结果：通过 {passed}，失败 {failed}（无法登录，中止）")
        return 1
    check("登录获取 token", True, f"(len={len(token)})")

    session = "verify-session-" + secrets.token_hex(4)
    tid = "verify-" + secrets.token_hex(4)
    ref = f"insight:{tid}:1"

    # 1. dry_run 无溯源 → 应被拒（422）
    st, d = req("POST", "/api/agent/memories",
                {"text": "测试：用户通常 23:00 入睡。", "topic_key": tid,
                 "session_id": session, "source_refs": [], "dry_run": True}, token=token)
    check("dry_run 缺溯源被拒", st == 422 and not d.get("ok"), f"(code={st})")

    # 2. 写入 staging（有溯源）
    st, d = req("POST", "/api/agent/memories",
                {"text": "测试：用户通常 23:00 入睡。", "topic_key": tid,
                 "session_id": session, "source_refs": [ref], "tags": ["sleep"]}, token=token)
    check("写入 staging 成功", st == 200 and d.get("ok") and d.get("memory_id"),
          f"(id={d.get('memory_id')}, state={d.get('state')})")
    mid = d.get("memory_id")

    # 3. 列表含该 staging
    st, d = req("GET", "/api/agent/memories?state=staging", token=token)
    check("staging 列表可见", st == 200 and any(m["memory_id"] == mid for m in d.get("memories", [])),
          f"(count={d.get('count')})")

    # 4. 晋升 live
    st, d = req("POST", "/api/agent/memories/promote",
                {"memory_id": mid, "session_id": session, "force": True}, token=token)
    check("晋升 live", st == 200 and d.get("ok") and d.get("state") == "live",
          f"(state={d.get('state')})")

    # 5. 检索命中
    st, d = req("POST", "/api/agent/memories/retrieve", {"question": "用户几点睡"}, token=token)
    check("检索命中 live", st == 200 and any(h["memory_id"] == mid for h in d.get("memories", [])),
          f"(hits={d.get('count')})")

    # 6. 反馈（有用）
    st, d = req("POST", "/api/agent/memories/feedback",
                {"memory_id": mid, "useful": True}, token=token)
    check("反馈写入", st == 200 and d.get("ok"), "")

    # 7. 健康检查
    st, d = req("GET", "/api/agent/memories/health", token=token)
    states = d.get("states", {})
    check("health.live >= 1", st == 200 and states.get("live", 0) >= 1,
          f"(states={states})")
    check("chroma 在容器内可用", bool(d.get("chroma_available")), "")

    # 8. 回滚清理（应在撤销之前：rollback 仅清理非 revoked 的会话记忆，
    #    会把 live 转回 revoked，等于完成测试会话的清理）
    st, d = req("POST", "/api/agent/memories/rollback", {"session_id": session}, token=token)
    check("回滚清理", st == 200 and d.get("ok") and (d.get("affected", 0) or d.get("removed", 0)) >= 1,
          f"(affected={d.get('affected')}, removed={d.get('removed')})")

    # 9. 撤销（对已回滚为 revoked 的记忆做幂等验证）
    st, d = req("POST", "/api/agent/memories/revoke", {"memory_id": mid}, token=token)
    check("撤销成功", st == 200 and d.get("ok") and d.get("state") == "revoked",
          f"(state={d.get('state')})")

    print(f"\n结果：通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
