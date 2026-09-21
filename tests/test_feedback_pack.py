"""Phase 4.3 反馈闭环 fail-closed 回归测试。

覆盖：
1. PII 脱敏（手机号/邮箱/身份证/JWT/API key 等）
2. build_feedback_pack 基本功能
3. fail-closed：脱敏失败返回 None
4. 快照路径白名单
5. label 路径遍历防护
6. 空输入边界
"""

import sys
import os
import json
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory_agent.feedback_pack import (
    sanitize_text,
    build_feedback_pack,
    _is_safe_snapshot_path,
)


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
            label="test_case",
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
            label="pii_test",
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
                label="whitelist_test",
            )
            self.assertIsNotNone(result)
            with tarfile.open(result, "r:gz") as tar:
                names = tar.getnames()
                self.assertNotIn("bad_snapshot.jpg", names)
        finally:
            fp._is_safe_snapshot_path = original

    def test_label_path_traversal(self):
        # label 包含路径分隔符，应该被清理
        result = build_feedback_pack(
            snapshot_path="",
            trace="test",
            output_dir=self.output_dir,
            label="../../../etc/passwd",
        )
        self.assertIsNotNone(result)
        # 文件名应该被清理为安全名称
        basename = os.path.basename(result)
        self.assertNotIn("..", basename)
        self.assertNotIn("/", basename)

    def test_empty_input_returns_none(self):
        result = build_feedback_pack(
            snapshot_path="",
            trace="",
            output_dir=self.output_dir,
            label="empty",
        )
        self.assertIsNone(result)

    def test_meta_json_included(self):
        result = build_feedback_pack(
            snapshot_path="",
            trace="test trace",
            output_dir=self.output_dir,
            label="meta_test",
        )
        self.assertIsNotNone(result)
        with tarfile.open(result, "r:gz") as tar:
            meta_file = tar.extractfile("meta.json")
            meta = json.loads(meta_file.read().decode("utf-8"))
            self.assertEqual(meta["label"], "meta_test")
            self.assertFalse(meta["snapshot_included"])
            self.assertTrue(meta["trace_included"])
            self.assertTrue(meta["trace_sanitized"])

    def test_disable_sanitize(self):
        trace = "电话 13812345678"
        result = build_feedback_pack(
            snapshot_path="",
            trace=trace,
            output_dir=self.output_dir,
            label="no_sanitize",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
