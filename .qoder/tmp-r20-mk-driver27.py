# run26 档 -> run27 档：按字面成对替换，任何一条找不到实文本就整体不落盘（先算后写）。
import io
import sys

P = ".qoder/tmp-c82-driver27.sh"
src = io.open(P, encoding="utf-8", newline="").read()

PAIRS = [
    # 快照与档位命名
    ("SNAP=c82snap20261008\nTGZ=.qoder/tmp-c82-snap26.tgz",
     "SNAP=c82snap27\nTGZ=.qoder/tmp-c82-snap27.tgz"),
    ('bash -n .qoder/tmp-c82-remote26.sh; guard REMOTE_SYNTAX_RC 0 $?',
     'bash -n .qoder/tmp-c82-remote27.sh; guard REMOTE_SYNTAX_RC 0 $?'),
    ('"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c82-remote26.sh; guard LINT_RC 0 $?',
     '"$PY" .qoder/tmp-c37-lint-remote13.py .qoder/tmp-c82-remote27.sh; guard LINT_RC 0 $?'),
    ('guard HEAD_IS_82 fdad983 "$(git rev-parse --short HEAD)"',
     'guard HEAD_IS_84 af3e0fe "$(git rev-parse --short HEAD)"'),
    ('guard UNPUSHED_4 4 "$(git rev-list --count origin/main..HEAD)"',
     'guard UNPUSHED_5 5 "$(git rev-list --count origin/main..HEAD)"'),
    # 快照口径注释里的档名
    ("# 快照口径：snap26 打的是", "# 快照口径：snap27 打的是"),
    # 清单文件名
    ("| sort -u > .qoder/tmp-c82-filelist26.txt\necho FILES26=$(wc -l < .qoder/tmp-c82-filelist26.txt)",
     "| sort -u > .qoder/tmp-c82-filelist27.txt\necho FILES27=$(wc -l < .qoder/tmp-c82-filelist27.txt)"),
    ('"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c82-filelist26.txt > .qoder/tmp-c82-hashes-local26.out 2>&1; guard HASHES_LOCAL_RC 0 $?\ncat .qoder/tmp-c82-hashes-local26.out',
     '"$PY" .qoder/tmp-c68-hashes.py .qoder/tmp-c82-filelist27.txt > .qoder/tmp-c82-hashes-local27.out 2>&1; guard HASHES_LOCAL_RC 0 $?\ncat .qoder/tmp-c82-hashes-local27.out'),
    ('guard HASH_MISSING 0 "$(grep -oE \'HASH_MISSING=[0-9]+\' .qoder/tmp-c82-hashes-local26.out | cut -d= -f2)"',
     'guard HASH_MISSING 0 "$(grep -oE \'HASH_MISSING=[0-9]+\' .qoder/tmp-c82-hashes-local27.out | cut -d= -f2)"'),
    # 本轮全量本机档（换成 #84 那次跑完的档）
    ('guard LOCAL_SUITE_EXISTS 0 "$([ -s .qoder/tmp-c82-suite-local.out ] && echo 0 || echo 1)"\ntail -3 .qoder/tmp-c82-suite-local.out',
     'guard LOCAL_SUITE_EXISTS 0 "$([ -s .qoder/tmp-r20-suite-local.out ] && echo 0 || echo 1)"\ntail -3 .qoder/tmp-r20-suite-local.out'),
    ('guard LOCAL_SUITE_RC 0 "$(grep -oE \'LOCAL_SUITE_RC=[0-9]+\' .qoder/tmp-c82-suite-local.out | cut -d= -f2)"',
     'guard LOCAL_SUITE_RC 0 "$(grep -oE \'LOCAL_SUITE_RC=[0-9]+\' .qoder/tmp-r20-suite-local.out | cut -d= -f2)"'),
    ('guard LOCAL_SUITE_FAILED 0 "$(grep -cE \'^FAILED |[0-9]+ failed\' .qoder/tmp-c82-suite-local.out)"',
     'guard LOCAL_SUITE_FAILED 0 "$(grep -cE \'^FAILED |[0-9]+ failed\' .qoder/tmp-r20-suite-local.out)"'),
    # 上传与暂存：三份 harness
    ('"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-remote26.sh "$NAS:/tmp/ma_c82_remote26.sh"; guard REMOTE_SCP_RC 0 $?',
     '"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-remote27.sh "$NAS:/tmp/ma_c82_remote27.sh"; guard REMOTE_SCP_RC 0 $?'),
    ('"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-probe.py "$NAS:/tmp/ma_c82_probe.py"; guard PROBE_SCP_RC 0 $?',
     '"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-probe.py "$NAS:/tmp/ma_c82_probe.py"; guard PROBE_SCP_RC 0 $?\n"$SCP" "${OPTS[@]}" -q .qoder/tmp-r20-mut.py "$NAS:/tmp/ma_c82_mutr20.py"; guard MR20_SCP_RC 0 $?'),
    ('"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-filelist26.txt "$NAS:/tmp/ma_c82_filelist.txt"; guard FILELIST_SCP_RC 0 $?',
     '"$SCP" "${OPTS[@]}" -q .qoder/tmp-c82-filelist27.txt "$NAS:/tmp/ma_c82_filelist.txt"; guard FILELIST_SCP_RC 0 $?'),
    ("tr -d '\\r' < /tmp/ma_c82_mut82.py > /tmp/ma_c82_mut82_unix.tmp",
     "tr -d '\\r' < /tmp/ma_c82_mut82.py > /tmp/ma_c82_mut82_unix.tmp && tr -d '\\r' < /tmp/ma_c82_mutr20.py > /tmp/ma_c82_mutr20_unix.tmp"),
    ("docker cp /tmp/ma_c82_mut82_unix.tmp memory-agent:/tmp/${SNAP}_mut82.py",
     "docker cp /tmp/ma_c82_mut82_unix.tmp memory-agent:/tmp/${SNAP}_mut82.py && docker cp /tmp/ma_c82_mutr20_unix.tmp memory-agent:/tmp/${SNAP}_mutr20.py"),
    ("chown 10001:10001 /tmp/${SNAP}_mut81.py /tmp/${SNAP}_mut82.py",
     "chown 10001:10001 /tmp/${SNAP}_mut81.py /tmp/${SNAP}_mut82.py /tmp/${SNAP}_mutr20.py"),
    ("rm -f /tmp/ma_c82_*.tmp /tmp/ma_c82_mut81.py /tmp/ma_c82_mut82.py",
     "rm -f /tmp/ma_c82_*.tmp /tmp/ma_c82_mut81.py /tmp/ma_c82_mut82.py /tmp/ma_c82_mutr20.py"),
    ("tr -d '\\r' < /tmp/ma_c82_remote26.sh > /tmp/ma_c82_remote26_unix.sh && bash -n /tmp/ma_c82_remote26_unix.sh",
     "tr -d '\\r' < /tmp/ma_c82_remote27.sh > /tmp/ma_c82_remote27_unix.sh && bash -n /tmp/ma_c82_remote27_unix.sh"),
    ('bash /tmp/ma_c82_remote26_unix.sh $SNAP',
     'bash /tmp/ma_c82_remote27_unix.sh $SNAP'),
]

