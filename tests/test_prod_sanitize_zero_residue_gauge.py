#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针 `scripts/prod_sanitize_zero_residue_check.py` 的回归锁（第十三面）。

为什么这把尺要有锁：`.gates.toml` 的 `extra_skip_globs` 里有 `"scripts/*"`（"一次性脚本不参与
门禁"），`scripts/pyflakes_gate.sh` 的 `TARGET_DIR` 又只到 `src/memory_agent` ⇒ **仓内没有任何
一道权威门罩得住 `scripts/`**。这把尺的结论行 `PROD_SANITIZE_ZERO_RESIDUE=PASS` 是 PII 审计
S1「零残留」判定的出境读数（DCD 与链主侧文档都在引用它），尺自己写坏了⛔ 任何门会红。
所以这里钉的是**判据本身**（尺能不能拒绝一个弱掩码），⛔ 钉真仓当前命中数。

每枚夹具只违**一个**判据——第一版我把"弱掩码"写成"号段中间打星"，结果 `ok` 的三判据塌成一条
时这枚腿照旧绿（因为某个样例连"输出变了"都不满足，FAIL 是**错理由**撞出来的）。三枚夹具的形状：
· `LEAK_DIGITS`：姓名处理了、文本变了，≥5 位数字段一位没动 ⇒ 只有数字残留这一条能判红；
· `LEAK_NAME`：数字整段掩掉了、文本变了，成员姓名一位没动 ⇒ 只有姓名残留这一条能判红；
· `STRONG`：两条都满足 ⇒ 尺该 PASS。

射程（7 枚腿）：① 数字残留必须 FAIL；② 姓名残留必须 FAIL；③ 强掩码 PASS 且逐样例出声；
④ ⛔ 复述被脱敏文本（docstring 承诺"只给布尔与长度"）；⑤ 目标树 import 不进去时报 RC=2
而⛔ 抛裸栈；⑥ 读的是 `--src` 那棵树，⛔ 是环境里先到的那棵；⑦ 正控＝对本仓 `src/` 现跑。
★ `changed`（输出变了）这一判据**没有**孤立夹具：样例里必含数字，输出没变就必然有数字残留，
两条判据在合成输入上物理绑定 ⇒ 本锁⛔ 单独钉它，这是射程边界，⛔ 假装钉住了。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAUGE = os.path.join(REPO, "scripts", "prod_sanitize_zero_residue_check.py")

MASK_MARK = "<<MASKED>>"

STRONG_STORE = f'''
import re

class Store:
    @staticmethod
    def _sanitize_pii(text, member_names=None):
        out = re.sub(r"\\d{{5,}}", "{MASK_MARK}", text)
        for n in (member_names or []):
            out = out.replace(n, "{MASK_MARK}")
        return out
'''

# 违「数字零残留」这一条：姓名掩了、串也改了，≥5 位数字段原样留着。
LEAK_DIGITS_STORE = '''
class Store:
    @staticmethod
    def _sanitize_pii(text, member_names=None):
        out = text
        for n in (member_names or []):
            out = out.replace(n, "**")
        return out.replace(" ", "_")
'''

# 违「姓名零残留」这一条：数字整段掩掉、串也改了，成员姓名原样留着。
LEAK_NAME_STORE = f'''
import re

class Store:
    @staticmethod
    def _sanitize_pii(text, member_names=None):
        return re.sub(r"\\d{{5,}}", "{MASK_MARK}", text)
'''


def _tree(tmp_path, store_src):
    pkg = tmp_path / "tree" / "memory_agent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "store.py").write_text(store_src, encoding="utf-8")
    return str(tmp_path / "tree")


def _run(src_dir, pythonpath=None):
    env = dict(os.environ)
    if pythonpath is None:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = pythonpath
    p = subprocess.run([sys.executable, GAUGE, "--src", src_dir], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=REPO, env=env)
    return p.returncode, p.stdout + p.stderr


def test_the_probe_fails_a_sanitizer_that_leaves_digit_residue(tmp_path):
    rc, out = _run(_tree(tmp_path, LEAK_DIGITS_STORE))
    assert rc == 1, out
    assert "PROD_SANITIZE_ZERO_RESIDUE=FAIL" in out, out
    assert "zero_residue=False" in out, out


def test_the_probe_fails_a_sanitizer_that_leaves_the_member_name(tmp_path):
    rc, out = _run(_tree(tmp_path, LEAK_NAME_STORE))
    assert rc == 1, out
    assert "PROD_SANITIZE_ZERO_RESIDUE=FAIL" in out, out
    assert "name_residue=True" in out, out


def test_the_probe_passes_a_strong_sanitizer_and_reports_sample_by_sample(tmp_path):
    rc, out = _run(_tree(tmp_path, STRONG_STORE))
    assert rc == 0, out
    assert "PROD_SANITIZE_ZERO_RESIDUE=PASS" in out, out
    for i in range(3):                              # 逐样例出声，⛔ 只报一个汇总字
        assert f"sample[{i}]" in out, out
    assert "changed=True" in out and "zero_residue=True" in out, out
    assert "name_residue=False" in out, out


def test_the_probe_never_echoes_the_sanitized_text(tmp_path):
    """④ docstring 承诺"打印只给布尔与长度"——真实调用里那段文本可能含用户内容。"""
    rc, out = _run(_tree(tmp_path, STRONG_STORE))
    assert rc == 0, out
    assert MASK_MARK not in out, out                # 一旦回显脱敏结果，这枚腿立刻红
    assert "13800001111" not in out and "张三丰" not in out, out


def test_the_probe_reports_import_failure_rather_than_traceback(tmp_path):
    """⑤ 目标树里根本没有 memory_agent.store ⇒ RC=2 且明写 IMPORT_FAIL，⛔ 栈崩了算通过。"""
    empty = tmp_path / "nothing"
    empty.mkdir()
    rc, out = _run(str(empty))
    assert rc == 2, out
    assert "IMPORT_FAIL" in out, out
    assert "Traceback" not in out, out
    assert "PROD_SANITIZE_ZERO_RESIDUE" not in out, out   # ⛔ 跑到函数就⛔ 出结论行


def test_the_probe_reads_the_tree_it_was_pointed_at_not_the_one_that_arrives_first(tmp_path):
    """⑥ 尺的整个存在理由＝"验证**这一棵**树上的掩码真生效"。

    环境里先放一枚**强掩码**的 `memory_agent`（`PYTHONPATH`），`--src` 指向**弱掩码**那棵树。
    尺必须报 FAIL——若它把 `--src` 追加到 `sys.path` 末尾，先到的强掩码会赢，
    读数就成了"我验过环境里那份码"而⛔ "我验过你指给我的那棵树"。
    """
    decoy = _tree(tmp_path / "decoy", STRONG_STORE)
    weak = _tree(tmp_path / "weak", LEAK_DIGITS_STORE)
    rc, out = _run(weak, pythonpath=decoy)
    assert rc == 1, out
    assert "zero_residue=False" in out, out


def test_positive_control_on_the_real_src_tree():
    """⑦ 正控：本仓 src/ 现跑——这条钉的就是出境的那句结论。"""
    rc, out = _run(os.path.join(REPO, "src"))
    assert rc == 0, out
    assert "PROD_SANITIZE_ZERO_RESIDUE=PASS" in out, out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
