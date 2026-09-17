"""日志配置单测：确保 logger.info 真的能输出（此前全部被静默丢弃）。"""
import importlib
import logging
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

import memory_agent.logging_setup as ls  # noqa: E402


def test_configure_sets_level_and_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("MA_LOG_LEVEL", "DEBUG")
    level = ls.configure_logging(force=True)
    assert level == "DEBUG"
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert root.handlers


def test_module_logger_info_is_emitted(capsys, monkeypatch):
    """回归核心缺陷：未配置时 info 被丢弃；配置后应能落到 stdout。"""
    monkeypatch.setenv("MA_LOG_LEVEL", "INFO")
    # 先还原成"从未配置"的状态
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
    ls.configure_logging(force=True)

    log = logging.getLogger("memory_agent.probe")
    log.info("hello-info")
    out = capsys.readouterr().out
    assert "hello-info" in out


def test_invalid_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("MA_LOG_LEVEL", "NOT_A_LEVEL")
    level = ls.configure_logging(force=True)
    assert level == "INFO"
    assert logging.getLogger().level == logging.INFO


def test_noisy_libraries_are_quieted(monkeypatch):
    monkeypatch.setenv("MA_LOG_LEVEL", "DEBUG")
    ls.configure_logging(force=True)
    # DEBUG 下第三方仍被抬到 WARNING，避免刷屏
    assert logging.getLogger("httpx").level == logging.WARNING


def test_import_app_configures_logging(monkeypatch):
    """导入 app 模块即完成配置（uvicorn 以 memory_agent.app:combined_app 启动）。"""
    monkeypatch.setenv("MA_LOG_LEVEL", "INFO")
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
    import memory_agent.app  # noqa: F401

    # configure_logging 幂等（生产只导入一次）；测试里需重置标志才能验证"导入即配置"
    ls._configured = False
    importlib.reload(memory_agent.app)
    assert root.level <= logging.INFO
    assert root.handlers
