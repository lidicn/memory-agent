#!/usr/bin/env python3
"""WO-MA-003 §十一-C: fence 测试的无 pytest 执行外壳。

职责：sys.path 指到 /app（或仓库 src）→ import 测试模块 →
依次调用三个 test_* 函数 → 每个打一行 PASS/FAIL + 末尾 exit code。

硬要求：
  ① 不改测试文件本身（sha256 冻结校验）
  ② 只用 stdlib（无 pip、无网络、无容器操作）
  ③ 能在容器里以 `python - < tools/run_fence_no_pytest.py` 或
     `docker exec -i memory-agent python /data/…` 跑通

用例数：4（1 个 sha256 冻结校验 + 3 个 test_* 函数）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import traceback
from pathlib import Path

# 冻结的测试文件 sha256（WO-MA-003 §十一-C，PM 实测冻结）
FROZEN_SHA256 = "3bf23a030412314403a7f65b03dde20904f09597c4be14bc1975e50e91dffef3"

# 测试模块名和相对路径
TEST_MODULE_NAME = "test_acp_session_cross_owner_denied"
TEST_REL_PATH = os.path.join("tests", TEST_MODULE_NAME + ".py")

# 三个 test_* 函数（顺序即执行顺序）
TEST_FUNCS = [
    "test_cancel_cross_owner_denied_before_run_id_check",
    "test_cancel_same_owner_with_run_id_passes_owner_check",
    "test_check_owner_fail_close",
]


def _repo_root() -> Path:
    """推断仓库根：优先 /app（容器），其次本文件上两级。"""
    if os.path.isdir("/app"):
        return Path("/app")
    # tools/run_fence_no_pytest.py → 上两级是仓库根
    return Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _import_test_module(repo_root: Path):
    """从文件路径 import 测试模块，不依赖 pytest。"""
    test_path = repo_root / TEST_REL_PATH
    if not test_path.exists():
        # 容器内可能在 /data 或其他位置，尝试 cwd
        test_path = Path.cwd() / TEST_REL_PATH
    if not test_path.exists():
        raise FileNotFoundError(f"找不到测试文件: {test_path}")

    # 测试模块内部有 sys.path.insert 指到 src，但保险起见也加
    src_path = repo_root / "src"
    if src_path.exists() and str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    spec = importlib.util.spec_from_file_location(TEST_MODULE_NAME, test_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {test_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[TEST_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    repo_root = _repo_root()
    test_path = repo_root / TEST_REL_PATH
    if not test_path.exists():
        test_path = Path.cwd() / TEST_REL_PATH

    passed = 0
    failed = 0
    total = 1 + len(TEST_FUNCS)  # sha256 校验 + 3 个测试

    print(f"fence runner: repo_root={repo_root}")
    print(f"fence runner: test_file={test_path}")
    print(f"fence runner: 用例数={total}")
    print("-" * 60)

    # 用例 1: sha256 冻结校验
    try:
        actual = _sha256_file(test_path)
        if actual.lower() == FROZEN_SHA256.lower():
            print(f"[PASS] 1/4 sha256 冻结校验 ({actual[:16]}…)")
            passed += 1
        else:
            print(f"[FAIL] 1/4 sha256 冻结校验: 期望 {FROZEN_SHA256[:16]}…, 实际 {actual[:16]}…")
            failed += 1
    except Exception as e:
        print(f"[FAIL] 1/4 sha256 冻结校验: {e}")
        failed += 1

    # import 测试模块
    try:
        module = _import_test_module(repo_root)
    except Exception as e:
        print(f"[FATAL] 无法 import 测试模块: {e}")
        traceback.print_exc()
        print("-" * 60)
        print(f"结果: {passed} passed, {failed + len(TEST_FUNCS)} failed (import 失败，剩余用例全部计失败)")
        return 1

    # 用例 2-4: 三个 test_* 函数
    for i, func_name in enumerate(TEST_FUNCS, start=2):
        func = getattr(module, func_name, None)
        if func is None:
            print(f"[FAIL] {i}/{total} {func_name}: 函数不存在")
            failed += 1
            continue
        try:
            func()
            print(f"[PASS] {i}/{total} {func_name}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {i}/{total} {func_name}: 断言失败 - {e}")
            failed += 1
        except Exception as e:
            print(f"[FAIL] {i}/{total} {func_name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("-" * 60)
    print(f"结果: {passed} passed, {failed} failed, {total} total")
    print(f"exit={'0' if failed == 0 else '1'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
