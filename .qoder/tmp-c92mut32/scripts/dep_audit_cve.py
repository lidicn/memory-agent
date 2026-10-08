"""依赖 CVE 扫描（补齐 A5/A7 审计缺口：pip-audit 在容器里跑不起来）。

口径（读结果前先看这三条）：
1. 扫的是**运行面 Python 依赖**（容器内 `pip freeze` 的导出件），不含 Debian 基础镜像的 OS 包
   （那部分要 `trivy`/`grype` 扫镜像层，本脚本不覆盖，不代表无 CVE）。
2. 数据源是 OSV（`api.osv.dev`），**容器无出站网络**，所以本脚本在能出网的一侧跑，
   输入是容器 `pip freeze` 的快照；快照要带采集时间，过期读数按过期处理。
3. 判红只认「该版本落在受影响区间内、且上游有可升的版本」；无修复版的单列「待观察」，
   不与「已修」混成一档。

退出码：0 = 扫描完成（无论有无命中）；2 = 扫描未完成（出网失败/接口异常），绝不把
「没扫成」读成「没有洞」。
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BATCH_URL = "https://api.osv.dev/v1/querybatch"
VULN_URL = "https://api.osv.dev/v1/vulns/"
ECOSYSTEM = "PyPI"
# 严重度排序用（OSV 的 severity 是 CVSS vector 或 schema 里的枚举，取不到记 UNKNOWN）
SEVERITY_ORDER = ("LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL")


def parse_freeze(path: str) -> list:
    pkgs = []
    seen = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "==" not in line:
                continue
            name, _, ver = line.partition("==")
            name, ver = name.strip(), ver.strip().split(" ")[0].strip()
            if not name or not ver or name.lower() in seen:
                continue
            seen.add(name.lower())
            pkgs.append({"package": {"name": name, "ecosystem": ECOSYSTEM}, "version": ver})
    return pkgs


def post_json(url: str, payload: dict, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get_json(url: str, timeout: int) -> dict:
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def best_severity(vuln: dict) -> str:
    """取最高严重度：OSV 里 GHSA 常把档位放在 `database_specific.severity`，
    `severity[]` 数组给的是 CVSS vector/分数。两处都读，取不到才记 UNKNOWN（不猜档）。"""
    sev, score = "UNKNOWN", -1.0
    for entry in vuln.get("severity") or []:
        txt = str(entry.get("score") or entry.get("type") or "")
        try:
            s = float(txt.split("/")[0]) if "/" in txt else -1.0
        except ValueError:
            s = -1.0
        if s > score:
            score = s
            sev = ("CRITICAL" if s >= 9.0 else "HIGH" if s >= 7.0
                   else "MODERATE" if s >= 4.0 else "LOW" if s > 0 else "UNKNOWN")
    db_sev = str((vuln.get("database_specific") or {}).get("severity") or "").upper()
    if db_sev in SEVERITY_ORDER:
        cur = SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else -1
        if SEVERITY_ORDER.index(db_sev) > cur:
            return db_sev
    return sev


def cve_aliases(vuln: dict) -> str:
    aliases = [a for a in (vuln.get("aliases") or []) if str(a).startswith("CVE-")]
    return ",".join(aliases) if aliases else "-"


def fixed_versions(vuln: dict, installed: str) -> str:
    out = []
    for aff in vuln.get("affected") or []:
        if (aff.get("package") or {}).get("name", "").lower() != installed.lower():
            continue
        for rng in aff.get("ranges") or []:
            for ev in rng.get("events") or []:
                if "fixed" in ev:
                    out.append(str(ev["fixed"]))
    return ",".join(sorted(set(out))[-3:]) if out else "无修复版"


def hits_from(result: dict) -> list:
    """OSV 批量接口的每条命中键在不同版本里叫过 `vulns` 和 `matches` 两个名字。

    只认其中一个的话，另一个形状会让整表**静默归零**（本机实测：`matches` 单读 ⇒
    已知有洞的 requests 2.19.1 也报 0 条）。所以两个都读；
    `{}` 是**该包无已知漏洞**的合法空返回，不能和「键名对不上」混成一档——后者要判红、
    前者只是 0 条。
    """
    if not result:
        return []
    if "vulns" not in result and "matches" not in result:
        raise RuntimeError(f"OSV 响应里没有 vulns/matches 任一键，键集={sorted(result)}——"
                           f"接口形状变了，读数作废，不许当成『无洞』")
    rows = (result.get("vulns") or []) + (result.get("matches") or [])
    return [str(r.get("id") or r.get("vuln_id") or "-") for r in rows]


def run_canary(timeout: int) -> int:
    """量具自证：拿一个确定有 CVE 的版本打一次，不咬就说明整套读数作废。"""
    canary = {"queries": [{"package": {"name": "requests", "ecosystem": ECOSYSTEM},
                           "version": "2.19.1"}]}
    resp = post_json(BATCH_URL, canary, timeout)
    results = resp.get("results") or []
    n = len(hits_from(results[0])) if results else 0
    print(f"[DEP-AUDIT] 反例自检 requests==2.19.1 命中={n} 条 -> "
          f"{'咬，读数可用' if n else '不咬：接口/判据失效，本次读数作废'}")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OSV 依赖 CVE 扫描（stdlib-only，不装包）")
    ap.add_argument("--freeze", required=True, help="容器 `pip freeze` 导出件路径")
    ap.add_argument("--snapshot", default="", help="快照采集时间，原样登记进读数")
    ap.add_argument("--timeout", type=int, default=25)
    ap.add_argument("--details", type=int, default=40, help="最多拉取多少个漏洞详情")
    ap.add_argument("--no-canary", action="store_true",
                    help="跳过反例自检（跳过时 0 命中只能记『未证实』，不能记『无洞』）")
    args = ap.parse_args(argv)

    pkgs = parse_freeze(args.freeze)
    if not pkgs:
        print("[DEP-AUDIT] ❌ 快照里一个包都没解析出来（格式不对？要 `name==version`）")
        return 2

    canary_ok = None
    if not args.no_canary:
        try:
            canary_ok = run_canary(args.timeout) > 0
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            print(f"[DEP-AUDIT] ❌ 反例自检未完成（没扫成）：{type(exc).__name__}: {exc}")
            print("[DEP-AUDIT]    这条读数是「没扫成」，不是「没有洞」——按 RC=2 登记。")
            return 2
        if not canary_ok:
            print("[DEP-AUDIT] ❌ 反例不咬——判据或接口失效，此时任何 0 命中都无意义。")
            return 2
    else:
        print("[DEP-AUDIT] ⚠️ 已跳过反例自检：下面的 0 命中只能记『未证实』。")

    print(f"[DEP-AUDIT] 数据源=OSV api.osv.dev 生态={ECOSYSTEM} "
          f"快照={args.snapshot or '未标注'} 包数={len(pkgs)}")

    try:
        resp = post_json(BATCH_URL, {"queries": pkgs}, args.timeout)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
        print(f"[DEP-AUDIT] ❌ 扫描未完成：{type(exc).__name__}: {exc}")
        print("[DEP-AUDIT]    这条读数是「没扫成」，不是「没有洞」——按 RC=2 登记。")
        return 2
    results = resp.get("results") or []
    if len(results) != len(pkgs):
        print(f"[DEP-AUDIT] ❌ 响应条数 {len(results)} != 查询条数 {len(pkgs)}，"
              f"逐包对位不可靠，读数作废（RC=2）")
        return 2
    hits = []
    try:
        for q, r in zip(pkgs, results):
            for vid in hits_from(r):
                hits.append({"pkg": q["package"]["name"], "ver": q["version"], "vuln_id": vid})
    except RuntimeError as exc:
        print(f"[DEP-AUDIT] ❌ {exc}")
        return 2
    print(f"[DEP-AUDIT] 命中漏洞={len(hits)} 涉及包={len({h['pkg'] for h in hits})} "
          f"（已扫 {len(pkgs)} 个包）")

    by_pkg = {}
    cache, detail_n = {}, 0
    for h in hits:
        vinfo = {"sev": "UNKNOWN", "cve": "-", "fix": "未取详情"}
        if h["vuln_id"] in cache:
            vinfo = cache[h["vuln_id"]]
        elif detail_n < args.details:
            try:
                v = get_json(VULN_URL + urllib.parse.quote(h["vuln_id"], safe=""), args.timeout)
                vinfo = {"sev": best_severity(v), "cve": cve_aliases(v),
                         "fix": fixed_versions(v, h["pkg"])}
                cache[h["vuln_id"]] = vinfo
                detail_n += 1
            except Exception as exc:  # noqa: BLE001 - 单条详情失败不影响整表，但要标出来
                vinfo = {"sev": "UNKNOWN", "cve": "-", "fix": f"详情未取到({type(exc).__name__})"}
        by_pkg.setdefault(f"{h['pkg']}=={h['ver']}", []).append((h["vuln_id"], vinfo))

    unpatched = sum(1 for v in by_pkg.values() for _, i in v if i["fix"] == "无修复版")
    not_fetched = sum(1 for v in by_pkg.values() for _, i in v if i["fix"] == "未取详情")
    for key in sorted(by_pkg, key=lambda k: -len(by_pkg[k])):
        items = sorted(by_pkg[key], key=lambda x: -SEVERITY_ORDER.index(x[1]["sev"])
                       if x[1]["sev"] in SEVERITY_ORDER else 0)
        worst = items[0][1]["sev"]
        nofix = sum(1 for _, i in items if i["fix"] == "无修复版")
        print(f"  {key:<42} 漏洞={len(items):<3} 最高={worst:<8} 无修复版={nofix}")
        for vid, i in items[:4]:
            print(f"      {vid:<16} {i['cve']:<19} {i['sev']:<9} 修复={i['fix']}")
        if len(items) > 4:
            print(f"      …其余 {len(items) - 4} 条省略（--details 调）")
    print(f"[DEP-AUDIT] 汇总：命中 {len(hits)} 条 / 涉及包 {len(by_pkg)} 个 / "
          f"无上游修复 {unpatched} 条 / 详情已取 {detail_n} 条 / 未取详情 {not_fetched} 条"
          f"（未取详情的不能读成『有修复版』，也不能读成『无洞』）")
    print("[DEP-AUDIT] 边界：本表不覆盖基础镜像 OS 包与 `|| true` 吞掉的构建期失败；"
          "pin 决策属交付面，需 DCD 定向后在停机窗与镜像重烤同批落。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
