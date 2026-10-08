r"""#75 扫描量具的**控制腿**：把三条已知缺陷原样注入一份一次性副本树，看量具是否逐条捞出。

量具（`tmp-c75-docscan.py`）在干净树上读 `PHANTOM=0`，这句话本身没有信息量——
它可能只是"什么都扫不出来"。所以按第十五轮立的规矩补反例腿：
* 控制腿（本脚本）：注入 3 条**已知为假**的文案 ⇒ 必须新增 3+ 条嫌疑，且逐条对得上工具名；
* 已知为真腿：`get_data_coverage` 的文案（§四十八 已修，键都在载荷里）⇒ 必须继续静默。

只写 `.qoder/tmp-c75-ctl/` 下的一次性副本，工作树零改动（跑完按 sha 自证）。

    python .qoder/tmp-c75-ctl.py
    MA_DOCSCAN_ROOT=.qoder/tmp-c75-ctl/src/memory_agent python .qoder/tmp-c75-docscan.py
"""
import ast
import hashlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(ROOT, ".qoder", "tmp-c75-ctl", "src", "memory_agent")
SRC = os.path.join(ROOT, "src", "memory_agent")

#: (文件, 现文案锚点, 注入的死键文案) —— 三条就是本轮改掉的那三处，原样退回
INJECT = [
    ("mcp_server.py",
     '"""聚合数据质量：逐项 `checks`，未通过项的名字列在 `issues`。',
     '"""聚合数据质量：现有 data_quality_issues + 镜像缺口（mirror_dirty）。'),
    ("mcp_server.py",
     "return_hints=True 时另给顶层键 `hints`（规划阶段生成的候选追问，调试/混合用）。",
     "return_hints=True 时返回 semantic_hints / agent_memory_hints（调试/混合）。"),
    ("tool_schema.py",
     '"取值是意图名（device_usage/behavior/anomaly/rhythm/activity/persona，认不出来时 auto），"',
     '"这是规划工具，调用后必须接着按计划调用 recommended_tool 才能真正取数。"'),
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


def main():
    before = {rel: _sha(os.path.join(SRC, rel)) for rel in ("mcp_server.py", "tool_schema.py")}
    shutil.rmtree(os.path.join(ROOT, ".qoder", "tmp-c75-ctl"), ignore_errors=True)
    shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    for rel, new, bad in INJECT:
        path = os.path.join(DST, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if text.count(new) != 1:
            print("INJECT_BAD=%s anchor count=%d" % (rel, text.count(new)))
            return 1
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace(new, bad, 1))
        # 注入不许把源码改坏：语法一坏，量具的"响"就不是断言造成的了（mut75 L4 首版踩过）
        with open(path, encoding="utf-8") as fh:
            try:
                ast.parse(fh.read(), filename=path)
            except SyntaxError as exc:
                print("CTL_INJECT_SYNTAX=%s %r" % (rel, exc.msg))
                return 1

    after = {rel: _sha(os.path.join(SRC, rel)) for rel in ("mcp_server.py", "tool_schema.py")}
    print("INJECT_LEGS=%d COPIED=%s" % (
        len(INJECT), os.path.isdir(os.path.join(DST, "skills_bundle"))))
    print("CTL_TREE=%s" % DST)
    print("WORKTREE_UNCHANGED=%s %s" % (
        before == after, " ".join("%s=%s" % (k[:6], v) for k, v in after.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
