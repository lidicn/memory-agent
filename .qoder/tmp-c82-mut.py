"""#82 变异自证 harness（DCD 20261007 §四 Q1 裁 B：两条路并存、按优先级）。

跑在副本树 `.qoder/tmp-c82-mut/`，不碰工作树。裁 B 的形状里有五个"改一下就错、
但跑起来一声不响"的地方，这一档逐条造反例：

- 就绪判定（HA 那条要两键齐全才算会发声）；
- 回落门槛（"两条都不通"不是"任一条不通"）；
- 路由方向与并行/优先级（优先级错成并行 = 同一次事件两处说话）；
- `trace_id`（契约 §七 E 必填、桥拒空串）；
- 返回值的诚实性（没发声就报 False，报成 True 是把配置错误洗成一切正常）。

M-0 是对照腿（什么都不改），它必须绿；每条腿注入前先 `ast.parse`，跑完还原并复核哈希。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or r"E:\NAS\memory-agent"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or os.path.join(WT, ".qoder", "tmp-c82-mut")
OUT = os.environ.get("MUT_OUT") or os.path.join(
    WT, ".qoder", "tmp-c82-mut%s.out" % (sys.argv[2] if len(sys.argv) > 2 else "82"))

FILES = {
    "ann": os.path.join("src", "memory_agent", "announcer.py"),
    "rt": os.path.join("src", "memory_agent", "runtime.py"),
}
TESTS = ["tests" + os.sep + t for t in (
    "test_announcer.py",
    "test_vma_dcd_20261006_linkage_codes.py",
    "test_perception_ingest.py",
)]

LEGS = [
    ("N01", "ann",
     "        self.enabled = self.switch and bool(self.tts_entity) and bool(self.target)\n",
     "        self.enabled = self.switch and bool(self.tts_entity)\n",
     "HA 就绪判定丢掉播放设备：只配实体也算会发声（实际只合成不出声，回落路还被挤掉）"),
    ("N02", "ann",
     "        if not self.enabled and not self.inbox_ready:\n",
     "        if not self.enabled or not self.inbox_ready:\n",
     "「两条都不通」写成「任一条不通」：HA 没配起来时连回落都不走"),
    ("N03", "ann",
     "        return self._via_inbox(msg) if not self.enabled else self._via_ha(msg)\n",
     "        return self._via_ha(msg) if not self.enabled else self._via_inbox(msg)\n",
     "路由倒装：没配 HA 的两键时反而往 HA 打，配齐了却去投收件箱"),
    ("N04", "ann",
     "        return self._via_inbox(msg) if not self.enabled else self._via_ha(msg)\n",
     "        if self.enabled:\n            self._via_ha(msg)\n        return self._via_inbox(msg)\n",
     "优先级翻成并行：HA 就绪时另投一份，同一次事件两处说话"),
    ("N05", "ann",
     "        tid = uuid.uuid4().hex\n",
     '        tid = ""\n',
     "trace_id 给空串：契约 §七 E 必填非空，桥会当场拒发"),
    ("N06", "ann",
     "            ok = bool(self.mqtt.publish_speak(msg, trace_id=tid))\n",
     "            ok = bool(self.mqtt.publish_speak(msg))\n",
     "少交契约必填键（`trace_id` 是 keyword-only，漏传当场 TypeError）"),
    ("N07", "ann",
     "        self.switch = bool(enabled)\n",
     "        self.switch = True\n",
     "总开关硬置开：`announce_enabled=False` 也照样往外发声"),
    ("N08", "ann",
     "        return bool(self.switch and self.mqtt is not None\n"
     "                    and getattr(self.mqtt, \"enabled\", False))\n",
     "        return bool(self.switch and self.mqtt is not None)\n",
     "回落路不看桥的 enabled：桥停着也往里投，投了没人收"),
    ("N09", "ann",
     "        if not self.switch:\n            return False\n",
     "        if not self.enabled:\n            return False\n",
     "总闸那一行改读 HA 就绪：HA 没配起来时连回落都被当场拦掉。"
     "（原腿 `if False:` 是**等价变异**——enabled/inbox_ready 两个因子都已含 switch，"
     "删掉这行行为不变，任何用例都不该响；换成这个不等价读法才有判据）"),
    ("N10", "ann",
     "        else:\n"
     "            logger.warning(\"announcer: 播报未成功: %s\", res)\n"
     "        return ok\n",
     "        else:\n"
     "            logger.warning(\"announcer: 播报未成功: %s\", res)\n"
     "        return True\n",
     "HA 回了 ok=False 也报成播报成功"),
    ("N11", "ann",
     "            logger.warning(\"announcer: 收件箱播报未成功，trace_id=%s\", tid)\n"
     "        return ok\n",
     "            logger.warning(\"announcer: 收件箱播报未成功，trace_id=%s\", tid)\n"
     "        return True\n",
     "桥按契约码拒发也报成 True"),
    ("N12", "ann",
     "        ok = bool(res.get(\"ok\")) if isinstance(res, dict) else False\n",
     "        ok = bool(res.get(\"ok\"))\n",
     "不判 HA 回包形状：非 dict 时 AttributeError 抛穿采集主链路"),
    ("N13", "ann",
     "        if not self.switch:\n            return False\n",
     "        if not self.switch:\n            return False\n        self._last.clear()\n",
     "冷却表每次被清空：同一类事件刷屏（冷却只活一次调用）"),
    ("N14", "rt",
     "            mqtt=self.mqtt,\n",
     "",
     "运行面构造 Announcer 时不投桥：回落路在 production 永远不在场"),
]

# 只补跑指定腿：加用例只会让红更红，不会让已判红的腿转绿，所以补跑针对上一轮存活的
# 那几条即可；M-0 每轮都重跑，用它确认新用例在 pristine 树上是绿的。
if len(sys.argv) > 1 and sys.argv[1] != "all":
    wanted = {x.strip() for x in sys.argv[1].split(",") if x.strip()}
    LEGS = [leg for leg in LEGS if leg[0] in wanted]


def read(p):
    with open(p, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write(p, text):
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_copy(log):
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(os.path.join(DST, "tests"))
    shutil.copy2(os.path.join(WT, "pytest.ini"), os.path.join(DST, "pytest.ini"))
    shutil.copytree(os.path.join(WT, "src"), os.path.join(DST, "src"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in TESTS:
        shutil.copy2(os.path.join(WT, rel), os.path.join(DST, rel))
    log(f"copy tree ready: {DST}")


def run_pytest():
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-rf", "--tb=no", "-p", "no:cacheprovider"],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = sorted({ln.split(" ")[1].split("::")[-1]
                     for ln in out.splitlines() if ln.startswith("FAILED ")})
    tail = [ln.strip() for ln in out.splitlines()
            if any(w in ln for w in ("passed", "failed", "error")) and any(c.isdigit() for c in ln)]
    return proc.returncode, failed, (tail[-1] if tail else "NO SUMMARY LINE")


def main():
    lines = []

    def log(msg):
        lines.append(str(msg))
        with open(OUT, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines) + "\n")

    wt_before = {rel: sha(read(os.path.join(WT, rel))) for rel in FILES.values()}
    build_copy(log)
    pristine = {tag: read(os.path.join(DST, rel)) for tag, rel in FILES.items()}

    rc, failed, summary = run_pytest()
    log(f"[M-0] 对照腿（什么都不改）rc={rc} summary={summary!r} failed={failed}")
    if rc != 0 or failed:
        log("M-0 不绿：副本树基线本身有问题，本轮所有读数作废，停在这里。")
        return 1
    log("M-0 绿：副本树与工作树同口径，后续红都是注入造成的。")

    killed = survived = invalid = 0
    for leg, tag, anchor, new, defect in LEGS:
        rel = FILES[tag]
        path = os.path.join(DST, rel)
        text = pristine[tag]
        n = text.count(anchor)
        if n != 1:
            invalid += 1
            log(f"[{leg}] INVALID 锚点命中 {n} 次（要求恰好 1 次），未注入：{defect}")
            continue
        mutated = text.replace(anchor, new, 1)
        try:
            ast.parse(mutated)
        except SyntaxError as exc:
            invalid += 1
            log(f"[{leg}] INVALID 注入后语法不过（{exc.msg} @ line {exc.lineno}），未跑：{defect}")
            continue
        write(path, mutated)
        try:
            rc, failed, summary = run_pytest()
        finally:
            write(path, pristine[tag])
            if sha(read(path)) != sha(pristine[tag]):
                log(f"[{leg}] !! 还原失败：{rel} 哈希不符")
        if failed:
            killed += 1
            log(f"[{leg}] KILLED {rel} :: {defect}\n        {summary} :: 红用例 {len(failed)} 条: "
                + ", ".join(failed[:6]) + (" ..." if len(failed) > 6 else ""))
        elif rc != 0:
            killed += 1
            log(f"[{leg}] KILLED(非零退出，无用名) {rel} :: {defect} :: {summary}")
        else:
            survived += 1
            log(f"[{leg}] SURVIVED !! {rel} :: {defect} :: {summary}")

    wt_after = {rel: sha(read(os.path.join(WT, rel))) for rel in FILES.values()}
    log(f"WT_UNTOUCHED={wt_before == wt_after}")
    log(f"totals: killed={killed} survived={survived} invalid={invalid} legs={len(LEGS)}")
    log(f"python: {sys.version.split()[0]} harness-exe={PY}")
    return 0 if survived == 0 and invalid == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
