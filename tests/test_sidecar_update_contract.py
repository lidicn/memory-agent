#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`G-MA-07`（宿主侧 sidecar 可用）判据重述所依据的**盘上事实**的契约锁。

为什么要钉（2026-10-15 第十七面）：路线图 rev 4 §一对 MA 的特令是「`G-MA-07` 的判据①②③
**必须按 sidecar 形态重述**，⛔ 照抄 AF/DB 的命令当判据（那是拿别人的尺量自己的门）」，
而 §巡检发现 16:5x 行逐字写着「**在 MA 自己重述之前**，本席的巡检只能报『判据未定形⇒缺读数』」。
⇒ 重述要引的是**自家四枚真源**的字面与顺序（compose 的挂载行／容器侧路由／两枚宿主脚本），
而 `scripts/`＋`docker-compose.yml` 在 MA 仓**没有任何权威门罩得住**（`scripts/` 被
`.gates.toml` 的 `extra_skip_globs` 跳过、`pyflakes_gate.sh` 只罩 `src/memory_agent`，见 §六十三），
所以这些锚点会漂而门⛔ 会红。本文件把重述依赖的六条事实钉成会红的闸。

★ 本锁**⛔ 证明容器侧行为**（那要窗口，见 §六十七）。它只钉「盘上契约」这一半（七条）：
① 容器⛔ 执行 git，只写 marker——判据取**调用点**（`ast`），⛔ 取文案面：本模块的 docstring 与
   出货文案里每次都写着"宿主脚本执行 git pull"，按文本扫＝恒红（在册盲区「文本与 AST 口径混用」）；
② 宿主脚本里**成功结果写在 `docker restart` 之前**
   ⇒ 判据⛔ 能停在 `result.ok`，必须含"重启后读回自己的版本号"那一列；
   ②b 三条失败出口都写 `write_result False` 并摘 marker（⛔ 摘＝真失败被读成"还在跑"）；
③ 三面文件名（request/result/available）容器侧与宿主侧逐字相等；
④ marker 的 `ref` 只来自**配置面**（`update_branch`），⛔ 来自请求体——宿主随后按该 ref `reset --hard`；
⑤ `update_status` 载荷含 `current_version`（＝判据②的取数面在场）；
⑥ 备份回收排在备份创建**之后**，且 `--keep` 为正整数；
⑦ `docker-compose.yml` 的 `./src:/app/src` 带 `:ro`（判据①的配置面半边；运行中的负控⛔ 在本锁射程内）。

