"""元宝第四轮 P2-5：`insights/utils.py` 的 `KEYWORD_DOMAINS` 曾被后定义整份覆盖。

原缺陷（第四轮 :40 起）：同文件里 `:742`（英文键 8 个）与 `:1109`（中文键 15 + `ac`）同名，
后者覆盖前者 ⇒ `light`/`switch`/`climate`/`media`/`sensor`/`tv`/`aircon` 七个英文关键词在
`resolve_domains()`（旧 `insights_legacy.domains_for`，legacy 引擎内部 :369/:425 两处调用）
里静默解不出 domain，`ac` 的值也从 `('climate.ac',)` 退成 `('climate',)`。

现读（本文件锁之前先量的）：`utils.py` 内 `^KEYWORD_DOMAINS` 只剩 1 处（:1104，中英合并版），
`:744`/`:1102` 是解释性注释。也就是说 P2-5 的修复**只有注释在守**，一条测试都没有——
注释挡不住第三次重复定义。这里补三条：AST 数定义、英文键逐个出 domain、中文键不回归。

另一条只登记不改动：门面路径（`insights/api.py:321` → `parser/entity.py:35`）是**另一份**
同名常量，英文键集合与 utils 版不等价（如 `aircon`/`switch`/`media` 只在 utils 版里）。
两条路径各服务一个引擎，词表要合口径属于**改内置词表**，不在自主决定范围内（只在台账登记）。
"""

import ast
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.insights.utils import (KEYWORD_DOMAINS,  # noqa: E402
                                         resolve_domains)

_UTILS = os.path.abspath(os.path.join(_SRC, "memory_agent", "insights", "utils.py"))

# 第四轮点名"7 个失效"的那批英文键 + 值被改动过的 ac
_LOST_ENGLISH = ("light", "switch", "climate", "media", "sensor", "tv", "aircon")


def _module_level_targets(tree: ast.Module) -> list[str]:
    """模块顶层被赋值过的名字（`Assign` 与带注解的 `AnnAssign` 都算）。"""
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def test_keyword_domains_has_exactly_one_module_level_definition():
    """P2-5 的缺陷类是"模块顶层同名第二次赋值"，量具必须直接数定义而不是数键。

    第二次定义会在运行时静默覆盖第一次——pyflakes 看不见（它报的是 undefined），
    键集合断言也可能被"两版都含中文键"糊过去。只有"定义次数 == 1"能挡住复发。
    """
    with open(_UTILS, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    hits = [n for n in _module_level_targets(tree) if n == "KEYWORD_DOMAINS"]
    assert len(hits) == 1, (
        f"insights/utils.py 顶层出现 {len(hits)} 次 KEYWORD_DOMAINS 赋值 ⇒ 后一份会整份覆盖前一份")
    # 顶层不许有 `global KEYWORD_DOMAINS` 之类的后门：全文件出现次数只允许在注释/docstring 外
    # 由那一次赋值 + 读取点构成，这里量"赋值语句"总数（含函数内的）
    assigns = [node for node in ast.walk(tree)
               if isinstance(node, (ast.Assign, ast.AnnAssign))
               and any((isinstance(t, ast.Name) and t.id == "KEYWORD_DOMAINS")
                       for t in ([node.target] if isinstance(node, ast.AnnAssign)
                                 else node.targets))]
    assert len(assigns) == 1, [getattr(n, "lineno", 0) for n in assigns]


def test_english_keywords_resolve_to_domains():
    """七个曾失效的英文键逐个必须解析出非空 domain（大小写与空格由 normalize_text 兜）。"""
    for word in _LOST_ENGLISH:
        assert resolve_domains(query=word), f"英文键 {word!r} 又解不出 domain 了"
        assert resolve_domains(query=f"  {word.upper()}  ") == resolve_domains(query=word)


def test_english_key_values_are_the_merged_version():
    """键在还不算修好：`ac` 被覆盖后从 `('climate.ac',)` 退成 `('climate',)` 也是这次的现场。"""
    assert KEYWORD_DOMAINS["ac"] == ("climate.ac",), KEYWORD_DOMAINS.get("ac")
    assert KEYWORD_DOMAINS["aircon"] == ("climate.ac",), KEYWORD_DOMAINS.get("aircon")
    assert KEYWORD_DOMAINS["switch"] == ("switch", "input_boolean")
    assert KEYWORD_DOMAINS["media"] == ("media_player", "remote")
    assert "light" in resolve_domains(query="light")
    assert "climate.ac" in resolve_domains(query="aircon")


def test_ascii_key_population_is_the_merged_set():
    """ASCII 键的**人口**必须正好是这 8 个：少一个=又被覆盖，多一个=口径变了要重新登记。"""
    ascii_keys = {k for k in KEYWORD_DOMAINS if k.isascii()}
    assert ascii_keys == set(_LOST_ENGLISH) | {"ac"}, sorted(ascii_keys)


def test_module_level_vocab_tables_never_define_the_same_name_twice_with_different_values():
    """P2-5 的缺陷类是「同名模块级第二次赋值静默覆盖第一次」，锁要按缺陷类开，不只盯 KEYWORD_DOMAINS。

    现读：`utils.py` 顶层 `CATEGORY_DOMAINS` 有 **2** 处（:271 与 :1091），逐键逐值相同 ⇒ 后一份
    只是冗余重复，运行时行为不变；这条冗余本身登记在台账里等批（内置词表不自主改，见 #38 裁定），
    本条锁的是**真正会出事的那一步**：哪天有人只改其中一份 ⇒ 两版不再相等 ⇒ 当场判红。
    """
    with open(_UTILS, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    seen: dict[str, list[dict]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        if not isinstance(node.value, ast.Dict):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id.endswith("_DOMAINS"):
                seen.setdefault(t.id, []).append(
                    {ast.literal_eval(k): ast.literal_eval(v) for k, v in
                     zip(node.value.keys, node.value.values)})
    for name, defs in sorted(seen.items()):
        if len(defs) < 2:
            continue
        assert all(d == defs[0] for d in defs), (
            f"{name} 顶层 {len(defs)} 份定义内容不一致 ⇒ 后一份会静默覆盖前一份（P2-5 原现场）")


def test_chinese_keys_do_not_regress_while_fixing_english():
    """反过来也一样：中文键是现网主路径，修英文不许把中文挤掉。"""
    for word, expect in (("空调", "climate"), ("灯", "light"), ("加湿", "humidifier"),
                         ("电视", "media_player"), ("扫地", "vacuum"), ("窗帘", "cover")):
        assert expect in resolve_domains(query=f"主卧{word}"), word


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
