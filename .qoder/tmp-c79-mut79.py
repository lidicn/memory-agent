r"""#79 的变异档：温控环比回到新引擎之后，七把锁必须真会红。

纪律（台账 §四十八/§五十/§五十二 那几条坑的正面执行）：
* 只在**一次性副本树**里改，绝不在飞行的权威树或本仓工作树上动手；每条腿用完即删；
* 跑之前先 `--verify`：锚点在当前树里现场量、语法合法、腿名不重复——不跑测试，只静态量；
* M-0 是"什么都不改"的对照腿：必须全绿，否则整表读数为零信息量；
* 判据是"这条腿让 FAILED>0"，不是"输出里出现了某个词"；
* 每条腿打完补丁先 `ast.parse`：注入把源码改坏时测试也会"响"，那响不是断言造成的。

被裁乙案的形状是"引擎经门面注入的回调取数"，所以腿分两组：
L1/L7 动**载荷**（那一格没了 / 降级信封没了它），L2 动**注入方向**（引擎去读 self.legacy），
L3 动**构造点**（热更新那条漏注入），L4 动**窗口口径**（provider 忽略入参 → 环比恒 0），
L5 动**降级形状**（异常被吞成空表 = 静默降级复发），L6 动**键名**（就地手搓、不叫 utils 那两名）。

    python .qoder/tmp-c79-mut79.py --verify
    python .qoder/tmp-c79-mut79.py
"""
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_PY = os.path.join("src", "memory_agent", "insights", "service.py")
API_PY = os.path.join("src", "memory_agent", "insights", "api.py")
TEST_FILE = os.path.join("tests", "test_insights_facade_contract.py")

BLOCK_LINE = '            "climate_comparison": self._climate_comparison(cur, prev),\n'
PROVIDER_READ = "        provider = self.climate_provider"
PROVIDER_LEGACY = "        provider = self.legacy.climate_sessions"
SWALLOW = '            return _climate_unavailable("%s: %s" % (type(exc).__name__, exc))'
SWALLOW_TO_EMPTY = (
    '            return {"current": aggregate_climate_sessions([]),\n'
    '                    "previous": aggregate_climate_sessions([]),\n'
    '                    "delta_hours": 0.0, "delta_avg_setpoint_c": None}')
UTILS_CALL = "            return compare_climate(cur_agg, prev_agg)"
HANDROLLED = ('            return {"cur": cur_agg, "prev": prev_agg,\n'
              '                    "delta_hours": round(cur_agg["hours"] - prev_agg["hours"], 1),\n'
              '                    "delta_setpoint": None}')
DEFAULTS = '                    "filters": {}, "climate_comparison": _climate_unavailable("整页降级，温控块未计算")}'
DEFAULTS_BARE = '                    "filters": {}}'
PROVIDER_BODY = "        out = self.legacy.climate_sessions(start=start_iso, end=end_iso)"
PROVIDER_FROZEN = ('        out = self.legacy.climate_sessions(\n'
                   '            start="2000-01-01T00:00:00", end="2099-01-01T00:00:00")')
CONSTRUCT = ("        self.core = BehaviorService(self.repo, self.resolver, self.config,\n"
             "                                    climate_provider=self._climate_sessions_for_window)")
CONSTRUCT_BARE = "        self.core = BehaviorService(self.repo, self.resolver, self.config)"

#: occ = 替换第几处（0 基）；check_target=False 用于"替换后文本本来就是树的一部分"那些腿
LEGS = [
    {"name": "M-0", "desc": "什么都不改（对照腿：必须全绿）", "rel": None,
     "anchor": None, "repl": None, "occ": 0, "check_target": True},
    {"name": "L1-block-dropped", "desc": "环比载荷里那一格又被洗掉（修复 #9 复发的形状）",
     "rel": SERVICE_PY, "anchor": BLOCK_LINE, "repl": "", "occ": 0, "check_target": False},
    {"name": "L2-engine-reads-self-legacy", "desc": "引擎绕过注入回调直接读 self.legacy",
     "rel": SERVICE_PY, "anchor": PROVIDER_READ, "repl": PROVIDER_LEGACY,
     "occ": 0, "check_target": True},
    {"name": "L3-hotreload-site-loses-injection", "desc": "reload_config 那个构造点漏注入 provider",
     "rel": API_PY, "anchor": CONSTRUCT, "repl": CONSTRUCT_BARE, "occ": 1, "check_target": False},
    {"name": "L4-provider-ignores-window", "desc": "provider 不吃入参、两窗口取同一段（环比恒 0）",
     "rel": API_PY, "anchor": PROVIDER_BODY, "repl": PROVIDER_FROZEN, "occ": 0, "check_target": True},
    {"name": "L5-error-swallowed-into-empty", "desc": "取数异常被吞成空表（静默降级复发）",
     "rel": SERVICE_PY, "anchor": SWALLOW, "repl": SWALLOW_TO_EMPTY, "occ": 0, "check_target": True},
    {"name": "L6-hand-rolled-keys", "desc": "不叫已迁好的 utils、就地手搓且键名改了",
     "rel": SERVICE_PY, "anchor": UTILS_CALL, "repl": HANDROLLED, "occ": 0, "check_target": True},
    {"name": "L7-envelope-loses-block", "desc": "整页降级信封不再带那一格",
     "rel": SERVICE_PY, "anchor": DEFAULTS, "repl": DEFAULTS_BARE, "occ": 0, "check_target": True},
]


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _patch(text, anchor, repl, occ):
    """把第 occ 处锚点换成 repl（L3 两处构造点必须各自可寻）。

    第一版用 `anchor.join(parts[…])` 拼，把边界上那个锚点又插回去了——
    `--verify` 现场把七条腿全判成"补丁不合法语法"，正是这个自伤形状该拦的东西。
    """
    if not anchor:
        raise ValueError("空锚点")
    idx = -1
    for _ in range(occ + 1):
        idx = text.find(anchor, idx + 1)
        if idx < 0:
            raise ValueError("锚点出现次数不足（需要第 %d 处）" % occ)
    return text[:idx] + repl + text[idx + len(anchor):]


