# 变异自咬（run11 / 容器口径）：任务表 #61 本批的六族新锁，每条都拆回审计报告原文描述的旧形状。
#
# 形状沿用 .qoder/tmp-c37-mutate.py（run10）：PY=sys.executable，根目录 MUT_ROOT，
# 先算出字节再写（`open(p,'wb')` 会立刻截断，绝不能串在可能失败的表达式前面），
# 每条跑完逐字还原并字节比对；MUTATION_BAD=0 且 restored_identical=OK 才算这批的门真咬得住。
#
# 「什么都不改」档由改动前的 TARGETED/SUITE 全绿读数充当，不在这里重复一次。
#
# 每条对应的旧形状（都是审计报告原文写的那一种，不是随手挑的字符串）：
#   N1/N2  第十三轮 P4-3：`max(...)` 一句里混进畸形/非字符串 ts ⇒ 整批基准换成容器墙钟、
#          或 TypeError 直冲没有 try/except 的调用点（behavior_routes.py:990、mcp_server.py:2770）
#   N3     第二轮 P2-4：同日多次出现靠 `sorted()` 取午后首次；去掉排序即退回顺序依赖
#   N4     第四轮 P2-5：同文件两处 KEYWORD_DOMAINS 各写各的 ⇒ 再加一处即复活分叉
#   N5/N6  第九轮 P2-8：learning_* 曾住在 src；attic 那份刻意保留裸绝对导入（登记不改）
#   N7     量具形状：UTF-8 BOM 让 `open(p, encoding="utf-8") + ast.parse` 当场 SyntaxError，
#          带 `except SyntaxError` 的扫描器会把这个文件从审计覆盖面里静默摘掉
#   N8     P2-5 的缺陷类本身：同名模块级第二份定义内容不一致 ⇒ 后一份静默覆盖前一份。
#          CATEGORY_DOMAINS 现下两份**逐键逐值相同**（:271 与 :1091），冗余本身只登记不改
#          （内置词表不自主动，#38 裁定），这里改后一份证明锁会响
import os
import subprocess
import sys

ROOT = os.environ.get("MUT_ROOT", ".")
PYLIBS = os.environ.get("PYLIBS", "/tmp/pylibs")

INTENT = "src/memory_agent/intent_inference.py"
PRED = "src/memory_agent/behavior_predictor.py"
UTILS = "src/memory_agent/insights/utils.py"
ATTIC = "attic/learning/learning_api.py"
SCRIPT = "scripts/reindex_embeddings.py"
STUB = "src/memory_agent/learning_models.py"

T_INTENT = ("tests/test_vma121_intent_and_pii.py",)
T_PRED = ("tests/test_vma_behavior_predictor_shapes.py",)
T_P25 = ("tests/test_vma_p25_keyword_domains_single_definition.py",)
T_P28 = ("tests/test_vma_p28_learning_attic_unreachable.py",)
T_BOM = ("tests/test_vma_gate_scanners_read_every_py.py",)

STUB_BYTES = b"def propose(*a, **k):\n    return None\n"

