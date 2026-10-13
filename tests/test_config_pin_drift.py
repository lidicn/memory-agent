"""`scripts/config_pin_drift.py` 的回归锁（只读钉值诊断器）。

为什么要有这一格：`Config.save()` 写的是 `asdict(self)` **全量转储** ⇒ 存过一次盘，
当时的默认值就被冻进 `/data/config.json`，此后改代码默认值对现网不生效。
路线图 rev 9 的「那一次点击要同时办两件」里第二件到底改哪几行，靠的就是这把尺的分类。
所以三条面都要锁住：
① **分类不能漂**（ABSENT／SAME／DIFFERS／UNCOMPARED／UNKNOWN 五桶各归各位）；
② **出境纪律不能塌**（任何字符串值、任何 list／dict 值、键名命中敏感词的，一律只出三档指纹，
   ⛔ 值本体不进 stdout）——反空集腿是「布尔与数字**照样**回显」，
   否则「没打出来」可能只是尺本身取不到值（在册判例：永远不红的闸与测不出收益的闸等价）；
③ **真仓的两条面不能漂**（env_map／WRITABLE_FIELDS 的键必须都是 Config 的字段；
   user 键在 env 面而它的口令孪生键不在 ⇒ 认证必然失败，这条锁 v0.7 踩过的坑）。

尺塌必须**响亮**：源码字面量取不到（改名、删除、出现两枚、形状不对）⇒ 退出码 3 ＋⛔ 不出读数。
"""

import hashlib
import importlib
import json
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "config_pin_drift.py")
_REAL_SRC = os.path.join(_ROOT, "src", "memory_agent")

PLANTED = "Sup3rSecretValue00000000001"          # 27 字符，够长够独特，只做测试夹具

CFG_TMPL = '''
import os
from dataclasses import dataclass, field


DEFAULT_EXPR = "x"


@dataclass
class Config:
    alpha_flag: bool = False
    beta_count: int = 7
    gamma_name: str = "g"
    delta_ratio: float = 0.5
    opt_text: str | None = None
    eps_host: str = "127.0.0.1"
    zeta_pass: str = ""
    eta_mode: str = "fast"
    theta_days: int = 30
    iota_url: str = "http://x"
    room_labels: str = ","
    lambda_map: dict = {{"a": 1}}
    factory_field: list = field(default_factory=list)
    expr_field: str = DEFAULT_EXPR


def get_config():
{env_block}    return Config()
'''

ROUTES_TMPL = '''WRITABLE_FIELDS = ({writable_body},)
'''

_ENV_ONE = '    env_map = {"eps_host": "EPS_HOST", "zeta_pass": "ZETA_PASS"}\n'
_ENV_TWO = _ENV_ONE + '    env_map = {"eta_mode": "ETA_MODE"}\n'
_ENV_EMPTY = "    env_map = {}\n"


def _mod():
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    return importlib.import_module("config_pin_drift")


def _mk_tree(tmp, env_block=_ENV_ONE, writable_body='"zeta_pass", "opt_text"', routes=True):
    """造一棵合成源码树（真仓形状：env_map 在函数体内，不在模块级）。"""
    root = os.path.join(str(tmp), "src", "memory_agent")
    os.makedirs(os.path.join(root, "api"), exist_ok=True)
    with open(os.path.join(root, "config.py"), "w", encoding="utf-8") as fh:
        fh.write(CFG_TMPL.format(env_block=env_block))
    if routes:
        with open(os.path.join(root, "api", "config_routes.py"), "w", encoding="utf-8") as fh:
            fh.write(ROUTES_TMPL.format(writable_body=writable_body))
    return root


def _row(stdout):
    """把渲染行按字段名归位（列宽是显示面，断言⛔ 不依赖它）。"""
    rows = {}
    for line in stdout.splitlines():
        if line.startswith("  ") and "env=" in line:
            cols = line.split()
            rows[cols[1]] = line
    return rows


def _run(*argv):
    return subprocess.run([sys.executable, "-X", "utf8", _SCRIPT, *argv],
                          capture_output=True, text=True, encoding="utf-8", cwd=_ROOT)


