#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""欠锁棘轮 `scripts/lock_face_ratchet.py` 的回归锁（第十三面余项 ⇒ 变成一道闸）。

为什么这把尺自己也得有锁：它判的是"**谁欠一枚锁**"，而它本身也落在 `scripts/` 里——
`.gates.toml` 的 `extra_skip_globs` 含 `"scripts/*"`、`pyflakes_gate.sh` 只罩 `src/memory_agent`
⇒ **仓内⛔ 任何权威门罩得住它**。写坏了它，"欠锁几枚"这个读数就没人守了（本席 2026-10-14
现跑第一版时就把它写崩过：把 `doc/` 也计进欠锁面，NAKED 从 **3** 涨到 **18**——那不是债务，
是自家文档引用过自家脚本）。**判据本身要钉住，⛔ 钉仓内当前枚数**（枚数会随代码漂，
钉成期望值只会让下一只手去改期望值而⛔ 去看尺）。

射程（对应尺的五个部件）：
① 欠锁面＝**对端出境面**（`--face` ＋ `PEER_ROSTER`）；`doc/` 命中只算 `DOC_ONLY`，⛔ 计欠；
   "有锁"认**两种形状**：锁面文本里的词边界命中，或锁面里的 `test_<名>.py`／`<名>_test.py` 同名约定件
   （只认第一种会把"按约定建了锁、内容⛔ 复述名"判成欠锁＝**假红**；两种各钉一枚腿）。
② 名字匹配＝**词边界**口径（子串口径会把同前缀的别枚吃成"被引用"）；
③ 豁免**必须带非空理由**；豁免名已⛔ 在 `scripts/` 面＝过期豁免 ⇒ 判红；
④ 名单里的手抄名字⛔ 在面上 ⇒ 判红（本尺真仓首跑就靠这条咬出"把链主仓的尺抄进 MA 名册"）；
⑤ fail-closed：`scripts`/`tests` 取材面塌掉时尺**自己拒跑**，⛔ 交一份"范围变窄"的 0 欠锁读数。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "lock_face_ratchet", os.path.join(REPO, "scripts", "lock_face_ratchet.py"))
lfr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lfr)


def _mk(tmp_path, name, body=""):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tree(tmp_path, scripts=("alpha_tool", "beta_tool"), tests=("test_alpha_tool",),
          peer=("alpha_tool",), doc=("gamma_tool",), prefix_only=None):
    """合成三面：scripts=被量对象，tests=锁面，peer=对端出境面，doc=仓内引用面。

    ★ 锁面永远至少有一枚⛔ 对应任何 scripts 名的文件（`test_zzz_other.py`）：
    尺对空锁面是 **fail-closed 拒跑**（⑤），`tests=()` 那几枚腿要的"没锁"⛔ 是"空锁面"。
    """
    s = _mk(tmp_path, "scripts")
    for n in scripts:
        (s / f"{n}.py").write_text(f"#!/usr/bin/env python3\n# {n}\n", encoding="utf-8")
    t = _mk(tmp_path, "tests")
    (t / "test_zzz_other.py").write_text('"""与合成本里任何 scripts 名都⛔ 相干"""\n',
                                         encoding="utf-8")
    for n in tests:
        (t / f"{n}.py").write_text(f'"""锁住 {n}"""\n', encoding="utf-8")
    p = _mk(tmp_path, "peer")
    lines = [f"引用了 {n} 的读数" for n in peer]
    # 词边界诱饵：`prefix_only` 里写 "alpha_tool_ext"，⛔ 让 alpha_tool 变成"被引用"
    lines += [f"引用了 {n} 的读数" for n in (prefix_only or [])]
    (p / "note.md").write_text("\n".join(lines), encoding="utf-8")
    d = _mk(tmp_path, "doc")
    (d / "report.md").write_text("\n".join(f"本仓文档引用 {n}" for n in doc), encoding="utf-8")
    return str(s), str(t), str(p), str(d)


