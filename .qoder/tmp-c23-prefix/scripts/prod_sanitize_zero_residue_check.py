#!/usr/bin/env python3
"""只读运行时探针：证明 `_sanitize_pii` 的**零残留**口径在目标代码树上真响过。

用途：`2a418a4` 把手机号/邮箱/身份证的"部分打码"改成整段掩码后，PII 审计 S1 的判定
从"有残段"变为"无残段"。但**落码 ≠ 生效**——生产挂载目录曾长期在跑修复前的旧掩码写法。
本探针直接对目标源码调一次函数，用合成输入（假姓名/假号码/假邮箱/假证件号）验三件事：
输出变了、输出里不再有 ≥5 位连续数字、原文里的姓名不再出现。

只读：不碰数据库、不碰网络、不写文件。打印只给布尔与长度，**不回显脱敏后的文本**
（真实调用里那段文本可能含用户内容）。

用法（容器内，对生产码）：
    docker exec memory-agent python /tmp/xxx.py            # 默认 sys.path=/app/src
本地（对指定树）：
    python scripts/prod_sanitize_zero_residue_check.py --src /path/to/src
"""
from __future__ import annotations

import argparse
import re
import sys

# 合成样例：号码/邮箱/证件号都是文档用的假值，不是任何真实实体的数据。
SAMPLES = [
    ("成员张三丰 的手机 13800001111 与邮箱 zhangsanfei@example.com 需要脱敏", "张三丰"),
    ("证件 110101199003074571 属于 李四，联系 13900002222", "李四"),
    ("纯编号类：SN 1234567890123 与订单 9876543210999", ""),
]

_LONG_DIGITS = re.compile(r"\d{5,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/app/src", help="要验证的 src 根目录")
    args = ap.parse_args()

    sys.path.insert(0, args.src)
    try:
        from memory_agent.store import Store
    except Exception as exc:
        print(f"IMPORT_FAIL {type(exc).__name__}: {exc}")
        return 2

    ok = True
    for i, (text, name) in enumerate(SAMPLES):
        names = [name] if name else None
        out = Store._sanitize_pii(text, member_names=names)
        residue_digits = _LONG_DIGITS.findall(out)
        residue_name = bool(name) and name in out
        changed = out != text
        print(f"sample[{i}] changed={changed} zero_residue={not residue_digits} "
              f"name_residue={residue_name} len_in={len(text)} len_out={len(out)}")
        ok = ok and changed and not residue_digits and not residue_name

    print(f"PROD_SANITIZE_ZERO_RESIDUE={'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
