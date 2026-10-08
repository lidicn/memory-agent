"""变异自证：Q3-4 的 `window` 回显（tests/test_vma_insights_window_echo.py）。

每一格都要求「改坏 ⇒ 至少一把锁判红」，且每步复原后与原始字节完全相等；
基线档（什么都不改）必须 10 passed——门自证含「什么都不改」那一档（裁6 §三.4）。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "src", "memory_agent", "insights", "service.py")
PY = sys.executable
TEST = "tests/test_vma_insights_window_echo.py"

ORIGINAL = io.open(TARGET, encoding="utf-8", newline="").read()

WITH_WINDOW = ('    def _with_window(self, out: Dict[str, Any], tr: Any) -> Dict[str, Any]:\n'
               '        out["window"] = self._window_echo(tr)\n'
               '        return out\n')
TRY_TODICT = '                return dict(tr.to_dict() or {})\n'
FALLBACK = '            start_iso, end_iso = self._tr_days(tr)\n'
USAGE_TAIL = '        return self._with_window(out, tr)\n'

MUTS = [
    ("W1 usage 的 window 下架（只回显一次，另两条漏掉）",
     [(USAGE_TAIL, "        return out\n", 1)]),
    ("W2 _window_echo 恒回空 dict（有 tr 也说没口径）",
     [(TRY_TODICT, "                return {}\n", 1)]),
    ("W3 _window_echo 编一个固定窗（不来自本次 tr）",
     [('        try:\n            if hasattr(tr, "to_dict"):\n',
       '        return {"start": "2020-01-01T00:00:00", "end": "2020-01-02T00:00:00",\n'
       '                "start_ts": 1577836800.0, "end_ts": 1577923200.0, "days": 1.0,\n'
       '                "label": ""}\n        try:\n            if hasattr(tr, "to_dict"):\n',
       1)]),
    ("W4 降级信封不带 window（出错时把口径一起弄丢）",
     [(WITH_WINDOW,
       '    def _with_window(self, out: Dict[str, Any], tr: Any) -> Dict[str, Any]:\n'
       '        if out.get("ok"):\n            out["window"] = self._window_echo(tr)\n'
       '        return out\n', 1)]),
    ("W6 window 截成日粒度（等于本次 tr 的 to_dict 这一格就该红）",
     [(TRY_TODICT,
       '                d = dict(tr.to_dict() or {})\n'
       '                return {"start": str(d.get("start") or "")[:10],\n'
       '                        "end": str(d.get("end") or "")[:10],\n'
       '                        "days": d.get("days")}\n', 1)]),
]


def run_suite():
    p = subprocess.run([PY, "-m", "pytest", TEST, "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
    lines = [l for l in (p.stdout or "").splitlines()
             if " passed" in l or " failed" in l or "error" in l.lower()]
    return p.returncode, (lines[-1].strip() if lines else "(no summary)")


def write(text):
    payload = text.encode("utf-8")        # 先算字节再开文件：open(...,'wb') 会立刻截断
    with io.open(TARGET, "wb") as fh:
        fh.write(payload)


rc, summary = run_suite()
print("BASELINE rc=%s %s" % (rc, summary))
if rc != 0:
    print("基线就不绿，变异台账无意义")
    sys.exit(1)

bitten = 0
for name, edits in MUTS:
    mutated = ORIGINAL
    ok = True
    for old, new, count in edits:
        if mutated.count(old) < count:
            print("SKIP %s：锚点 %r 命中 %d 次（需 %d）" % (name, old[:40], mutated.count(old), count))
            ok = False
            break
        idx = mutated.find(old)
        for _ in range(count):
            mutated = mutated[:idx] + new + mutated[idx + len(old):]
            idx = mutated.find(old, idx + len(new))
    if not ok:
        continue
    write(mutated)
    rc, summary = run_suite()
    red = rc != 0
    bitten += 1 if red else 0
    print("%s 判红=%s rc=%s %s" % (name, red, rc, summary))
    write(ORIGINAL)
    restored = io.open(TARGET, encoding="utf-8", newline="").read() == ORIGINAL
    print("   RESTORED=%s" % restored)

rc, summary = run_suite()
print("FINAL rc=%s %s RESTORED_FINAL=%s BITTEN=%d/%d"
      % (rc, summary, io.open(TARGET, encoding="utf-8", newline="").read() == ORIGINAL,
         bitten, len(MUTS)))
