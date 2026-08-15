"""Agent 记忆（参与式写回）端到端验收脚本。

在容器内运行：
    docker compose exec memory-agent python -m memory_agent.scripts.verify_agent_memory

覆盖链路：dry_run 校验 → 写入 staging → 晋升 live → 检索命中 →
反馈调信任 → 撤销 → 健康检查计数 → 回滚清理。
"""

import os
import sys
import time
import uuid

from memory_agent.config import Config
from memory_agent.store import SQLiteStore
from memory_agent.agent_memory import AgentMemoryService


def run() -> int:
    cfg = Config.load()
    # 允许外部覆盖（本地隔离验收时指向临时 db + 真实 chroma 地址）
    if os.environ.get("DB_PATH"):
        cfg.db_path = os.environ["DB_PATH"]
    if os.environ.get("CHROMA_HOST"):
        cfg.chroma_host = os.environ["CHROMA_HOST"]
    if os.environ.get("CHROMA_PORT"):
        cfg.chroma_port = int(os.environ["CHROMA_PORT"])

    store = SQLiteStore(cfg.db_path)
    svc = AgentMemoryService(store, chroma_host=cfg.chroma_host, chroma_port=cfg.chroma_port, cfg=cfg)

    tid = "verify-" + uuid.uuid4().hex[:8]
    session = "verify-session-" + uuid.uuid4().hex[:8]
    passed = 0
    failed = 0

    def check(name: str, cond: bool, extra: str = ""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name} {extra}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {extra}")

    print("== Agent 记忆端到端验收 ==")

    # 1. dry_run 无溯源应被拒绝
    r = svc.add_semantic_memory(
        text="测试：用户通常 23:00 入睡。",
        topic_key=tid,
        session_id=session,
        source_refs=[],
        dry_run=True,
    )
    check("dry_run 缺溯源被拒", not r.get("ok") and r.get("code") == 422,
          f"(code={r.get('code')})")

    # 2. 写入 staging（有溯源）
    ref = f"insight:{tid}:1"
    r = svc.add_semantic_memory(
        text="测试：用户通常 23:00 入睡。",
        topic_key=tid,
        session_id=session,
        source_refs=[ref],
        tags=["sleep", "schedule"],
        dry_run=False,
    )
    check("写入 staging 成功", r.get("ok") and r.get("memory_id"), f"(id={r.get('memory_id')})")
    mid = r.get("memory_id")

    # 3. 列表含该 staging
    lst = svc.list_agent_memories("staging")
    check("staging 列表可见", any(m["memory_id"] == mid for m in lst.get("memories", [])),
          f"(count={lst.get('count')})")

    # 4. 晋升 live
    pro = svc.promote_memory(mid, session_id=session, force=True)
    check("晋升 live", pro.get("ok") and pro.get("state") == "live",
          f"(state={pro.get('state')})")

    # 5. 检索命中
    hits = svc.retrieve("用户几点睡", top_k=5)
    check("检索命中 live", any(h["memory_id"] == mid for h in hits),
          f"(hits={len(hits)})")

    # 6. 反馈（不依赖 chroma）
    fb_ok = svc.feedback_memory(mid, True)
    check("反馈写入", fb_ok.get("ok"), "")

    # 7. 健康检查计数
    h = svc.health()
    states = h.get("states", {})
    check("health.live >= 1", (states.get("live", 0) >= 1),
          f"(states={states})")
    check("chroma 可用", bool(h.get("chroma_available")), "")

    # 8. 撤销
    rev = svc.revoke_memory(mid)
    check("撤销成功", rev.get("ok") and rev.get("state") == "revoked",
          f"(state={rev.get('state')})")

    # 9. 回滚清理
    rb = svc.rollback_agent_memory(session)
    rb_removed = rb.get("removed", 0)
    check("回滚清理", rb.get("ok") and rb_removed >= 1,
          f"(removed={rb_removed})")

    print(f"\n结果：通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