def _run(monkeypatch, scripts, tests, doc, faces, *, exempt=None, roster=None):
    monkeypatch.setattr(lfr, "EXEMPT", exempt if exempt is not None else {})
    monkeypatch.setattr(lfr, "PEER_ROSTER", roster if roster is not None else {})
    argv = ["lock_face_ratchet.py", "--scripts", scripts, "--tests", tests, "--doc", doc]
    for f in faces:
        argv += ["--face", f]
    monkeypatch.setattr(sys, "argv", argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lfr.main()
    return rc, buf.getvalue()


def test_peer_referenced_unscripted_lock_is_red(tmp_path, monkeypatch):
    """①：对端引用了 `alpha_tool` 而 tests 面只有 `beta_tool` 的锁 ⇒ 判红，且逐枚点名。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool", "beta_tool"),
                       tests=("test_beta_tool",), peer=("alpha_tool",))
    rc, out = _run(monkeypatch, s, t, d, [p])
    assert rc == 1 and "LOCK_FACE_RATCHET=FAIL" in out, out
    assert "★欠锁｜alpha_tool" in out, out
    assert "BAD_EXEMPT(欠锁且未列豁免)=1" in out, out
    assert "beta_tool" not in out.split("NAKED")[1].split("EXEMPT")[0], out   # 有锁那枚⛔ 进欠锁面


def test_lock_present_is_green_and_doc_only_hits_are_not_debt(tmp_path, monkeypatch):
    """①的另一半：`alpha_tool` 有锁 ⇒ 绿；`gamma_tool` 只被 `doc/` 引用 ⇒ 记 DOC_ONLY，⛔ 计欠。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool", "gamma_tool"),
                       tests=("test_alpha_tool",), peer=("alpha_tool",), doc=("gamma_tool",))
    rc, out = _run(monkeypatch, s, t, d, [p])
    assert rc == 0 and "LOCK_FACE_RATCHET=PASS" in out, out
    assert "NAKED(对端引用且无锁)=0" in out, out
    assert "DOC_ONLY(仅本仓引用且无锁)=1" in out, out      # ★ 本批就是被这条口径救回来的
    assert "★欠锁" not in out, out                        # 对端面⛔ 吃到东西 ⇒ 一枚⛔ 点名


def test_word_boundary_not_substring(tmp_path, monkeypatch):
    """②：对端只提 `alpha_tool_ext` ⇒ ⛔ 算 `alpha_tool` 被引用（子串口径会误判成欠锁）。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool",), tests=(), peer=(),
                       doc=(), prefix_only=("alpha_tool_ext",))
    rc, out = _run(monkeypatch, s, t, d, [p])
    assert "NAKED(对端引用且无锁)=0" in out, out
    assert rc == 0 and "LOCK_FACE_RATCHET=PASS" in out, out


def test_both_lock_shapes_count_and_a_near_miss_filename_does_not(tmp_path, monkeypatch):
    """①的两枚形状：内容命中（⛔ 叫 `test_<名>.py`）算锁；同名约定件（内容⛔ 复述名）也算锁；
    而"前缀像"的约定件名（`test_alpha_tool_ext.py`）⛔ 算 `alpha_tool` 的锁（约定件按**整名**配对，同②口径）。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool", "beta_tool"), tests=(), peer=())
    with open(os.path.join(t, "test_shape_by_content.py"), "w", encoding="utf-8") as fh:
        fh.write('"""内容里写了 scripts/beta_tool.py，但文件名⛔ 是 test_beta_tool"""\n')
    with open(os.path.join(t, "test_alpha_tool_ext.py"), "w", encoding="utf-8") as fh:
        fh.write('"""文件名只是前缀像，内容⛔ 出现任何被量名的裸形"""\n')
    rc, out = _run(monkeypatch, s, t, d, [p])
    assert "LOCKED=1" in out, out                                    # 只有 beta_tool（内容形状）被认到
    assert "alpha_tool" not in out, out                              # 约定件名⛔ 整名配对 ⇒ ⛔ 认成锁，也⛔ 欠锁（对端面没引用）
    # 同批正控：把约定件改成整名 ⇒ 认到锁
    os.remove(os.path.join(t, "test_alpha_tool_ext.py"))
    with open(os.path.join(t, "test_alpha_tool.py"), "w", encoding="utf-8") as fh:
        fh.write('"""整名约定的锁件，内容⛔ 不复述名"""\n')
    rc2, out2 = _run(monkeypatch, s, t, d, [p])
    assert rc2 == 0 and "LOCKED=2" in out2, out2


