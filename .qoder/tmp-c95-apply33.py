"""run33 落码：DCD 20261008 三件（MA-29 乙 / MA-32 甲 / MA-34 甲）。

用法：check = 只逐条验锚点；write = 全对才落盘。
每条锚点要求在文件里**恰好命中 1 次**；命中 0 次或 >1 次 ⇒ 整批不落盘（不落半成品）。
行尾按各文件自身 EOL 归一（activity_inference.py / settings.js / 两份 tests 是 CRLF），
否则 diff 会变成整份重写。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rd(rel):
    with io.open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8", newline="") as fh:
        return fh.read()


def eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def fix_eol(s, eol):
    s = s.replace("\r\n", "\n")
    return s.replace("\n", eol) if eol == "\r\n" else s


# ── MA-29 乙：五支动作桩如实降级 ─────────────────────────────────────────
STUBS = [
    ("""    def _action_alert(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"推送告警动作。\"\"\"
        message = action.get("message", f"规则触发: {rule['name']}")
        channel = action.get("channel", "bark")
        # TODO: 调用 AlertDispatcher 或直接推送
        logger.info(f"[RuleAlert] {channel}: {message} (规则: {rule['name']}, 事件: {event.get('kind')})")
        # 记录触发历史
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "channel": channel, "message": message}""",
     """    def _action_alert(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"推送告警动作：**未实现**（DCD 20261008 MA-29 乙）——只记历史，不派发、不报成功。\"\"\"
        message = action.get("message", f"规则触发: {rule['name']}")
        channel = action.get("channel", "bark")
        logger.info(f"[RuleAlert] 未实现，仅记录: {channel}: {message} "
                    f"(规则: {rule['name']}, 事件: {event.get('kind')})")
        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},
                          dry_run=True)
        return {"ok": False, "not_implemented": True, "dry_run": True,
                "dispatched": False, "channel": channel, "message": message}"""),

    ("""    def _action_webhook(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"调用 webhook 动作。\"\"\"
        url = action.get("url")
        if not url:
            return {"ok": False, "error": "webhook url 未配置"}
        # TODO: 实际调用 webhook
        logger.info(f"[RuleWebhook] {url}: {rule['name']}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "url": url}""",
     """    def _action_webhook(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"调用 webhook 动作：**未实现**（MA-29 乙）——URL 缺失照旧报错，有 URL 也只记历史。\"\"\"
        url = action.get("url")
        if not url:
            return {"ok": False, "error": "webhook url 未配置"}
        logger.info(f"[RuleWebhook] 未实现，仅记录: {url}: {rule['name']}")
        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},
                          dry_run=True)
        return {"ok": False, "not_implemented": True, "dry_run": True,
                "dispatched": False, "url": url}"""),

    ("""    def _action_tts(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"语音播报动作。\"\"\"
        text = action.get("text", f"规则触发: {rule['name']}")
        room = action.get("room", event.get("room", ""))
        # TODO: 实际调用 TTS
        logger.info(f"[RuleTTS] {room}: {text}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "room": room, "text": text}""",
     """    def _action_tts(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"语音播报动作：**未实现**（MA-29 乙）——播报不会真的发生。\"\"\"
        text = action.get("text", f"规则触发: {rule['name']}")
        room = action.get("room", event.get("room", ""))
        logger.info(f"[RuleTTS] 未实现，仅记录: {room}: {text}")
        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},
                          dry_run=True)
        return {"ok": False, "not_implemented": True, "dry_run": True,
                "dispatched": False, "room": room, "text": text}"""),

    ("""    def _action_light(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"控制灯光动作。\"\"\"
        device = action.get("device", "livingroom_light")
        cmd = {k: v for k, v in action.items() if k in ("on", "scene", "brightness", "color")}
        # TODO: 实际控制灯光
        logger.info(f"[RuleLight] {device}: {cmd}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "device": device, "cmd": cmd}""",
     """    def _action_light(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"控制灯光动作：**未实现**（MA-29 乙）——灯不会被改动。\"\"\"
        device = action.get("device", "livingroom_light")
        cmd = {k: v for k, v in action.items() if k in ("on", "scene", "brightness", "color")}
        logger.info(f"[RuleLight] 未实现，仅记录: {device}: {cmd}")
        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},
                          dry_run=True)
        return {"ok": False, "not_implemented": True, "dry_run": True,
                "dispatched": False, "device": device, "cmd": cmd}"""),

    ("""    def _action_camera(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"控制摄像头动作。\"\"\"
        device = action.get("device", "livingroom_camera")
        cam_action = action.get("action", "snapshot")
        # TODO: 实际控制摄像头
        logger.info(f"[RuleCamera] {device}: {cam_action}")
        self._log_trigger(rule["rule_id"], event, action)
        return {"ok": True, "device": device, "action": cam_action}""",
     """    def _action_camera(self, rule: dict, event: dict, action: dict) -> dict:
        \"\"\"控制摄像头动作：**未实现**（MA-29 乙）——不会抓拍、不会转动。\"\"\"
        device = action.get("device", "livingroom_camera")
        cam_action = action.get("action", "snapshot")
        logger.info(f"[RuleCamera] 未实现，仅记录: {device}: {cam_action}")
        self._log_trigger(rule["rule_id"], event, {**action, "dispatched": False},
                          dry_run=True)
        return {"ok": False, "not_implemented": True, "dry_run": True,
                "dispatched": False, "device": device, "action": cam_action}"""),
]

