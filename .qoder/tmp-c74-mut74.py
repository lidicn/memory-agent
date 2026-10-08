"""变异档 MUT74：证明 §四十七 那批文案锁真的会咬。

五腿：
  M-0  什么都不改（门自己没坏的那条基线，缺它其余全红也看不出差别）
  L1   docstring 回滚到改前那句（承诺 has_data / first-last）⇒ 第 1、2 条锁必须红
  L2   载荷不再给 `peak_hours`（"文案说有、实现没有"的最小可构造形状）⇒ 第 1 条红
  L3   `start_day` 被写成首个有数据日（把窗口边界与数据边界混掉）⇒ 第 4 条红
  L4   技能包 SKILL.md 的承诺回到死键版 ⇒ 第 5/6 条（prose 面）红
  L5   用户手册 user_manual.js 的承诺回到死键版 ⇒ 第 5/6 条（prose 面）红

规矩（沿用上轮教训）：
  - `if __name__ == "__main__"` 保护，import 无副作用；
  - 锚点唯一性**当场量**，不从上轮记忆抄；单行 `return …` 这类会撞号的必须带上文；
  - **出货文案面里有 CRLF 文件**（SKILL.md / user_manual.js 整份 CRLF），锚点按该文件真实行尾归一，
    否则"锚点不唯一"其实是行尾对不上；
  - 每条腿跑完按字节还原并比 sha256，还原不成立即停；
  - PYTHONPATH 是**追加**不是覆盖（覆盖会摘掉容器里的 /tmp/pylibs ⇒ 腿自己死，产品没红）；
  - 只在 `MUT_ROOT`（一次性副本树）里跑，绝不动工作树。
"""

import hashlib
import os
import re
import subprocess
import sys

MCP = "src/memory_agent/mcp_server.py"
SVC = "src/memory_agent/insights/service.py"
SKILL = "src/memory_agent/skills_bundle/insight/SKILL.md"
MANUAL = "src/memory_agent/static/js/pages/user_manual.js"
FILES = (MCP, SVC, SKILL, MANUAL)
TESTS = ["tests/test_vma_coverage_docstring_contract.py",
         "tests/test_vma_insights_window_echo.py"]

OLD_DOC_HEADLINE = "        \"\"\"数据覆盖报告：逐日给出事件量与空日标记，定位\"为什么某天没数据\"。\n"
NEW_DOC_HEADLINE_BACKDATED = (
    "        \"\"\"数据覆盖报告：明确告诉你窗口内实际「有数据」的日期。\n"
    "\n"
    "        避免误以为前几天的空白也是「有数据」。返回每天的 events 量与 has_data 标记、\n"
    "        first/last 有数据的日期、以及 missing_days 列表。取数前先调它确认窗口完整性。\n"
    "        \"\"\"\n")
# 改前整段（5 行）—— 用它替换当前 7 行版本，等价于"把文案回滚到承诺死键的那版"
CUR_DOC_BLOCK = (
    "        \"\"\"数据覆盖报告：逐日给出事件量与空日标记，定位\"为什么某天没数据\"。\n"
    "\n"
    "        `days[]` 每格是 `{day, events, active_hours, hours, empty}`（`empty=true` 即当天 0 条），\n"
    "        `missing_days` 是这些空日的清单；`start_day/end_day` 是**查询窗口边界**（不等于\"有数据的\n"
    "        首末日\"，首末日要从 `days[]`/`missing_days` 自己读）；覆盖率看 `day_coverage`（有数据天数\n"
    "        占比）与 `hour_coverage`，高峰看 `peak_hours`。取数前先调它确认窗口完整性。\n"
    "        \"\"\"\n")
PEAK_LINE = '            "peak_hours": peak,\n'
START_DAY_LINE = '            "start_day": start_day,\n'
START_DAY_MUT = ('            "start_day": next((d for d in all_days if day_counts.get(d)), start_day),\n')

#: 两处 prose 面的锚点取**不含行尾、不含引号**的那段（各自在文件里出现 1 次，上一格量过）。
#: SKILL.md / user_manual.js 整份是 CRLF：锚点若带 `\n`，在 CRLF 文件里 count 会变 0，
#: 看起来像"锚点不唯一"，其实是行尾对不上 ⇒ 这两条腿锚死在单行内部。
SKILL_ANCHOR = ("- `get_data_coverage(days=7)` —— 取数前先调它确认窗口完整性。"
                "`days[]` 每格给 `{day, events, active_hours, hours, empty}`，")
