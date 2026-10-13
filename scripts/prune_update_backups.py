"""WebUI 自更新的备份件回收（宿主侧 sidecar 的看家门）。

背景（2026-10-13 现读）：`host_update.sh` 每收到一次更新请求就 `tar czf` 一枚
`data/update-backup-<时间戳>.tar.gz`，⛔ 从来没有回收过 —— 部署目录里 **64 枚共 143 MB**
（最旧 2026-10-10 14:33，那是 sidecar bring-up 期按分钟连点留下的）。按分钟连点是量得出的故障模式
（标记件卡住没被消费＝每分钟一枚），所以这不是"将来可能满"，是"已经在长"。

口径（写死在代码里，测试逐条锁）：
- ⛔ 只删 `update-backup-YYYYMMDD-HHMMSS.tar.gz` 这一种形状，别的名字一律不碰；
- ⛔ 不递归子目录、⛔ 不跟随符号链接（链接本身也跳过，⛔ 顺着删到目录外）；
- 按 mtime 新→旧保留 `--keep` 枚（默认 10），其余删除；`--keep` 小于 1 直接拒绝（防"清空"手滑）；
- ⛔ 只用于 MA 自己的 data 目录；别席的 `/tmp/vs*`、`.log` 一类文件与本脚本无关。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

PREFIX = "update-backup-"
SUFFIX = ".tar.gz"
STEM_RE = re.compile(r"^update-backup-\d{8}-\d{6}$")


def candidates(dir_path: str) -> list[tuple[str, str]]:
    """返回符合形状的真实文件，按 mtime 新→旧排序，元素为 (文件名, 全路径)。"""
    rows = []
    for name in os.listdir(dir_path):
        if not (name.startswith(PREFIX) and name.endswith(SUFFIX)):
            continue
        if not STEM_RE.match(name[: -len(SUFFIX)]):
            continue
        full = os.path.join(dir_path, name)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        rows.append((os.stat(full).st_mtime, name, full))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [(name, full) for _mtime, name, full in rows]


def prune(dir_path: str, keep: int, dry_run: bool = False) -> tuple[int, list[str]]:
    """回收旧备份，返回 (保留枚数, 被删/将被删的文件名列表)。"""
    if keep < 1:
        raise ValueError("keep 必须 >= 1 —— 需要全清请人工逐条确认，本工具不提供 keep=0")
    files = candidates(dir_path)
    victims = files[keep:]
    removed = []
    for name, full in victims:
        if not dry_run:
            os.remove(full)
        removed.append(name)
    return len(files) - len(removed), removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回收 sidecar 更新备份件（只认 update-backup-*.tar.gz）")
    parser.add_argument("--dir", required=True, help="存放备份件的目录（MA 自己的 data 目录）")
    parser.add_argument("--keep", type=int, default=10, help="按 mtime 保留最近几枚（默认 10，最小 1）")
    parser.add_argument("--dry-run", action="store_true", help="只列将要删的，⛔ 不动手")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.dir):
        print(f"PRUNE_ERR=not_a_dir {args.dir}")
        return 2

    scanned = len(candidates(args.dir))
    try:
        kept, removed = prune(args.dir, args.keep, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"PRUNE_ERR={exc}")
        return 2

    print(
        f"PRUNE_SCANNED={scanned} PRUNE_KEPT={kept} PRUNE_DELETED={len(removed)} "
        f"DRY={1 if args.dry_run else 0}"
    )
    for name in removed:
        print(f"  {'would delete' if args.dry_run else 'deleted'}: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
