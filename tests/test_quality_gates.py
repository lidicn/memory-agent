"""质量门禁的 pytest 钩子 —— 复制到你的 `tests/` 下即可生效。

它做的事只有一件：**把 `homesdk.gates` 的报告转成一次失败的测试**，
让「改坏了」在 CI 里红，而不是在评审时靠人眼。

放在 `tests/` 还是 `.github/workflows/` 都一样——门禁的强度来自「它一定会跑」，
所以请把它挂在你已有的那条 `pytest` 命令的路径上。

环境变量：
  GATES_SMOKE=1    额外跑 import 冒烟。**只在部署镜像里开**（见 README 第三节）。
  GATES_REQUIRE=1  门禁必须真跑：缺工具或扫描根不对一律红，不许静默 skip。
                   （容器权威回归里 homesdk 没装、REPO_ROOT 落在 /tmp，
                    默认口径会把「没跑」报成「跑过且干净」——这一位就是堵这个洞。）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ↓ 按本文件相对仓库根的实际深度改一层（`tests/x.py` → parents[1]）
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 「门禁必须真跑」：CI/容器权威回归里置 1，本地随手跑可以不管。
GATES_REQUIRE = os.getenv("GATES_REQUIRE") == "1"

pytestmark = pytest.mark.skipif(
    os.getenv("GATES_DISABLE") == "1",
    reason="显式关闭门禁只允许临时逃生，且必须在本轮 MR 里说明理由",
)


def _report():
    try:
        from homesdk.gates import GateConfig, scan_repo
    except ImportError:
        if GATES_REQUIRE:
            pytest.fail(
                "GATES_REQUIRE=1 但 homesdk 未安装：这一路 pytest 根本没执行门禁，"
                "结果里的绿色是假的。要么把 homesdk 装进该环境，要么别置这一位。"
            )
        pytest.skip("homesdk 未安装（门禁工具，非项目运行时依赖）")

    if not (REPO_ROOT / ".gates.toml").is_file():
        if GATES_REQUIRE:
            pytest.fail(
                f"GATES_REQUIRE=1 但 {REPO_ROOT} 下没有 .gates.toml："
                "扫描根不是仓库根，门禁会扫出一堆不存在的路径（或零路径）而不是真实违规。"
            )
        pytest.skip(f"{REPO_ROOT} 不是仓库根（无 .gates.toml），门禁不适用")

    config = GateConfig.load(REPO_ROOT)
    return scan_repo(
        REPO_ROOT,
        config,
        use_baseline=True,
        with_smoke=os.getenv("GATES_SMOKE") == "1",
    )


def test_no_new_gate_violations():
    """新增违规一律红；`.gates-baseline.txt` 里的存量放行，但只准减少。"""
    report = _report()
    lines = [v.render() for v in report.active]
    detail = "\n".join(lines[:40]) + ("\n… 其余略" if len(lines) > 40 else "")
    assert not report.active, (
        f"新增 {len(report.active)} 条门禁违规（error {report.error_count} / warn {report.warn_count}）。\n"
        f"要么修掉，要么在 `.gates-baseline.txt` 里逐条写明为什么放行。\n{detail}"
    )


def test_baseline_shrinks_when_you_fix_things():
    """修好了却不删基线条目 = 红。这条测试存在的唯一理由是防止基线肥化。"""
    report = _report()
    assert not report.stale, (
        f"{len(report.stale)} 条基线条目已不再命中，请从 `.gates-baseline.txt` 删掉：\n"
        + "\n".join(report.stale)
    )