def _write_cfg(tmp, payload, name="config.json"):
    path = os.path.join(str(tmp), name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


# ─────────────────────────── 真仓读数腿 ───────────────────────────

def test_real_repo_no_orphan_env_or_writable_keys():
    """`env_map` 与 `WRITABLE_FIELDS` 里的字段名必须**都是** Config 的字段。

    这条锁对的是本仓自己踩过的坑（`config.py` 注释原话：v0.7 之前「只映射了 user 未映射 pass」
    ⇒ 配了用户名却永远拿不到密码）。字段改名／拼错时 env 与 HTTP 两面都会**静默失效**，
    而 `Config.load()` 又忽略未知键，所以没有任何现存的门会红。
    """
    defaults, env_map, writable = _mod().build_gauge(_REAL_SRC)
    assert sorted(set(env_map) - set(defaults)) == []
    assert sorted(writable - set(defaults)) == []


def test_real_repo_mqtt_pair_is_both_mapped():
    """凡 env 面里有 `X_user` 的，`X_pass`／`X_password` 也必须在（历史缺陷的直接复现锁）。

    v0.7 那条注释原话：「此前只映射了 user 未映射 pass ⇒ 配了用户名却永远拿不到密码，
    broker 一律返回未授权」。规则写成通用的，⛔ 只点名 MQTT 一对；末腿保证这条尺**不是空集**。
    """
    _, env_map, _ = _mod().build_gauge(_REAL_SRC)
    user_keys = [k for k in env_map if k.endswith("_user")]
    assert "tv_mqtt_user" in user_keys                        # 反空集：本尺确实有对象可查
    for user_key in user_keys:
        prefix = user_key[: -len("_user")]
        twins = [v for v in env_map if re.match(rf"^{prefix}_(pass|password)$", v)]
        assert twins, f"{user_key} 在 env 面而它的口令孪生键不在 ⇒ 认证必然失败"


def test_real_repo_gauge_reads_both_surfaces():
    """现读真仓：联动开关的**两条通道**是路线图那格的事实面，漂了要红。"""
    defaults, env_map, writable = _mod().build_gauge(_REAL_SRC)

    assert defaults["vision_alert_mqtt_enabled"] is False
    assert defaults["vision_alert_mqtt_topic"] == "ma/insights"
    # 这两枚⛔ 没有 env 通道 ⇒ 只能走 HTTP／WebUI 翻面（路线图「那一次点击」的第二件）
    assert "vision_alert_mqtt_enabled" not in env_map
    assert "vision_alert_mqtt_topic" not in env_map
    # 但它们**在**可写面里 ⇒ 不是"只能重烤镜像"的死键
    assert "vision_alert_mqtt_enabled" in writable and "vision_alert_mqtt_topic" in writable
    # 口令这类有 env 通道的键，取值口径不能被诊断器改写
    assert env_map["tv_mqtt_pass"] == "TV_MQTT_PASS"
    assert env_map["ma_mqtt_enabled"] == "MA_MQTT_ENABLED"
    assert "ma_mqtt_enabled" not in writable
    assert len(defaults) >= 100


def test_real_repo_render_marks_the_linkage_switch_masked_and_unusable_via_env(tmp_path):
    cfg = _write_cfg(tmp_path, {"vision_alert_mqtt_enabled": False,
                                "vision_alert_mqtt_topic": "ma/insights"})
    r = _run("--file", cfg)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "env=-" in r.stdout                       # ⛔ env 路径
    assert "WRITABLE" in r.stdout                    # 可经 /api/config 写


# ─────────────────────────── 五桶分类 ───────────────────────────

def test_five_buckets_classify_as_designed(tmp_path):
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {
        "alpha_flag": False,        # 与默认值相同 ⇒ 冗余钉值
        "beta_count": 9,            # 与默认值不同 ⇒ 人设 或 旧默认残留
        "factory_field": [1],       # 默认值取不到字面量 ⇒ 不比对
        "ghost_key": "who",         # 配置文件里有、Config 里没有 ⇒ load 会忽略
    })
    r = _run("--file", cfg, "--src-root", src, "--all")
    assert r.returncode == 0, r.stdout + r.stderr
    head = r.stdout.splitlines()[0]
    assert "DRIFT_SAME_AS_DEFAULT=1" in head
    assert "DRIFT_DIFFERS_FROM_DEFAULT=1" in head
    assert "DRIFT_UNCOMPARED_FACTORY_OR_EXPR=1" in head
    assert "DRIFT_UNKNOWN_IGNORED_BY_LOAD=1" in head
    assert "DRIFT_ABSENT=11" in head                  # 14 个字段 − 文件里出现的 3 枚
    rows = _row(r.stdout)
    assert "SAME_AS_DEFAULT" in rows["alpha_flag"]
    assert "DIFFERS_FROM_DEFAULT" in rows["beta_count"]
    assert "UNKNOWN_IGNORED_BY_LOAD" in rows["ghost_key"]
    # factory／表达式都取不到可比字面量 ⇒ 文件里写了就归 uncompared，⛔ 不许静默当 None
    assert "UNCOMPARED_FACTORY_OR_EXPR" in rows["factory_field"]
    # 文件里没写 + 默认值是表达式 ⇒ 只能如实说「默认值取不到」，⛔ 不许印个 "…" 让人以为读到了
    assert "ABSENT" in rows["expr_field"] and "<默认值取不到>" in rows["expr_field"]
    assert "ABSENT" in rows["eta_mode"]


