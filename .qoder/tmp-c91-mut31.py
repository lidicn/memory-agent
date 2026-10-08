"""run31 变异自证（副本树）：UNVERIFIED 减五格所依赖的十三"能判红"证据。

任务表 #85 收尾第二件的尺子侧：`scan_claimed_semantics.UNVERIFIED` 从 23 减到 18，
前提是这五格现在**有直接断言**——而"有断言"必须由变异腿证明，不是由用例名字证明。

跑在一次性副本树（MUT_DST）：工作树/快照树只读；每腿注入前 `ast.parse`、
锚点唯一性先判（命中 != 1 ⇒ INVALID，既不算杀也不算活），跑完把 pristine 内容写回并复核哈希。
Q-0 是对照腿（什么都不改），它必须绿。

十三条腿都是把那一格**退回它在 docstring 里承诺之前的写法**，不是随机破坏：

    app._is_trusted_source     T01 缺 IP 放行 / T02 坏 IP 放行 / T03 退回 `is_private`（本批真改动过的那条）/
                               T04 不拆 IPv4-mapped / T05 网表少 10/8 / T06 ULA 换成文档段
    auth._trusted_proxy_networks T07 坏 CIDR 换来 0.0.0.0/0（fail-open）/ T08 缓存短路关掉
    mcp_tokens.migrate_legacy    T09 判重拆掉（重复执行会再加一格）/ T10 prefix 存整串明文
    task_record.upsert_task_record T11 同键不更新而插第二行
    store.make_event_id          T12 payload 去掉时间戳（同秒相互覆盖）/ T13 加 monotonic_ns（不再确定）

T03 是本批最有价值的一条：补断言时量出 Python 的 `is_private` 把 TEST-NET / 保留 /
链路本地段都算私有，docstring 写的"内网段"比实现窄 ⇒ 代码按 fail-closed 收紧。
这条腿证明"收紧"不是空话：退回旧写法必须让新用例判红。

与 run30 的差别：本批五格**没有一格依赖 mcp 工具本体**，所以十三腿在本机和容器都能跑；
容器档仍然要跑（权威门），并把本机跑不起来的三条 `test_rounds11_19` 用例（旧 SDK skip）实跑。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or "/tmp/c91snap31"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or "/tmp/c91mut31"
OUT = os.environ.get("MUT_OUT") or "/tmp/c91mut31.out"

M = os.path.join("src", "memory_agent")
FILES = {
    "app": os.path.join(M, "app.py"),
    "auth": os.path.join(M, "auth.py"),
    "tokens": os.path.join(M, "mcp_tokens.py"),
    "taskrec": os.path.join(M, "task_record.py"),
    "store": os.path.join(M, "store.py"),
}
TESTS = ["tests" + os.sep + "test_vma_phase3_claims_direct.py"]

TRUSTED_NETS = [
    '    ipaddress.ip_network("10.0.0.0/8"),\n',
    '    ipaddress.ip_network("fc00::/7"),        # IPv6 ULA\n',
]
TRUSTED_ANY = '    return any(ip.version == net.version and ip in net for net in _TRUSTED_SOURCE_NETS)\n'
TRUSTED_MAPPED = ('    mapped = getattr(ip, "ipv4_mapped", None)\n'
                  '    if mapped is not None:\n'
                  '        ip = mapped\n')
TRUSTED_NOIP = "    if not client_ip:\n        return False\n"
TRUSTED_BADIP = "    except ValueError:\n        return False\n"
PROXY_EXCEPT = ('        except ValueError:\n'
                '            _LOG.warning("trusted_proxy_cidrs 有一条无法解析，已跳过（不视为可信代理）：%r", item)\n')
PROXY_CACHE = "    if _trusted_nets_cache[0] == raw:\n        return _trusted_nets_cache[1]\n"
LEGACY_ALREADY = "                if not already:\n"
LEGACY_PREFIX = '                        "prefix": value[:PREFIX_LEN],\n'
UPSERT_ROW = "        if row:\n"
EVENTID_PAYLOAD = '    payload = f"{entity_id}|{raw_ts}".encode("utf-8", errors="replace")\n'

LEGS = [
    ("T01", "app", TRUSTED_NOIP, "    if not client_ip:\n        return True\n",
     "缺 client_ip 放行 ⇒ dbg_ 令牌在无来源信息时也能用（fail-open）"),
    ("T02", "app", TRUSTED_BADIP, "    except ValueError:\n        return True\n",
     "坏 IP 放行 ⇒ 传不成 IP 的字符串被当成内网"),
    ("T03", "app", TRUSTED_ANY, "    return ip.is_loopback or ip.is_private\n",
     "退回 is_private ⇒ 文档段/保留段/链路本地段（203.0.113.7、169.254.1.1、2001:db8::1）都算可信"),
    ("T04", "app", TRUSTED_MAPPED, "",
     "不拆 IPv4-mapped ⇒ ::ffff:10.1.2.3 这类真实内网来源被拒（口径不对称）"),
    ("T05", "app", TRUSTED_NETS[0], "",
     "网表少 10.0.0.0/8 ⇒ 一段 RFC1918 内网不再可信"),
    ("T06", "app", TRUSTED_NETS[1], '    ipaddress.ip_network("2001:db8::/32"),\n',
     "ULA 换成 IPv6 文档段 ⇒ 该拒的放行、该放的拒"),
    ("T07", "auth", PROXY_EXCEPT,
     '        except ValueError:\n            nets.append(ipaddress.ip_network("0.0.0.0/0"))\n',
     "坏 CIDR 换来 0.0.0.0/0 ⇒ 配置出错退化成「谁都信」"),
    ("T08", "auth", PROXY_CACHE, "    if False:\n        return _trusted_nets_cache[1]\n",
     "缓存短路失效 ⇒ 改配置后仍拿旧网表（缓存与配置脱钩）"),
    ("T09", "tokens", LEGACY_ALREADY, "                if True:\n",
     "判重拆掉 ⇒ migrate_legacy 重复执行会再加一格（不幂等）"),
    ("T10", "tokens", LEGACY_PREFIX, '                        "prefix": value,\n',
     "prefix 存整串明文 ⇒ 迁移后配置里仍留有可用的完整令牌"),
    ("T11", "taskrec", UPSERT_ROW, "        if False:\n",
     "同键不更新而插第二行 ⇒ upsert 不再幂等"),
    ("T12", "store", EVENTID_PAYLOAD, '    payload = f"{entity_id}".encode("utf-8", errors="replace")\n',
     "payload 去掉原始时间戳 ⇒ 同秒事件相互覆盖"),
    ("T13", "store", EVENTID_PAYLOAD,
     '    payload = f"{entity_id}|{raw_ts}|{__import__(\'time\').monotonic_ns()}"'
     '.encode("utf-8", errors="replace")\n',
     "id 掺入进程内计时 ⇒ 同输入不再同输出，重复投递会写两行"),
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
    shutil.copytree(os.path.join(WT, "src"), os.path.join(DST, "src"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # §6 那条"登记自身的形状"用例用 `../scripts/scan_claimed_semantics.py` 现读台账，
    # 副本树缺 scripts 会让对照腿 Q-0 直接不绿（实测：1 failed 40 passed）⇒ 必须一起拷。
    shutil.copytree(os.path.join(WT, "scripts"), os.path.join(DST, "scripts"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in TESTS:
        shutil.copy2(os.path.join(WT, rel), os.path.join(DST, rel))
    log(f"COPY_TREE_READY={DST} src_files={len(os.listdir(os.path.join(DST, M)))}")


def run_pytest():
    # `-r` 是单选项：写成 "-rf" "-rs" 两串会让后一个整体覆盖前一个 ⇒ FAIL 名单被吞（实测）。
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-rfEs", "--tb=no", "-p", "no:cacheprovider"],
        cwd=DST, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
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
