"""MA-裁3 变异红证：每条红必须咬住自己那把锁，跑完原样回写。

用法：python .qoder/tmp-ma-cd3-mutate.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PACK = os.path.join(ROOT, "src", "memory_agent", "feedback_pack.py")
SRC_ROUTES = os.path.join(ROOT, "src", "memory_agent", "api", "behavior_routes.py")
TESTS = ["tests/test_feedback_pack.py"]

MUT = [
    ("M1 anon 拿 S3 的结果冒充（分层变成一句空话）", SRC_PACK,
     "            trace_anon = anon_out if isinstance(anon_out, str) and anon_out else None",
     "            trace_anon = trace_sanitized"),
    ("M2 trace.txt 也过 S1（改了已交付产物的口径）", SRC_PACK,
     '                _add_text_member(tar, "trace.txt", trace_sanitized, safe_label)',
     '                _add_text_member(tar, "trace.txt", trace_anon or trace_sanitized, safe_label)'),
    ("M3 去掉成员姓名这一维校验", SRC_PACK,
     "    if hits:",
     "    if () and hits:"),
    ("M4 段落正则去掉 re.ASCII（全角数字过关）", SRC_PACK,
     "    re.ASCII,\n)",
     ")"),
    ("M5 段落白名单放宽成任意字符", SRC_PACK,
     '    r"|(?:(?:[a-z][a-z0-9]{0,31}))",',
     '    r"|(?:(?:.{1,32}))",'),
    ("M6 尾分隔符检查删除", SRC_PACK,
     "    if raw[-1] in _LABEL_SEG_SEP:",
     "    if False and raw[-1]:"),
    ("M7 白名单拒收后照样落盘", SRC_PACK,
     "    if not passed or not safe_label:",
     "    if False:"),
    ("M8 调用点把真 S1 入口换成 None", SRC_ROUTES,
     "            anon_sanitizer=rt.store.sanitize_feedback_text,",
     "            anon_sanitizer=None,"),
    ("M9 调用点把打包放回事件循环", SRC_ROUTES,
     "        result = await asyncio.to_thread(\n            build_feedback_pack,",
     "        result = build_feedback_pack("),
]


def run_tests() -> tuple[int, list[str]]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    failed = [ln.split("::", 1)[1].split(" ")[0] for ln in p.stdout.splitlines()
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
        text = pristinies[path].decode("utf-8")
        hits = text.count(old)
        if hits != 1:
            print(f"[{name}] 锚点命中 {hits} 次 ≠ 1，放弃")
            return 3
        with open(path, "wb") as fh:
            fh.write(text.replace(old, new).encode("utf-8"))
        rc2, failed2 = run_tests()
        with open(path, "wb") as fh:
            fh.write(pristinies[path])
        with open(path, "rb") as fh:
            restored = fh.read() == pristinies[path]
        print(f"[{name}] RC={rc2} red={len(failed2)} {failed2} RESTORED={restored}")
        if rc2 == 0 or not restored:
            return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
