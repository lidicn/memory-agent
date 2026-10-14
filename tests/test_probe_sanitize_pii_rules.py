"""`scripts/probe_sanitize_pii_rules.py` 的回归锁（旧／新 PII 规则对照探针）。

为什么要锁它：`receipts/20261004-MA-进度盘点回执…【已受理】.md` 把它的读数抄出了仓——
**出境面引用过的一把尺⛔ 有门罩它**（`.gates.toml` 的 `extra_skip_globs` 含 `"scripts/*"`），
写坏了它，那份回执里"新规则能清干净"的结论就⛔ 可复现。本锁由 `scripts/lock_face_ratchet.py` 数着。

射程（对应探针的三个部件）：
① **片段残留不能回来**（#30 修的那一族）：一段 17 位连续数字，旧规则会在**串内部**匹配手机号前缀，
   把尾巴留在掩码外；新规则的前后视（`(?<!\\d)`／`(?!\\d)`）让它整段落到兜底的 `\\d{7,}` ⇒ `"***"`。
② **带分隔符的号码要能吃掉**（旧规则只认连续 11 位，`138-1234-5678` 从它指缝里漏走）。
③ **一行多枚都要吃掉**（邮箱枚数⛔ 只有一枚时，`***@` 的枚数就是证据）。
④ **形状锚点**：新规则四档齐在，手机号／身份证两档的前后视⛔ 静默脱落（脱了就是①的复发）。

★ 输入全是探针自带的**合成夹具**（`138…`／`110101…`／`nobody@example.com` 这类占位形状），
⛔ 真实号码、⛔ 真实证件号、⛔ 真实邮箱——本锁只主张"规则对形状的行为"。
"""

import importlib.util
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "probe_sanitize_pii_rules", os.path.join(_ROOT, "scripts", "probe_sanitize_pii_rules.py"))
psp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(psp)

PHONE = "13812345678912345"                    # 17 位合成串：探针样例列表里就有这一枚
PHONE_DASH = "电话138-1234-5678"
IDCARD = "身份证11010119900101123X"
MAILS = "a@x.com 与 nobody@example.com 两枚"


def _digit_run(text: str) -> int:
    return max((len(m) for m in re.findall(r"\d+", text)), default=0)


def test_long_digit_run_leaves_no_fragment_residue():
    """①：新规则把整段吃掉＝"***"；旧规则在串内部匹配 ⇒ 掩码外留着尾数（#30 那族的形状）。"""
    assert psp.apply(psp.NEW, PHONE) == "***"
    old = psp.apply(psp.OLD, PHONE)
    assert old != "***" and _digit_run(old) >= 6, old          # ★ 反控：旧规则确实会漏，⛔ 空转断言


def test_separators_are_eaten_by_new_rules_only():
    """②：`138-1234-5678` 旧规则⛔ 认（只拼连续位），新规则按 `[-\\s]?` 认掉。"""
    assert _digit_run(psp.apply(psp.NEW, PHONE_DASH)) < 7, psp.apply(psp.NEW, PHONE_DASH)
    assert "138" in psp.apply(psp.OLD, PHONE_DASH)             # 旧规则原样放行＝这枚是新旧分叉的证据


def test_every_mail_on_the_line_is_masked():
    """③：一行两枚邮箱 ⇒ 两个 `***@`（⛔ 只吃到第一枚就是子串口径的老坑）。

    ★ 规则只管 **local part**，域名面按字面保留（`[A-Za-z0-9._%+-]+@`→`***@`）——
    本锁钉的就是这个形状；哪天改成连域名一起打码，这条会红，那是要显式改判据⛔ 顺手漂。
    """
    out = psp.apply(psp.NEW, MAILS)
    assert out.count("***@") == 2, out
    assert out == "***@x.com 与 ***@example.com 两枚", out
    assert "nobody" not in out, out


def test_new_rules_keep_their_lookaround_anchors():
    """④：四档齐在，且手机号／身份证两档的前后视⛔ 脱落（脱落＝①的复发）。"""
    assert len(psp.NEW) == 4, psp.NEW
    phone_pat, idcard_pat = psp.NEW[0][0], psp.NEW[2][0]
    for pat in (phone_pat, idcard_pat):
        assert pat.startswith("(?<!\\d)"), pat
        assert pat.endswith("(?!\\d)"), pat
    assert psp.NEW[1][1] == "***@" and psp.NEW[3] == (r"\d{7,}", "***"), psp.NEW


def test_positive_control_still_masks_the_plain_shapes():
    """正控：连续 11 位手机号与带 X 校验位的身份证都得被吃掉（⛔ 让上面几枚变成"尺根本⛔ 命中"的假绿）。"""
    assert psp.apply(psp.NEW, "手机13800000000") == "手机****"
    assert psp.apply(psp.NEW, IDCARD).startswith("身份证") and _digit_run(
        psp.apply(psp.NEW, IDCARD)) < 7, psp.apply(psp.NEW, IDCARD)
