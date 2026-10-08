"""按字节量 CR（交付纪律 §六.9：改完文件查 CR 必须为 0，且必须按字节量）。

文本模式读会吞掉 `\\r\\n` 的差别，所以这里一律 read_bytes + count(b"\\r")。
"""
import sys

FILES = ("src/memory_agent/insights/api.py",
         "src/memory_agent/insights/service.py",
         "src/memory_agent/insights/nlquery.py",
         "scripts/scan_qb_param_landing.py",
         "tests/test_vma_qb_param_landing.py",
         ".qoder/tmp-c37-remote13.sh",
         ".qoder/tmp-c37-driver13.sh",
         ".qoder/tmp-c37-mutate13.py",
         ".qoder/tmp-c37-probe-anom13.py",
         ".qoder/tmp-c37-emu13.py",
         ".qoder/tmp-c37-lint-remote13.py")
bad = 0
for f in FILES:
    data = open(f, "rb").read()
    cr = data.count(b"\r")
    print("%-45s CR=%d LF=%d BYTES=%d ENDS_NL=%s"
          % (f, cr, data.count(b"\n"), len(data), data.endswith(b"\n")))
    if cr:
        bad += 1
print("CR_CLEAN=%s" % ("OK" if bad == 0 else "FAIL(%d)" % bad))
sys.exit(1 if bad else 0)