判据本身钉住，**⛔ 钉现网枚数**：这里没有一条腿断言"data 目录有几枚备份"这类会漂的读数。
"""

from __future__ import annotations

import ast
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSE = os.path.join(REPO, "docker-compose.yml")
ROUTES = os.path.join(REPO, "src", "memory_agent", "api", "system_routes.py")
HOST_UPDATE = os.path.join(REPO, "scripts", "host_update.sh")
HOST_CHECK = os.path.join(REPO, "scripts", "host_check_update.sh")


def _read(path: str) -> str:
    assert os.path.isfile(path), f"契约面缺文件：{path}"
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def _lineno(path: str, pattern: str) -> "int | None":
    """首个命中行号（1 起）；⛔ 命中＝None，由调用方判红（⛔ 用 -1 冒充'没找到'）。"""
    rx = re.compile(pattern)
    for i, line in enumerate(_read(path).splitlines(), 1):
        if rx.search(line):
            return i
    return None


def _code_calls(path: str):
    """取**调用点**口径（⛔ 文本面扫 `git`：本模块 docstring 与文案里"宿主脚本执行 git pull"
    每次都会把叙述面读成代码面——同族盲区，见 §六十三 的"文本与 AST 口径混用"）。"""
    tree = ast.parse(_read(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    return imported, called


# ---------- ① 容器⛔ 执行 git，只写标记文件 ----------

def test_container_writes_marker_but_never_runs_git():
    """①：铁律 Q1=B 的字面——容器⛔ 执行 git 操作。判据取**调用点**，⛔ 取文案面出现 `git` 字样。"""
    src = _read(ROUTES)
    assert 'MARKER_PATH' in src and 'RESULT_PATH' in src, "容器侧连 marker/result 常量都读不到，重述无从谈起"
    # 反空集：判据取数面（三跳路由）必须在场
    for route in ("/api/system/update/check", "/api/system/update", "/api/system/update/status"):
        assert f'Route("{route}"' in src, f"自更新路由少了 {route} 一跳：{route}"
    imported, called = _code_calls(ROUTES)
    assert "subprocess" not in imported, \
        f"容器侧 import 了 subprocess（{sorted(imported)}）⇒ 铁律 Q1=B（⛔ 容器内写宿主源码）被破"
    execish = {"system", "popen", "run", "check_output", "check_call", "execv", "execve",
               "spawnl", "spawnv", "Popen"} & called
    assert not execish, f"容器侧出现执行面调用点 {sorted(execish)} ⇒ '容器只写 marker' 这条事实作废，判据重述要重裁"


# ---------- ② ★ 成功结果写在 docker restart 之前 ----------

def test_host_script_reports_success_before_restart():
    """②：`write_result True` 的行号必须**小于** `docker restart` 的行号。

    ★ 这条钉的⛔ 是"脚本顺序对不对"，而是**"判据能停在哪儿"**：因为成功结果先写、重启后发生，
    `result.ok=True` 单独**⛔ 证明容器起来了**。哪天有人把顺序改成"重启后再写结果"，
    这条腿会红——那正是"判据②可以放宽回 `result.ok`"的信号，要**显式改判据**而⛔ 顺手漂。
    """
    w = _lineno(HOST_UPDATE, r'^\s*write_result\s+True\b')
    r = _lineno(HOST_UPDATE, r'^\s*docker\s+restart\s+')
    assert w is not None and r is not None, f"宿主脚本读不到 write_result True（{w}）或 docker restart（{r}）"
    assert w < r, f"顺序变了（write_result@{w} vs restart@{r}）⇒ 判据②的『重启后必须读回版本号』那一列要重裁"


def test_failure_paths_remove_the_marker_and_report_error():
    """②的另一半：三条失败出口（fetch／reset／py_compile）都要 `write_result False` ＋摘 marker。

    判据要的"可区分形状"＝ pending=False ∧ result.ok=False ∧ error 非空；
    ⛔ 如果失败只写 result 而⛔ 摘 marker，WebUI 会永远 pending=True＝把真失败读成"还在跑"。
    """
    src = _read(HOST_UPDATE)
    n_fail = len(re.findall(r"^\s*write_result\s+False\b", src, flags=re.M))
    n_rm = len(re.findall(r'^\s*rm\s+-f\s+"\$MARKER"', src, flags=re.M))
    assert n_fail >= 3, f"失败出口只剩 {n_fail} 处 write_result False（fetch/reset/py_compile 至少三处）"
    assert n_rm >= n_fail, f"有 {n_fail} 处判失败但只摘了 {n_rm} 次 marker ⇒ 失败会被读成'还在跑'"


# ---------- ③ 三面文件名两侧逐字相等 ----------

def test_marker_result_preview_filenames_match_on_both_sides():
    """③：容器侧常量与宿主侧变量必须指同一枚文件名——漂一枚就是"两边各说各话"的假绿。"""
    routes = _read(ROUTES)
    host_u = _read(HOST_UPDATE)
    host_c = _read(HOST_CHECK)
    for name in (".update_request.json", ".update_result.json", ".update_available.json"):
        assert name in routes, f"容器侧读不到 {name}"
        in_host = (name in host_u) or (name in host_c)
        assert in_host, f"{name} 只在容器侧出现：宿主侧没人写/读它 ⇒ 三跳链断在盘上"


# ---------- ④ marker 的 ref 只来自配置面 ----------

def test_marker_ref_comes_from_config_not_from_request_body():
    """④：`ref` 取 `cfg.update_branch`（出资人配置面），⛔ 取请求体。

    正控同批出：请求体里 MA 只认 `confirm_version`（Q4=B 的版本号二次确认）。
    ★ 这条腿是安全面的判据（宿主随后 `git fetch --force origin "$REF"`＋`reset --hard`，
    ref 若可由请求体控制，WebUI 的一个 POST 就能把部署指到任意 ref）。
    """
    src = _read(ROUTES)
    body = src[src.index("async def apply_update"):src.index("async def update_status")]
    assert re.search(r'"ref":\s*branch', body), f"marker 的 ref ⛔ 取自 branch 变量：{body[:200]}"
    assert re.search(r"branch\s*=\s*\(getattr\(cfg,\s*[\"']update_branch[\"']", body), \
        "branch ⛔ 从配置面 update_branch 取 ⇒ 判据④作废"
    assert 'body.get("ref")' not in body and "body.get('ref')" not in body, \
        "请求体里出现 body.get('ref')＝把部署 ref 交给调用方"
    assert 'body.get("confirm_version")' in body, "正控失效：二次确认键 confirm_version 不在场"


# ---------- ⑤ status 载荷带 current_version（判据②的取数面） ----------

def test_status_payload_carries_the_readback_field():
    """⑤：`update_status` 必须回 `pending`／`result`／`current_version` 三键。

    判据②重述成"重启后读回容器自己的版本号"（系统自己的信号），取数面就是这一格；
    键掉了 ⇒ 判据只能退回 `result.ok` 那种"重启前写好的成功"。
    """
    body = _read(ROUTES)
    body = body[body.index("async def update_status"):body.index("async def breakers_status")]
    for key in ("pending", "result", "current_version"):
        assert f'"{key}"' in body, f"update_status 载荷少了 {key} 键 ⇒ 判据②无处取数"


# ---------- ⑥ 备份回收排在创建之后，且 keep 为正 ----------

def test_backup_prune_runs_after_backup_creation_with_positive_keep():
    """⑥：`tar czf`（每请求留一枚）之后才跑回收器，且 `--keep` 是正整数。

    反序＝本次新件可能被自己删掉（退路消失）；keep⛔ 为正＝回收器变成"删光"。
    """
    src = _read(HOST_UPDATE)
    tar = _lineno(HOST_UPDATE, r'^\s*tar\s+czf\s+')
    prune = _lineno(HOST_UPDATE, r'prune_update_backups\.py')
    assert tar is not None and prune is not None, f"备份面读不全：tar@{tar} prune@{prune}"
    assert tar < prune, f"回收（@{prune}）排在备份创建（@{tar}）之前＝本次新件可能被自己删掉"
    keep = re.search(r"--keep\s+(\S+)", src)
    assert keep and keep.group(1).isdigit() and int(keep.group(1)) > 0, \
        f"--keep ⛔ 是正整数：{keep.group(1) if keep else None}"


# ---------- ⑦ 判据①的盘上半边：src 只读挂载 ----------

def test_compose_mounts_host_src_read_only():
    """⑦：`docker-compose.yml` 里 `./src:/app/src` 必须带 `:ro`（路线图 G-MA-07 判据①的字面）。

    ★ 这条腿⛔ 证明"容器内写该目录会失败"——那是运行中容器的负控，要窗口（见 §六十七）。
    它只钉**配置面**：这一行漂成可写（`./src:/app/src` 或 `:rw`），铁律 P0-10 的红线就在**配置**上塌了，
    而配置面是 MA 一只手现在就能量到的那一半。
    """
    lines = [l.strip() for l in _read(COMPOSE).splitlines()]
    src_mounts = [l for l in lines if re.match(r"-\s+\./src:/app/src", l)]
    assert src_mounts, "compose 里⛔ 找到 ./src:/app/src 挂载行 ⇒ 判据①的盘上半边无处可引"
    for m in src_mounts:
        assert m.endswith(":ro"), f"src 挂载漂成可写形态：{m}"
    # 反空集正控：同文件的 .env 也按同一口径读得到（证明⛔ 是尺窄）
    assert any(re.match(r"-\s+\./\.env:/app/\.env:ro", l) for l in lines), \
        "反控失效：compose 里连 .env 的只读挂载行都读不到 ⇒ 本腿的取材面塌了"
