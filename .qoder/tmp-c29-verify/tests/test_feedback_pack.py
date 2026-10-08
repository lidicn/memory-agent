"""Phase 4.3 反馈闭环 fail-closed 回归测试。

覆盖：
1. PII 脱敏（手机号/邮箱/身份证/JWT/API key 等）
2. build_feedback_pack 基本功能
3. fail-closed：脱敏失败返回 None
4. 快照路径白名单
5. label 白名单（DCD 20261004 MA-裁3 Q2）：只收结构化 label，姓名进不了文件名
6. 空输入边界
7. 出境面分层（裁3 Q1=A）：包内 `trace.txt`（S3，姓名可读）+ `trace_anon.txt`（再叠 S1）
"""

import sys
import os
import json
import re
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.feedback_pack import (
    ANON_SANITIZER_ID,
    LABEL_KINDS,
    build_feedback_pack,
    sanitize_text,
    validate_label,
    _is_safe_snapshot_path,
)

BEHAVIOR_ROUTES_SRC = (Path(__file__).resolve().parents[1]
                       / "src" / "memory_agent" / "api" / "behavior_routes.py")


class TestSanitizeText(unittest.TestCase):
    """PII 脱敏测试。"""

    def test_phone_number(self):
        text = "联系电话 13812345678 请拨打"
        result = sanitize_text(text)
        self.assertNotIn("13812345678", result)
        self.assertIn("<REDACTED-PHONE>", result)

    def test_email(self):
        text = "邮箱 test@example.com 请联系"
        result = sanitize_text(text)
        self.assertNotIn("test@example.com", result)
        self.assertIn("<REDACTED-EMAIL>", result)

    def test_id_card(self):
        text = "身份证 110101199001011234 请核实"
        result = sanitize_text(text)
        self.assertNotIn("110101199001011234", result)
        self.assertIn("<REDACTED-ID_CARD>", result)

    def test_jwt_token(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Token: {jwt} 请使用"
        result = sanitize_text(text)
        self.assertNotIn("eyJhbGci", result)
        self.assertIn("<REDACTED-JWT>", result)

    def test_api_key(self):
        text = "API Key: sk-abc123def456ghi789jkl012mno345 请保密"
        result = sanitize_text(text)
        self.assertNotIn("sk-abc123", result)
        self.assertIn("<REDACTED-API_KEY>", result)

    def test_bearer_token(self):
        text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize_text(text)
        self.assertIn("<REDACTED-TOKEN>", result)

    def test_ipv4(self):
        text = "服务器地址 192.168.1.100 请连接"
        result = sanitize_text(text)
        self.assertNotIn("192.168.1.100", result)
        self.assertIn("<REDACTED-IP>", result)

    def test_mac_address(self):
        text = "MAC 地址 AA:BB:CC:DD:EE:FF 请记录"
        result = sanitize_text(text)
        self.assertIn("<REDACTED-MAC>", result)

    def test_multiple_pii(self):
        text = "电话 13812345678，邮箱 a@b.com，IP 10.0.0.1"
        result = sanitize_text(text)
        self.assertIn("<REDACTED-PHONE>", result)
        self.assertIn("<REDACTED-EMAIL>", result)
        self.assertIn("<REDACTED-IP>", result)

    def test_empty_text(self):
        self.assertEqual(sanitize_text(""), "")
        self.assertIsNone(sanitize_text(None))

    def test_no_pii(self):
        text = "这是一段普通文本，没有敏感信息"
        self.assertEqual(sanitize_text(text), text)


class TestBuildFeedbackPack(unittest.TestCase):
    """build_feedback_pack 测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.tmpdir, "output")

    def test_basic_pack_with_trace(self):
        result = build_feedback_pack(
            snapshot_path="",
            trace="这是一段 trace 文本",
            output_dir=self.output_dir,
            label="bad_case_basic",
        )
        self.assertIsNotNone(result)
        self.assertTrue(os.path.exists(result))
        # 验证 tar.gz 内容
        with tarfile.open(result, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("trace.txt", names)
            self.assertIn("meta.json", names)
            # 读取 trace 内容
            trace_file = tar.extractfile("trace.txt")
            self.assertIsNotNone(trace_file)
            content = trace_file.read().decode("utf-8")
            self.assertEqual(content, "这是一段 trace 文本")

    def test_pii_sanitized_in_trace(self):
        trace = "用户电话 13812345678，邮箱 test@example.com"
        result = build_feedback_pack(
            snapshot_path="",
            trace=trace,
            output_dir=self.output_dir,
            label="bad_case_pii",
        )
        self.assertIsNotNone(result)
        with tarfile.open(result, "r:gz") as tar:
            trace_file = tar.extractfile("trace.txt")
            content = trace_file.read().decode("utf-8")
            self.assertNotIn("13812345678", content)
            self.assertNotIn("test@example.com", content)
            self.assertIn("<REDACTED-PHONE>", content)

    def test_snapshot_path_whitelist(self):
        # 创建一个不在 /data/ 下的快照文件
        bad_snapshot = os.path.join(self.tmpdir, "bad_snapshot.jpg")
        with open(bad_snapshot, "w") as f:
            f.write("fake image")
        # 不在 /data/ 白名单内，应该不包含快照
        # 注意：Windows 上 /data/ 路径检查行为不同，这里验证函数逻辑
        import memory_agent.feedback_pack as fp
        original = fp._is_safe_snapshot_path
        # mock：非 /data/ 开头的路径返回 False
        fp._is_safe_snapshot_path = lambda p: p.startswith("/data/") if p else False
        try:
            result = build_feedback_pack(
                snapshot_path=bad_snapshot,
                trace="test",
                output_dir=self.output_dir,
                label="bad_case_whitelist",
            )
            self.assertIsNotNone(result)
            with tarfile.open(result, "r:gz") as tar:
                names = tar.getnames()
                self.assertNotIn("bad_snapshot.jpg", names)
        finally:
            fp._is_safe_snapshot_path = original

    def test_label_path_traversal(self):
        """裁3 Q2 之前这条断言的是"清洗成怪名字照样落盘"；白名单上线后直接拒。

        清洗逻辑本身还在 ``validate_label`` 的出口里（非 word 字符替换为下划线），它守的是
        通过校验的 label 里剩下的边角；但路径遍历这种 label 现在到不了那一步。
        """
        result = build_feedback_pack(
            snapshot_path="",
            trace="test",
            output_dir=self.output_dir,
            label="../../../etc/passwd",
        )
        self.assertIsNone(result)
        self.assertEqual(os.listdir(self.output_dir), [])

    def test_empty_input_returns_none(self):
        result = build_feedback_pack(
            snapshot_path="",
            trace="",
            output_dir=self.output_dir,
            label="bad_case_empty",
        )
        self.assertIsNone(result)

    def test_meta_json_included(self):
        result = build_feedback_pack(
            snapshot_path="",
            trace="test trace",
            output_dir=self.output_dir,
            label="bad_case_meta",
        )
        self.assertIsNotNone(result)
        with tarfile.open(result, "r:gz") as tar:
            meta_file = tar.extractfile("meta.json")
            meta = json.loads(meta_file.read().decode("utf-8"))
            self.assertEqual(meta["label"], "bad_case_meta")
            self.assertFalse(meta["snapshot_included"])
            self.assertTrue(meta["trace_included"])
            self.assertTrue(meta["trace_sanitized"])

    def test_disable_sanitize(self):
        trace = "电话 13812345678"
        result = build_feedback_pack(
            snapshot_path="",
            trace=trace,
            output_dir=self.output_dir,
            label="bad_case_nosanitize",
            sanitize=False,
        )
        self.assertIsNotNone(result)
        with tarfile.open(result, "r:gz") as tar:
            trace_file = tar.extractfile("trace.txt")
            content = trace_file.read().decode("utf-8")
            # 未脱敏，原始内容保留
            self.assertIn("13812345678", content)


class TestSafeSnapshotPath(unittest.TestCase):
    """快照路径白名单测试（使用 mock 避免跨平台路径差异）。"""

    def test_data_path_safe(self):
        # mock realpath 行为：/data/ 开头返回 True
        import memory_agent.feedback_pack as fp
        original = fp.os.path.realpath
        fp.os.path.realpath = lambda p: p  # 不做规范化，直接返回原路径
        try:
            self.assertTrue(_is_safe_snapshot_path("/data/snapshots/test.jpg"))
        finally:
            fp.os.path.realpath = original

    def test_non_data_path_unsafe(self):
        import memory_agent.feedback_pack as fp
        original = fp.os.path.realpath
        fp.os.path.realpath = lambda p: p
        try:
            self.assertFalse(_is_safe_snapshot_path("/etc/passwd"))
            self.assertFalse(_is_safe_snapshot_path("/tmp/test.jpg"))
            self.assertFalse(_is_safe_snapshot_path("relative/path.jpg"))
        finally:
            fp.os.path.realpath = original

    def test_empty_path_unsafe(self):
        self.assertFalse(_is_safe_snapshot_path(""))

    def test_path_traversal_unsafe(self):
        # /data/../etc/passwd 规范化后不在 /data/ 下
        import memory_agent.feedback_pack as fp
        original = fp.os.path.realpath
        # mock realpath：返回规范化后的路径
        fp.os.path.realpath = lambda p: "/etc/passwd" if ".." in p else p
        try:
            self.assertFalse(_is_safe_snapshot_path("/data/../etc/passwd"))
        finally:
            fp.os.path.realpath = original

# ── DCD 20261004 MA-裁3：出境面分层（两份口径）+ label 白名单 ────────────────

ROSTER_ROOMS = ["书房", "客厅", "主卧"]
ROSTER_NAMES = ["张小山", "李四", "Tom"]
RULED_LABEL = "vlm_failed-书房-20261004"


def _s1_stub(text: str) -> str:
    """S1 入库口径的同形替身（姓名 → 成员N，按名册顺序）。

    只用来验「分层这条通道有没有被走」；口径本体由
    ``TestOutboundLayering.test_anon_layer_is_the_real_s1_output`` 用真 Store 锁。
    """
    for idx, name in enumerate(ROSTER_NAMES, start=1):
        text = text.replace(name, f"成员{idx}")
    return text


def _unpack(path: str) -> dict:
    """读出包里每个文本成员的内容（键 = arcname）。"""
    with tarfile.open(path, "r:gz") as tar:
        return {m.name: tar.extractfile(m).read().decode("utf-8")
                for m in tar.getmembers() if m.isfile()}


def _make_store():
    from memory_agent.store import Store
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)  # Store 要的是"还没建库"的路径，sqlite 自己建
    store = Store(db_path, tz_offset_hours=8.0)
    store.init_schema()
    return store, db_path


def _drop_store(store, db_path):
    try:
        store.close()
    except Exception:
        pass
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass


class TestOutboundLayering(unittest.TestCase):
    """裁3 Q1=A：一个包里两份 trace，取哪份出境是出境动作的事，MA 不预设收件方有名册。"""

    def setUp(self):
        self.output_dir = tempfile.mkdtemp()

    def _build(self, trace, **overrides):
        params = dict(label=RULED_LABEL, known_rooms=ROSTER_ROOMS,
                      member_names=ROSTER_NAMES, anon_sanitizer=_s1_stub)
        params.update(overrides)
        return build_feedback_pack(snapshot_path="", trace=trace,
                                   output_dir=self.output_dir, **params)

    def test_both_layers_present_and_trace_txt_keeps_the_ruled_shape(self):
        texts = _unpack(self._build("张小山在书房，电话 13812345678"))
        self.assertEqual(sorted(texts), ["meta.json", "trace.txt", "trace_anon.txt"])
        # 出境口径那份「姓名可读」是裁定的前提，不是漏脱敏：收件方要拿它还原谁在哪个房间
        self.assertIn("张小山", texts["trace.txt"])
        self.assertIn("<REDACTED-PHONE>", texts["trace.txt"])
        # 匿名那份 = S3 之后再叠 S1：S3 的掩码串原样留着（两份能 diff），只多脱了姓名
        self.assertIn("成员1", texts["trace_anon.txt"])
        self.assertNotIn("张小山", texts["trace_anon.txt"])
        self.assertIn("<REDACTED-PHONE>", texts["trace_anon.txt"])

    def test_anon_layer_is_the_real_s1_output(self):
        """全链锁：真 Store + 真名册 + 真 S1，不用桩子。"""
        from memory_agent.store import Store
        store, db_path = _make_store()
        try:
            store.create_member("张小山")
            trace = "张小山的备注 1234567890123"
            texts = _unpack(self._build(trace, anon_sanitizer=store.sanitize_feedback_text))
            self.assertEqual(texts["trace_anon.txt"],
                             Store._sanitize_pii(sanitize_text(trace), ["张小山"]))
            # 13 位数字：S3（银行卡要 16-19 位）放它过关，S1 的「≥7 位整段」才收掉。
            # 这一条是「两份文件确实不是同一个口径」的机器证据。
            self.assertIn("1234567890123", texts["trace.txt"])
            self.assertNotIn("1234567890123", texts["trace_anon.txt"])
            self.assertIn("张小山", texts["trace.txt"])
            self.assertNotIn("张小山", texts["trace_anon.txt"])
        finally:
            _drop_store(store, db_path)

    def test_anon_layer_is_still_made_when_s3_is_disabled(self):
        texts = _unpack(self._build("张小山的手机 13812345678", sanitize=False))
        self.assertIn("13812345678", texts["trace.txt"])
        self.assertNotIn("张小山", texts["trace_anon.txt"])
        self.assertIn("成员1", texts["trace_anon.txt"])

    def test_missing_s1_entrypoint_omits_the_anon_file_instead_of_faking_it(self):
        """没有 S1 入口就不产 anon，而不是拿 S3 的结果冒充——收件方是按文件名选口径的。"""
        texts = _unpack(self._build("张小山", anon_sanitizer=None))
        self.assertEqual(sorted(texts), ["meta.json", "trace.txt"])
        meta = json.loads(texts["meta.json"])
        self.assertFalse(meta["trace_anon_included"])
        self.assertEqual(meta["trace_anon_sanitizer"], "")

    def test_s1_failure_omits_the_anon_file_but_keeps_the_pack(self):
        def boom(_text):
            raise RuntimeError("s1 unavailable")
        texts = _unpack(self._build("张小山", anon_sanitizer=boom))
        self.assertEqual(sorted(texts), ["meta.json", "trace.txt"])
        self.assertIn("张小山", texts["trace.txt"])

    def test_s1_returning_empty_is_not_shipped_as_anonymized(self):
        texts = _unpack(self._build("张小山", anon_sanitizer=lambda _t: ""))
        self.assertNotIn("trace_anon.txt", texts)
        self.assertFalse(json.loads(texts["meta.json"])["trace_anon_included"])

    def test_meta_records_the_layering_without_touching_the_old_keys(self):
        meta = json.loads(_unpack(self._build("张小山"))["meta.json"])
        self.assertTrue(meta["trace_anon_included"])
        self.assertEqual(meta["trace_anon_sanitizer"], ANON_SANITIZER_ID)
        # 裁定要求「加产物、不改已有产物格式」：老四键的语义一字未动
        self.assertEqual(meta["label"], RULED_LABEL)
        self.assertFalse(meta["snapshot_included"])
        self.assertTrue(meta["trace_included"])
        self.assertTrue(meta["trace_sanitized"])


class TestLabelWhitelist(unittest.TestCase):
    """裁3 Q2：label 会长成文件名，所以只收结构化 label。"""

    def setUp(self):
        self.output_dir = tempfile.mkdtemp()

    def test_ruled_shapes_pass(self):
        for label in [RULED_LABEL, "vlm_failed-书房-2026-10-04",
                      "low_confidence-客厅-20261004", "skipped-主卧-3",
                      "bad_case", "bad_case_42", "bad_case-42",
                      "vlm_failed-living_room-20261004"]:
            passed, reason, safe = validate_label(label, known_rooms=ROSTER_ROOMS,
                                                  member_names=ROSTER_NAMES)
            self.assertTrue(passed, f"{label} 被拒：{reason}")
            self.assertEqual(safe, label)

    def test_name_bearing_labels_are_rejected_and_the_reason_stays_nameless(self):
        for label in ["vlm_failed-张小山-20261004", "vlm_failed-李四-20261004",
                      "bad_case_Tom", "bad_case-李四-书房"]:
            passed, reason, safe = validate_label(label, known_rooms=ROSTER_ROOMS,
                                                  member_names=ROSTER_NAMES)
            self.assertFalse(passed, label)
            self.assertEqual(safe, "")
            for name in ROSTER_NAMES:
                # 理由串会被写进 HTTP 响应，不能把命中的姓名再念一遍
                self.assertNotIn(name, reason)

    def test_cjk_segment_has_to_be_a_named_room(self):
        self.assertTrue(validate_label(RULED_LABEL, known_rooms=ROSTER_ROOMS)[0])
        # 名册读不到 → 中文段落一律不认：缺名册只会让校验更严，不会更松
        self.assertFalse(validate_label(RULED_LABEL, known_rooms=[])[0])
        # 点过名以外的中文（含没在名册里的房间）不认
        self.assertFalse(validate_label("vlm_failed-阳台-20261004",
                                       known_rooms=ROSTER_ROOMS)[0])

    def test_longest_room_name_wins(self):
        passed, reason, _ = validate_label("vlm_failed-书房门口-20261004",
                                          known_rooms=["书房", "书房门口"])
        self.assertTrue(passed, reason)

    def test_fullwidth_digits_are_not_a_valid_segment(self):
        """``re.ASCII`` 是这段校验里唯一挡得住全角数字的开关。"""
        for label in ["vlm_failed-１２３", "bad_case-１"]:
            self.assertFalse(validate_label(label, known_rooms=ROSTER_ROOMS)[0], label)

    def test_separators_are_single_and_never_trailing(self):
        for label in ["vlm_failed-", "vlm_failed-书房-", "vlm_failed--书房", "bad_case__42"]:
            passed, reason, _ = validate_label(label, known_rooms=ROSTER_ROOMS)
            self.assertFalse(passed, f"{label} 通过了：{reason}")

    def test_kind_must_come_from_the_closed_set(self):
        self.assertFalse(validate_label("notebook-书房-20261004",
                                        known_rooms=ROSTER_ROOMS)[0])
        for kind in LABEL_KINDS:
            # 裸类型词是端点默认 label（bad_case）与最简形态，必须放行
            self.assertTrue(validate_label(kind)[0], kind)

    def test_legacy_http_defaults_still_pack(self):
        """收紧入参不能把两个端点的默认 label 一起判红：bad_case 与 bad_case_{event_id}。"""
        for label in ["bad_case", "bad_case_4213"]:
            self.assertIsNotNone(build_feedback_pack(
                snapshot_path="", trace="t", output_dir=self.output_dir, label=label), label)

    def test_rejected_label_writes_no_file_at_all(self):
        before = os.listdir(self.output_dir)
        out = build_feedback_pack(snapshot_path="", trace="张小山的 trace",
                                  output_dir=self.output_dir,
                                  label="vlm_failed-张小山-20261004",
                                  known_rooms=ROSTER_ROOMS, member_names=ROSTER_NAMES,
                                  anon_sanitizer=_s1_stub)
        self.assertIsNone(out)
        self.assertEqual(os.listdir(self.output_dir), before)

    def test_both_export_endpoints_wire_the_whitelist_and_the_real_s1(self):
        """调用点锁（与 tests/test_vma_r5_event_loop_offload.py 同一手法）：
        两处导出端点都要 ①带名册过白名单 ②把真 S1 入口交给打包 ③离线程跑。"""
        src = BEHAVIOR_ROUTES_SRC.read_text(encoding="utf-8")
        self.assertEqual(src.count("anon_sanitizer=rt.store.sanitize_feedback_text"), 2)
        self.assertEqual(
            src.count("validate_label(label, known_rooms=rooms, member_names=names)"), 2)
        self.assertEqual(
            len(re.findall(r"asyncio\.to_thread\(\s*\n\s*build_feedback_pack,", src)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