def test_none_default_is_not_mistaken_for_unreadable(tmp_path):
    """`opt_text: str | None = None` 是**真默认值**；把取不到也记成 None 会造出假 SAME_AS_DEFAULT。"""
    src = _mk_tree(tmp_path)
    same = _write_cfg(tmp_path, {"opt_text": None}, "same.json")
    diff = _write_cfg(tmp_path, {"opt_text": "set-by-human"}, "diff.json")
    a = _run("--file", same, "--src-root", src)
    b = _run("--file", diff, "--src-root", src)
    assert "DRIFT_SAME_AS_DEFAULT=1" in a.stdout and "DRIFT_DIFFERS_FROM_DEFAULT=0" in a.stdout
    assert "DRIFT_SAME_AS_DEFAULT=0" in b.stdout and "DRIFT_DIFFERS_FROM_DEFAULT=1" in b.stdout


def test_surfaces_columns_come_from_the_two_source_files(tmp_path):
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {"eps_host": "1.2.3.4", "zeta_pass": PLANTED, "eta_mode": "slow"})
    r = _run("--file", cfg, "--src-root", src)
    rows = _row(r.stdout)
    assert "env=EPS_HOST" in rows["eps_host"] and "http=NO" in rows["eps_host"]
    assert "env=ZETA_PASS" in rows["zeta_pass"] and "http=WRITABLE" in rows["zeta_pass"]
    assert "env=-" in rows["eta_mode"] and "http=NO" in rows["eta_mode"]


def test_key_filter_does_not_change_counts(tmp_path):
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {"beta_count": 9, "eta_mode": "slow"})
    a = _run("--file", cfg, "--src-root", src, "--all")
    b = _run("--file", cfg, "--src-root", src, "--all", "--key", "beta")
    head_a, head_b = a.stdout.splitlines()[0], b.stdout.splitlines()[0]
    assert head_a == head_b                              # 计数是分类读数，⛔ 不受显示过滤影响
    assert b.stdout.count("beta_count") == 1
    assert "eta_mode" not in b.stdout                    # 显示面确实被过滤了


# ─────────────────────────── 出境纪律（含反空集对照） ───────────────────────────

def test_secret_key_value_never_reaches_stdout(tmp_path):
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {"zeta_pass": PLANTED, "opt_text": "plain-visible-value"})
    r = _run("--file", cfg, "--src-root", src, "--all")
    assert r.returncode == 0, r.stdout + r.stderr
    assert PLANTED not in r.stdout and PLANTED not in r.stderr
    assert "value=<掩码 len=%d shape=" % len(PLANTED) in r.stdout   # 三档指纹齐全，⛔ 无值本体


def test_every_string_value_is_masked_not_only_secret_named_ones(tmp_path):
    """现网踩出来的口径：字符串**一律**遮（`vision_alert_mqtt_topic` 那枚带家人称呼拼音的值
    既⛔ 无敏感键名、也⛔ 不是结构化值，第一版只按键名与形状两把尺，它整条放行到了 stdout）。"""
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {"eta_mode": "客厅-主卧-提示", "alpha_flag": True, "beta_count": 42})
    r = _run("--file", cfg, "--src-root", src)
    rows = _row(r.stdout)
    assert "客厅" not in r.stdout and "主卧" not in r.stdout
    assert "<掩码" in rows["eta_mode"]
    assert "value=true" in rows["alpha_flag"] and "value=42" in rows["beta_count"]   # 反空集：标量仍回显


