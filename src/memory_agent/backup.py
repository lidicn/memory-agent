#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份 / 灾难恢复（先于 v0.8 存量记忆改写就位）。

策略
----
* SQLite 主库：``VACUUM INTO`` 到 ``backup_dir/ma-<YYYYMMDD>.db``（原子、可独立打开）。
* chroma 数据目录：若 ``chroma_data_dir`` 可访问则整目录快照到
  ``backup_dir/chroma-<YYYYMMDD>``；否则跳过（chroma 可由 SQLite 经
  ``mirror_days`` + ``reindex_embeddings.py`` 完整重建，无需快照）。
* 轮转：保留最近 ``backup_retention`` 份（按文件名时间戳），超出删除最旧。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from typing import Any


class BackupManager:
    """每日备份 SQLite 主库 + 可选 chroma 目录快照，并做份数轮转。"""

    def __init__(self, config: Any = None):
        self.config = config

    def _cfg(self) -> Any:
        return self.config

    def _date_stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d")

    def run_once(self) -> dict:
        cfg = self._cfg()
        if not getattr(cfg, "backup_enabled", False):
            return {"ok": False, "reason": "backup_enabled 未开启"}
        backup_dir = (getattr(cfg, "backup_dir", "") or "/data/backups").strip()
        retention = int(getattr(cfg, "backup_retention", 14) or 14)
        db_path = getattr(cfg, "db_path", "") or ""
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except Exception as exc:
            return {"ok": False, "reason": f"无法创建备份目录 {backup_dir}: {exc}"}

        stamp = self._date_stamp()
        results: dict = {"ok": True, "date": stamp}

        # ── SQLite VACUUM 快照 ──
        if db_path and os.path.exists(db_path):
            dest = os.path.join(backup_dir, f"ma-{stamp}.db")
            try:
                # VACUUM INTO 不允许目标文件已存在（同一天内重跑会直接报错），
                # 因此先删除当日旧快照再导出，保证可重复执行。
                if os.path.exists(dest):
                    os.remove(dest)
                # mode=ro：只读打开源库，避免与运行中的写连接互锁；
                # VACUUM INTO 把整库导出到全新文件，不受只读限制。
                # 安全（审计 O1）：VACUUM INTO 目标路径来自配置，原先用 f-string 拼接
                # 存在 SQL 注入面（路径含引号可逃逸）。改用参数绑定；并对路径做基本
                # 校验作双保险。
                if "'" in dest or "\x00" in dest:
                    raise ValueError("备份目标路径含非法字符（引号/NUL）")
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    conn.execute("VACUUM INTO ?", (dest,))
                finally:
                    conn.close()
                results["db"] = dest
            except Exception as exc:
                results["db_error"] = f"{type(exc).__name__}: {exc}"
                results["ok"] = False
        else:
            results["db_error"] = f"主库不存在: {db_path}"
            results["ok"] = False

        # ── chroma 数据目录快照（可选）──
        chroma_dir = (getattr(cfg, "chroma_data_dir", "") or "").strip()
        if chroma_dir and os.path.isdir(chroma_dir):
            dest = os.path.join(backup_dir, f"chroma-{stamp}")
            try:
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(chroma_dir, dest)
                results["chroma"] = dest
            except Exception as exc:
                results["chroma_error"] = f"{type(exc).__name__}: {exc}"
        else:
            results["chroma"] = "skipped（chroma_data_dir 未配置或不可访问）"

        # ── 轮换 ──
        results["retained"] = self._rotate(backup_dir, retention)
        return results

    def _rotate(self, backup_dir: str, retention: int) -> int:
        """删除最旧的 db / chroma 快照，仅保留最近 retention 份。返回保留总数。"""
        try:
            db_entries = sorted(
                e for e in os.listdir(backup_dir)
                if e.startswith("ma-") and e.endswith(".db")
            )
        except Exception:
            db_entries = []
        try:
            chroma_entries = sorted(
                e for e in os.listdir(backup_dir)
                if e.startswith("chroma-") and os.path.isdir(os.path.join(backup_dir, e))
            )
        except Exception:
            chroma_entries = []

        for overflow in (db_entries[:-retention] if len(db_entries) > retention else []):
            try:
                os.remove(os.path.join(backup_dir, overflow))
            except OSError:
                pass
        for overflow in (chroma_entries[:-retention] if len(chroma_entries) > retention else []):
            try:
                shutil.rmtree(os.path.join(backup_dir, overflow))
            except OSError:
                pass
        return len(db_entries) + len(chroma_entries)