SKILL_MUT = ("- `get_data_coverage(days=7)` —— 取数前先调它确认窗口完整性。返回每天 `events` 量与 "
             "`has_data` 标记、")
MANUAL_ANCHOR = ("返回每天的事件量与 empty 空日标记（empty=true 即当天 0 条）、missing_days 空日清单、"
                 "day_coverage / hour_coverage 覆盖率、peak_hours 高峰；start_day / end_day 只是查询窗口边界，"
                 "不等于「有数据的首末日」，首末日要从 days[] 自己读。")
MANUAL_MUT = ("返回每天的事件量与 has_data 标记、first/last 有数据日期、missing_days 列表。")

LEGS = [
    ("M-0", "什么都不改（基线：证明门自己没坏）", MCP, None, None),
    ("L1", "文案回滚到承诺 has_data/first-last 的那版", MCP, CUR_DOC_BLOCK, NEW_DOC_HEADLINE_BACKDATED),
    ("L2", "载荷不再给 peak_hours（文案说有、实现没有）", SVC, PEAK_LINE, ""),
    ("L3", "start_day 写成首个有数据日（边界与数据边界混掉）", SVC, START_DAY_LINE, START_DAY_MUT),
    ("L4", "技能包 SKILL.md 的承诺回滚到死键版（模型读的那份）", SKILL, SKILL_ANCHOR, SKILL_MUT),
    ("L5", "用户手册 user_manual.js 的承诺回滚到死键版（人读的那份）", MANUAL, MANUAL_ANCHOR, MANUAL_MUT),
]


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def verify(root):
    """AST 之外还要量的三件事：锚点在**当前树里唯一**、还原前指纹能记下来、腿名不重复。"""
    bad = []
    seen = set()
    for name, _desc, rel, old, new in LEGS:
        if name in seen:
            bad.append("DUP_LEG " + name)
        seen.add(name)
        if old is None:
            continue
        src = os.path.join(root, rel)
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        c = text.count(old)
        if c != 1:
            bad.append("ANCHOR_BAD %s count=%d" % (name, c))
        if old == new:
            bad.append("NO_OP_LEG " + name)
    print("MUTANTS_PARSED=%d VERIFY_BAD=%d" % (len(LEGS), len(bad)))
    for b in bad:
        print("VERIFY " + b)
    return 0 if not bad else 1


def run_leg(root, name, desc, rel, old, new):
    path = os.path.join(root, rel)
    mutated = None
    original = None
    before = _sha(path)
    if old is not None:
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        assert text.count(old) == 1, "%s 锚点不唯一" % name
        original = text
        mutated = text.replace(old, new)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(mutated)
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    src = os.path.abspath(os.path.join(root, "src"))
    env["PYTHONPATH"] = (src + os.pathsep + pp) if pp else src
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"] + TESTS,
                          cwd=root, env=env, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = [ln for ln in out.strip().split("\n") if " passed" in ln or " failed" in ln]
    failed = len(re.findall(r"^FAILED ", out, flags=re.M))
    if old is not None:
        # 还原写回的是**原文那一份**（不是 mutated）——上一版把 mutated 又写了一遍，
        # 于是三条腿全报 restored=BAD：腿确实咬住了，但"没污染树"这句话当时并不成立。
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(original)
        restored = "OK" if _sha(path) == before else "BAD"
    else:
        restored = "NA"
    if name == "M-0":
        bites = proc.returncode == 0
    else:
        bites = proc.returncode != 0 and restored == "OK" and failed > 0
    print("%s %s -> RC=%d %s%s | %s" % (
        name, desc, proc.returncode,
        ("failed=%d 咬住了 " % failed) if name != "M-0" else "",
        ("restored=" + restored) if name != "M-0" else "基线",
        (tail[-1] if tail else out.strip()[-160:])))
    return bites


def main(root):
    if verify(root) != 0:
        print("VERIFY_FAILED 未跑任何腿")
        return 2
    ok = []
    for leg in LEGS:
        ok.append(run_leg(root, *leg))
    bad = sum(1 for x in ok if not x)
    print("MUT_COUNT=%d MUTATION_BAD=%d" % (len(LEGS), bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--verify"]
    root = os.path.abspath(args[0] if args else ".")
    # `--verify` = 只读预检（远端门的 PREFLIGHT 格要用它，不许开跑腿）
    if "--verify" in sys.argv[1:]:
        sys.exit(verify(root))
    sys.exit(main(root))
