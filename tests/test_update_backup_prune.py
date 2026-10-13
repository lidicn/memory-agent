"""sidecar 更新备份件回收的回归锁（`scripts/prune_update_backups.py`）。

为什么要有这一格：`host_update.sh` 每收一次 WebUI 更新请求就 `tar czf` 一枚备份，
2026-10-13 现读部署目录里 **64 枚共 143 MB**、⛔ 从来没有回收过。这不是理论风险：
最旧那批是 2026-10-10 14:33 起**按分钟**连排的（标记件没被消费掉时 cron 每分钟跑一次）。
回收脚本一旦删错东西或被人顺手关掉，损失落在 NAS 的 data 卷上且不可逆 ⇒ 形状与边界都要有锁。

口径来源见脚本 docstring：只认 `update-backup-YYYYMMDD-HHMMSS.tar.gz`、不递归、不跟随符号链接、
按 mtime 保留最近 N 枚、`--keep < 1` 拒绝。
"""

import importlib.util
import io
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "prune_update_backups.py")
_HOST_SH = os.path.join(_ROOT, "scripts", "host_update.sh")


def _load():
    spec = importlib.util.spec_from_file_location("prune_update_backups", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk(dir_path, name, mtime):
    path = os.path.join(str(dir_path), name)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("x")
    os.utime(path, (mtime, mtime))
    return path


def _names(dir_path):
    return set(os.listdir(str(dir_path)))


# ─────────────────────────── 保留 newest N ───────────────────────────


def test_prune_keeps_newest_by_mtime_and_deletes_the_rest(tmp_path):
    mod = _load()
    for i in range(12):
        _mk(tmp_path, "update-backup-202610%02d-%02d0000.tar.gz" % (10 + i // 6, i % 6), 1_700_000_000 + i * 60)

    kept, removed = mod.prune(str(tmp_path), keep=5)

    assert kept == 5
    assert len(removed) == 7
    survivors = _names(tmp_path)
    assert len(survivors) == 5
    # 留下的必须是最新的 5 枚（i=7..11）
    for i in range(7, 12):
        assert ("update-backup-202610%02d-%02d0000.tar.gz" % (10 + i // 6, i % 6)) in survivors
    assert "update-backup-20261010-000000.tar.gz" not in survivors


def test_keep_larger_than_inventory_deletes_nothing(tmp_path):
    mod = _load()
    _mk(tmp_path, "update-backup-20261011-223920.tar.gz", 1_700_000_000)
    kept, removed = mod.prune(str(tmp_path), keep=10)
    assert (kept, removed) == (1, [])
    assert _names(tmp_path) == {"update-backup-20261011-223920.tar.gz"}


# ─────────────────────────── 边界：只认这一种形状 ───────────────────────────


def test_prune_leaves_everything_that_is_not_an_update_backup(tmp_path):
    mod = _load()
    lookalikes = [
        "update-backup-20261011-223920.tar",      # 后缀不对
        "update-backup-notes.txt",                 # 中间不是时间戳
        "update-backup-2026-10-11.tar.gz",         # 时间戳形状不对
        "update-backup-manual-keep.tar.gz",        # 前后缀都对，但中间不是时间戳（人手工留的一份）
        "backup-20261011-223920.tar.gz",           # 前缀不对
        "docker-compose.yml",
        "update.log",
    ]
    for n, name in enumerate(lookalikes):
        _mk(tmp_path, name, 1_700_000_000 + n)
    _mk(tmp_path, "update-backup-20261001-000000.tar.gz", 1_700_000_000)
    os.mkdir(os.path.join(str(tmp_path), "update-backup-20261002-000000.tar.gz"))  # 同名目录

    kept, removed = mod.prune(str(tmp_path), keep=1)

    assert removed == []                     # 唯一那枚真备份正好被 keep=1 保住
    assert kept == 1
    assert set(lookalikes).issubset(_names(tmp_path))
    assert "update-backup-20261002-000000.tar.gz" in _names(tmp_path)  # 目录不动


def test_prune_does_not_follow_symlinks(tmp_path):
    mod = _load()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = _mk(outside, "real_data.tar.gz", 1_700_000_000)
    link = os.path.join(str(tmp_path), "update-backup-20260101-000000.tar.gz")
    try:
        os.symlink(victim, link)
    except (OSError, NotImplementedError):
        assert True, "本平台无法建符号链接，跳过"
        return

    kept, removed = mod.prune(str(tmp_path), keep=1)

    assert (kept, removed) == (0, [])
    assert os.path.isfile(victim)            # 链接指向的真文件⛔ 未被删


# ─────────────────────────── dry-run 与 keep 下限 ───────────────────────────


def test_dry_run_reports_the_same_victims_but_deletes_nothing(tmp_path):
    mod = _load()
    for i in range(4):
        _mk(tmp_path, "update-backup-20261010-00000%d.tar.gz" % i, 1_700_000_000 + i)

    kept, planned = mod.prune(str(tmp_path), keep=2, dry_run=True)

    assert kept == 2
    assert planned == ["update-backup-20261010-000001.tar.gz", "update-backup-20261010-000000.tar.gz"]
    assert len(_names(tmp_path)) == 4

    rc = mod.main(["--dir", str(tmp_path), "--keep", "2", "--dry-run"])
    assert rc == 0
    assert len(_names(tmp_path)) == 4


def test_keep_below_one_is_refused(tmp_path):
    mod = _load()
    _mk(tmp_path, "update-backup-20261010-000000.tar.gz", 1_700_000_000)

    try:
        mod.prune(str(tmp_path), keep=0)
    except ValueError:
        pass
    else:
        raise AssertionError("keep=0 必须被拒绝（那等于一键清空全部备份）")

    rc = mod.main(["--dir", str(tmp_path), "--keep", "0"])
    assert rc == 2
    assert _names(tmp_path) == {"update-backup-20261010-000000.tar.gz"}


def test_missing_dir_exits_two(tmp_path):
    mod = _load()
    assert mod.main(["--dir", os.path.join(str(tmp_path), "nope")]) == 2


# ─────────────────────────── 接线形状：host_update.sh 真调它 ───────────────────────────


def test_host_script_wires_the_pruner_after_the_backup(tmp_path):
    """备份一落地就回收旧件；顺序反了（先删后备）会让本次备份自己进回收射程。"""
    with io.open(_HOST_SH, encoding="utf-8") as fh:
        sh = fh.read()

    assert "prune_update_backups.py" in sh, "host_update.sh 不再调用回收脚本 ⇒ 143 MB 那种增长会复发"
    call_idx = sh.index('python3 "$PRUNER"')
    backup_idx = sh.index('tar czf "$BACKUP"')
    assert call_idx > backup_idx, "回收必须排在备份创建之后"
    assert 'PRUNER="$REPO_DIR/scripts/prune_update_backups.py"' in sh[:call_idx], "调用点要用仓库内那份脚本，⛔ 依赖 PATH"

    # 旧部署目录在"这次更新之前"还没有这个脚本 ⇒ 必须带存在性护栏，⛔ 让 cron 因缺文件而报错
    assert 'if [ -f "$PRUNER" ]; then' in sh[:call_idx], "缺护栏：脚本还没被拉下来时 cron 每次都要红一下"
    tail = sh[call_idx:].splitlines()[0]
    assert "|| log" in tail, "回收失败只记 WARN —— ⛔ 不能因为回收失败就把这次自更新判死"

    m = re.search(r"prune_update_backups\.py.*?--keep\s+(\d+)", sh, re.S)
    assert m is not None, "调用点要显式给 --keep"
    assert 2 <= int(m.group(1)) <= 20, "keep 要有上下界：<2 一次误算就清零，>20 等于没收口"


def test_pruner_runs_end_to_end_on_a_temp_tree(tmp_path):
    """端到端：真的用 python3 起一次进程（本席位本机跑过＝形状锁之外的真读数）。"""
    for i in range(6):
        _mk(tmp_path, "update-backup-20261010-12000%d.tar.gz" % i, 1_700_000_000 + i)

    proc = subprocess.run(
        [sys.executable, _SCRIPT, "--dir", str(tmp_path), "--keep", "2"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PRUNE_SCANNED=6 PRUNE_KEPT=2 PRUNE_DELETED=4 DRY=0" in proc.stdout
    assert len(_names(tmp_path)) == 2
