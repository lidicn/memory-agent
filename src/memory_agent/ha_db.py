#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Home Assistant MariaDB（recorder 库）只读客户端 —— 方案 B 采集源。

与 ``HAClient.get_history`` 返回**完全一致的结构**，因此 ``CollectService``
的差分逻辑（``_events_from_series``）无需改动即可复用。

为什么直连 DB 更快
------------------
HA 的 REST ``/api/history/period`` 每次请求都要经过应用层序列化，且默认按
10 个实体一批、单批 60s 超时；直连 recorder 库则是一条带索引的 SQL：
``states`` 走 ``(metadata_id, last_updated_ts)`` 索引，一次可拉数百实体。
对一周回填（上千实体 × 数万状态）而言，从「小时级」降到「秒级」。

schema 自适配
-------------
HA recorder 表结构在 2024.2 做了拆分，这里在运行时探测并缓存：

* ``split``（>= 2024.2）：``states`` + ``states_meta`` + ``state_attributes``。
  实测中 ``states.attributes`` / ``states.last_changed`` 可能为空，权威数据在
  ``state_attributes.shared_attrs``（经 ``attributes_id``）与 epoch 列
  ``last_updated_ts`` / ``last_changed_ts``。
* ``monolithic``（<= 2024.1）：``states`` 自带 ``entity_id`` / ``attributes`` /
  ``last_changed`` / ``last_updated``。

本客户端**只做 SELECT**，绝不写库；连接断开会自动重连一次。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pymysql
import pymysql.cursors


def _iso_from_ts(ts: Optional[float]) -> str:
    """把 unix  epoch（秒）转成 ISO-8601 UTC 字符串。

    HA recorder 的 ``last_updated_ts`` / ``last_changed_ts`` 是 UTC epoch，
    始终有值，比可能为 NULL 的 ``last_updated`` 字符列更可靠。
    """
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