def test_exempt_must_carry_a_reason_and_must_still_exist(tmp_path, monkeypatch):
    """③：豁免空理由⇒红；豁免名已从 scripts 面消失⇒红（过期豁免＝假绿的一种）。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool",), tests=(), peer=("alpha_tool",))
    rc, out = _run(monkeypatch, s, t, d, [p], exempt={"alpha_tool": "   "})
    assert rc == 1 and "EMPTY_REASON=1" in out, out
    # ★ 过期豁免那一枚必须**单独成立**：`alpha_tool` 自己也列进豁免（带理由），
    # 否则 BAD_EXEMPT 会连带把 rc 打成 1 ⇒ 这条腿红了也⛔ 证明是 STALE 判的（08:43 变异轮 M4 存活抓出来的形状）。
    rc2, out2 = _run(monkeypatch, s, t, d, [p],
                     exempt={"alpha_tool": "容器路径一次性件", "gone_tool": "确实⛔ 在面上了"})
    assert rc2 == 1 and "STALE(豁免名已⛔ 在面)=1" in out2 and "✗ gone_tool" in out2, out2
    assert "BAD_EXEMPT(欠锁且未列豁免)=0" in out2 and "EMPTY_REASON=0" in out2, out2
    # 同批正控：带理由的在册豁免 ⇒ 绿，且仍在欠锁清单里点名（列而⛔ 删）
    rc3, out3 = _run(monkeypatch, s, t, d, [p], exempt={"alpha_tool": "容器路径一次性件"})
    assert rc3 == 0 and "豁免｜alpha_tool" in out3 and "BAD_EXEMPT(欠锁且未列豁免)=0" in out3, out3


def test_hand_transcribed_roster_name_absent_is_red(tmp_path, monkeypatch):
    """④：手抄名单里一枚名字⛔ 在 scripts 面 ⇒ 判红（真仓首跑就靠这条咬出抄错的对端尺名）。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool",), tests=("test_alpha_tool",))
    rc, out = _run(monkeypatch, s, t, d, [p], roster={"not_a_real_tool": "某对端件 §一"})
    assert rc == 1 and "ROSTER_ABSENT(名单里名字⛔ 在 scripts 面)=1" in out, out
    assert "✗ not_a_real_tool" in out, out
    # ★ 同批反控：名单里那枚⛔ 面上真有 ⇒ 0
    rc2, out2 = _run(monkeypatch, s, t, d, [p], roster={"alpha_tool": "某对端件 §一"})
    assert rc2 == 0 and "ROSTER_ABSENT(名单里名字⛔ 在 scripts 面)=0" in out2, out2


def test_gauge_refuses_to_report_when_a_face_collapses(tmp_path, monkeypatch):
    """⑤ fail-closed：取材面塌了⛔ 交一份"0 欠锁"的假绿，尺自己抛。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool",), tests=("test_alpha_tool",))
    empty_scripts = str(_mk(tmp_path, "empty_scripts"))
    with pytest.raises(AssertionError) as exc:
        _run(monkeypatch, empty_scripts, t, d, [p])
    assert "量具口径失效" in str(exc.value)
    empty_tests = str(_mk(tmp_path, "empty_tests"))
    with pytest.raises(AssertionError) as exc2:
        _run(monkeypatch, s, empty_tests, d, [p])
    assert "量具口径失效" in str(exc2.value)


def test_missing_peer_face_is_declared_as_a_lower_bound(tmp_path, monkeypatch):
    """头部声明：--face 指到⛔ 存在的目录时，读数要写明"少扫了几面＝下界"，⛔ 静默把范围读窄。"""
    s, t, p, d = _tree(tmp_path, scripts=("alpha_tool",), tests=("test_alpha_tool",))
    rc, out = _run(monkeypatch, s, t, d, [p, str(tmp_path / "ghost_face")])
    assert rc == 0, out
    assert "跳过⛔ 在场 1 枚" in out and "欠锁数是**下界**" in out, out


def test_real_repo_default_face_is_green_with_locked_positive_control(monkeypatch):
    """真仓正控（当场量，⛔ 写死枚数）：默认面（PEER_ROSTER-only）跑通，且名单那几枚都有锁。

    ★ 有锁／豁免都从**取材面本身**算（`lfr.locked` 的等价式），⛔ 从尺的 stdout 里 parse：
    名字清单打在 `LOCKED=` 那行**之后**，拿 `split("LOCKED")[0]` 去取名字必然取空（本席 16:2x 就栽过一次）。
    """
    monkeypatch.setattr(lfr, "EXEMPT", dict(lfr.EXEMPT))
    monkeypatch.setattr(sys, "argv", ["lock_face_ratchet.py", "--scripts",
                                      os.path.join(REPO, "scripts"), "--tests",
                                      os.path.join(REPO, "tests"), "--doc", os.path.join(REPO, "doc")])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lfr.main()
    out = buf.getvalue()
    assert rc == 0, out
    assert "LOCK_FACE_RATCHET=PASS" in out, out
    assert "NAKED(对端引用且无锁)=0" in out, out
    tests_blob, test_files = lfr.blob(os.path.join(REPO, "tests")), lfr.filenames(os.path.join(REPO, "tests"))
    assert lfr.hits(tests_blob, "lock_face_ratchet") > 0, "本尺⛔ 被自己的锁面认到 ⇒ 它自己就是欠锁的"
    assert int(out.split("LOCKED=")[1].split("｜")[0]) > 0, out          # 有锁面⛔ 塌
    for n in lfr.PEER_ROSTER:                                            # 名单里每一枚都得有锁（豁免另判）
        assert os.path.exists(os.path.join(REPO, "scripts", f"{n}.py")), n
        locked = lfr.hits(tests_blob, n) > 0 or f"test_{n}.py" in test_files or f"{n}_test.py" in test_files
        assert locked or n in lfr.EXEMPT, (n, out)