def verify(root):
    """只读预检：锚点计数在当前树现场量，替换目标不许已在树里（那会让读数不可信）。"""
    bad = []
    seen = set()
    texts = {}
    for leg in LEGS:
        if leg["name"] in seen:
            bad.append("腿名重复：%s" % leg["name"])
        seen.add(leg["name"])
        rel = leg["rel"]
        if rel is None:
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            bad.append("文件不在：%s（腿 %s）" % (rel, leg["name"]))
            continue
        if rel not in texts:
            with open(path, encoding="utf-8") as fh:
                texts[rel] = fh.read()
        text = texts[rel]
        n = text.count(leg["anchor"])
        if n < leg["occ"] + 1:
            bad.append("腿 %s 锚点计数 %d < 需要的第 %d 处：%s"
                       % (leg["name"], n, leg["occ"], leg["anchor"][:44]))
        if leg["name"] == "L3-hotreload-site-loses-injection" and n != 2:
            bad.append("构造点锚点应为 2 处（__init__ 与 reload_config），实得 %d" % n)
        if leg["check_target"]:
            target = leg["repl"]
            if target and target in text:
                bad.append("腿 %s 的替换目标已在树里，判红读数不可信：%s"
                           % (leg["name"], target[:44]))
        try:
            ast.parse(_patch(text, leg["anchor"], leg["repl"], leg["occ"]), filename=rel)
        except (SyntaxError, ValueError) as exc:
            bad.append("腿 %s 的补丁不合法/不可施加：%s" % (leg["name"], exc))
    if not os.path.isfile(os.path.join(root, TEST_FILE)):
        bad.append("测试文件不在：%s" % TEST_FILE)
    print("VERIFY_LEGS=%d VERIFY_BAD=%d" % (len(LEGS), len(bad)))
    for row in bad:
        print("  BAD: " + row)
    return 1 if bad else 0


def _copy_tree(root, dst):
    """一次性副本树：只带 src/ 与 tests/（用例自己按 __file__/../src 取源码）。"""
    for sub in ("src", "tests"):
        shutil.copytree(os.path.join(root, sub), os.path.join(dst, sub),
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst


def _pytest(dst):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [os.path.join(dst, "src"), os.path.join(dst, "tests"), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run([sys.executable, "-m", "pytest", TEST_FILE, "-q", "-p", "no:cacheprovider"],
                          cwd=dst, env=env, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = len(re.findall(r"^FAILED ", out, flags=re.M))
    names = re.findall(r"^FAILED (\S+)", out, flags=re.M)
    match = re.search(r"(\d+) passed.*|(\d+) failed.*", out.strip()[-260:] if out.strip() else "")
    return proc.returncode, failed, (match.group(0) if match else "?"), names


def main(root):
    bad = 0
    touched = (SERVICE_PY, API_PY)
    before = {rel: _sha(os.path.join(root, rel)) for rel in touched}
    print("%-36s %-4s %s" % ("腿", "RC", "读数"))
    for leg in LEGS:
        dst = tempfile.mkdtemp(prefix="ma_c79_leg_")
        try:
            _copy_tree(root, dst)
            rel = leg["rel"]
            if rel is not None:
                path = os.path.join(dst, rel)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                try:
                    patched = _patch(text, leg["anchor"], leg["repl"], leg["occ"])
                except ValueError as exc:
                    bad += 1
                    print("%-36s %-4s 该响 FAILED=?   <<< 锚点不可施加（%s），这条腿无效"
                          % (leg["name"], "-", exc))
                    continue
                try:
                    ast.parse(patched, filename=path)
                except SyntaxError as exc:
                    bad += 1
                    print("%-36s %-4s 该响 FAILED=?   <<< 变异副本语法不合法（%s），这条腿无效"
                          % (leg["name"], "-", exc.msg))
                    continue
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(patched)
            rc, failed, summary, names = _pytest(dst)
            ok = (rc == 0 and failed == 0) if rel is None else (failed > 0)
            if not ok:
                bad += 1
            print("%-36s %-4s %s failed=%d %s%s" % (
                leg["name"], rc, "对照" if rel is None else "该响", failed, summary,
                "" if ok else "   <<< 这条腿没响，锁无效"))
            if names:
                print("     红在=%s" % ", ".join(sorted(
                    n.split("::")[-1] for n in names)))
            if not ok:
                print("     说明=%s" % leg["desc"])
        finally:
            shutil.rmtree(dst, ignore_errors=True)
    print("MUT79_COUNT=%d MUTATION_BAD=%d" % (len(LEGS), bad))
    restored = all(before[rel] == _sha(os.path.join(root, rel))
                   and os.path.isfile(os.path.join(root, rel)) for rel in touched)
    print("SRC_UNCHANGED=%s %s" % (
        restored, " ".join("%s_SHA=%s" % (os.path.basename(rel)[:6],
                                          _sha(os.path.join(root, rel))[:12])
                           for rel in touched)))
    return 1 if bad or not restored else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--verify"]
    tree = os.path.abspath(args[0] if args else ROOT)
    if "--verify" in sys.argv[1:]:
        sys.exit(verify(tree))
    sys.exit(main(tree))
