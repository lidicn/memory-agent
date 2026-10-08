"""元宝第四轮 P2-5：`insights/utils.py` 的 `KEYWORD_DOMAINS` 曾被后定义整份覆盖。

原缺陷（第四轮 :40 起）：同文件里 `:742`（英文键 8 个）与 `:1109`（中文键 15 + `ac`）同名，
后者覆盖前者 ⇒ `light`/`switch`/`climate`/`media`/`sensor`/`tv`/`aircon` 七个英文关键词在
`resolve_domains()`（旧 `insights_legacy.domains_for`，legacy 引擎内部 :369/:425 两处调用）
里静默解不出 domain，`ac` 的值也从 `('climate.ac',)` 退成 `('climate',)`。

现读（本文件锁之前先量的）：`utils.py` 内 `^KEYWORD_DOMAINS` 只剩 1 处（:1104，中英合并版），
`:744`/`:1102` 是解释性注释。也就是说 P2-5 的修复**只有注释在守**，一条测试都没有——
注释挡不住第三次重复定义。这里补三条：AST 数定义、英文键逐个出 domain、中文键不回归。

另一条路径（门面：`insights/api.py:321` → `parser/entity.py:35`）是**另一份**同名常量，
`KEYWORD_DOMAINS` 的键集合与 utils 版不等价（`aircon`/`switch`/`media` 只在 utils 版，
`motion`/`温度`/`电量` 只在 parser 版）。原本这条只在台账登记；DCD 20261006 §四.3 判
**丙**（不合并、差异显式在册 + 一条"差异集合必须等于登记值"的锁），所以下面
`test_two_vocab_paths_differ_by_exactly_the_registered_sets` 把它钉住；同批**追认可删**
的 `CATEGORY_DOMAINS` 冗余重复（utils 内两份逐键逐值相同）也已删掉，由
`test_category_domains_now_has_exactly_one_module_level_definition` 守复发。
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

    本批（DCD 20261006 §四.3 追认可删）之前：`utils.py` 顶层 `CATEGORY_DOMAINS` 有 2 处
    （:271 与 :1091），逐键逐值相同 ⇒ 后一份只是冗余重复，运行时行为不变。删掉前一份后
    这条锁仍然留着——它守的是**真正会出事的那一步**：哪天有人只改其中一份 ⇒ 两版不再
    相等 ⇒ 当场判红；「同一名字第二次定义」本身由下面两条单定义锁各自封住。
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


# ── DCD 20261006 §四.3：CATEGORY_DOMAINS 去重（追认可删）+ 两版词表差异在册加锁 ──────

# 现读的两条路径差异（`insights/utils.py` = 新引擎，`insights/parser/entity.py` = 门面路径）。
# 裁定 Q2 **丙**：不合并、不改名，把差异**显式在册**并加"差异集合必须等于登记值"的锁——
# 在拿到"结果集变化"的签字之前，最差的选择是"无人知道的差异"。
_KW_ONLY_IN_UTILS = {"aircon", "climate", "media", "sensor", "switch", "投影"}
_KW_ONLY_IN_PARSER = {"motion", "occupancy", "人体", "功率", "媒体", "存在", "播放",
                      "有人", "温度", "湿度", "热水器", "电量", "门"}
_KW_SAME_KEY_DIFF_VALUE = {"ac", "light", "tv"}


def _module_dicts_named(name: str) -> list[dict]:
    with open(_UTILS, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out: list[dict] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Dict):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            out.append({ast.literal_eval(k): ast.literal_eval(v)
                        for k, v in zip(node.value.keys, node.value.values)})
    return out


def test_category_domains_now_has_exactly_one_module_level_definition():
    """去重已获追认 ⇒ 缺陷类锁从 KEYWORD_DOMAINS 扩到 CATEGORY_DOMAINS。

    改前这里是 2 处（:271 与 :1091）且逐键逐值相同，本仓库当时只锁"两版不许分叉"；
    裁定追认可删之后，**第二次定义本身**就该判红（那正是 P2-5 静默覆盖的现场形状）。
    """
    defs = _module_dicts_named("CATEGORY_DOMAINS")
    assert len(defs) == 1, f"utils.py 顶层 CATEGORY_DOMAINS 又出现 {len(defs)} 份定义"
    with open(_UTILS, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    assigns = [node for node in ast.walk(tree)
               if isinstance(node, (ast.Assign, ast.AnnAssign))
               and any((isinstance(t, ast.Name) and t.id == "CATEGORY_DOMAINS")
                       for t in ([node.target] if isinstance(node, ast.AnnAssign)
                                 else node.targets))]
    assert len(assigns) == 1, [getattr(n, "lineno", 0) for n in assigns]


def test_two_vocab_paths_differ_by_exactly_the_registered_sets():
    """两条路径的词表**允许**不等价，但不许**悄悄**不等价：三个差异集合必须逐字等于登记值。

    - `CATEGORY_DOMAINS` 两边逐键逐值相同 ⇒ 不许有一侧单独加类别；
    - `KEYWORD_DOMAINS` 是 utils 26 键 / parser 33 键，差异只有这三堆：
      只在 utils 6 个、只在 parser 13 个、同名不同值 3 个（parser 侧一律更粗：
      `ac=('climate',)` 对 utils 的 `('climate.ac',)`）。
    哪天有人合并/扩表 ⇒ 本条判红，把"结果集变了"这件事推到台面上签字（裁定 Q2 丙）。
    """
    from memory_agent.insights.parser import entity as P

    assert _module_dicts_named("CATEGORY_DOMAINS")[0] == P.CATEGORY_DOMAINS
    utils_kw, parser_kw = KEYWORD_DOMAINS, P.KEYWORD_DOMAINS
    assert {k for k in utils_kw if k not in parser_kw} == _KW_ONLY_IN_UTILS
    assert {k for k in parser_kw if k not in utils_kw} == _KW_ONLY_IN_PARSER
    shared = set(utils_kw) & set(parser_kw)
    assert {k for k in shared if utils_kw[k] != parser_kw[k]} == _KW_SAME_KEY_DIFF_VALUE
    # 差异值本身也钉住：这三键是"同名不同口径"的全部现场，值一改就是改判据
    assert tuple(parser_kw["ac"]) == ("climate",) and tuple(utils_kw["ac"]) == ("climate.ac",)
    assert tuple(parser_kw["light"]) == ("light",)
    assert tuple(parser_kw["tv"]) == ("media_player",)
    assert len(utils_kw) == 26 and len(parser_kw) == 33, (len(utils_kw), len(parser_kw))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
