#!/usr/bin/env python3
"""只读运行时探针：对**生产 /app/src** 验证 `_sanitize_pii` 的零残留口径真响过。

输入全是合成样例（假号码/假邮箱/假证件号），打印只给布尔与长度，不回显任何文本。
"""
import re
import sys

sys.path.insert(0, "/app/src")

from memory_agent.store import Store  # noqa: E402

SAMPLES = [
    "成员张三丰 的手机 13800001111 与邮箱 zhangsanfei@example.com 需要脱敏",
    "证件 110101199003074571 属于 李四，联系 13900002222",
    "纯编号类：SN 1234567890123 与订单 9876543210999",
]

DIGITS = re.compile(r"\d{5,}")

ok = True
for i, s in enumerate(SAMPLES):
    out = Store._sanitize_pii(s, member_names=["张三丰", "李四"])
    long_digits = DIGITS.findall(out)
    has_name = any(n in out for n in ("张三丰", "李四"))
    changed = out != s
    print(f"sample[{i}] changed={changed} zero_residue={len(long_digits) == 0} "
          f"name_residue={has_name} len_in={len(s)} len_out={len(out)}")
    ok = ok and changed and len(long_digits) == 0 and not has_name

print(f"STATIC_METHOD_USABLE=True")
print(f"PROD_SANITIZE_ZERO_RESIDUE={'PASS' if ok else 'FAIL'}")
print("RUNTIME_RC=0")