# execute_action 的 docstring 要如实交代这五支（否则门面文案又比代码宽）
EXEC_DOC = ("""        ``dry_run_override`` 让调用方在不改规则mode的情况下强制试运行——""",
            """        MA-29 乙（DCD 20261008）：``alert`` / ``webhook`` / ``tts`` / ``light`` / ``camera``
        五支动作**未实现**，返回 ``ok=False`` + ``not_implemented=True`` + ``dry_run=True``
        并只写触发历史（``dry_run=1``）——调用方据 ``dry_run`` 记 ``logged_only``，
        不得把它们当已派发。``log`` 与 ``infer_activity`` 是实做的，照常返回成功。

        ``dry_run_override`` 让调用方在不改规则mode的情况下强制试运行——""")

# G1 豁免表随裁定一起清空（留着就会以 EXEMPT_STALE 反向判红）
G1_EXEMPT = ("""#: 呈 DCD 待裁的桩（登记 = 承认它在，不假装它绿）。裁定落地后**必须**从这张表里删掉，
#: 删不掉就会以 EXEMPT_STALE 反向判红。
EXEMPT = {
    ("rule_engine.py", "_action_alert"): "MA-29 五动作桩体：呈 DCD（20261008 回执 §四 Q1，甲=接真实现 / 乙=如实降级）",
    ("rule_engine.py", "_action_webhook"): "MA-29 同上",
    ("rule_engine.py", "_action_tts"): "MA-29 同上",
    ("rule_engine.py", "_action_light"): "MA-29 同上",
    ("rule_engine.py", "_action_camera"): "MA-29 同上",
}""",
             """#: 呈 DCD 待裁的桩（登记 = 承认它在，不假装它绿）。裁定落地后**必须**从这张表里删掉，
#: 删不掉就会以 EXEMPT_STALE 反向判红。
#: DCD 20261008 MA-29 裁定乙已落码（五支动作桩改为如实降级、不再声称 ok）⇒ 本表清空，
#: 今后再有"声称成功却没做"的桩就是 PROBLEM，不再享有豁免。
EXEMPT = {}""")

# ── MA-32 甲：删掉不存在的开关 ───────────────────────────────────────────
CFG_FIELD = ("""    # ── 家庭成员 / 生活习惯档案 ───────────────────────────────────────────
    auto_discover_persona: bool = False  # 是否主动把发现的标签推送给用户（默认关闭：仅记录、需确认才存档）

""", "")

CFG_ROUTES_KEY = ("""    "auto_discover_persona",
""", "")

CFG_ROUTES_PAYLOAD = ("""            "auto_discover_persona": cfg.auto_discover_persona,
""", "")

