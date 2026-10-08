import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from memory_agent.feedback_pack import validate_label, build_feedback_pack  # noqa: E402

ROOMS = ["书房", "客厅", "主卧", "书房门口"]
NAMES = ["张小山", "李四", "Tom"]

CASES = [
    ("vlm_failed-书房-20261004", True),
    ("vlm_failed-书房-2026-10-04", True),
    ("vlm_failed-书房门口-20261004", True),
    ("vlm_failed-客厅-12", True),
    ("bad_case", True),
    ("bad_case_42", True),
    ("bad_case-42", True),
    ("low_confidence-书房-20261004", True),
    ("vlm_failed-张三-20261004", False),
    ("vlm_failed-张小山", False),
    ("vlm_failed-李四-20261004", False),
    ("bad_case_tom", False),
    ("vlm_failed-unknown_room-20261004", True),
    ("vlm_failed-kitchen-20261004", True),
    ("vlm_failed-书房-20261004-备注", False),
    ("vlm_failed-书房-20261004/../evil", False),
    ("../../../etc/passwd", False),
    ("", False),
    ("notebook-书房", False),
    ("vlm_failed-书房—20261004", False),
    ("vlm_failed-１２３", False),
    ("vlm_failed-", False),
    ("vlm_failed-书房-", False),
    ("vlm_failed--书房", False),
    ("bad_case__42", False),
    ("bad_case-2026-13-40", True),   # 日期分支不匹配 → 落进"数字 + 分隔符"链，见下方说明
]

fails = []
for label, want in CASES:
    ok, reason, safe = validate_label(label, known_rooms=ROOMS, member_names=NAMES)
    if ok is not want:
        fails.append((label, want, ok, reason))
    if ok:
        print(f"PASS {label!r} -> {safe!r}")
    else:
        print(f"REJECT {label!r} :: {reason}")
print("---- mismatch:", fails)

out_dir = tempfile.mkdtemp()
S1 = lambda t: t.replace("张小山", "成员1").replace("李四", "成员2").replace("Tom", "成员3")  # noqa: E731
p = build_feedback_pack("", "张小山在书房，电话 13812345678", out_dir,
                        label="vlm_failed-书房-20261004",
                        anon_sanitizer=S1, known_rooms=ROOMS, member_names=NAMES)
print("pack:", p)
with tarfile.open(p, "r:gz") as tar:
    print("members:", tar.getnames())
    print("trace.txt:", tar.extractfile("trace.txt").read().decode())
    print("trace_anon.txt:", tar.extractfile("trace_anon.txt").read().decode())
    print("meta.json:", tar.extractfile("meta.json").read().decode())

p2 = build_feedback_pack("", "张小山", out_dir, label="bad_case_7",
                         known_rooms=ROOMS, member_names=NAMES)
with tarfile.open(p2, "r:gz") as tar:
    print("no-anon members:", tar.getnames())
    print("no-anon meta:", tar.extractfile("meta.json").read().decode())

p3 = build_feedback_pack("", "张小山", out_dir, label="vlm_failed-张小山",
                         anon_sanitizer=S1, known_rooms=ROOMS, member_names=NAMES)
print("name-label pack:", p3)
print("RC_DONE")
