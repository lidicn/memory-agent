#!/usr/bin/env python3
"""vMA-1.2.2 召回验收基线：20 条提示词跑命中率。

在容器内运行：
    docker exec memory-agent python /tmp/recall_baseline.py
"""
import sys, os, time

# 确保在容器内正确的工作目录
os.chdir("/app")

from memory_agent.config import Config
from memory_agent.store import Store
from memory_agent.agent_memory import AgentMemoryService
from memory_agent.history import HistoryManager

TEST_CASES = [
    # ── 公共记忆召回 ──
    ("怎么判断家里有人？", "防盗门", "", "公共:在家判断规则"),
    ("小凯周六有什么安排", "钢琴课", "", "公共:钢琴课"),
    ("家里晚上一般几点看电视", "22:00", "", "公共:作息规律"),
    ("作息时间是什么样的", "07:00", "", "公共:作息(语义近邻)"),
    ("客厅最活跃是什么时候", "客厅", "", "公共:客厅活跃"),
    ("电视开着能不能判断有人", "电视", "", "语义:电视与在家判断"),
    ("凌晨门磁响了怎么回事", "凌晨", "", "语义:凌晨门磁"),
    ("周末有什么课", "钢琴", "", "语义:周末课程"),
    ("防盗门传感器", "防盗门", "", "关键词:防盗门"),
    ("钢琴", "钢琴", "", "关键词:钢琴"),
    ("作息", "作息", "", "关键词:作息"),
    ("在家", "门", "", "关键词:在家判断"),
    ("晚上几点安静下来", "23:00", "", "语义:安静时间"),

    # ── member_id 隔离 ──
    ("behavior record for member A", "test_A", "test_A_MA005CMP_9ac89561", "成员A隔离"),
    ("behavior record for member B", "test_B", "test_B_MA005CMP_9ac89561", "成员B隔离"),

    # ── 负例：应返回空或不命中 ──
    ("张三的私人偏好", None, "", "负例:不存在人名"),
    ("明天天气怎么样", None, "", "负例:天气话题"),
    ("今天吃什么", None, "", "负例:饮食话题"),
    ("股票行情", None, "", "负例:金融话题"),
    ("世界杯", None, "", "负例:体育话题"),
]

def main():
    cfg = Config.load()
    store = Store(cfg.db_path, cfg.tz_offset_hours)
    history = HistoryManager(cfg, store)
    svc = AgentMemoryService(cfg, store, history)

    results = []
    hit_count = 0
    member_ok_count = 0

    print("=" * 72)
    print("vMA-1.2.2 召回验收基线报告")
    print("=" * 72)

    for i, (question, expected_kw, expected_member, note) in enumerate(TEST_CASES, 1):
        t0 = time.time()
        hits = svc.retrieve(question, top_k=5, member_id=expected_member)
        elapsed = round((time.time() - t0) * 1000)

        found = False
        matched_text = ""
        if expected_kw is None:
            # 负例：hits 为空或不包含关键词就算通过
            found = len(hits) == 0
            matched_text = "(empty)" if not hits else f"got {len(hits)} hits (expected empty)"
        else:
            for h in hits:
                if expected_kw in (h.get("text", "") or ""):
                    found = True
                    matched_text = h["text"][:60]
                    break

        # 成员隔离验证
        member_ok = True
        for h in hits:
            if expected_member:
                if h.get("member_id", "") != expected_member:
                    member_ok = False

        # schema 字段验证
        has_schema = all(h.get("schema") == "ma-recall/1" for h in hits) if hits else True

        if found:
            hit_count += 1
        if member_ok:
            member_ok_count += 1

        status = "PASS" if found else "MISS"
        flag = "OK " if found else "MISS"
        mi_flag = "lock" if member_ok else "LEAK"
        print(f"  [{flag}] #{i:02d} [{mi_flag:4s}] hits={len(hits)} ({elapsed:4d}ms) {question[:35]:35s} | {note}")
        results.append((i, question, status, len(hits), member_ok, has_schema, elapsed, note))

    total = len(results)
    schema_ok = sum(1 for r in results if r[5])
    print()
    print("-" * 72)
    print(f"总用例: {total}")
    print(f"命中率: {hit_count}/{total} = {hit_count/total*100:.1f}%")
    print(f"成员隔离: {member_ok_count}/{total}")
    print(f"schema 字段: {schema_ok}/{total}")
    print(f"平均延迟: {sum(r[6] for r in results)/total:.0f}ms")
    print()
    print("注意: live 记忆仅 5 条（3 公共 + 2 测试残留），命中率受数据量限制。")
    print("本基线用于验证召回机制正确性（fail-closed + schema + 隔离），非内容丰富度指标。")

if __name__ == "__main__":
    main()
