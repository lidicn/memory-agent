"""#81 变异自证 harness（DCD 20261007 §五 裁乙 + 裁丙）。

跑在副本树 `.qoder/tmp-c81-mut/`，不碰工作树：每腿注入前 ast.parse、锚点唯一性先判、
跑完把 pristine 内容写回并复核哈希。M-0 是对照腿（什么都不改），它必须绿——
否则这一轮的"红"全是环境问题而不是判据。
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys

WT = os.environ.get("MUT_ROOT") or r"E:\NAS\memory-agent"
PY = os.environ.get("MUT_PY") or sys.executable
DST = os.environ.get("MUT_DST") or os.path.join(WT, ".qoder", "tmp-c81-mut")
OUT = os.environ.get("MUT_OUT") or os.path.join(
    WT, ".qoder", "tmp-c81-mut%s.out" % (sys.argv[2] if len(sys.argv) > 2 else "81"))

FILES = {
    "auth": os.path.join("src", "memory_agent", "auth.py"),
    "routes": os.path.join("src", "memory_agent", "api", "auth_routes.py"),
    "cfg": os.path.join("src", "memory_agent", "config.py"),
    "app": os.path.join("src", "memory_agent", "app.py"),
}
TESTS = ["tests" + os.sep + t for t in (
    "test_vma_login_rate_limit.py",
    "test_vma_a3_p22_auth_loop_blocking.py",
    "test_vma_phase2_batch3_shared_ruler.py",
    "test_wo_ma_012_g1_security.py",
    "test_device_event_feed.py",
)]

LEGS = [
    ("M01", "auth",
     "    if not trust_proxy:\n        return peer_ip\n",
     "    if False:\n        return peer_ip\n",
     "开关失效：MA_TRUST_PROXY 关着（默认）也去读 XFF"),
    ("M02", "auth",
     "    return parts[-1] if parts else peer_ip\n",
     "    return parts[0] if parts else peer_ip\n",
     "取 XFF 首位而不是末位：首位是客户端自己写的"),
    ("M03", "auth",
     '    nets = _trusted_proxy_networks(trusted_proxy_cidrs or "")\n',
     '    nets = _trusted_proxy_networks(trusted_proxy_cidrs or "") or (ipaddress.ip_network("0.0.0.0/0"),)\n',
     "网段为空时 fail-open：一个字段都没配就等于谁都可信"),
    ("M04", "auth",
     '        except ValueError:\n'
     '            _LOG.warning("trusted_proxy_cidrs 有一条无法解析，已跳过（不视为可信代理）：%r", item)\n',
     '        except ValueError:\n'
     '            nets.append(ipaddress.ip_network("0.0.0.0/0"))\n',
     "坏 CIDR 从「跳过该条」变成「全放行」"),
    ("M05", "auth",
     "    if _trusted_nets_cache[0] == raw:\n",
     "    if _trusted_nets_cache[1]:\n",
     "缓存命中不再看配置串：改网段后旧口径赖着不走"),
    ("M06", "auth",
     "    if not any(addr in net for net in nets):\n        return peer_ip\n",
     "    if False:\n        return peer_ip\n",
     "不再核对请求方是否登记：开关一开就信 XFF"),
    ("M07", "auth",
     "    try:\n        addr = ipaddress.ip_address(peer_ip)\n"
     "    except ValueError:\n        return peer_ip\n",
     "    addr = ipaddress.ip_address(peer_ip)\n",
     "对端 IP 解析不出时不再按不可信处理，而是抛穿"),
    ("M08", "auth",
     "            if len(global_fails) >= _GLOBAL_FAIL_BUDGET:\n",
     "            if len(global_fails) > _GLOBAL_FAIL_BUDGET:\n",
     "预算 off-by-one：要到 61 次才打穿"),
    ("M09", "auth",
     "                    _login_global_until = now + _GLOBAL_BACKOFF_SECONDS\n",
     "                    _login_global_until = now\n",
     "退避写成 0 秒：打穿之后照常放行"),
    ("M10", "auth",
     "            _login_global[:] = global_fails\n",
     "            _login_global[:] = []\n",
     "全局列表不回写：预算永不累积，墙永不立"),
    ("M11", "auth",
     "                return False, max(1, int(_login_global_until - now))\n",
     "                return False, int(_login_global_until - now)\n",
     "剩余秒数没有下限保护：不足 1 秒时报 0"),
    ("M12", "auth",
     "            if _login_global_until > now:\n",
     "            if False:\n",
     "login_allowed 完全不查全局退避"),
    ("M13", "auth",
     "            global_fails.append(now)\n",
     "",
     "失败不进预算计数"),
    ("M14", "auth",
     "        with _login_guard:\n"
     "            for k in self._login_keys(ip, username):\n"
     "                _login_fails.pop(k, None)\n"
     "                _login_locked.pop(k, None)\n",
     "        with _login_guard:\n            _login_global.clear()\n"
     "            for k in self._login_keys(ip, username):\n"
     "                _login_fails.pop(k, None)\n"
     "                _login_locked.pop(k, None)\n",
     "成功登录顺手清全局预算：边被打边登录 = 免费清账"),
    ("M15", "auth",
     "_GLOBAL_WINDOW_SECONDS = 60\n",
     "_GLOBAL_WINDOW_SECONDS = 86400\n",
     "窗口从每分钟变成每天：一次打穿拦一整天"),
    ("M16", "routes",
     '        return error(f"尝试过于频繁，请在 {max(1, (retry + 59) // 60)} 分钟后重试", 429)\n',
     '        return error(f"尝试过于频繁，请在 {retry // 60 + 1} 分钟后重试", 429)\n',
     "429 文案把 60 秒退避说成 2 分钟"),
    ("M17", "routes",
     "        trust_proxy=cfg.trust_proxy,\n",
     "        trust_proxy=True,\n",
     "/login 入口硬开信任，不读运行时 config"),
    ("M18", "cfg",
     "    trust_proxy: bool = False\n",
     "    trust_proxy: bool = True\n",
     "默认值从关翻成开"),
    ("M19", "cfg",
     '        "trust_proxy": "MA_TRUST_PROXY",\n',
     "",
     "env 映射里删掉 MA_TRUST_PROXY：开关拨不动"),
    ("M20", "cfg",
     '        "trusted_proxy_cidrs": "MA_TRUSTED_PROXY_CIDRS",\n',
     "",
     "env 映射里删掉 MA_TRUSTED_PROXY_CIDRS：网段配不进去"),
    ("M21", "cfg",
     '    trusted_proxy_cidrs: str = ""\n',
     '    trusted_proxy_cidrs: str = "172.16.0.0/12"\n',
     "默认给整段私有网段：信任面从一台代理扩到整个 docker 网"),
    ("M22", "app",
     '            ip = resolve_client_ip(\n'
     '                client_ip or "unknown",\n'
     '                headers.get("x-forwarded-for", ""),\n'
     '                trust_proxy=config.trust_proxy,\n'
     '                trusted_proxy_cidrs=config.trusted_proxy_cidrs,\n'
     '            )\n',
     '            ip = client_ip or "unknown"\n',
     "Basic Auth 入口退回 TCP 对端：两条入口的桶键再次分叉"),
]

# 只补跑指定腿（逗号分隔）：加用例只会让红更红，不会让已判红的腿转绿，所以补跑
# 针对"上一轮存活"的那几条即可；M-0 每轮都重跑，用它确认新用例在 pristine 上也是绿的。
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
