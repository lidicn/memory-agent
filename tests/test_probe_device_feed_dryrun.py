"""`scripts/probe_device_feed_dryrun.py` 的回归锁（设备 feed 干跑对照探针的**纯函数半边**）。

为什么锁它：`receipts/20261004-MA-进度盘点回执…【已受理】.md` 抄走了它的读数，而 `scripts/`
⛔ 在任何权威门的射程里（`.gates.toml` 的 `extra_skip_globs`）⇒ 由 `scripts/lock_face_ratchet.py`
记为欠锁。本锁把它**能在本机真跑的那两半**钉住。

★ 射程（写明⛔ 覆盖的部分，⛔ 拿半覆盖当全覆盖）：
- `full_days`：**"没跑完的一天⛔ 进日均"**这条判据（23 时段有事件才算整天）。
- `mask_factory`：**出境打码**——实体 id 命中成员姓名／设备友好名要换成 `**`。
  这半边 2026-10-14 现跑时抓出一处真漏（见 `test_case_folded_hit_is_replaced`），已同批修。
- ⛔ 覆盖：`run_once` 的三门计数、60 秒／3 次滑窗门槛、采集连续性——它们要**生产库快照**
  （`--db /data/memory_agent.db`，`mode=ro`），本席位⛔ 碰现网⛔ 代跑 ⇒ 余项随窗口那族（任务表 #29 同族）。
"""

import importlib.util
import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "probe_device_feed_dryrun",
    os.path.join(_ROOT, "scripts", "probe_device_feed_dryrun.py"))
pfd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pfd)


class _Store:
    """只要 `list_members()` 这一个面——打码逻辑不依赖别的（⛔ 为此建整套假 Store）。"""

    def __init__(self, members):
        self._members = members

    def list_members(self):
        return self._members


def _events_db(tmp_path, rows):
    path = os.path.join(str(tmp_path), "events.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE events (day TEXT, ts TEXT)")
    conn.executemany("INSERT INTO events(day, ts) VALUES (?, ?)", rows)
    conn.commit()
    return conn


def test_partial_day_is_excluded_from_the_daily_mean(tmp_path):
    """判据：只有"23 时段有事件"的日子才算整天——跑到一半的那天⛔ 拿来摊日均。"""
    conn = _events_db(tmp_path, [
        ("2026-10-01", "2026-10-01T07:00:00"), ("2026-10-01", "2026-10-01T23:59:59"),
        ("2026-10-02", "2026-10-02T08:00:00"), ("2026-10-02", "2026-10-02T22:59:59"),
        ("2026-10-03", "2026-10-03T23:00:00"),
    ])
    assert pfd.full_days(conn, 10) == ["2026-10-01", "2026-10-03"]
    conn.close()


def test_limit_takes_the_most_recent_full_days(tmp_path):
    """LIMIT 取"最近 N 个整天"（不是前 N 天），返回按日升序。"""
    conn = _events_db(tmp_path, [
        ("2026-10-01", "2026-10-01T23:10:00"),
        ("2026-10-02", "2026-10-02T23:10:00"),
        ("2026-10-03", "2026-10-03T23:10:00"),
    ])
    assert pfd.full_days(conn, 2) == ["2026-10-02", "2026-10-03"]
    conn.close()


def test_case_folded_hit_is_replaced():
    """★ 出境纪律：命中判定是小写折叠的，替换也必须是。

    修前实测（本席 2026-10-14）：token="Amy"（成员姓名占位）对 `sensor.amy_phone` **原样返回**
    ⇒ 一枚没打码的姓名跟着 `reachable_entity_ids_masked`／`top_max_60s` 的键出仓。
    HA 的 entity_id 按约定是小写，姓名／友好名常带大写 ⇒ 这是常态⛔ 边角。
    """
    mask = pfd.mask_factory(_Store([{"name": "Amy"}, {"name": "Zzz"}]), {})
    assert mask("sensor.amy_phone") == "sensor.**_phone"
    assert mask("sensor.AMY_phone") == "sensor.**_phone"
    assert mask("sensor.boiler") == "sensor.boiler"          # 反控：无命中的⛔ 被误打码


def test_friendly_name_tokens_and_length_floor_and_precedence():
    """友好名一样进名单；长度 <2 的 token⛔ 参与（否则 "a" 会把一切打花）；长 token 优先。"""
    mask = pfd.mask_factory(
        _Store([{"name": "LivingRoomSensor"}, {"name": "x"}, {"name": ""}]),
        {"sensor.x_1": "BoilerTank"})
    assert mask("binary.livingroomsensor_motion") == "binary.**_motion"
    assert mask("sensor.x_1") == "sensor.x_1"                # "x"（单字符）与 "BoilerTank"⛔ 命中这串的其它部分
    assert "boiler" not in mask("sensor.boilertank_val")     # 友好名大小写无关地被打码
    # 长 token 优先：短 token 是长 token 的子串时，先试长的（有序表按 len 降序）
    mask2 = pfd.mask_factory(_Store([{"name": "Ann"}, {"name": "Anne"}]), {})
    assert mask2("sensor.anne_door") == "sensor.**_door"
