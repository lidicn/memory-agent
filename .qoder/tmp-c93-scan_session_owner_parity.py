#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACP 会话属主的「入口对等」门（DCD `decisions/20261008-AF两件与MA四回执-裁定.md` §五 判例 2）。

判例原话：**"已设计过的校验，漏一个入口，第一次产生安全后果"**——MA-36 那一族里
`check_owner` 已写在 history / delete / cancel 三个入口上，第四个入口 `prompt` 没接，
结果是 B 拿 A 的 `sessionId` 能读到并污染 A 的历史。"已经设计过"在这里成了
"只有三个入口安全"的错觉，而人肉读码只会读那三个见过的。

口径（全部写死，避免把自己读红）：
  分支 = 目标函数体内 `if method == <CONST>: / elif method == <CONST>:` 的每一支
         （`elif` 在 AST 里是 orelse 里的嵌套 If ⇒ 递归沿 orelse 走，父支不会把子支吞进来）
  读会话 = 该支内出现 `<任何名字>.get("sessionId")`（含 `params.get("sessionId")` 赋值）
  有闸 = 该支内出现 `*.check_owner(...)` 调用，**或** `except SessionOwnerConflict` 处理支
         （新建分支走的是"冲突即拒"，语义等价，分开登记以免被当成同一种）
  判红 = ① 读了 sessionId 却两种闸都没有（新入口没接属主校验）
         ② `GUARDED` / `EXEMPT` 里登记的分支名在树里已经不存在（台账只准减）
  不算 = 分支内用变量间接传进来的会话 id（本门只看"这一支从请求里取 sessionId"的形状；
         间接传参的入口由测试与 code review 覆盖，不在静态口径里假装能看）

用法：
    python scripts/scan_session_owner_parity.py                       # 默认扫 src/memory_agent/acp_server.py
    python scripts/scan_session_owner_parity.py --self-test