SETTINGS_CARD = ("""    <!-- 家庭成员与行为画像 -->
    <div class="card p-5">
      <div class="flex items-center gap-2.5 mb-3">
        <div class="w-8 h-8 rounded-lg grad-brand grid place-items-center text-white">
          <svg viewBox="0 0 24 24" class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>
        </div>
        <h3 class="font-semibold text-sm">家庭成员与行为画像</h3>
      </div>
      <label class="flex items-center gap-2.5 cursor-pointer">
        <div class="switch scale-90" :class="cfg.auto_discover_persona && 'on'" @click="cfg.auto_discover_persona = !cfg.auto_discover_persona"></div>
        <span class="text-xs text-txt-2">主动推送生活习惯发现（关闭则仅记录，需用户确认才存档）</span>
      </label>
      <p class="hint">开启后，AI 助手在对话中发现某成员的行为偏好（如夜猫子🦉）时会主动询问是否写入「生活习惯档案」；无论开关状态，写回前都会先征得确认。</p>
    </div>

""", "")

# ── MA-34 甲：默认不写库，落库要显式 ─────────────────────────────────────
AI_DEFAULTS = [
    ("""                          persist: bool = True, min_near_miss: int = 2,""",
     """                          persist: bool = False, min_near_miss: int = 2,"""),
    ("""                     persist: bool = True, emit_rules: bool = True,""",
     """                     persist: bool = False, emit_rules: bool = True,"""),
    ("""                   persist: bool = True, max_rows: int = 200000) -> dict:""",
     """                   persist: bool = False, max_rows: int = 200000) -> dict:"""),
    ("""        ``persist=True`` 且某缺口出现次数 ≥ ``min_near_miss`` 时，产出**放宽建议**""",
     """        ``persist=True``（**默认 False**，DCD 20261008 MA-34 甲）且某缺口出现次数 ≥ ``min_near_miss`` 时，产出**放宽建议**"""),
    ("""        - ``persist``：异常写 ``behavior_anomalies``（按 case 幂等，保留人工复核 status）""",
     """        - ``persist``：异常写 ``behavior_anomalies``（按 case 幂等，保留人工复核 status）；
          **默认 False**（MA-34 甲）——只算不落，要写库的调用方必须显式传 ``persist=True``"""),
    ("""        :param persist: 漂移点/异常时段写 ``behavior_drifts``（按 (桶,类型) 幂等）""",
     """        :param persist: 漂移点/异常时段写 ``behavior_drifts``（按 (桶,类型) 幂等）；
                        **默认 False**（MA-34 甲），要落库请显式传 ``persist=True``"""),
]

# 三处都要改，但各自带一行上下文以便锚点唯一（裁定只管 persist，emit_rules 默认不动）
BR_DEFAULTS = [
    ("""        persist=bool(body.get("persist", True)),
        emit_rules=bool(body.get("emit_rules", True)),""",
     """        persist=bool(body.get("persist", False)),
        emit_rules=bool(body.get("emit_rules", True)),"""),
    ("""        bucket_sec=bsec, window_size=wsize, min_score=mscore,
        persist=bool(body.get("persist", True)),""",
     """        bucket_sec=bsec, window_size=wsize, min_score=mscore,
        persist=bool(body.get("persist", False)),"""),
    ("""        persist=bool(body.get("persist", True)),
        min_near_miss=mnm,""",
     """        persist=bool(body.get("persist", False)),
        min_near_miss=mnm,"""),
    ("""    ``persist``、``emit_rules``、``min_variant_support``。""",
     """    ``persist``（**默认 False**：不传就不写库）、``emit_rules``、``min_variant_support``。"""),
    ("""    body 可选：``days``（默认 14）、``bucket_sec``、``window_size``、``persist``。""",
     """    body 可选：``days``（默认 14）、``bucket_sec``、``window_size``、
    ``persist``（**默认 False**，MA-34 甲）。"""),
    ("""    body 可选：``days``（默认 14）、``rooms``(list)、``persist``、``min_near_miss``。""",
     """    body 可选：``days``（默认 14）、``rooms``(list)、``persist``（**默认 False**，MA-34 甲）、
    ``min_near_miss``。"""),
]

