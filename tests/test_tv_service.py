"""电视截屏多模态识别（TVService）单元测试。

契约见 docs/电视截屏多模态识别功能_交接单.md。
不依赖真实 HA / 电视 / VLM / MQTT：HA 与 httpx 均用 fake 注入，
VLM 调用用 fake vision 替换，验证的是「状态解析 / URL 不缓存 / 错误映射 /
提示词分级 / 失败容忍」这类纯逻辑。
"""

import json
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_SRC))

from memory_agent.config import Config  # noqa: E402
from memory_agent.tv_service import (  # noqa: E402
    TVError,
    TVService,
    _as_bool,
    _extract_capture_ts,
    _parse_vlm_json,
    _split_app,
)


# ── 纯函数 ──────────────────────────────────────────────────────────────────

def test_split_app_name_and_package():
    assert _split_app("桌面 - com.mitv.tvhome") == ("桌面", "com.mitv.tvhome")
    assert _split_app("com.fongmi.android.tv") == ("", "com.fongmi.android.tv")
    assert _split_app("飞牛TV") == ("飞牛TV", "")
    assert _split_app("") == ("", "")


def test_extract_capture_ts():
    url = "http://192.168.2.238:6095/request?action=getResource&name=screenCapture&timestamp=1788442177909&opaque=abc"
    assert _extract_capture_ts(url) == 1788442177909
    assert _extract_capture_ts("http://x/y") is None


def test_parse_vlm_json_robust():
    assert _parse_vlm_json('prefix {"a":1} suffix') == {"a": 1}
    # 带 markdown 围栏
    assert _parse_vlm_json('```json\n{"title":"x"}\n```') == {"title": "x"}
    assert _parse_vlm_json("no json here") == {}
    assert _parse_vlm_json("") == {}


def test_as_bool():
    assert _as_bool(True) is True
    assert _as_bool("true") is True
    assert _as_bool("播放中") is True
    assert _as_bool("false") is False
    assert _as_bool(None, fallback=True) is True
    assert _as_bool(1) is True


# ── state / capture ───────────────────────────────────────────────────────

class _FakeHA:
    def __init__(self, state):
        self._state = state
        self.calls = 0

    def get_state(self, entity):
        self.calls += 1
        return self._state


def _ha_state(app_current="飞牛TV - com.fongmi.android.tv", capture="http://tv:6095/cap?timestamp=111",
              entity_state="playing"):
    return {
        "state": entity_state,
        "attributes": {
            "friendly_name": "lidicn的电视 播放控制",
            "app_current": app_current,
            "app_page": "播放页",
            "capture": capture,
            "capture_token": "tok",
        },
    }


@pytest.fixture
def svc():
    cfg = Config()
    cfg.tv_media_player_entity = "media_player.xiaomi_rmh1_6103_play_control"
    cfg.tv_mqtt_enabled = False  # 不连真实 MQTT
    ha = _FakeHA(_ha_state())
    return TVService(cfg, ha)


def test_state_parses_app_and_capture(svc):
    info = svc.state()
    assert info["app_name"] == "飞牛TV"
    assert info["app_package"] == "com.fongmi.android.tv"
    assert info["app_current"] == "飞牛TV - com.fongmi.android.tv"
    assert info["has_capture"] is True
    assert info["capture_ts"] == 111
    assert info["screen_on"] is True
    assert info["_capture_url"].startswith("http://tv:6095/cap")


def test_state_tv_off_detected(svc):
    svc.ha._state = _ha_state(entity_state="standby")
    info = svc.state()
    assert info["screen_on"] is False


def test_state_no_entity_configured():
    cfg = Config()
    cfg.tv_media_player_entity = ""
    svc = TVService(cfg, _FakeHA(None))
    with pytest.raises(TVError) as exc:
        svc.state()
    assert exc.value.code == "no_entity"


def test_state_ha_unreachable():
    svc = TVService(Config(), _FakeHA(None))
    with pytest.raises(TVError) as exc:
        svc.state()
    assert exc.value.code == "ha_unreachable"