退出码：0 干净 / 1 有判红 / 2 量具自检失败
"""
import argparse
import ast
import io
import os
import sys

TARGET_DEFAULT = os.path.join("src", "memory_agent", "acp_server.py")
HANDLER = "acp_handle"
SID_KEY = "sessionId"

#: 现读在册：五个读 sessionId 的分支各自的闸是哪一种。
#: 新增入口**必须**同时进这张表（或在 `EXEMPT` 里写明为什么不用闸），否则本门判红。
GUARDED = {
    "M_SESSION_NEW": "conflict",      # 建成即属主冲突 ⇒ SessionOwnerConflict 拒
    "M_SESSION_HISTORY": "check_owner",
    "M_SESSION_DELETE": "check_owner",
    "M_CANCEL": "check_owner",
    "M_PROMPT": "check_owner",        # MA-36：这一支曾经缺过，缺的就是那条越权路
}
EXEMPT = {}


def _reads_sid(stmts):
    """这一支里有没有 `<x>.get("sessionId")`（只走分支自有 body，不碰 elif 兄弟支）。"""
    for st in stmts:
        for sub in ast.walk(st):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get" and sub.args
                    and isinstance(sub.args[0], ast.Constant)
                    and sub.args[0].value == SID_KEY):
                return True
    return False


def _guard_kind(stmts):
    """返回 'check_owner' / 'conflict' / None（两种闸都算有闸，登记时分开写）。"""
    for st in stmts:
        for sub in ast.walk(st):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "check_owner":
                return "check_owner"
    for st in stmts:
        for sub in ast.walk(st):
            if isinstance(sub, ast.ExceptHandler) and isinstance(sub.type, ast.Name) \
                    and sub.type.id == "SessionOwnerConflict":
                return "conflict"
    return None


def _branch_const(test):
    """`method == M_XXX` ⇒ 'M_XXX'，其余返回 None。"""
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq) \
            and isinstance(test.left, ast.Name) and test.left.id == "method" \
            and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Name):
        return test.comparators[0].id
    return None


def _collect(stmts, out, seen):
    """沿 body 找分支；`elif` 只沿 orelse 递归，父支不把子支算进自己的读数。"""
    for st in stmts:
        if isinstance(st, ast.If):
            name = _branch_const(st.test)
            if name:
                if name not in seen:
                    seen.add(name)
                    out.append({"name": name, "line": st.lineno,
                                "reads": _reads_sid(st.body), "guard": _guard_kind(st.body)})
                _collect(st.orelse, out, seen)
                continue
            _collect(st.body, out, seen)
            _collect(st.orelse, out, seen)
        elif isinstance(st, (ast.With, ast.Try)):
            for child in st.body:
                _collect([child], out, seen)
            for extra in (getattr(st, "orelse", []) or []) + (getattr(st, "finalbody", []) or []):
                _collect([extra], out, seen)
        elif isinstance(st, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef, ast.For, ast.AsyncFor)):
            _collect(getattr(st, "body", []), out, seen)


def scan(path, handler=HANDLER):
    """返回 [{'name','line','reads','guard'}] —— 目标函数里每一个 `method ==` 分支。"""
    src = io.open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == handler:
            out, seen = [], set()
            _collect(node.body, out, seen)
            return out
    raise LookupError("%s 里没有函数 %s" % (path, handler))


def problems_of(branches):
    probs = []
    for b in branches:
        if not b["reads"]:
            continue
        if b["guard"]:
            want = GUARDED.get(b["name"])
            if want is None:
                probs.append("OWNER_UNREGISTERED %s:%d %s 有闸但没登记（登记集合必须 == 在册入口）"
                             % (b["name"], b["line"], b["guard"]))
            elif want != b["guard"]:
                probs.append("OWNER_KIND_DRIFT %s:%d 登记=%s 实际=%s（闸的哪一种变了要说一声）"
                             % (b["name"], b["line"], want, b["guard"]))
            continue
        if b["name"] in EXEMPT:
            continue
        probs.append("OWNER_GUARD_MISSING %s:%d 从请求里读 %s 却既无 check_owner 也无 SessionOwnerConflict"
                     % (b["name"], b["line"], SID_KEY))
    return probs


def stale_of(branches):
    names = {b["name"] for b in branches if b["reads"]}
    return [k for k in list(GUARDED) + list(EXEMPT) if k not in names]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default=TARGET_DEFAULT)
    ap.add_argument("--handler", default=HANDLER)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    branches = scan(args.target, args.handler)
    readers = [b for b in branches if b["reads"]]
    probs = problems_of(branches)
    stale = stale_of(branches)
    print("FILE=%s BRANCHES=%d READERS=%d GUARDED=%d EXEMPT_TOTAL=%d EXEMPT_HIT=%d PROBLEM=%d STALE=%d"
          % (args.target.replace(os.sep, "/"), len(branches), len(readers),
             len([b for b in readers if b["guard"]]), len(EXEMPT),
             len([b for b in readers if b["name"] in EXEMPT]), len(probs), len(stale)))
    if args.verbose:
        for b in branches:
            print("  %s %s:%d reads=%s guard=%s"
                  % ("READER" if b["reads"] else "      ", b["name"], b["line"],
                     b["reads"], b["guard"] or "-"))
    for k in stale:
        print("OWNER_ENTRY_STALE %s（登记/豁免的分支在树里已经不读 sessionId 了，台账要一起减）" % k)
    for p in probs:
        print(p)
    if probs or stale:
        print("SCAN_RC=1 判红 %d 条 / 失效登记 %d 条" % (len(probs), len(stale)))
        return 1
    print("SCAN_RC=0 读 sessionId 的入口与登记集合逐一对上，每一支都有闸")
    return 0


CLEAN = (
    "async def acp_handle(scope, params):\n"
    "    method = params.get('method')\n"
    "    if method == M_INITIALIZE:\n"
    "        return {'capabilities': {}}\n"
    "    if method == M_SESSION_NEW:\n"
    "        try:\n"
    "            sid = STORE.new(params.get('sessionId'))\n"
    "        except SessionOwnerConflict:\n"
    "            return 'occupied'\n"
    "        return sid\n"
    "    if method == M_SESSION_HISTORY:\n"
    "        sid = params.get('sessionId')\n"
    "        if not STORE.check_owner(sid, owner):\n"
    "            return 'denied'\n"
    "        return sid\n"
    "    elif method == M_PROMPT:\n"
    "        sid = params.get('sessionId')\n"
    "        if sid and not STORE.check_owner(sid, owner):\n"
    "            return 'denied'\n"
    "        return sid\n"
    "    if method == M_SESSION_DELETE:\n"
    "        sid = params.get('sessionId')\n"
    "        if not STORE.check_owner(sid, owner):\n"
    "            return 'denied'\n"
    "        return STORE.delete(sid)\n"
    "    if method == M_CANCEL:\n"
    "        sid = params.get('sessionId')\n"
    "        STORE.check_owner(sid, owner)\n"
    "        return STORE.cancel(sid)\n"
)

MISSING = CLEAN.replace(
    "    if method == M_SESSION_HISTORY:\n"
    "        sid = params.get('sessionId')\n"
    "        if not STORE.check_owner(sid, owner):\n"
    "            return 'denied'\n",
    "    if method == M_SESSION_HISTORY:\n"
    "        sid = params.get('sessionId')\n")

UNREGISTERED = CLEAN.replace("M_PROMPT", "M_PROMPT_EXTRA")

STALE = CLEAN.replace(
    "    elif method == M_PROMPT:\n"
    "        sid = params.get('sessionId')\n"
    "        if sid and not STORE.check_owner(sid, owner):\n"
    "            return 'denied'\n"
    "        return sid\n",
    "    elif method == M_PROMPT:\n"
    "        return 'no session read here'\n")


def _self_test():
    import tempfile
    ok = True
    cases = [("clean", CLEAN, 0, 0), ("missing", MISSING, 1, 0),
             ("unregistered", UNREGISTERED, 1, 1), ("stale", STALE, 0, 1)]
    with tempfile.TemporaryDirectory() as tmp:
        for name, src, want_probs, want_stale in cases:
            path = os.path.join(tmp, "%s.py" % name)
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src)
            branches = scan(path)
            probs = problems_of(branches)
            stale = stale_of(branches)
            if len(probs) != want_probs or len(stale) != want_stale:
                print("SELFTEST_MISS %s 期望 判红%d/失效%d，实际 判红%d/失效%d :: %s"
                      % (name, want_probs, want_stale, len(probs), len(stale),
                         " | ".join(probs + stale)))
                ok = False
            else:
                print("SELFTEST_HIT %s 判红%d 失效%d" % (name, len(probs), len(stale)))
        # 负例：`elif` 不能被子支吞掉（M_PROMPT 的闸不该记到 M_SESSION_HISTORY 头上）
        with io.open(os.path.join(tmp, "chain.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(CLEAN)
        got = {b["name"]: b for b in scan(os.path.join(tmp, "chain.py"))}
        if got.get("M_SESSION_NEW", {}).get("guard") != "conflict":
            print("SELFTEST_FALSE 新建分支的 conflict 闸没读出来")
            ok = False
        else:
            print("SELFTEST_CLEAN conflict/check_owner 两种闸分得开")
        if got.get("M_INITIALIZE", {}).get("reads"):
            print("SELFTEST_FALSE initialize 分支不读 sessionId 却被当成读者")
            ok = False
        else:
            print("SELFTEST_CLEAN 不读会话 id 的分支不进读数（initialize）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