RUNTIME_CALLS = [
    ("""                                pres = await asyncio.to_thread(
                                    self.activity.mine_process,
                                    None, None,
                                    int(getattr(self.config, "process_mining_days", 7) or 7),
                                )""",
     """                                # MA-34 甲：三个挖掘入口默认只算不落；日任务是要落库的调用方，显式写出来
                                pres = await asyncio.to_thread(
                                    self.activity.mine_process,
                                    None, None,
                                    int(getattr(self.config, "process_mining_days", 7) or 7),
                                    persist=True,
                                )"""),
    ("""                                dres = await asyncio.to_thread(
                                    self.activity.mine_drift, None, None,
                                    int(getattr(self.config, "drift_days", 14) or 14),
                                )""",
     """                                dres = await asyncio.to_thread(
                                    self.activity.mine_drift, None, None,
                                    int(getattr(self.config, "drift_days", 14) or 14),
                                    persist=True,
                                )"""),
    ("""                                    min_near_miss=int(getattr(
                                        self.config, "rule_recall_min_near_miss", 2) or 2),""",
     """                                    persist=True,
                                    min_near_miss=int(getattr(
                                        self.config, "rule_recall_min_near_miss", 2) or 2),"""),
]

TEST_CALLS = [
    ("tests/test_drift.py",
     """    res = svc.mine_drift(start=start, end=end, bucket_sec=3600)""",
     """    res = svc.mine_drift(start=start, end=end, bucket_sec=3600, persist=True)"""),
    ("tests/test_process_mining.py",
     """    res = svc.mine_process(start=start, end=end)""",
     """    res = svc.mine_process(start=start, end=end, persist=True)"""),
]

EDITS = [
    ("src/memory_agent/rule_engine.py", STUBS + [EXEC_DOC]),
    ("scripts/scan_stub_claims_success.py", [G1_EXEMPT]),
    ("src/memory_agent/config.py", [CFG_FIELD]),
    ("src/memory_agent/api/config_routes.py", [CFG_ROUTES_KEY, CFG_ROUTES_PAYLOAD]),
    ("src/memory_agent/static/js/pages/settings.js", [SETTINGS_CARD]),
    ("src/memory_agent/activity_inference.py", AI_DEFAULTS),
    ("src/memory_agent/api/behavior_routes.py", BR_DEFAULTS),
    ("src/memory_agent/runtime.py", RUNTIME_CALLS),
]


def build():
    texts, plan, bad = {}, [], 0
    for rel, pairs in EDITS:
        text = rd(rel)
        eol = eol_of(text)
        for old, new in pairs:
            o, nw = fix_eol(old, eol), fix_eol(new, eol)
            n = text.count(o)
            if n != 1:
                print("ANCHOR_BAD %s 命中%d次 :: %s" % (rel, n, old.strip().split("\n")[0][:72]))
                bad += 1
            text = text.replace(o, nw, 1)
        texts[rel] = text
        plan.append((rel, text, eol))
    for rel, old, new in TEST_CALLS:
        text = rd(rel)
        eol = eol_of(text)
        o, nw = fix_eol(old, eol), fix_eol(new, eol)
        n = text.count(o)
        if n != 1:
            print("ANCHOR_BAD %s 命中%d次 :: %s" % (rel, n, old.strip()[:72]))
            bad += 1
        texts[rel] = text.replace(o, nw, 1)
        plan.append((rel, texts[rel], eol))
    return plan, bad


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    plan, bad = build()
    print("ANCHOR_BAD=%d" % bad)
    if bad:
        return 2
    for rel, text, eol in plan:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        before = rd(rel)
        d_cr_old, d_cr_new = before.count("\r"), text.count("\r")
        if mode == "write":
            with io.open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            raw = open(p, "rb").read()
            print("WROTE %s CR=%d(前%d) LF=%d Δ行=%d"
                  % (rel, raw.count(b"\r"), d_cr_old, raw.count(b"\n"),
                     raw.count(b"\n") - before.count("\n")))
        else:
            print("OK %s eol=%r 行数Δ=%d" % (rel, eol, text.count("\n") - before.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