missing = [old for old, _ in PAIRS if src.count(old) != 1]
if missing:
    print("MISSING_OR_AMBIGUOUS=%d" % len(missing))
    for m in missing:
        print("  count=%d :: %s" % (src.count(m), m[:70]))
    sys.exit(2)

out = src
for old, new in PAIRS:
    out = out.replace(old, new)

# 尺1 追加：本轮被改文件与新测试、mutr20 harness 的 ast
anchor = '    "$PY" -c "import ast,io,sys;ast.parse(io.open(sys.argv[1],encoding=\'utf-8\').read())" "$f"; guard AST_$(basename $f) 0 $?\ndone'
out = out.replace(anchor, anchor + '\n"$PY" -c "import ast,io;ast.parse(io.open(\'.qoder/tmp-r20-mut.py\',encoding=\'utf-8\').read())"; guard MR20_PARSE_RC 0 $?')

# 尺3 追加：mutr20 本机档
anchor2 = "guard M82_TOTALS"
ins = (
    "# 本轮 mutr20 档：6 腿全杀、0 存活、0 无效、控制腿绿、工作树未动\n"
    'guard MR20_OUT_PRESENT 0 "$([ -s .qoder/tmp-r20-mut-local1.out ] && echo 0 || echo 1)"\n'
    "guard MR20_KILLED 6 \"$(grep -c 'KILLED' .qoder/tmp-r20-mut-local1.out)\"\n"
    "guard MR20_SURVIVED 0 \"$(grep -c 'SURVIVED' .qoder/tmp-r20-mut-local1.out)\"\n"
    "guard MR20_INVALID 0 \"$(grep -c 'INVALID' .qoder/tmp-r20-mut-local1.out)\"\n"
    "guard MR20_WT True \"$(grep -oE 'WT_UNTOUCHED=[A-Za-z]+' .qoder/tmp-r20-mut-local1.out | cut -d= -f2)\"\n"
    "guard MR20_TOTALS \"totals: killed=6 survived=0 invalid=0 legs=6\" \"$(grep -E '^totals' .qoder/tmp-r20-mut-local1.out)\"\n"
    'grep -E "^\\[P-0\\]" .qoder/tmp-r20-mut-local1.out\n'
    'guard MR20_CONTROL_PASSED "28 passed" "$(grep -oE \'[0-9]+ passed\' .qoder/tmp-r20-mut-local1.out | head -1)"\n'
    "guard MR20_LEGS 6 \"$(grep -cE '^[[:space:]]+\\\\(\"P[0-9]+\", ' .qoder/tmp-r20-mut.py)\"\n"
    'guard MR20_OUTENV 1 "$(grep -cF \'MUT_OUT\' .qoder/tmp-r20-mut.py)"\n'
    'guard MR20_ROOTENV 1 "$(grep -cF \'MUT_ROOT\' .qoder/tmp-r20-mut.py)"\n'
    'guard MR20_AST 1 "$(grep -cF \'ast.parse(mutated\' .qoder/tmp-r20-mut.py)"\n'
    'guard MR20_UNCHANGED 1 "$(grep -cF \'WT_UNTOUCHED=\' .qoder/tmp-r20-mut.py)"\n'
    "\n"
)
out = out.replace(anchor2, ins + anchor2, 1)