class _FakeResp:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


# 真实 JPEG 至少上千字节；这里用 2KB 占位满足 capture 的「非 JPEG / 过短」判定。
_FAKE_JPEG = b"\xff\xd8\xff" + bytes(2000)


def test_capture_returns_fresh_frame(monkeypatch, svc):
    # 关键：capture 每次都重新调 HA 拿 URL，不缓存
    captured = {}
    def fake_get(url, timeout=15):
        captured["url"] = url
        return _FakeResp(_FAKE_JPEG)
    monkeypatch.setattr("memory_agent.tv_service.httpx.get", fake_get)
    frame, info = svc.capture()
    assert frame.startswith(b"\xff\xd8\xff")
    assert captured["url"] == "http://tv:6095/cap?timestamp=111"  # 用的是当次 URL


def test_capture_rejects_non_jpeg(monkeypatch, svc):
    monkeypatch.setattr("memory_agent.tv_service.httpx.get",
                        lambda url, timeout=15: _FakeResp(b"<html>404</html>"))
    with pytest.raises(TVError) as exc:
        svc.capture()
    assert exc.value.code == "bad_image"


def test_capture_tv_off(monkeypatch, svc):
    svc.ha._state = _ha_state(entity_state="off")
    with pytest.raises(TVError) as exc:
        svc.capture()
    assert exc.value.code == "tv_off"


# ── 提示词分级 ────────────────────────────────────────────────────────────

def test_prompt_includes_detail_fields():
    info = svc_state_only()
    brief = TVService(Config(), _FakeHA(None)).build_prompt(info, None, "brief")
    detailed = TVService(Config(), _FakeHA(None)).build_prompt(info, None, "detailed")
    assert "episode" not in brief
    assert "episode" in detailed


def svc_state_only():
    return {"app_current": "x", "entity_state": "playing", "media_playing": True}


# ── analyze 端到端（mock VLM）─────────────────────────────────────────────

class _FakeVision:
    def __init__(self, text):
        self._text = text
        self.called = 0

    def vlm_analyze(self, frame, prompt):
        self.called += 1
        return self._text, 1234


def test_analyze_returns_structured(monkeypatch, svc):
    monkeypatch.setattr("memory_agent.tv_service.httpx.get",
                        lambda url, timeout=15: _FakeResp(_FAKE_JPEG))
    vlm_text = json.dumps({
        "content_type": "movie", "title": "无间道",
        "playing": True, "paused": False,
        "description": "用户正在用飞牛TV看《无间道》",
    })
    svc.vision = _FakeVision(vlm_text)
    result = svc.analyze(include_tv_state=False, detail_level="brief", save_snapshot=False)
    assert result["ok"] is True
    assert result["content_type"] == "movie"
    assert result["title"] == "无间道"
    assert result["playing"] is True
    assert result["description"] == "用户正在用飞牛TV看《无间道》"
    assert result["app_name"] == "飞牛TV"
    # MQTT 未启用 → tv_state 为 None，不影响主链路
    assert result["tv_state"] is None
    assert "screenshot_url" in result


def test_analyze_unknown_content_type_fallback(monkeypatch, svc):
    monkeypatch.setattr("memory_agent.tv_service.httpx.get",
                        lambda url, timeout=15: _FakeResp(_FAKE_JPEG))
    svc.vision = _FakeVision(json.dumps({"content_type": "肥皂剧", "title": "", "description": "x"}))
    result = svc.analyze(include_tv_state=False, save_snapshot=False)
    assert result["content_type"] == "other"  # 模型自造词收敛到 other


def test_mqtt_disabled_returns_none(svc):
    assert svc.mqtt_state() is None


def test_tv_error_to_dict():
    e = TVError("tv_off", "电视未亮屏", hint="先开机")
    d = e.to_dict()
    assert d["code"] == "tv_off"
    assert d["hint"] == "先开机"
