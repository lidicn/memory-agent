"""视觉识别服务：go2rtc 取帧 + doubao 多模态识别 + 光线门槛与节流

对应 docs/vision-behavior-spec.md。职责边界：
- 本服务是「何时调 VLM」节流策略的唯一持有者（spec §2）；
- 光线门槛（房间开灯才轮询）位于节流链**最前**，先于冷却/去重/上限，
  在拉帧之前拦截，省 go2rtc 4~13s 等待与 VLM 调用费用；
- TV 上报的身份直接采信（端侧 ArcFace），无 TV 房间由 VLM 输出外观描述。

失败语义（spec §10）：豆包挂了只影响「在干嘛」，不得影响事件入库与既有功能；
任何失败都降级为缺数据（status=vlm_failed 落库留痕），不抛出到请求链路。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any

import httpx

from .face_node_registry import FaceNodeRegistry
from .store import now_local

_logger = logging.getLogger(__name__)

# 豆包失败退避：60s 起指数翻倍，上限 30 分钟
_BACKOFF_BASE_S = 60
_BACKOFF_MAX_S = 1800
# 房间灯态查询缓存 TTL（秒），避免高频触发打爆 HA REST
_LIGHT_CACHE_TTL_S = 10
_ON_STATES = frozenset({"on", "open", "playing"})

# ── 视觉识别提示词预设（供 MCP / 内置 LLM 共用）─────────────────────────────
# 调优集中在此，避免散落到多处。custom 由调用方自由输入。
_PROMPT_PRESETS: dict[str, str] = {
    "people": (
        "这是家庭监控画面。请观察并回答：\n"
        "1) 画面中有几个人；\n"
        "2) 每个人在做什么（动作/姿态/互动对象）；\n"
        "3) 画面光线如何（明亮/昏暗）；\n"
        "4) 一句话概括当前场景。\n"
        "只输出简洁中文结论，不要输出 JSON 或多余解释。"
    ),
    "security": (
        "这是家庭监控画面，请做安全巡检。判断是否存在需要关注的情况：\n"
        "陌生人、非法入侵、人员摔倒、危险行为、烟雾/火情、门窗异常、遗留可疑物品等。\n"
        "若一切正常，请说明『未发现异常』并简述画面；若有异常，请明确描述异常类型、位置与严重程度。\n"
        "只输出简洁中文结论。"
    ),
    "object": (
        "这是家庭监控画面。请识别画面中是否有：宠物（猫/狗等）、快递包裹、或特定物品（如钥匙、包、鞋）。\n"
        "若有，请描述其种类、位置与状态；若没有，请说明『未发现上述物品』。\n"
        "只输出简洁中文结论。"
    ),
}


class VisionService:
    """多模态行为识别中枢。同步实现 + to_thread 包装，巡检为 asyncio 常驻任务。"""

    def __init__(self, config, store, ha) -> None:
        self.config = config
        self.store = store
        self.ha = ha
        # 人脸识别节点池（ArcFace 可插拔）。runtime 会注入共享实例；
        # 兜底：此处自建一个独立实例，保证不直接依赖 runtime。
        self.face: FaceNodeRegistry | None = FaceNodeRegistry()
        # 运行态（进程内，不落盘）
        self._last_call: dict[str, float] = {}        # room -> monotonic
        self._hour_calls: dict[tuple, int] = {}       # (room, 'MMDDHH') -> count
        self._backoff_until: dict[str, float] = {}    # room -> monotonic
        self._fail_streak: dict[str, int] = {}        # room -> 连续失败次数
        self._light_cache: dict[str, tuple] = {}      # entity_id -> (monotonic, on|None)
        self._skip_counts: dict[str, dict] = {}       # room -> {reason: n}
        self._last_result: dict[str, dict] = {}       # room -> 最近一次结果摘要
        self._last_cleanup_day: str = ""
        self._patrol_task: asyncio.Task | None = None

    # ── 配置便捷访问 ──────────────────────────────────────────────────────

    @property
    def cameras(self) -> list[dict]:
        return [c for c in (self.config.vision_cameras or []) if isinstance(c, dict)]

    def camera_for_room(self, room: str) -> dict | None:
        for c in self.cameras:
            if c.get("room") == room:
                return c
        return None

    def reconfigure(self, config) -> None:
        self.config = config

    # ── 光线门槛 ──────────────────────────────────────────────────────────

    def _light_entities_for(self, room: str, camera: dict | None) -> list[str]:
        """房间参与光线门槛判定的实体列表。

        优先级：UI 显式勾选的 light_entities > 房间注册表下全部 light.* 实体。
        注意自动回退**只认 light.* 域**：switch.* 里混有大量非照明开关
        （摄像头自身开关、指示灯开关、水泵等，见起居室注册表），
        盲目纳入会让门槛失效。墙壁开关类照明（如起居室
        switch.lumi_..._on_p_2_1）由用户在 UI 显式勾选，显式勾选优先级最高。"""
        camera = camera or self.camera_for_room(room)
        configured = [e for e in ((camera or {}).get("light_entities") or []) if e]
        if configured:
            return configured
        room_cfg = (self.config.rooms or {}).get(room) or {}
        entities = room_cfg.get("entities", {}) if isinstance(room_cfg, dict) else {}
        return sorted(e for e in entities if str(e).startswith("light."))

    def _light_candidates(self, room: str) -> list[str]:
        """UI 勾选候选：light.* + switch.*（用户自行甄别哪些是照明）。"""
        room_cfg = (self.config.rooms or {}).get(room) or {}
        entities = room_cfg.get("entities", {}) if isinstance(room_cfg, dict) else {}
        return sorted(
            e for e in entities
            if str(e).startswith(("light.", "switch."))
        )

    def _light_on(self, entity_id: str) -> bool | None:
        """单灯状态（带缓存）。None = HA 不可达/实体不存在（无法判定）。"""
        now = time.monotonic()
        cached = self._light_cache.get(entity_id)
        if cached and now - cached[0] < _LIGHT_CACHE_TTL_S:
            return cached[1]
        on: bool | None = None
        try:
            state = self.ha.get_state(entity_id)
            if isinstance(state, dict):
                on = str(state.get("state", "")).strip().lower() in _ON_STATES
        except Exception:
            on = None
        self._light_cache[entity_id] = (now, on)
        return on

    def room_light_state(self, room: str, camera: dict | None = None) -> dict:
        """房间光线门槛判定。

        返回 {gate_on: bool|None, lights: [...], degraded: bool, reason: str}：
        - gate_on=True  有灯亮 → 放行
        - gate_on=False 灯全关 → 拦截（环境暗，画面无价值）
        - gate_on=None  无法判定（未配置灯实体 / HA 不可达）→ 放行（fail-open）
        """
        entities = self._light_entities_for(room, camera)
        if not entities:
            return {
                "gate_on": None, "lights": [], "degraded": False,
                "reason": "no_lights_configured",
            }
        lights, any_on, degraded = [], False, False
        for eid in entities:
            on = self._light_on(eid)
            if on is None:
                degraded = True
            elif on:
                any_on = True
            lights.append({"entity_id": eid, "on": on})
        if degraded and not any_on:
            # 有查询失败且没有确认亮着的灯：无法确信「全暗」，按 fail-open 放行
            return {"gate_on": None, "lights": lights, "degraded": True,
                    "reason": "ha_unreachable"}
        return {
            "gate_on": bool(any_on), "lights": lights, "degraded": False,
            "reason": "ok" if any_on else "all_off",
        }

    # ── 节流闸门（spec §6，光线门槛置首）──────────────────────────────────

    @staticmethod
    def _hour_bucket() -> str:
        return now_local().strftime("%m%d%H")

    def _hour_count(self, room: str) -> int:
        return self._hour_calls.get((room, self._hour_bucket()), 0)

    def _bump_hour(self, room: str) -> None:
        key = (room, self._hour_bucket())
        self._hour_calls[key] = self._hour_calls.get(key, 0) + 1

    def _bump_skip(self, room: str, reason: str) -> None:
        self._skip_counts.setdefault(room, {})
        self._skip_counts[room][reason] = self._skip_counts[room].get(reason, 0) + 1

    def _backoff_remaining(self, room: str) -> float:
        return max(0.0, self._backoff_until.get(room, 0) - time.monotonic())

    # ── go2rtc 取帧 ──────────────────────────────────────────────────────

    def fetch_frame(
        self, stream: str, timeout: float = 30.0, auth: tuple | None = None, retries: int = 2
    ) -> tuple[bytes, int]:
        """从 go2rtc 拉单帧 JPEG，返回 (bytes, 耗时ms)。米家关键帧间隔长，实测 4~13s。

        auth 可由测试接口传入表单当前值（未保存也能测）。
        retries 仅对「传输层瞬断」(超时 / 连接被重置 / 连接被拒) 重试，
        401/404 等 HTTP 状态错误不重试。"""
        stream = (stream or "").strip()
        base = (self.config.go2rtc_base_url or "").rstrip("/")
        url = f"{base}/api/frame.jpeg?src={urllib.parse.quote(stream)}"
        if auth is None:
            auth = (
                (self.config.go2rtc_user, self.config.go2rtc_pass)
                if self.config.go2rtc_user else None
            )
        _logger.info(
            "go2rtc fetch_frame stream=%s url=%s auth_user=%s has_pass=%s",
            stream, url, (auth or (None, None))[0], bool((auth or (None, None))[1]),
        )
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                t0 = time.monotonic()
                resp = httpx.get(url, auth=auth, timeout=timeout)
                _logger.info("go2rtc response stream=%s status=%s", stream, resp.status_code)
                resp.raise_for_status()
                if not resp.content:
                    raise RuntimeError("go2rtc 返回空帧（0 字节）")
                return resp.content, int((time.monotonic() - t0) * 1000)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # 传输层瞬断：容器↔go2rtc 经宿主网络偶有抖动，退避后重试
                last_err = exc
                if attempt < retries:
                    _logger.warning(
                        "go2rtc 取帧第 %d 次失败（%s），%ss 后重试",
                        attempt + 1, type(exc).__name__, 0.5 * (attempt + 1),
                    )
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise

    # ── 多模态 LLM（doubao2api）──────────────────────────────────────────

    def _vlm_prompt(self, room: str, persons: list[dict] | None) -> str:
        time_str = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d %H:%M")
        if persons:
            named = "、".join(
                f"{p.get('name')}（{p.get('score', '')}）" for p in persons if p.get("name")
            ) or "无"
            return (
                f"你是家庭监控画面分析助手。这是{room}的摄像头画面，时间{time_str}。\n"
                f"画面中已识别到：{named}。人数应为{len(persons)}人。\n"
                '请只输出如下 JSON，不要输出其他内容：\n'
                '{"persons":[{"identity":"<上列名字或\'未识别\'>","action":"<10-20字动作描述>",'
                '"posture":"坐/站/躺/走","interaction":"<与谁互动或\'无\'>","confidence":0.0-1.0}],'
                '"scene":"<一句话场景概括>","snapshot_quality":"good|dim|occluded"}\n'
                "注意：不要猜测未列出的人的身份；画面模糊时 confidence 调低。"
            )
        members = self.store.list_members()
        named = [m for m in members if m.get("name")]
        ref = "、".join(m["name"] for m in named) if named else "家庭成员若干"
        # 已知成员外观档案摘要，供 VLM 直接给出候选身份（P1：第二路信号）
        profiles = []
        for m in named:
            ap = m.get("appearance_json")
            try:
                ap = json.loads(ap) if isinstance(ap, str) else ap
            except Exception:
                ap = None
            if isinstance(ap, dict) and any(
                ap.get(k) for k in ("approx_age", "gender", "clothing", "hair")
            ):
                bits = []
                if ap.get("approx_age"):
                    bits.append(f"约{ap['approx_age']}岁")
                if ap.get("gender"):
                    bits.append(ap["gender"])
                if ap.get("clothing"):
                    bits.append(f"穿{ap['clothing']}")
                if ap.get("hair"):
                    bits.append(f"{ap['hair']}")
                profiles.append(f"{m['name']}（{'，'.join(bits)}）")
        profile_text = "；".join(profiles) if profiles else "（暂无成员外观档案，先只描述外观）"
        return (
            f"你是家庭监控画面分析助手。这是{room}的摄像头画面，时间{time_str}。\n"
            f"家庭成员候选：{ref}。\n"
            f"已知成员外观档案：{profile_text}。\n"
            "对画面中每个人：先描述外观与动作，再在候选成员中尝试匹配身份。"
            "只输出 JSON，不要输出其他内容：\n"
            '{"persons":[{"approx_age":<数字>,"gender":"male|female",'
            '"clothing":"<上衣/下装/配饰>","hair":"<发型>",'
            '"action":"<动作>","posture":"坐/站/躺/走","confidence":0.0-1.0,'
            '"identity":"<匹配到的成员名或\'未识别\'>","match_confidence":0.0-1.0}],'
            '"scene":"<一句话>"}\n'
            "identity 仅在外观与某成员档案高度吻合时填写其名字，否则填'未识别'。"
        )

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从 VLM 输出中容错提取 JSON（剥 markdown 围栏 / 前后杂文）。"""
        if not text:
            return None
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def vlm_analyze(
        self, frame: bytes, prompt: str, *,
        base_url: str | None = None, api_key: str | None = None,
        model: str | None = None, endpoint_path: str | None = None,
    ) -> tuple[str, int]:
        """调多模态 VLM（OpenAI 兼容）。端点路径由 endpoint_path / vlm_endpoint_path
        配置决定，默认 /v1/chat/completions；doubao2api 可填 /v1/images/analyses 回退。
        返回 (文本, 耗时ms)。

        base_url/api_key/model/endpoint_path 可由测试接口传入表单当前值（未保存也能测）。"""
        base = (base_url or self.config.vlm_base_url or "").rstrip("/")
        path = (endpoint_path or self.config.vlm_endpoint_path or "/v1/chat/completions").strip()
        if not path.startswith("/"):
            path = "/" + path
        b64 = base64.b64encode(frame).decode()
        payload = {
            "model": model or self.config.vlm_model or "doubao",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64," + b64}},
                ],
            }],
        }
        headers = {"Content-Type": "application/json"}
        key = api_key if api_key is not None else self.config.vlm_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        t0 = time.monotonic()
        last_err: Exception | None = None
        for _ in range(max(1, int(self.config.vlm_max_retries or 1))):
            try:
                resp = httpx.post(
                    f"{base}{path}",
                    json=payload, headers=headers,
                    timeout=float(self.config.vlm_timeout_s or 25),
                )
                resp.raise_for_status()
                data = resp.json()
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                if not text.strip():
                    raise RuntimeError("VLM 返回空内容（会话可能失效）")
                return text, int((time.monotonic() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        raise RuntimeError(f"VLM 调用失败: {last_err}")

    # ── 快照 ─────────────────────────────────────────────────────────────

    def _save_snapshot(self, room: str, frame: bytes) -> str:
        now = now_local(self.config.tz_offset_hours)
        day = now.strftime("%Y-%m-%d")
        rel_dir = os.path.join("snapshots", day)
        abs_dir = os.path.join(self.config.data_dir, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)
        filename = f"{room}_{now.strftime('%Y%m%d_%H%M%S')}.jpg"
        abs_path = os.path.join(abs_dir, filename)
        with open(abs_path, "wb") as f:
            f.write(frame)
        return f"/data/{rel_dir}/{filename}"

    def _cleanup_snapshots(self) -> int:
        """按保留天数清理过期快照文件并置空对应行。返回清理文件数。"""
        days = int(self.config.vision_snapshot_retention_days or 0)
        if days <= 0:
            return 0
        from datetime import timedelta

        cutoff = (now_local(self.config.tz_offset_hours) - timedelta(days=days)).strftime("%Y-%m-%d")
        removed = 0
        for path in self.store.clear_behavior_snapshots(cutoff):
            try:
                base = self.config.data_dir.rstrip("/")
                # 存的是 /data/... 形式，映射回实际挂载目录
                rel = path[len("/data/"):] if path.startswith("/data/") else path
                os.remove(os.path.join(base, rel))
                removed += 1
            except OSError:
                pass
        return removed

    # ── 外观→成员 匹配（spec §3 P0：从外观下结论是谁）──────────────────────

    @staticmethod
    def _norm_age(v) -> int | None:
        """把年龄规整为整数；支持区间字符串如 "30-35" → 取中点 32。"""
        if v is None:
            return None
        nums = re.findall(r"\d+", str(v))
        if not nums:
            return None
        if len(nums) >= 2:
            return (int(nums[0]) + int(nums[1])) // 2
        return int(nums[0])

    # 性别归一：中/英写法都映射到 male/female/other，便于档案与 VLM 输出对齐
    _GENDER_MAP = {
        "男": "male", "女": "female", "其他": "other", "中性": "other",
        "man": "male", "woman": "female", "male": "male", "female": "female",
        "other": "other",
    }

    @classmethod
    def _norm_gender(cls, v) -> str:
        if not v:
            return ""
        return cls._GENDER_MAP.get(str(v).strip().lower(), str(v).strip().lower())

    @staticmethod
    def _appearance_tokens(s) -> set[str]:
        """把穿搭/发型描述切成可比较的词元集合。

        兼容两种来源：
        - VLM 直接输出的字符串（如 "深色上衣, 牛仔裤"）；
        - 前端结构化档案的 dict（如 {top_color, top_style, ...}），此处拼成字符串再分词。
        """
        if isinstance(s, dict):
            s = ", ".join(str(v) for v in s.values() if v not in (None, ""))
        if not isinstance(s, str):
            return set()
        s = s.replace("，", ",").replace("、", ",").replace("；", ",").replace(";", ",")
        return {tok.strip() for tok in s.split(",") if tok.strip()}

    @classmethod
    def resolve_appearance_to_member(
        cls, appearance: dict, members: list[dict]
    ) -> dict | None:
        """把 VLM 外观描述匹配到已知成员外观档案。

        评分维度（0~1）：性别(±0.4，错配强负分) + 年龄段(≤0.3) + 穿搭关键词重叠(≤0.2)
        + 发型关键词重叠(≤0.1)。返回分最高的 {member_id, name, score}；无候选或全低分返回 None。
        阈值判定在调用方做（建议 ≥0.6），此处只返回最优，便于多路信号（VLM identity）择优。
        """
        if not isinstance(appearance, dict) or not members:
            return None
        best: dict | None = None
        for m in members:
            try:
                ap = m.get("appearance_json")
                ap = json.loads(ap) if isinstance(ap, str) else ap
            except Exception:
                ap = None
            if not isinstance(ap, dict):
                continue
            score = 0.0
            g1 = cls._norm_gender(appearance.get("gender"))
            g2 = cls._norm_gender(ap.get("gender"))
            if g1 and g2:
                score += 0.4 if g1 == g2 else -0.5
            a1, a2 = cls._norm_age(appearance.get("approx_age")), cls._norm_age(ap.get("approx_age"))
            if a1 is not None and a2 is not None:
                diff = abs(a1 - a2)
                score += max(0.0, 0.3 - diff / 50.0)  # 同龄 +0.3，约每差 15 岁 -0.1
            c1, c2 = cls._appearance_tokens(appearance.get("clothing")), cls._appearance_tokens(ap.get("clothing"))
            if c1 and c2:
                union = c1 | c2
                score += 0.2 * (len(c1 & c2) / max(1, len(union)))
            h1, h2 = cls._appearance_tokens(appearance.get("hair")), cls._appearance_tokens(ap.get("hair"))
            if h1 and h2:
                union = h1 | h2
                score += 0.1 * (len(h1 & h2) / max(1, len(union)))
            bc1, bc2 = cls._appearance_tokens(appearance.get("body_type_custom")), cls._appearance_tokens(ap.get("body_type_custom"))
            if bc1 and bc2:
                union = bc1 | bc2
                score += 0.1 * (len(bc1 & bc2) / max(1, len(union)))
            bt1, bt2 = appearance.get("body_type"), ap.get("body_type")
            if bt1 and bt2 and str(bt1).strip() == str(bt2).strip():
                score += 0.1
            w1, w2 = cls._appearance_tokens(appearance.get("weight")), cls._appearance_tokens(ap.get("weight"))
            if w1 and w2:
                union = w1 | w2
                score += 0.05 * (len(w1 & w2) / max(1, len(union)))
            score = max(0.0, min(1.0, score))
            if best is None or score > best["score"]:
                best = {"member_id": m.get("id"), "name": m.get("name"), "score": score}
        return best

    # ── 人脸识别节点池（ArcFace 可插拔，统一识别路由 + VLM 降级）──────────────

    def recognize_face(
        self, image: bytes | str, room: str | None = None, min_conf: float | None = None
    ) -> dict | None:
        """统一识别路由：选权重最高的在线 Arcface 节点转发识别。

        入参 ``image`` 为帧字节（自动转 base64 data URL）或已是字符串（b64/data URL）。
        返回 ``{name, confidence, node, via:"arcface"}``；
        无在线节点 / 调用失败 / 置信度不足 → 返回 ``None``，由调用方降级 VLM（不报错）。
        """
        reg = self.face
        if reg is None:
            return None
        min_conf = min_conf if min_conf is not None else float(self.config.face_min_conf or 0.6)
        timeout = float(self.config.face_node_timeout_s or 3.0)
        node = reg.select_node(room)
        if node is None:
            return None
        if isinstance(image, (bytes, bytearray)):
            payload_img = "data:image/jpeg;base64," + base64.b64encode(bytes(image)).decode()
        else:
            payload_img = image  # 已是 b64 / data URL
        url = (node.url or "").rstrip("/") + "/recognize"
        try:
            resp = httpx.post(
                url, json={"image": payload_img, "min_conf": min_conf}, timeout=timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            reg.mark_error(node.node_id, str(exc)[:200])
            return None
        # 兼容两种节点返回：{name, confidence} 或 {faces:[{name, confidence, box?}]}
        name, conf = None, 0.0
        if isinstance(data, dict):
            if data.get("faces"):
                faces = [f for f in data["faces"] if isinstance(f, dict)]
                if faces:
                    best_face = max(faces, key=lambda f: float(f.get("confidence", 0) or 0))
                    name, conf = best_face.get("name"), float(best_face.get("confidence", 0) or 0)
            else:
                name, conf = data.get("name"), float(data.get("confidence", 0) or 0)
        if not name or conf < min_conf:
            return None
        return {"name": name, "confidence": conf, "node": node.node_id, "via": "arcface"}

    def supplement_with_face(
        self, persons_out: list[dict], frame: bytes, room: str,
        min_conf: float | None = None,
    ) -> list[dict]:
        """VLM 之后补认：生物识别优先于 VLM 外观匹配。

        - 端侧已给身份（via=="face"，TV ArcFace）→ 直接采信，跳过；
        - 未识别 或 appearance_matched 置信 < 阈值 → 触发一次整帧 Arcface 识别；
        - 命中则覆盖该 person 的 name / via="arcface" / match_confidence，并关联 member_id。
        无在线节点 / 失败 → 保持 VLM 原结果（降级，不报错）。
        多待确认 person 时，Arcface 单帧返回最优人脸覆盖第一个（家庭单主体场景足够）。
        """
        need = [p for p in persons_out if p.get("via") != "face"]
        if not need:
            return persons_out  # 全部是端侧人脸，已为生物识别，无需补认
        if min_conf is None:
            min_conf = float(self.config.face_min_conf or 0.6)
        to_confirm = [
            p for p in need
            if (not p.get("name")) or str(p.get("name")) in ("未识别", "未识别成员", "")
            or (p.get("via") == "appearance_matched"
                and float(p.get("match_confidence") or 0) < min_conf)
        ]
        if not to_confirm:
            return persons_out
        res = self.recognize_face(frame, room, min_conf)
        if not res:
            return persons_out  # 降级到 VLM
        members = self.store.list_members() if self.store else []
        name_to_id = {m.get("name"): m.get("id") for m in members if m.get("name")}
        target = to_confirm[0]
        target["name"] = res["name"]
        target["via"] = "arcface"
        target["match_confidence"] = res["confidence"]
        target["node"] = res["node"]
        target["member_id"] = name_to_id.get(res["name"])
        return persons_out

    # ── 主流程：单房间识别 ────────────────────────────────────────────────

    def analyze_room(
        self,
        room: str,
        *,
        camera: dict | None = None,
        force: bool = False,
        trigger: str = "patrol",
        persons: list[dict] | None = None,
        device_ts: int | None = None,
    ) -> dict:
        """识别一个房间当前画面。同步实现，路由层用 asyncio.to_thread 包装。

        force=True 仅绕过光线门槛与冷却（调试用），仍受每小时硬上限与退避保护。
        """
        cfg = self.config
        if not cfg.vision_enabled:
            return {"ok": False, "skipped": True, "reason": "disabled"}
        camera = camera or self.camera_for_room(room)
        if not camera:
            return {"ok": False, "error": f"房间「{room}」未注册摄像头"}
        stream = camera.get("stream") or ""
        if not stream or not camera.get("enabled", True):
            return {"ok": False, "skipped": True, "reason": "camera_disabled"}

        gate_info: dict = {}
        # 1) 光线门槛：房间开灯才轮询（force 绕过，留审计）
        if cfg.vision_light_gate and camera.get("light_gate", True):
            gate_info = self.room_light_state(room, camera)
            if gate_info.get("gate_on") is False:
                self._bump_skip(room, "light_off")
                summary = {"ts": now_local(cfg.tz_offset_hours).isoformat(),
                           "skipped": True, "reason": "light_off",
                           "trigger": trigger}
                self._last_result[room] = summary
                return {"ok": False, "skipped": True, "reason": "light_off", "gate": gate_info}
        if force:
            trigger = "manual_bypass_gate" if trigger in ("manual", "patrol") else trigger

        # 2) 冷却
        now = time.monotonic()
        if not force:
            remain = cfg.vision_cooldown_s - (now - self._last_call.get(room, -1e9))
            if remain > 0:
                self._bump_skip(room, "cooldown")
                return {"ok": False, "skipped": True, "reason": "cooldown",
                        "retry_after_s": round(remain)}

        # 3) 每小时硬上限（force 也不豁免，防误用打爆豆包）
        if self._hour_count(room) >= int(cfg.vision_max_per_hour or 20):
            self._bump_skip(room, "max_per_hour")
            return {"ok": False, "skipped": True, "reason": "max_per_hour"}

        # 4) 失败退避（force 豁免：运维显式重试意图明确，流恢复后应能立即再试）
        #    不豁免会导致 outage 后房间被「假死」锁住最长 _BACKOFF_MAX_S(30min)，
        #    且连强制取帧也被 skip（handoff P1）。
        remain = self._backoff_remaining(room)
        if remain > 0 and not force:
            self._bump_skip(room, "backoff")
            return {"ok": False, "skipped": True, "reason": "backoff",
                    "retry_after_s": round(remain)}

        # 5) 拉帧（门槛全过之后才等 go2rtc 的 4~13s）
        try:
            frame, fetch_ms = self.fetch_frame(stream)
        except Exception as exc:  # noqa: BLE001
            self._register_failure(room, stream, trigger, f"go2rtc 取帧失败: {exc}")
            return {"ok": False, "error": f"go2rtc 取帧失败: {exc}"}

        # 6) 快照
        snapshot_path = ""
        try:
            snapshot_path = self._save_snapshot(room, frame)
        except Exception as exc:  # noqa: BLE001
            print(f"[Vision] 快照保存失败（不影响识别）: {exc}")

        # 7) VLM 识别（JSON 解析失败重试 1 次，spec §8.3）
        prompt = self._vlm_prompt(room, persons)
        text, latency_ms, parse_failed = "", 0, False
        try:
            text, latency_ms = self.vlm_analyze(frame, prompt)
            data = self._extract_json(text)
            if data is None and persons is None:
                # 8.3 外观模式：附「上次输出不是合法 JSON」重试一次
                text, latency_ms2 = self.vlm_analyze(
                    frame, prompt + "\n注意：上次输出不是合法 JSON，请严格只输出 JSON。"
                )
                latency_ms += latency_ms2
                data = self._extract_json(text)
                parse_failed = data is None
        except Exception as exc:  # noqa: BLE001
            self._register_failure(room, stream, trigger, str(exc),
                                   snapshot_path=snapshot_path, device_ts=device_ts)
            hint = "会话可能失效，请到 doubao2api 管理面板扫码重登"
            return {"ok": False, "error": f"{exc}（{hint}）"}

        # 8) 解析并落库
        vlm_persons = (data or {}).get("persons") or []
        scene = (data or {}).get("scene") or ""
        quality = (data or {}).get("snapshot_quality") or ""
        confidence = None
        actions: list[str] = []
        persons_out: list[dict] = []
        # 外观模式（无 TV 人脸）：预载成员档案，用于外观→成员匹配
        members = self.store.list_members() if persons is None else []
        known_names = {m.get("name") for m in members if m.get("name")}
        known_name_to_id = {m.get("name"): m.get("id") for m in members if m.get("name")}
        for p in vlm_persons:
            if not isinstance(p, dict):
                continue
            if persons:
                # TV 端 ArcFace 已给出身份（高置信），直接采信（spec §6 人脸优先）
                identity = p.get("identity") or "未识别"
                via = "face"
                match_conf = 0.0
                member_id = None
            else:
                # 外观模式：VLM identity 候选 + 代码侧外观匹配，取高置信者
                identity, via, match_conf, member_id = "未识别成员", "appearance", 0.0, None
                vlm_identity = (p.get("identity") or "").strip()
                vlm_match_conf = p.get("match_confidence")
                vlm_match_conf = float(vlm_match_conf) if isinstance(vlm_match_conf, (int, float)) else 0.0
                m = self.resolve_appearance_to_member(p, members)
                if m and m["score"] >= 0.6:
                    identity, member_id, match_conf, via = (
                        m["name"], m["member_id"], m["score"], "appearance_matched"
                    )
                elif vlm_identity and vlm_identity in known_names and vlm_match_conf >= 0.6:
                    identity, member_id, match_conf, via = (
                        vlm_identity, known_name_to_id.get(vlm_identity), vlm_match_conf, "appearance_matched"
                    )
            persons_out.append({
                "name": identity, "via": via, "detail": p,
                "match_confidence": match_conf, "member_id": member_id,
            })
            if isinstance(p.get("confidence"), (int, float)):
                confidence = max(confidence or 0, p["confidence"])
            if p.get("action"):
                actions.append(f"{identity} {p['action']}")
        action = "；".join(actions) if actions else "未检测到人"

        # 8.5) 生物识别补认（face_node_pool）：VLM 之后接 Arcface 节点池，
        # 命中则以生物识别结果覆盖 VLM 外观匹配（生物识别优先），无节点时降级 VLM。
        if self.face is not None and frame is not None:
            try:
                persons_out = self.supplement_with_face(persons_out, frame, room)
                # 覆写后重新聚合动作文案（名字可能已变化）
                actions = [
                    f"{p.get('name')} {p['action']}" for p in persons_out if p.get("action")
                ]
                action = "；".join(actions) if actions else "未检测到人"
            except Exception as exc:  # noqa: BLE001
                # 补认失败绝不影响主流程（VLM 结果照常落库）
                print(f"[Vision] 人脸补认异常（已忽略，保留 VLM 结果）: {exc}")

        status = "ok"
        if parse_failed:
            status = "vlm_failed"
        elif confidence is not None and confidence < 0.4:
            status = "low_confidence"

        event_id = self.store.insert_behavior_event({
            "room": room,
            "camera_src": stream,
            "persons": persons_out,
            "count": len(persons_out),
            "action": action,
            "scene": scene,
            "confidence": confidence,
            "appearance": None if persons else vlm_persons,
            "trigger": trigger,
            "vlm_latency_ms": latency_ms,
            "snapshot_path": snapshot_path,
            "raw_response": text,
            "status": status,
            "device_ts": device_ts,
        })

        # 9) 成功收尾：清退避、记调用
        self._last_call[room] = time.monotonic()
        self._fail_streak[room] = 0
        self._backoff_until.pop(room, None)
        self._bump_hour(room)
        self._last_result[room] = {
            "ts": now_local(cfg.tz_offset_hours).isoformat(),
            "event_id": event_id, "action": action, "scene": scene,
            "count": len(persons_out), "status": status,
            "latency_ms": latency_ms, "fetch_ms": fetch_ms,
            "snapshot_quality": quality, "trigger": trigger,
        }
        return {
            "ok": True, "event_id": event_id, "action": action, "scene": scene,
            "persons": persons_out, "status": status, "latency_ms": latency_ms,
            "snapshot_path": snapshot_path, "gate": gate_info,
        }

    def _register_failure(
        self, room: str, stream: str, trigger: str, error: str,
        snapshot_path: str = "", device_ts: int | None = None,
    ) -> None:
        """失败退避 + vlm_failed 留痕（spec §6.4：恢复后不回填）。"""
        streak = self._fail_streak.get(room, 0) + 1
        self._fail_streak[room] = streak
        wait = min(_BACKOFF_BASE_S * (2 ** (streak - 1)), _BACKOFF_MAX_S)
        self._backoff_until[room] = time.monotonic() + wait
        self._bump_hour(room)  # 失败也计入上限，避免死循环打爆上游
        self._last_call[room] = time.monotonic()
        try:
            self.store.insert_behavior_event({
                "room": room, "camera_src": stream, "persons": [], "count": 0,
                "action": "vlm_failed", "trigger": trigger,
                "snapshot_path": snapshot_path, "raw_response": error[:2000],
                "status": "vlm_failed", "device_ts": device_ts,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[Vision] 失败事件落库异常: {exc}")
        self._last_result[room] = {
            "ts": now_local(self.config.tz_offset_hours).isoformat(),
            "error": error[:300], "backoff_s": wait, "trigger": trigger,
        }

    # ── TV 人脸事件入口 ───────────────────────────────────────────────────

    def record_face_event(
        self, room: str, persons: list[dict], trigger: str, device_ts: int | None,
        camera: str | None = None,
    ) -> dict:
        """TV 端人脸事件（spec §5.1）。空 persons = 「房间没人了」，直接入库不调 VLM。"""
        if persons:
            # 有身份变化 → 触发一次识别（同步等 VLM 结果意义不大，走异步任务）
            asyncio.get_running_loop().create_task(
                asyncio.to_thread(
                    self.analyze_room, room,
                    trigger=trigger or "identity_change",
                    persons=persons, device_ts=device_ts,
                )
            )
            return {"accepted": True, "deduped": False, "vlm_dispatched": True}
        self.store.insert_behavior_event({
            "room": room, "camera_src": camera or (self.camera_for_room(room) or {}).get("stream", ""),
            "persons": [], "count": 0, "action": "房间无人",
            "trigger": trigger or "count_change", "device_ts": device_ts,
            "status": "ok",
        })
        return {"accepted": True, "deduped": False, "vlm_dispatched": False}

    # ── 无 TV 房间巡检 ───────────────────────────────────────────────────

    async def patrol_loop(self) -> None:
        """常驻巡检：每 vision_no_tv_interval_s 对所有 no_tv 摄像头跑一次识别。

        总开关关闭时空转；每日首次 tick 顺带清理过期快照。
        """
        print("[Vision] 巡检任务已启动")
        while True:
            try:
                interval = max(30, int(self.config.vision_no_tv_interval_s or 300))
                await asyncio.sleep(interval)
                if not self.config.vision_enabled:
                    continue
                today = now_local(self.config.tz_offset_hours).strftime("%Y-%m-%d")
                if self._last_cleanup_day != today:
                    self._last_cleanup_day = today
                    try:
                        removed = await asyncio.to_thread(self._cleanup_snapshots)
                        if removed:
                            print(f"[Vision] 清理过期快照 {removed} 个")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[Vision] 快照清理异常: {exc}")
                for camera in list(self.cameras):
                    if not camera.get("no_tv") or not camera.get("enabled", True):
                        continue
                    room = camera.get("room") or ""
                    try:
                        res = await asyncio.to_thread(
                            self.analyze_room, room, camera=camera, trigger="patrol"
                        )
                        if res.get("skipped"):
                            continue  # 冷却/门槛拦截属常态，静默
                    except Exception as exc:  # noqa: BLE001
                        print(f"[Vision] 巡检 {room} 异常: {exc}")
            except asyncio.CancelledError:
                print("[Vision] 巡检任务已停止")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[Vision] 巡检循环异常: {exc}")

    def start(self) -> None:
        if self._patrol_task is None or self._patrol_task.done():
            self._patrol_task = asyncio.create_task(self.patrol_loop())

    async def stop(self) -> None:
        task = self._patrol_task
        self._patrol_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # ── 状态（设置页运行状态块）──────────────────────────────────────────

    def status(self) -> dict:
        cfg = self.config
        rooms = []
        for camera in self.cameras:
            room = camera.get("room") or ""
            gate = self.room_light_state(room, camera)
            backoff = self._backoff_remaining(room)
            rooms.append({
                "room": room,
                "stream": camera.get("stream"),
                "enabled": bool(camera.get("enabled", True)),
                "no_tv": bool(camera.get("no_tv")),
                "light_gate": bool(camera.get("light_gate", True)) and bool(cfg.vision_light_gate),
                "gate_on": gate.get("gate_on"),
                "gate_reason": gate.get("reason"),
                "degraded": gate.get("degraded", False),
                "lights": gate.get("lights", []),
                "calls_this_hour": self._hour_count(room),
                "max_per_hour": int(cfg.vision_max_per_hour or 20),
                "cooldown_remaining_s": round(
                    max(0.0, cfg.vision_cooldown_s - (time.monotonic() - self._last_call.get(room, -1e9)))
                ),
                "backoff_remaining_s": round(backoff),
                "skips": dict(self._skip_counts.get(room, {})),
                "last_result": self._last_result.get(room),
            })
        return {
            "enabled": bool(cfg.vision_enabled),
            "light_gate": bool(cfg.vision_light_gate),
            "patrol_interval_s": int(cfg.vision_no_tv_interval_s or 300),
            "rooms": rooms,
        }

    # ── 体检（设置页测试按钮）────────────────────────────────────────────

    def test_camera(
        self, stream: str, *, go2rtc_user: str | None = None, go2rtc_pass: str | None = None,
    ) -> dict:
        """取一帧返回 base64 预览 + 耗时，供摄像头设置联调。

        凭据用表单当前值（未保存也能测）；表单没填则回退已保存配置。"""
        # 只有当表单同时提供了用户名和密码（密码不是掩码占位）时才用表单值；
        # 否则回退到已保存配置，避免「用户名明文、密码被掩码」导致传空密码鉴权失败。
        auth = None
        if go2rtc_user and go2rtc_pass is not None:
            auth = (go2rtc_user, go2rtc_pass)
        elif self.config.go2rtc_user:
            auth = (self.config.go2rtc_user, self.config.go2rtc_pass)
        try:
            frame, ms = self.fetch_frame(stream, auth=auth)
        except httpx.HTTPStatusError as exc:
            creds_hint = "已携带凭据" if auth else "未携带凭据（请检查是否填写并保存了 go2rtc 用户名/密码）"
            if exc.response.status_code == 401:
                raise RuntimeError(
                    f"go2rtc 鉴权失败(401)：流 '{stream}' {creds_hint}。"
                    "请在下方「识别频率与门槛」卡片填写 go2rtc 用户名/密码，点击「保存配置」后再试"
                ) from exc
            if exc.response.status_code == 404:
                raise RuntimeError(
                    f"go2rtc 流不存在(404)：'{stream}'。请确认 go2rtc 中已配置该流名"
                ) from exc
            raise RuntimeError(f"go2rtc 取帧失败({exc.response.status_code})：{exc.response.text[:200]}") from exc
        return {
            "size": len(frame),
            "latency_ms": ms,
            "preview": "data:image/jpeg;base64," + base64.b64encode(frame).decode(),
        }

    def test_llm(
        self, stream: str | None = None, *,
        go2rtc_user: str | None = None, go2rtc_pass: str | None = None,
        vlm_base_url: str | None = None, vlm_api_key: str | None = None,
        vlm_model: str | None = None, vlm_endpoint_path: str | None = None,
    ) -> dict:
        """端到端体检：取帧 → VLM 简单提问 → 返回回答与耗时。

        go2rtc / VLM 凭据均可用表单当前值覆盖（未保存也能测）。"""
        if not stream:
            cam = next((c for c in self.cameras if c.get("enabled", True)), None)
            if not cam:
                return {"ok": False, "error": "尚未注册任何启用的摄像头"}
            stream = cam.get("stream") or ""
        # 同 test_camera：密码为 None（掩码占位或未提供）时回退已保存配置
        auth = None
        if go2rtc_user and go2rtc_pass is not None:
            auth = (go2rtc_user, go2rtc_pass)
        elif self.config.go2rtc_user:
            auth = (self.config.go2rtc_user, self.config.go2rtc_pass)
        frame, fetch_ms = self.fetch_frame(stream, auth=auth)
        prompt = "这是家庭监控画面。简短回答：1)几个人 2)每人在做什么 3)画面光线如何。"
        text, ms = self.vlm_analyze(
            frame, prompt,
            base_url=vlm_base_url, api_key=vlm_api_key, model=vlm_model,
            endpoint_path=vlm_endpoint_path,
        )
        return {"ok": True, "stream": stream, "fetch_ms": fetch_ms,
                "latency_ms": ms, "answer": text[:2000]}

    # ── MCP / 内置 LLM 共用高层接口 ────────────────────────────────────────

    def list_cameras(self, only_enabled: bool = True) -> list[dict]:
        """返回对 LLM 友好的摄像头精简结构。"""
        out = []
        for c in self.cameras:
            if only_enabled and not bool(c.get("enabled", True)):
                continue
            out.append({
                "room": c.get("room") or "",
                "stream": c.get("stream") or "",
                "enabled": bool(c.get("enabled", True)),
                "no_tv": bool(c.get("no_tv")),
                "light_gate": bool(c.get("light_gate", True)),
            })
        return out

    def analyze_scene(
        self, room: str | None = None, stream: str | None = None,
        prompt: str | None = None, preset: str = "people",
        prompt_preset: str | None = None,
        bypass_limits: bool = True, include_preview: bool = False,
    ) -> dict:
        """取一帧 → VLM 识别 → 返回文字描述（默认不含图片，隐私优先）。

        供 MCP / 内置 LLM 调用：用户问「看看谁在客厅」「客厅有没有异常」时，
        由 LLM 选择 preset 或提供自定义 prompt。显式调用默认绕过光线门槛与
        每小时调用上限（bypass_limits=True），立即取帧。

        ``prompt_preset`` 是工具 schema 暴露给 LLM 的参数名，与 ``preset``
        同义；传入时以 ``prompt_preset`` 为准，保证 MCP / 内置 LLM 调用一致。

        失败遵循 spec §10：降级为缺数据文字说明，不抛出到请求链路。
        """
        # 统一 preset 别名：工具侧叫 prompt_preset，服务侧内部叫 preset
        if prompt_preset is not None:
            preset = prompt_preset

        # 1) 解析摄像头
        cam: dict | None = None
        if stream:
            cam = next((c for c in self.cameras if c.get("stream") == stream), None)
        if cam is None and room:
            key = room.strip().lower()
            cam = next(
                (c for c in self.cameras if (c.get("room") or "").strip().lower() == key),
                None,
            )
        if cam is None:
            cam = next((c for c in self.cameras if c.get("enabled", True)), None)
        if cam is None:
            return {"ok": False, "error": "尚未配置任何摄像头，请先在视觉识别设置页添加"}
        resolved_room = cam.get("room") or ""
        resolved_stream = cam.get("stream") or ""
        if not resolved_stream:
            return {"ok": False, "error": f"摄像头『{resolved_room}』未配置 go2rtc 流名"}

        # 2) 组装提示词
        if preset == "custom":
            effective_prompt = (prompt or "").strip() or _PROMPT_PRESETS["people"]
        elif prompt and prompt.strip():
            effective_prompt = prompt.strip()
        else:
            effective_prompt = _PROMPT_PRESETS.get(preset, _PROMPT_PRESETS["people"])

        # 3) 取帧（直接调用 fetch_frame 已绕过巡逻调度内的光线门槛/冷却）
        try:
            frame, fetch_ms = self.fetch_frame(resolved_stream)
        except httpx.HTTPStatusError as exc:
            creds_hint = "（请检查 go2rtc 用户名/密码是否已保存）" if exc.response.status_code == 401 else ""
            return {"ok": False, "error": f"go2rtc 取帧失败({exc.response.status_code}){creds_hint}",
                    "room": resolved_room, "stream": resolved_stream}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"go2rtc 取帧失败：{exc}",
                    "room": resolved_room, "stream": resolved_stream}

        # 4) VLM 识别
        try:
            text, vlm_ms = self.vlm_analyze(frame, effective_prompt)
            text = (text or "").strip()
            if not text:
                return {"ok": False, "error": "VLM 返回空内容（会话可能失效，请检查 VLM 配置）",
                        "room": resolved_room, "stream": resolved_stream,
                        "fetch_ms": fetch_ms}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"VLM 识别失败：{exc}",
                    "room": resolved_room, "stream": resolved_stream,
                    "fetch_ms": fetch_ms}

        # 4.5) 生物识别补认（face_node_pool）：有在线 Arcface 节点时整帧识别，
        # 命中则随结果一并返回 face_recognition；无节点/失败不报错（降级 VLM）。
        face_rec: dict | None = None
        if self.face is not None:
            try:
                face_rec = self.recognize_face(frame, resolved_room)
            except Exception as exc:  # noqa: BLE001
                print(f"[Vision] analyze_scene 人脸补认异常（已忽略）: {exc}")

        result = {
            "ok": True,
            "room": resolved_room,
            "stream": resolved_stream,
            "preset": preset,
            "description": text,
            "fetch_ms": fetch_ms,
            "vlm_ms": vlm_ms,
            "captured_at": now_local(self.config.tz_offset_hours).isoformat(timespec="seconds"),
            "bypassed_limits": bool(bypass_limits),
            "face_recognition": face_rec,
        }
        if include_preview:
            result["preview"] = "data:image/jpeg;base64," + base64.b64encode(frame).decode()
        return result
