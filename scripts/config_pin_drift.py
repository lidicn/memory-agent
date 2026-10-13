"""`/data/config.json` 钉值诊断（⛔ 只读，⛔ 不写任何文件）。

要回答的问题（路线图 `MA-联动路线图.md` rev 9 的 G-MA-02 现网半边与「那一次点击」）：
配置文件的值**压住代码默认值**是设计行为（`Config.load`：文件里有的键就用文件里的），
而 `Config.save()` 写的是 `asdict(self)` 全量转储 ⇒ 一旦存过盘，**当时那份默认值就被冻进文件**，
此后代码里改默认值对现网不生效。所以要区分三件事：
① 文件里**没有**这个键 ⇒ 代码默认值生效；
② 文件里的值**等于**当前默认值 ⇒ 冗余钉值（删掉它才能让将来的新默认自然生效）；
③ 文件里的值**不等于**当前默认值 ⇒ 要么是人特意设的，要么是**旧默认值残留**（人手判，本工具⛔ 不判）。

每条还给出**可翻面**：该键有没有 env 通道（`config.py` 里那张 `env_map` 的口径是
「只在当前值为空时使用环境变量」⇒ 对 `bool` 是**单向闸**：False 能被 env 打开，True 不能被 env 关掉，
所以 `device_feed_*` 那三个开关才另走了独立覆盖段），以及能不能经 `/api/config` 写
（唯一真源 `api/config_routes.WRITABLE_FIELDS`）。

口径来源：两个文件**现读 AST 取字面量**，⛔ 不在本脚本里重抄第二张表（在册教训：桶定义写错＝自造两把尺分歧）。
出境纪律（**第一版漏过一次**，所以口径是"凡是字符串一律遮"）：现网 2026-10-13 实测
`vision_alert_mqtt_topic` 的值里带着家人称呼的拼音——它⛔ 不含敏感键名、⛔ 不是结构化值，
只按"键名含 SECRET/ROOM"和"值是 list/dict"两把尺都会放行。所以遮档面是三面：
① 任何**字符串**值；② 任何**非标量**值（list／dict，例如 `llm_backends` 内嵌 api_key）；
③ 键名命中 SECRET/PASSWORD/PASSWD/TOKEN/CRED/API_KEY/_PASS 或 ROOM/PERSON/ENTIT/CAMERA/NAME/…（兜底）。
⇒ 进 stdout 的只有键名＋分档＋env／HTTP 两个面＋`true/false/数字/null`；
遮档行只报 长度＋字符形状掩码（字母→A、数字→9、其余原样）＋`sha256(值本体，不含换行、不含引号)[:8]`。

用法：
    python3 scripts/config_pin_drift.py [--file /data/config.json] [--key 子串] [--all]
退出码：0 正常 ｜ 2 配置文件缺失或不是合法 JSON 对象 ｜ 3 取不到源码字面量（尺塌了，⛔ 不出读数）
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys

SECRET_RX = re.compile(r"SECRET|PASSWORD|PASSWD|TOKEN|CRED|API_KEY|_PASS", re.I)
# 结构化值与"人名／实体／设备"类键名同样⛔ 不出 stdout：现网 config.json 里 `llm_backends`
# 是个内嵌 api_key 的列表、`rooms`/`vision_cameras` 可能带家人称呼——按键名敏感词漏得干干净净。
ENTITY_RX = re.compile(r"ROOM|PERSON|ENTIT|CAMERA|NICK|NAME|LABEL|MEMBER|DEVICE_ID", re.I)
SCALARS = (str, int, float, bool, type(None))
LINKAGE_RX = re.compile(r"vision_alert_mqtt|^ma_mqtt|^tv_mqtt|^adm_|_mqtt_topic", re.I)


def mask(value) -> str:
    """出境口径：⛔ 不回显值。三档全部按**值本体**算（字符串不含 JSON 引号），
    与跨席位复现用的 `sha256(值本体，不含换行、不含引号)[:8]` 同一条尺。"""
    if isinstance(value, str):
        body = value
    else:
        body = json.dumps(value, ensure_ascii=False)
    return "len=%d shape=%s sha8=%s" % (
        len(body),
        "".join("A" if c.isalpha() else ("9" if c.isdigit() else c) for c in body),
        hashlib.sha256(body.encode("utf-8")).hexdigest()[:8],
    )


_MISSING = object()


def _literal(node):
    """取字面量默认值；取不到（表达式）返回 _MISSING——⛔ 不能返回 None，
    因为 `x: Optional[str] = None` 是个真默认值，把「取不到」也记成 None 会造出假 SAME_AS_DEFAULT。"""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return _MISSING


def field_defaults(path: str) -> dict:
    """从 Config 的类定义取 {字段名: 默认值字面量}；factory／表达式记为 _MISSING。

    类不在位 ⇒ 尺塌（非零退出）。否则所有键都会被读成 UNKNOWN，那是一份看起来正常的假读数。
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    classes = [n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "Config"]
    if len(classes) != 1:
        raise SystemExit(f"DRIFT_ERR=Config 类命中 {len(classes)} 处（应为 1，尺塌于 {path}）")
    out = {}
    for stmt in classes[0].body:
        if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
            continue
        if stmt.value is None:
            continue
        out[stmt.target.id] = _MISSING if isinstance(stmt.value, ast.Call) else _literal(stmt.value)
    if len(out) < 10:
        raise SystemExit(f"DRIFT_ERR=Config 只取到 {len(out)} 个带默认值的字段（尺塌）")
    return out


