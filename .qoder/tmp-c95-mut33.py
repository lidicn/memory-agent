"""run33 变异自证（副本树）：DCD 20261008 三件落码 + 判例 2 门的 28 条"能判红"证据。

任务表 #88 的三件裁定共同形状是**门面承诺 ↔ 代码实际**必须一致，每件都写成默认值/返回形状，
所以最容易以"看起来改了、其实没锁住"的方式滑回去。每条腿把那一句承诺退回坏的那侧，
必须让 `tests/test_vma_dcd_20261008_rulings.py`（27 条）或 ACP 属主隔离用例当场判红：

    MA-29 乙（未实现的动作桩不得声称成功）
      R01..R05 五支各自回 `ok=True`（G1 门 + 直接断言双重杀）
      R06 丢 not_implemented / R07 丢 dry_run / R08 dispatched=True
      R09 历史行不再带 dry_run=1 / R10 连历史都不写
      R11 docstring 去掉 "MA-29" / R12 docstring 去掉五支名字
    MA-32 甲（那个开关不存在）
      R13 Config 字段复活 / R14 WRITABLE_FIELDS 复活 / R15 设置页卡片复活
    MA-34 甲（默认只算不落）
      R16..R18 三个挖掘入口默认翻回 True
      R19..R21 behavior_routes 三处 body.get 默认翻回 True
      R22..R24 日任务三处显式 persist=True 撤掉（撤掉=静默回落成不落库）
    判例 2（入口对等性门）
      R25 G1 豁免表复活（豁免必须随裁定死）
      R26 摘掉 M_CANCEL 的 check_owner（既有用例 + 门都要红）
      R27 门的读数根退回 `ast.walk(st)` ⇒ 兄弟支的闸被算成自己的（假绿，本轮实测抓出的缺陷）
      R28 门不再沿 `elif` 递归 ⇒ 分支静默消失（假绿）

跑法与 run32 一致：MUT_ROOT 指快照树/工作树，注入只发生在一次性副本树 MUT_DST；
锚点命中 != 1 ⇒ INVALID；注入后先 `ast.parse`（响过 ≠ 响对）；Q-0 对照腿必须绿；
跑完复核工作树哈希并出 `WT_UNTOUCHED`。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or "/tmp/c95snap33"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or "/tmp/c95mut33"
OUT = os.environ.get("MUT_OUT") or "/tmp/c95mut33.out"

M = os.path.join("src", "memory_agent")
FILES = {
    "re": os.path.join(M, "rule_engine.py"),
    "ai": os.path.join(M, "activity_inference.py"),
    "cfg": os.path.join(M, "config.py"),
    "croutes": os.path.join(M, "api", "config_routes.py"),
    "broutes": os.path.join(M, "api", "behavior_routes.py"),
    "rt": os.path.join(M, "runtime.py"),
    "js": os.path.join(M, "static", "js", "pages", "settings.js"),
    "acp": os.path.join(M, "acp_server.py"),
    "g1": os.path.join("scripts", "scan_stub_claims_success.py"),
    "g5": os.path.join("scripts", "scan_session_owner_parity.py"),
}
TESTS = [
    os.path.join("tests", "test_vma_dcd_20261008_rulings.py"),
    os.path.join("tests", "test_acp_session_cross_owner_denied.py"),
    os.path.join("tests", "test_acp_round20_owner_isolation.py"),
    os.path.join("tests", "test_vma_r3_rule_channel.py"),
    os.path.join("tests", "test_device_event_feed.py"),
]

# ── 锚点片段（都带上下文：五支的 return 首行逐字相同，只有第二行能区分）──────
ALERT_RET = (
    '        return {"ok": False, "not_implemented": True, "dry_run": True,\n'
    '                "dispatched": False, "channel": channel, "message": message}\n')
WEBHOOK_RET = (
    '        return {"ok": False, "not_implemented": True, "dry_run": True,\n'
    '                "dispatched": False, "url": url}\n')
TTS_RET = (
    '        return {"ok": False, "not_implemented": True, "dry_run": True,\n'
    '                "dispatched": False, "room": room, "text": text}\n')
LIGHT_RET = (
    '        return {"ok": False, "not_implemented": True, "dry_run": True,\n'
    '                "dispatched": False, "device": device, "cmd": cmd}\n')
CAMERA_RET = (
    '        return {"ok": False, "not_implemented": True, "dry_run": True,\n'
    '                "dispatched": False, "device": device, "action": cam_action}\n')
LIGHT_CALL = (
    '        logger.info(f"[RuleLight] 未实现，仅记录: {device}: {cmd}")\n'
    '        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},\n'
    '                          dry_run=True)\n')
CAMERA_CALL = (
    '        logger.info(f"[RuleCamera] 未实现，仅记录: {device}: {cam_action}")\n'
    '        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},\n'
    '                          dry_run=True)\n')
DOC_HEAD = (
    '        MA-29 乙（DCD 20261008）：``alert`` / ``webhook`` / ``tts`` / ``light`` / ``camera``\n'
    '        五支动作**未实现**，返回 ``ok=False`` + ``not_implemented=True`` + ``dry_run=True``\n')
DOC_NO_MARK = (
    '        未实现动作（DCD 20261008）：``alert`` / ``webhook`` / ``tts`` / ``light`` / ``camera``\n'
    '        五支动作**未实现**，返回 ``ok=False`` + ``not_implemented=True`` + ``dry_run=True``\n')
DOC_NO_TYPES = (
    '        MA-29 乙（DCD 20261008）：若干动作**未实现**，返回 ``ok=False`` + ``not_implemented=True``\n'
    '        + ``dry_run=True``\n')
CFG_FIELD = (
    '    member_tag_agent_writeback: bool = False          '
    '# 默认关；开启后 confirm_member_tag 才被放行\n')
CRITES_TUPLE = '    "member_tag_agent_writeback",\n'
JS_ANCHOR = '    <!-- 系统 / 在线更新 -->\n'
JS_READD = (
    '    <!-- 家庭成员与行为画像 -->\n'
    '    <div class="card p-5">\n'
    '      <h3 class="font-semibold text-sm">家庭成员与行为画像</h3>\n'
    '      <label><div class="switch" :class="cfg.auto_discover_persona && \'on\'"></div></label>\n'
    '    </div>\n\n')
AUDIT_DEF = '                          persist: bool = False, min_near_miss: int = 2,\n'
PROCESS_DEF = '                     persist: bool = False, emit_rules: bool = True,\n'
DRIFT_DEF = '                   persist: bool = False, max_rows: int = 200000) -> dict:\n'
BR_PROCESS = ('        persist=bool(body.get("persist", False)),\n'
              '        emit_rules=bool(body.get("emit_rules", True)),\n')
BR_DRIFT = ('        bucket_sec=bsec, window_size=wsize, min_score=mscore,\n'
            '        persist=bool(body.get("persist", False)),\n')
BR_AUDIT = ('        persist=bool(body.get("persist", False)),\n'
            '        min_near_miss=mnm,\n')
RT_PROCESS = ('                                    int(getattr(self.config, "process_mining_days", 7) or 7),\n'
              '                                    persist=True,\n')
RT_DRIFT = ('                                    int(getattr(self.config, "drift_days", 14) or 14),\n'
            '                                    persist=True,\n')
RT_AUDIT = ('                                    None,\n'
            '                                    persist=True,\n')
G1_EXEMPT = 'EXEMPT = {}\n'
CANCEL_BLOCK = (
    '    if method == M_CANCEL:\n'
    '        sid = params.get("sessionId")\n'
    '        # P0-9 owner check 必须在 run_id 查询之前：避免 B 通过不同错误码探测\n'
    '        # A 的会话是否有 in-flight run（跨 principal 运行态 oracle）。\n'
    '        if not _STORE.check_owner(sid, _owner):\n'
    '            return make_error(req_id, ERR_INVALID_PARAMS, "无权取消该会话（属主不匹配）"), None\n'
    '        meta = _STORE.get(sid) if sid else None\n')
CANCEL_NOGUARD = (
    '    if method == M_CANCEL:\n'
    '        sid = params.get("sessionId")\n'
    '        meta = _STORE.get(sid) if sid else None\n')
G5_WALK = ('                                "reads": _reads_sid(st.body), "guard": _guard_kind(st.body)})\n')
G5_WALK_BAD = ('                                "reads": _reads_sid(st), "guard": _guard_kind(st)})\n')
G5_ELIF = ('                _collect(st.orelse, out, seen)\n'
           '                continue\n')
G5_ELIF_BAD = ('                continue\n')


ALERT_OK = ('        return {"ok": True, "dispatched": True,\n'
            '                "channel": channel, "message": message}\n')
WEBHOOK_OK = '        return {"ok": True, "dispatched": True, "url": url}\n'
TTS_OK = ('        return {"ok": True, "dispatched": True,\n'
          '                "room": room, "text": text}\n')
LIGHT_OK = ('        return {"ok": True, "dispatched": True,\n'
            '                "device": device, "cmd": cmd}\n')
CAMERA_OK = ('        return {"ok": True, "dispatched": True,\n'
             '                "device": device, "action": cam_action}\n')
LIGHT_CALL_NODRY = (
    '        logger.info(f"[RuleLight] 未实现，仅记录: {device}: {cmd}")\n'
    '        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False})\n')
CAMERA_LOG_ONLY = \
    '        logger.info(f"[RuleCamera] 未实现，仅记录: {device}: {cam_action}")\n'
ALERT_RET_NOIMPL = (
    '        return {"ok": False, "dry_run": True, "dispatched": False,\n'
    '                "channel": channel, "message": message}\n')
WEBHOOK_RET_NODRY = \
    '        return {"ok": False, "not_implemented": True, "dispatched": False, "url": url}\n'
TTS_RET_DISPATCHED = (
    '        return {"ok": False, "not_implemented": True, "dry_run": True,\n'
    '                "dispatched": True, "room": room, "text": text}\n')
CFG_READD = CFG_FIELD + \
    '    auto_discover_persona: bool = False               # 复活那个不存在的开关\n'
CRITES_READD = CRITES_TUPLE + '    "auto_discover_persona",\n'
AUDIT_DEF_TRUE = AUDIT_DEF.replace("persist: bool = False", "persist: bool = True")
PROCESS_DEF_TRUE = PROCESS_DEF.replace("persist: bool = False", "persist: bool = True")
DRIFT_DEF_TRUE = DRIFT_DEF.replace("persist: bool = False", "persist: bool = True")
BR_PROCESS_TRUE = BR_PROCESS.replace('body.get("persist", False)', 'body.get("persist", True)')
BR_DRIFT_TRUE = BR_DRIFT.replace('body.get("persist", False)', 'body.get("persist", True)')
BR_AUDIT_TRUE = BR_AUDIT.replace('body.get("persist", False)', 'body.get("persist", True)')
RT_PROCESS_ONLY_DAYS = \
    '                                    int(getattr(self.config, "process_mining_days", 7) or 7),\n'
RT_DRIFT_ONLY_DAYS = \
    '                                    int(getattr(self.config, "drift_days", 14) or 14),\n'
RT_AUDIT_ONLY_NONE = '                                    None,\n'
G1_EXEMPT_READD = 'EXEMPT = {("rule_engine.py", "_action_alert"): "MA-29 待裁"}\n'

LEGS = [
    ("R01", "re", ALERT_RET, ALERT_OK,
     "alert 桩回到「未实现却报成功」——正是 MA-29 要消灭的形状"),
    ("R02", "re", WEBHOOK_RET, WEBHOOK_OK,
     "webhook 报成功 ⇒ 调用方以为钩子已经打出去了"),
    ("R03", "re", TTS_RET, TTS_OK,
     "tts 报成功 ⇒ 家里没响过却记成已播报"),
    ("R04", "re", LIGHT_RET, LIGHT_OK,
     "light 报成功 ⇒ 灯没动却记成已派发"),
    ("R05", "re", CAMERA_RET, CAMERA_OK,
     "camera 报成功 ⇒ 没抓拍却记成已执行"),
    ("R06", "re", ALERT_RET, ALERT_RET_NOIMPL,
     "丢掉 not_implemented ⇒ 消费方分不清「失败」与「根本没人实现」"),
    ("R07", "re", WEBHOOK_RET, WEBHOOK_RET_NODRY,
     "丢掉 dry_run ⇒ device feed 的 logged_only 判据失效，未派发被记进派发通道"),
    ("R08", "re", TTS_RET, TTS_RET_DISPATCHED,
     "dispatched=True 与「不派发任何副作用」直接矛盾"),
    ("R09", "re", LIGHT_CALL, LIGHT_CALL_NODRY,
     "触发历史不带 dry_run=1 ⇒ 观察期误报计数把未派发送进「真触发」那一栏"),
    ("R10", "re", CAMERA_CALL, CAMERA_LOG_ONLY,
     "连触发历史都不写 ⇒ 观察期天数/误报率没有样本，规则永远熬不出试运行"),
    ("R11", "re", DOC_HEAD, DOC_NO_MARK,
     "门面文案不再点名 MA-29 ⇒ 审计回来核销时无从对照（断言要求 docstring 带裁定号）"),
    ("R12", "re", DOC_HEAD, DOC_NO_TYPES,
     "docstring 不列出五支 ⇒ 「哪几支未实现」又变回要靠读代码才知道的口口相传"),
    ("R13", "cfg", CFG_FIELD, CFG_READD,
     "MA-32：字段回来 = 加载器会接受一个没人实现的开关"),
    ("R14", "croutes", CRITES_TUPLE, CRITES_READD,
     "MA-32：写侧白名单回来 = 面板能把值写进一个不存在的开关"),
    ("R15", "js", JS_ANCHOR, JS_READD + JS_ANCHOR,
     "MA-32：卡片回来 = 空壳面板（开关后面没有任何代码路径）"),
    ("R16", "ai", AUDIT_DEF, AUDIT_DEF_TRUE,
     "audit_rule_recall 默认偷偷写库 ⇒ 「只算不落」的承诺失效"),
    ("R17", "ai", PROCESS_DEF, PROCESS_DEF_TRUE,
     "mine_process 默认写库 ⇒ 每次手工试算都在改生产数据"),
    ("R18", "ai", DRIFT_DEF, DRIFT_DEF_TRUE,
     "mine_drift 默认写库 ⇒ 漂移点按调用次数堆积"),
    ("R19", "broutes", BR_PROCESS, BR_PROCESS_TRUE,
     "HTTP 面 process 入口不传 persist 就落库 ⇒ 与门面文档相反"),
    ("R20", "broutes", BR_DRIFT, BR_DRIFT_TRUE,
     "HTTP 面 drift 入口回落 True ⇒ 「默认写」是隐性副作用"),
    ("R21", "broutes", BR_AUDIT, BR_AUDIT_TRUE,
     "HTTP 面 recall 入口回落 True ⇒ 放宽建议静默入库"),
    ("R22", "rt", RT_PROCESS, RT_PROCESS_ONLY_DAYS,
     "日任务撤掉显式 persist=True ⇒ 默认 False 接管，过程挖掘从此不落库"),
    ("R23", "rt", RT_DRIFT, RT_DRIFT_ONLY_DAYS,
     "日任务撤掉漂移落库 ⇒ 漂移历史断供，面板只看当下"),
    ("R24", "rt", RT_AUDIT, RT_AUDIT_ONLY_NONE,
     "日任务撤掉召回落库 ⇒ 补召回建议永远停在内存里"),
    ("R25", "g1", G1_EXEMPT, G1_EXEMPT_READD,
     "裁定已落地却留着豁免 ⇒ 豁免表变成永久免检通道（基线只准减）"),
    ("R26", "acp", CANCEL_BLOCK, CANCEL_NOGUARD,
     "M_CANCEL 的属主闸摘掉 ⇒ B 能取消 A 的在飞任务，还能用错误码探测运行态"),
    ("R27", "g5", G5_WALK, G5_WALK_BAD,
     "门的读数根退回整棵 If ⇒ elif 兄弟支的闸被算成自己的（假绿，本轮实测抓出）"),
    ("R28", "g5", G5_ELIF, G5_ELIF_BAD,
     "门不再沿 elif 递归 ⇒ 分支静默消失，读数面上「没有需要闸的入口」（假绿）"),
]


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def to_eol(text, eol):
    return text.replace("\n", eol) if eol != "\n" else text


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_copy(log):
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    os.makedirs(os.path.join(DST, "tests"))
    shutil.copy2(os.path.join(WT, "pytest.ini"), os.path.join(DST, "pytest.ini"))
    for sub in ("src", "scripts"):
        shutil.copytree(os.path.join(WT, sub), os.path.join(DST, sub),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in TESTS:
        shutil.copy2(os.path.join(WT, rel), os.path.join(DST, rel))
    log(f"COPY_TREE_READY={DST} src_files={len(os.listdir(os.path.join(DST, M)))}")


def run_pytest():
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-rfEs", "--tb=no", "-p", "no:cacheprovider"],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800)
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = sorted({ln.split(" ")[1].split("::")[-1].split(" - ")[0]
                     for ln in out.splitlines() if ln.startswith("FAILED ")})
    tail = [ln.strip() for ln in out.splitlines()
            if any(w in ln for w in ("passed", "failed", "error")) and any(c.isdigit() for c in ln)]
    skipped = [ln for ln in out.splitlines() if ln.startswith("SKIPPED")]
    return proc.returncode, failed, (tail[-1] if tail else "NO SUMMARY LINE"), skipped


def main():
    lines = []

    def log(msg):
        lines.append(str(msg))
        with open(OUT, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines) + "\n")

    if os.environ.get("MUT_ANCHOR_ONLY") == "1":
        # 预检格：锚点命中数 + 注入后 ast.parse，不建副本、不跑用例。
        texts = {tag: read(os.path.join(WT, rel)) for tag, rel in FILES.items()}
        bad = 0
        for leg, tag, anchor, new, defect in LEGS:
            text = texts[tag]
            eol = "\r\n" if "\r\n" in text else "\n"
            n = text.count(to_eol(anchor, eol))
            msg = ""
            ok_parse = True
            if n == 1 and FILES[tag].endswith(".py"):
                try:
                    ast.parse(text.replace(to_eol(anchor, eol), to_eol(new, eol), 1))
                except SyntaxError as exc:
                    ok_parse = False
                    msg = " 语法不过(%s @ line %s)" % (exc.msg, exc.lineno)
            note = "" if n == 1 else " 命中 %d 次" % n
            status = "OK" if n == 1 and ok_parse else "BAD"
            if status == "BAD":
                bad += 1
            log(f"[{leg}] {status} {FILES[tag]}{msg}{note}")
        log("ANCHOR_OK=%d ANCHOR_BAD=%d legs=%d" % (len(LEGS) - bad, bad, len(LEGS)))
        return 0 if bad == 0 else 2

    wt_before = {rel: sha(read(os.path.join(WT, rel))) for rel in FILES.values()}
    build_copy(log)
    pristine = {tag: read(os.path.join(DST, rel)) for tag, rel in FILES.items()}

    rc, failed, summary, skipped = run_pytest()
    log(f"[Q-0] 对照腿（什么都不改）rc={rc} summary={summary!r} failed={failed} skipped={len(skipped)}")
    for ln in skipped:
        log(f"[Q-0] SKIPPED {ln}")
    if rc != 0 or failed:
        log("Q-0 不绿：副本树基线本身有问题，本轮所有读数作废，停在这里。")
        return 1
    log("Q-0 绿：副本树与快照树同口径，后续红都是注入造成的。")

    killed = survived = invalid = 0
    for leg, tag, anchor, new, defect in LEGS:
        rel = FILES[tag]
        path = os.path.join(DST, rel)
        text = pristine[tag]
        eol = "\r\n" if "\r\n" in text else "\n"
        a = to_eol(anchor, eol)
        n = text.count(a)
        if n != 1:
            invalid += 1
            log(f"[{leg}] INVALID 锚点命中 {n} 次（要求恰好 1 次），未注入：{defect}")
            continue
        mutated = text.replace(a, to_eol(new, eol), 1)
        # `ast.parse` 只认 Python：R15 的靶是 settings.js，按 JS 语法它本来就没法用 3.11 校验，
        # 强行解析只会把一条合法注入读成 INVALID（预检格同一口径：只有 .py 才 parse）。
        if rel.endswith(".py"):
            try:
                ast.parse(mutated)
            except SyntaxError as exc:
                invalid += 1
                log(f"[{leg}] INVALID 注入后语法不过（{exc.msg} @ line {exc.lineno}），未跑：{defect}")
                continue
        write(path, mutated)
        try:
            rc, failed, summary, skipped = run_pytest()
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
