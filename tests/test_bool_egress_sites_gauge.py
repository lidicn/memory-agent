#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分类量具 `scripts/bool_egress_sites.py` 的回归锁（任务表 #92／#116 那把尺）。

为什么这把尺一定要有锁：`.gates.toml` 的 `extra_skip_globs` 里写着 `"scripts/*"`（一次性脚本
不参与门禁），`scripts/pyflakes_gate.sh` 的 `TARGET_DIR` 又只有 `src/memory_agent`——
**仓内没有任何一道权威门罩得住 `scripts/`**。量具自己写坏了，门⛔ 会红，而 DCD 正等它这份读数
定"布尔出口归零"的范围（#92）。所以这里钉的是**判据本身**，⛔ 钉仓内当前命中数——
真仓的计数会随代码漂，把它钉成期望值只会让下一只手去改期望值而⛔ 去看尺。

射程（逐条对应尺的四个部件）：
① 声明面只认**模块级类**里标了 `-> bool` 的方法（⛔ 收模块级函数、⛔ 收无注解／别的注解）；
② 接收者分档——同名别对象（`presence.advertise` 那一类）落 `NOT_THE_BRIDGE`，⛔ 进 DISCARDED 计数；
③ 丢弃判据＝该调用的**直接父节点是 `ast.Expr`**；出声档位是**函数级**的；
④ 声明面读得太窄时尺**自己拒绝出读数**（fail-closed），⛔ 静默交一份范围变窄的清单。
"""

from __future__ import annotations

import importlib.util
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "bool_egress_sites", os.path.join(REPO, "scripts", "bool_egress_sites.py"))
bes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bes)

BRIDGE_SRC = '''
class MqttBridge:
    def publish(self) -> bool: ...
    def publish_raw(self, t) -> bool: ...
    def advertise(self) -> bool: ...
    def enabled(self) -> bool: ...
    def reset_client(self) -> bool: ...
    def _check_publish_rc(self) -> bool: ...
    def quiet(self) -> None: ...
    def plain(self): ...

def helper() -> bool: ...
'''

# 一真一假的两个接收者：`feed.enabled()` 与 `presence.advertise()` 命中的是**同名方法**，
# 但接收者⛔ 是桥——这正是尺的口径盲区①，它们必须落 NOT_THE_BRIDGE 档。
CALLER_SRC = '''
class AppRuntime:
    def _init_background(self):
        self.mqtt.publish_raw("a")
        ok = self.mqtt.publish("b")
        feed.enabled()
        presence.advertise()

    def _silent(self):
        self.mqtt.reset_client()
'''


def _tree(tmp_path):
    src = tmp_path / "src" / "memory_agent"
    src.mkdir(parents=True)
    (src / "mqtt_bridge.py").write_text(BRIDGE_SRC, encoding="utf-8")
    (src / "runtime.py").write_text(CALLER_SRC, encoding="utf-8")
    return str(src)


def test_declaration_face_takes_only_class_level_bool_returns(tmp_path):
    p = os.path.join(_tree(tmp_path), "mqtt_bridge.py")
    names = bes.bool_api(p)
    assert names == {"publish", "publish_raw", "advertise", "enabled", "reset_client",
                     "_check_publish_rc"}, names
    # 模块级 `helper() -> bool`、`-> None` 的 quiet、无注解的 plain 一律⛔ 进声明面
    assert not ({"helper", "quiet", "plain"} & names), names


def test_receiver_class_separates_the_bridge_from_same_named_others():
    assert bes.receiver_class("self.mqtt", "src/memory_agent/runtime.py") == "BRIDGE"
    assert bes.receiver_class("mqtt_bridge", "src/memory_agent/x.py") == "BRIDGE"
    assert bes.receiver_class("self", "src/memory_agent/mqtt_bridge.py") == "BRIDGE"
    # 同名别对象：假阳面，必须逐枚留档而⛔ 并进桥的计数
    assert bes.receiver_class("feed", "src/memory_agent/runtime.py") == "NOT_THE_BRIDGE"
    assert bes.receiver_class("presence", "src/memory_agent/runtime.py") == "NOT_THE_BRIDGE"
    assert bes.receiver_class("self", "src/memory_agent/runtime.py") == "NOT_THE_BRIDGE"


def test_voice_tier_is_function_wide(tmp_path, capsys):
    tree = _tree(tmp_path)
    src = open(os.path.join(tree, "runtime.py"), encoding="utf-8").read()
    import ast
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_init_background")
    assert bes.has_voice(fn) is False      # 这段里⛔ print／logger ⇒ DISCARDED/SILENT 那一档
    voiced = ast.parse("def f():\n    x = 1\n    print('x')\n")
    assert bes.has_voice(voiced.body[0]) is True


def test_gauge_counts_are_self_consistent_on_a_synthetic_tree(tmp_path, monkeypatch, capsys):
    """端到端跑一遍 main()：ALL_NAME_HITS 必须等于三档之和，且同名别对象⛔ 进桥的 DISCARDED。"""
    tree = _tree(tmp_path)
    # 尺用 os.path.relpath(p, REPO) 做相对显示：合成树在另一块盘符上（本机 tmp=C:、仓=E:）
    # 时它会 ValueError 而⛔ 出读数。这里把 REPO 一起指过去，⛔ 为此改尺的行为。
    monkeypatch.setattr(bes, "REPO", str(tmp_path))
    monkeypatch.setattr(bes, "SRC", tree)
    monkeypatch.setattr(bes, "BRIDGE", os.path.join(tree, "mqtt_bridge.py"))
    assert bes.main() == 0
    out = capsys.readouterr().out
    api = re.search(r"API\(`-> bool`，声明面现读\)= (\d+) 个", out)
    hits = re.search(r"ALL_NAME_HITS= (\d+)｜BRIDGE 面: DISCARDED= (\d+)｜USED= (\d+)"
                     r"｜NOT_THE_BRIDGE[^=]*= (\d+)", out)
    assert api and hits, out
    disc, used, other = (int(hits.group(i)) for i in (2, 3, 4))
    assert int(hits.group(1)) == disc + used + other       # 自洽：⛔ 静默吞掉任何一档
    assert disc == 2, out          # self.mqtt.publish_raw + self.mqtt.reset_client
    assert used == 1, out          # ok = self.mqtt.publish
    assert other == 2, out         # feed.enabled / presence.advertise 落假阳档
    # 桥面汇总里⛔ 混进别对象的同名方法（这条才是"接收者分档"真正咬住的地方）
    tail = [ln for ln in out.splitlines() if ln.startswith("DISCARDED(桥面) 按方法＝")]
    assert tail == ["DISCARDED(桥面) 按方法＝ publish_raw=1 | reset_client=1"], out
    assert "runtime.py:" in out and "DISCARDED/SILENT" in out
    assert out.rstrip().endswith("本席⛔ 自决扩大改动面。")   # NOTE 行⛔ 丢（它是范围等裁的边界声明）


def test_gauge_refuses_to_report_when_the_declaration_face_is_too_narrow(tmp_path, monkeypatch):
    """④ fail-closed：桥只剩 2 个 `-> bool` 时尺自己拒跑，⛔ 交一份范围静默变窄的清单。"""
    tree = _tree(tmp_path)
    narrow = '''
class MqttBridge:
    def publish(self) -> bool: ...
    def available(self) -> bool: ...
'''
    open(os.path.join(tree, "mqtt_bridge.py"), "w", encoding="utf-8").write(narrow)
    monkeypatch.setattr(bes, "REPO", str(tmp_path))
    monkeypatch.setattr(bes, "SRC", tree)
    monkeypatch.setattr(bes, "BRIDGE", os.path.join(tree, "mqtt_bridge.py"))
    with pytest.raises(AssertionError) as exc:
        bes.main()
    assert "量具口径失效" in str(exc.value)