def test_entity_named_and_structured_values_are_masked_too(tmp_path):
    """遮档⛔ 不能只按"键名含敏感词"算——现网的家人称呼与嵌在列表里的 api_key 都会从这两面漏出去。"""
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {"room_labels": "客厅,主卧", "lambda_map": {"api_key": PLANTED}})
    r = _run("--file", cfg, "--src-root", src)
    rows = _row(r.stdout)
    assert "客厅" not in r.stdout and "主卧" not in r.stdout        # 实体名键 ⇒ 遮
    assert PLANTED not in r.stdout                                  # 结构化值 ⇒ 遮（键名并不敏感）
    assert "<掩码" in rows["room_labels"] and "<掩码" in rows["lambda_map"]


def test_positive_control_scalars_are_still_echoed(tmp_path):
    """反空集腿：掩码面⛔ 等于"什么都不印"。数字与布尔必须照样回显，
    否则上面三条「没打出来」的断言全都证明不了任何事（在册判例：永远不红的闸＝测不出收益的闸）。"""
    src = _mk_tree(tmp_path)
    cfg = _write_cfg(tmp_path, {"beta_count": 4242, "alpha_flag": True, "delta_ratio": 0.25})
    r = _run("--file", cfg, "--src-root", src)
    rows = _row(r.stdout)
    assert "value=4242" in rows["beta_count"]
    assert "value=true" in rows["alpha_flag"]
    assert "value=0.25" in rows["delta_ratio"]
    assert "掩码" not in r.stdout


def test_mask_fingerprint_is_the_raw_value_body(tmp_path):
    """指纹口径＝`sha256(值本体，不含换行、不含引号)[:8]`，与跨席位复现用的是同一把尺。

    形状串 `AAA9AAAAAAAAAAAA99999999999` 是**先在屏上量出来**再钉的，⛔ 不是推的：
    旧口径按 JSON 串算，len 会多算两个引号（27 报成 29）。
    """
    got = _mod().mask(PLANTED)
    assert f"sha8={hashlib.sha256(PLANTED.encode()).hexdigest()[:8]}" in got
    assert f"len={len(PLANTED)}" in got
    assert PLANTED not in got
    assert got.split("shape=")[1].split(" sha8=")[0] == "AAA9AAAAAAAAAAAA99999999999"


# ─────────────────────────── 尺塌要响亮 / 输入面边界 ───────────────────────────

def test_missing_env_map_exits_3_and_prints_no_readings(tmp_path):
    src = _mk_tree(tmp_path, env_block="")
    cfg = _write_cfg(tmp_path, {"beta_count": 9})
    r = _run("--file", cfg, "--src-root", src)
    assert r.returncode == 3
    assert "env_map" in r.stdout and "DRIFT_FILE" not in r.stdout


def test_empty_env_map_exits_3(tmp_path):
    """env_map 存在但是**空 dict** ⇒ 整条 env 面读成「都没有通道」，是一份看起来正常的假读数。"""
    src = _mk_tree(tmp_path, env_block=_ENV_EMPTY)
    cfg = _write_cfg(tmp_path, {"beta_count": 9})
    r = _run("--file", cfg, "--src-root", src)
    assert r.returncode == 3
    assert "DRIFT_FILE" not in r.stdout


def test_duplicated_env_map_exits_3(tmp_path):
    """两枚同名赋值＝尺不知道取哪枚，静默取第一枚会给出看起来正常的假读数。"""
    src = _mk_tree(tmp_path, env_block=_ENV_TWO)
    cfg = _write_cfg(tmp_path, {"beta_count": 9})
    r = _run("--file", cfg, "--src-root", src)
    assert r.returncode == 3
    assert "命中 2 处" in r.stdout and "DRIFT_FILE" not in r.stdout


def test_missing_routes_file_exits_3(tmp_path):
    src = _mk_tree(tmp_path, routes=False)
    cfg = _write_cfg(tmp_path, {"beta_count": 9})
    r = _run("--file", cfg, "--src-root", src)
    assert r.returncode == 3 and "DRIFT_FILE" not in r.stdout


def test_config_file_boundary_exits_2(tmp_path):
    src = _mk_tree(tmp_path)
    missing = os.path.join(str(tmp_path), "nope.json")
    assert _run("--file", missing, "--src-root", src).returncode == 2
    arr = _write_cfg(tmp_path, [1, 2])
    r = _run("--file", arr, "--src-root", src)
    assert r.returncode == 2 and "not_a_json_object" in r.stdout
    junk = os.path.join(str(tmp_path), "junk.json")
    with open(junk, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert _run("--file", junk, "--src-root", src).returncode == 2
