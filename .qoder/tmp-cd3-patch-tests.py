import io
import re

P = "tests/test_feedback_pack.py"
# 这份文件在本机工作区是 CRLF、HEAD 里是 LF；统一按 LF 处理并写回 LF（与 HEAD 同形状，
# 免得一次小改让 diff 变成整份重写）。
src = io.open(P, encoding="utf-8", newline="").read().replace("\r\n", "\n")

SWAPS = [
    ('label="pii_test",', 'label="bad_case_pii",'),
    ('label="whitelist_test",', 'label="bad_case_whitelist",'),
    ('label="meta_test",', 'label="bad_case_meta",'),
    ('label="no_sanitize",', 'label="bad_case_nosanitize",'),
    ('label="empty",', 'label="bad_case_empty",'),
    ('self.assertEqual(meta["label"], "meta_test")', 'self.assertEqual(meta["label"], "bad_case_meta")'),
]
for old, new in SWAPS:
    n = src.count(old)
    assert n == 1, f"anchor {old!r} hits {n} times"
    src = src.replace(old, new)

OLD_TRAVERSAL = '''    def test_label_path_traversal(self):
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
'''
NEW_TRAVERSAL = '''    def test_label_path_traversal(self):
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
'''
assert src.count(OLD_TRAVERSAL) == 1
src = src.replace(OLD_TRAVERSAL, NEW_TRAVERSAL)

io.open(P, "w", encoding="utf-8", newline="").write(src)
print("PATCHED_OK")
