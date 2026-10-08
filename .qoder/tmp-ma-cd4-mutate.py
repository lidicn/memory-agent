"""MA-裁4 变异红证：每条红必须咬住自己那把锁，跑完原样回写。

用法：python .qoder/tmp-ma-cd4-mutate.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_MCP = os.path.join(ROOT, "src", "memory_agent", "mcp_server.py")
SRC_STORE = os.path.join(ROOT, "src", "memory_agent", "store.py")
TESTS = ["tests/test_mcp_contract.py"]

MUT = [
    ("M1 lean 不截 stable_id", SRC_MCP,
     "        if len(stable_id) > DEVICE_HEALTH_STABLE_ID_MAX_CHARS:",
     "        if False:"),
    ("M2 截了却不登记 truncated 键", SRC_MCP,
     '            d["stable_id_truncated"] = True',
     "            pass"),
    ("M3 未引用实体的 note 整键删除", SRC_MCP,
     '            d["note"] = ""',
     '            d.pop("note", None)'),
    ("M4 fields=full 开关失效", SRC_MCP,
     '    if str(fields or "").strip().lower() == "full":',
     "    if False:"),
    ("M5 has_more 退回拿 offset 判", SRC_MCP,
     "    has_more = bool(page) and consumed < full",
     "    has_more = rows_offset < full"),
    ("M7 has_more 不看 total", SRC_MCP,
     "    has_more = bool(page) and consumed < full",
     "    has_more = bool(page)"),
    ("M6 分页排序少 entity_id 兜底", SRC_STORE,
     '        sql += " ORDER BY updated_at DESC, entity_id"',
     '        sql += " ORDER BY updated_at DESC"'),
    ("M8 摘要退回点名「分页参数」", SRC_MCP,
     '        f"已展示前 {kb_cap}KB。请换更窄的参数重试（时间窗 / 过滤 / limit-offset 等，"\n'
     '        f"以该工具在 tools/list 里的参数面为准）。"',
     '        f"已展示前 {kb_cap}KB。请用更窄的时间窗 / 分页参数 / 专用精简查询接口获取完整结果。"'),
]


def run_tests() -> tuple[int, list[str]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    failed = [ln.split("::")[1].split(" ")[0] for ln in p.stdout.splitlines()
              if ln.startswith("FAILED")]
    return p.returncode, failed


def main() -> int:
    rc, failed = run_tests()
    print(f"[baseline] RC={rc} failed={failed}")
    if rc != 0:
        print("baseline 不绿，变异结论无意义")
        return 2

    pristinies: dict[str, bytes] = {}
    for name, path, old, new in MUT:
        if path not in pristinies:
            with open(path, "rb") as fh:
                pristinies[path] = fh.read()
        raw = pristinies[path]
        text = raw.decode("utf-8")
        hits = text.count(old)
        if hits != 1:
            print(f"[{name}] 锚点命中 {hits} 次 ≠ 1，放弃")
            return 3
        with open(path, "wb") as fh:
            fh.write(text.replace(old, new).encode("utf-8"))
        rc2, failed2 = run_tests()
        with open(path, "wb") as fh:
            fh.write(raw)
        with open(path, "rb") as fh:
            restored = fh.read() == raw
        print(f"[{name}] RC={rc2} red={len(failed2)} {failed2} RESTORED={restored}")
        if rc2 == 0 or not restored:
            return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