def assign_literal(tree: ast.AST, name: str, path: str):
    """按**结构**取赋值字面量（⛔ 不写死所在函数名——改名会让尺静默塌）。

    命中 0 枚或 >1 枚都算尺塌：非零退出，⛔ 不出读数。
    """
    found = [
        stmt for stmt in ast.walk(tree)
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in stmt.targets
        )
    ]
    if len(found) != 1:
        raise SystemExit(f"DRIFT_ERR={name} 命中 {len(found)} 处（应为 1，尺塌于 {path}）")
    return _literal(found[0].value)


def surfaces(src_root: str) -> tuple[dict, set]:
    cfg_path = os.path.join(src_root, "config.py")
    routes_path = os.path.join(src_root, "api", "config_routes.py")
    for p in (cfg_path, routes_path):
        if not os.path.isfile(p):
            raise SystemExit(f"DRIFT_ERR=源码文件不在位：{p}（尺塌）")
    env_map = assign_literal(ast.parse(open(cfg_path, encoding="utf-8").read()), "env_map", cfg_path)
    writable = assign_literal(
        ast.parse(open(routes_path, encoding="utf-8").read()), "WRITABLE_FIELDS", routes_path)
    if not isinstance(env_map, dict) or not env_map \
            or not isinstance(writable, (list, tuple)) or not writable:
        raise SystemExit("DRIFT_ERR=env_map／WRITABLE_FIELDS 形状不是非空 dict／tuple（尺塌）")
    return dict(env_map), set(writable)


def classify(data: dict, defaults: dict) -> dict:
    rows = {"ABSENT": [], "SAME_AS_DEFAULT": [], "DIFFERS_FROM_DEFAULT": [],
            "UNCOMPARED_FACTORY_OR_EXPR": [], "UNKNOWN_IGNORED_BY_LOAD": []}
    known = set(defaults)
    for key, value in sorted(data.items()):
        if key not in known:
            rows["UNKNOWN_IGNORED_BY_LOAD"].append((key, value))
            continue
        default = defaults[key]
        if default is _MISSING:
            rows["UNCOMPARED_FACTORY_OR_EXPR"].append((key, value))
        elif default == value:
            rows["SAME_AS_DEFAULT"].append((key, value))
        else:
            rows["DIFFERS_FROM_DEFAULT"].append((key, value))
    for key in sorted(known - set(data)):
        rows["ABSENT"].append((key, defaults[key]))
    return rows


def render(rows: dict, env_map: dict, writable: set, show_all: bool, only: str = "") -> list[str]:
    keep = (lambda k: only.lower() in k.lower()) if only else (lambda k: True)
    lines = []

    def emit(cls, key, value, prefix="  "):
        if value is _MISSING:                       # ABSENT 且默认值是 factory／表达式
            tag = "<默认值取不到>"
        elif SECRET_RX.search(key) or ENTITY_RX.search(key) \
                or isinstance(value, str) or not isinstance(value, SCALARS):   # 出境纪律三面
            tag = f"<掩码 {mask(value)}>"
        else:
            tag = json.dumps(value, ensure_ascii=False)
        lines.append(
            f"{prefix}{cls:26s} {key:34s} env={env_map.get(key) or '-':26s} "
            f"http={'WRITABLE' if key in writable else 'NO':8s} value={tag}"
        )

    def dump(clss, prefix=""):
        hit = False
        for cls in clss:
            for key, value in rows[cls]:
                if keep(key):
                    emit(cls, key, value, prefix or "  ")
                    hit = True
        return hit

    focus = [(cls, k, v) for cls, items in rows.items()
             for k, v in items if LINKAGE_RX.search(k)]
    if focus and not only:
        lines.append("── 联动开关面（路线图 G-MA-02/07 相关键）──")
        for cls, key, value in focus:
            emit(cls, key, value)
    lines.append("── 全量分类 ──")
    listed = dump(["DIFFERS_FROM_DEFAULT", "SAME_AS_DEFAULT", "UNKNOWN_IGNORED_BY_LOAD",
                   "UNCOMPARED_FACTORY_OR_EXPR"])
    if show_all:
        listed |= dump(["ABSENT"])
    if not listed:
        lines.append("  （无命中项）")
    return lines


def build_gauge(src_root: str) -> tuple[dict, dict, set]:
    defaults = field_defaults(os.path.join(src_root, "config.py"))
    env_map, writable = surfaces(src_root)
    return defaults, env_map, writable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="config.json 钉值诊断（⛔ 只读）")
    parser.add_argument("--file", default="/data/config.json")
    parser.add_argument("--key", default="", help="只看键名含该子串的条目（⛔ 不影响分类计数）")
    parser.add_argument("--all", action="store_true", help="连 ABSENT（文件没写的键）一起列")
    parser.add_argument("--src-root", default=None, help="源码根（默认取脚本所在仓的 src/memory_agent）")
    args = parser.parse_args(argv)

    src_root = args.src_root or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "memory_agent")
    try:
        defaults, env_map, writable = build_gauge(src_root)
    except SystemExit as exc:
        print(f"GAUGE_FAIL={exc}")
        return 3
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"GAUGE_FAIL={type(exc).__name__}: {exc}")
        return 3

    if not os.path.exists(args.file):
        print(f"DRIFT_ERR=file_missing {args.file}")
        return 2
    try:
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"DRIFT_ERR=unreadable {exc}")
        return 2
    if not isinstance(data, dict):
        print("DRIFT_ERR=not_a_json_object")
        return 2

    rows = classify(data, defaults)
    print(f"DRIFT_FILE={args.file} DRIFT_CONFIG_FIELDS={len(defaults)} "
          + " ".join(f"DRIFT_{k}={len(v)}" for k, v in sorted(rows.items())))
    for line in render(rows, env_map, writable, args.all, args.key):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