class HADBClient:
    """HA recorder 库只读客户端。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        db: str = "homeassistant",
        query_batch: int = 500,
        timeout: int = 30,
        enabled: bool = True,
        tz_offset_hours: float = 8.0,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.db = db
        self.query_batch = max(1, int(query_batch))
        self.timeout = int(timeout)
        self.enabled = enabled
        self.tz_offset_hours = float(tz_offset_hours)
        self._conn: Optional[pymysql.connections.Connection] = None
        self._schema_mode: Optional[str] = None

    # ── 连接管理 ─────────────────────────────────────────────────────────

    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.db,
            connect_timeout=10,
            read_timeout=self.timeout,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _get_conn(self) -> pymysql.connections.Connection:
        if self._conn is None or not self._conn.open:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # ── schema 探测（运行时，缓存） ─────────────────────────────────────────

    def detect_schema(self) -> str:
        if self._schema_mode is not None:
            return self._schema_mode
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema=%s",
                    (self.db,),
                )
                tables = {r["table_name"] for r in cur.fetchall()}
            mode = (
                "split"
                if ("states_meta" in tables and "state_attributes" in tables)
                else "monolithic"
            )
            print(f"[ha_db] schema_mode={mode} tables={sorted(tables)}")
            self._schema_mode = mode
            return mode
        except Exception as exc:
            # 探测失败时不阻断采集：已知目标环境为 split，作为安全默认
            print(f"[ha_db] schema 探测失败，回退 split: {exc}")
            self._schema_mode = "split"
            return "split"

    # ── SQL 构造 ─────────────────────────────────────────────────────────

    def _ts_from_iso(self, iso: str) -> float:
        """把 ISO 字符串转成 UTC epoch（秒）。

        调用方（poller）传入的通常是配置时区的 naive 本地时间，例如
        ``2026-08-04T19:10:00``；HA DB 的 ``last_updated_ts`` 存的是 UTC epoch，
        因此必须把 naive 时间按 ``tz_offset_hours`` 解释后再转 epoch。
        若字符串已带时区信息，则直接转 UTC epoch。
        """
        if not iso:
            return 0.0
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                return 0.0
        if dt.tzinfo is None:
            # naive datetime 视为本地时间（按配置时区）
            dt = dt.replace(tzinfo=timezone(timedelta(hours=self.tz_offset_hours)))
        return dt.astimezone(timezone.utc).timestamp()

    def _build_query(self, mode: str) -> str:
        if mode == "split":
            # 用 epoch 列保证时间戳有值；属性走 state_attributes.shared_attrs
            return (
                "SELECT sm.entity_id AS entity_id, s.state AS state, "
                "COALESCE(sa.shared_attrs, s.attributes) AS attributes, "
                "COALESCE(s.last_changed_ts, s.last_updated_ts) AS last_changed_ts, "
                "s.last_updated_ts AS last_updated_ts "
                "FROM states s "
                "JOIN states_meta sm ON sm.metadata_id = s.metadata_id "
                "LEFT JOIN state_attributes sa ON sa.attributes_id = s.attributes_id "
                "WHERE sm.entity_id IN ({ph}) AND s.last_updated_ts BETWEEN %s AND %s "
                "ORDER BY sm.entity_id, s.last_changed_ts"
            )
        # monolithic：列直接可用；时间戳用 UNIX_TIMESTAMP 转 epoch
        return (
            "SELECT entity_id, state, attributes, "
            "COALESCE(UNIX_TIMESTAMP(last_changed), UNIX_TIMESTAMP(last_updated)) "
            "AS last_changed_ts, "
            "UNIX_TIMESTAMP(last_updated) AS last_updated_ts "
            "FROM states "
            "WHERE entity_id IN ({ph}) AND UNIX_TIMESTAMP(last_updated) BETWEEN %s AND %s "
            "ORDER BY entity_id, last_changed"
        )

    @staticmethod
    def _row_to_series(row: dict) -> dict:
        attrs_raw = row.get("attributes")
        try:
            attrs = json.loads(attrs_raw) if attrs_raw else {}
        except (json.JSONDecodeError, TypeError):
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = {}
        state = row.get("state")
        if state is None:
            state = ""
        return {
            "entity_id": row["entity_id"],
            "state": state,
            "attributes": attrs,
            "last_changed": _iso_from_ts(row.get("last_changed_ts")),
            "last_updated": _iso_from_ts(row.get("last_updated_ts")),
        }

    # ── 公共 API ─────────────────────────────────────────────────────────

    def get_history(
        self, entity_ids: List[str], start_iso: str, end_iso: Optional[str] = None
    ) -> Dict[str, list]:
        """按实体批量拉取状态历史，返回 ``{entity_id: [记录...]}``。

        记录结构与 ``HAClient.get_history`` 完全一致，可被
        ``CollectService._events_from_series`` 直接消费。
        """
        if not entity_ids:
            return {}
        mode = self.detect_schema()
        start_ts = self._ts_from_iso(start_iso)
        end_ts = self._ts_from_iso(end_iso) if end_iso else time.time()
        if end_ts <= start_ts:
            end_ts = start_ts + 1.0

        result: Dict[str, list] = {}
        conn = self._get_conn()
        query_tmpl = self._build_query(mode)
        for i in range(0, len(entity_ids), self.query_batch):
            batch = entity_ids[i : i + self.query_batch]
            ph = ",".join(["%s"] * len(batch))
            sql = query_tmpl.format(ph=ph)
            params = list(batch) + [start_ts, end_ts]
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    for row in cur.fetchall():
                        rec = self._row_to_series(row)
                        result.setdefault(rec["entity_id"], []).append(rec)
            except pymysql.OperationalError:
                # 连接可能已被服务端回收，重连一次后重试
                self.close()
                conn = self._get_conn()
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    for row in cur.fetchall():
                        rec = self._row_to_series(row)
                        result.setdefault(rec["entity_id"], []).append(rec)
        return result

    def ping(self) -> Dict[str, Any]:
        """测试连接并返回 schema 模式，供设置页「测试连接」与 MCP 复用。"""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            mode = self.detect_schema()
            return {"ok": True, "mode": mode, "error": ""}
        except pymysql.OperationalError as exc:
            return {
                "ok": False,
                "mode": self._schema_mode or "unknown",
                "error": f"连接/认证失败: {exc}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mode": self._schema_mode or "unknown",
                "error": f"未知错误: {exc}",
            }