# 产品侧锚点：ACP 九枚 + 新测试五枚（本机先读，容器 0C4/0C5 对逐字）
anchor3 = "# vendor provenance：Dockerfile 装的那枚"
acpins = (
    "ACP=src/memory_agent/acp_server.py; T20=tests/test_acp_round20_owner_isolation.py\n"
    'guard ACP_CONFLICT_CLS 1 "$(grep -cF \'class SessionOwnerConflict(Exception):\' $ACP)"\n'
    'guard ACP_RAISE 1 "$(grep -cF \'raise SessionOwnerConflict(sid)\' $ACP)"\n'
    'guard ACP_ROUTE_EXCEPT 1 "$(grep -cF \'except SessionOwnerConflict:\' $ACP)"\n'
    'guard ACP_NEW_PREV 1 "$(grep -cF \'if prev is not None and prev.get("owner_token") != owner_token:\' $ACP)"\n'
    'guard ACP_PROMPT_CHECK 1 "$(grep -cF \'if requested and not _STORE.check_owner(requested, _owner):\' $ACP)"\n'
    'guard ACP_NEW_OWNER 1 "$(grep -cF \'session_id = requested or _STORE.new(owner_token=_owner)\' $ACP)"\n'
    'guard ACP_NEWARG 1 "$(grep -cF \'sid = _STORE.new(params.get("sessionId"), owner_token=_owner)\' $ACP)"\n'
    'guard ACP_CHECK_DEF 1 "$(grep -cF \'def check_owner(self\' $ACP)"\n'
    'guard ACP_CHECK_CALLS 4 "$(grep -cF \'_STORE.check_owner(\' $ACP)"\n'
    'guard L20_DEFS 8 "$(grep -c \'def test_\' $T20)"\n'
    'guard L20_PARITY 1 "$(grep -c \'def test_every_entry_that_reads_session_id_enforces_ownership():\' $T20)"\n'
    'guard L20_CROSS 1 "$(grep -c \'def test_prompt_cross_owner_is_denied_and_writes_nothing():\' $T20)"\n'
    'guard L20_TRIM 1 "$(grep -c \'def test_prompt_denies_a_session_whose_store_entry_is_gone_but_history_remains():\' $T20)"\n'
    'guard L20_UNAUTH 1 "$(grep -c \'def test_prompt_from_an_unauthenticated_principal_is_denied_for_existing_session():\' $T20)"\n'
    "\n"
)
out = out.replace(anchor3, acpins + anchor3, 1)

# CR 清单：本轮动过的文件
anchor4 = "         tests/test_tool_prose_promised_keys.py .qoder/tmp-c81-mut.py .qoder/tmp-c82-mut.py \\\n"
out = out.replace(anchor4, anchor4 +
                  "         src/memory_agent/acp_server.py tests/test_acp_round20_owner_isolation.py \\\n"
                  "         .qoder/tmp-r20-mut.py \\\n", 1)
out = out.replace("         .qoder/tmp-c82-probe.py .qoder/tmp-c82-remote26.sh; do",
                  "         .qoder/tmp-c82-probe.py .qoder/tmp-c82-remote27.sh; do", 1)

# 尺4：本轮新文件必须真在快照里
anchor5 = "         tests/test_tool_prose_promised_keys.py tests/test_insights_facade_contract.py \\\n"
out = out.replace(anchor5, anchor5 +
                  "         tests/test_acp_round20_owner_isolation.py tests/test_acp_server.py \\\n"
                  "         tests/test_acp_session_cross_owner_denied.py src/memory_agent/acp_server.py \\\n", 1)
out = out.replace("echo IN_SNAP_checked=17", "echo IN_SNAP_checked=21", 1)

io.open(P, "w", encoding="utf-8", newline="").write(out)
print("WROTE_OK bytes=%d CR=%d" % (len(out), out.count("\r")))