# (名称, 相对路径, 模式, 参数, 测试文件)
#   replace: (旧串, 新串, 第几次命中)    bom: () 前置 3 字节    newfile: (字节内容,)
MUTS = [
    ("N1 P4-3 坏 ts 兜回容器墙钟", INTENT, "replace",
     ("    except ValueError:\n        return None",
      "    except ValueError:\n        return datetime.now()", 1), T_INTENT),
    ("N2 P4-3 拆掉非字符串守卫（500 路径回来）", INTENT, "replace",
     ("    if not isinstance(raw, str) or not raw:\n        return None",
      "    if not raw:\n        return None", 1), T_INTENT),
    ("N3 P2-4 去掉按天分组后的排序（顺序依赖回来）", PRED, "replace",
     ("    dts_sorted = sorted(dts)", "    dts_sorted = list(dts)", 1), T_PRED),
    ("N4 P2-5 再加一处模块级 KEYWORD_DOMAINS（词表分叉回来）", UTILS, "replace",
     ('def resolve_domains(category: str = "", domain: str = "", query: str = "") -> list[str]:',
      'KEYWORD_DOMAINS = {"light": ("light",)}\n\n\n'
      'def resolve_domains(category: str = "", domain: str = "", query: str = "") -> list[str]:',
      1), T_P25),
    ("N5 P2-8 src 里放回 learning 模块", STUB, "newfile", (STUB_BYTES,), T_P28),
    ("N6 P2-8 attic 裸导入改成包内相对（登记口径变了）", ATTIC, "replace",
     ("from learning_models import LINK_LABEL",
      "from .learning_models import LINK_LABEL", 1), T_P28),
    ("N7 给在册文件加回 UTF-8 BOM（量具静默漏审的形状）", SCRIPT, "bom", (), T_BOM),
    ("N8 P2-5 两份 CATEGORY_DOMAINS 只改后一份（静默覆盖）", UTILS, "replace",
     ('    "presence": ("binary_sensor", "device_tracker", "person"),',
      '    "presence": ("binary_sensor",),', 2), T_P25),
]


def run_tests(paths):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.join(ROOT, "src"), PYLIBS, ROOT]))
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header",
                           "-p", "no:cacheprovider", *paths],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=ROOT, env=env)


def nth_position(text, needle, nth):
    pos = -1
    for _ in range(nth):
        pos = text.find(needle, pos + 1)
        if pos < 0:
            return -1
    return pos


def report(name, r, extra=""):
    failed = [ln.split(" ")[1] for ln in r.stdout.splitlines() if ln.startswith("FAILED")]
    tail = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln]
    print("%s\n    RC=%d -> %s%s" % (name, r.returncode,
                                     "咬住了" if r.returncode else "没咬住（变异存活）", extra))
    print("    %s" % (tail[-1] if tail else "无汇总行"))
    for f in failed[:4]:
        print("      x %s" % f)
    bites = r.returncode != 0 and bool(failed)
    if not bites:
        print("    异常：要么变异存活，要么 RC 非 0 但没有 FAILED 行（收集期崩了不算咬住）")
    return 0 if bites else 1


bad = 0
restore_fail = []
for name, rel, mode, args, tests in MUTS:
    path = os.path.join(ROOT, rel)
    if mode == "newfile":
        if os.path.exists(path):
            print("%s -> 桩文件已存在，变异未施加（自咬失败）" % name)
            bad += 1
            continue
        with open(path, "wb") as fh:
            fh.write(args[0])
        r = run_tests(tests)
        os.remove(path)
        ok_restore = not os.path.exists(path)
        if not ok_restore:
            restore_fail.append(rel)
        bad += report(name, r, "" if ok_restore else " / 桩文件没删掉")
        continue

    with open(path, "rb") as fh:
        original = fh.read()
    if mode == "bom":
        mutated = b"\xef\xbb\xbf" + original
    else:
        old, new, nth = args
        text = original.decode("utf-8")
        pos = nth_position(text, old, nth)
        if pos < 0 or (nth == 1 and text.count(old) != 1):
            print("%s -> 锚点命中 %d 次（要第 %d 次且首档唯一），变异未施加（自咬失败）"
                  % (name, text.count(old), nth))
            bad += 1
            continue
        mutated = (text[:pos] + new + text[pos + len(old):]).encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(mutated)
    r = run_tests(tests)
    with open(path, "wb") as fh:
        fh.write(original)
    ok_restore = open(path, "rb").read() == original
    if not ok_restore:
        restore_fail.append(rel)
    bad += report(name, r, "" if ok_restore else " / 还原失败")

print("restored_identical=%s" % ("OK" if not restore_fail else "FAIL:" + ",".join(restore_fail)))
print("MUTATION_BAD=%d" % bad)
