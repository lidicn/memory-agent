#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HA客户端"""
import json
import threading
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional
import httpx

# 到 HA 的 HTTP 连接按 (地址, 令牌) 复用：每次请求新建 client 的建连固定成本约占单次
# 耗时 74%（第七轮审计 · HA 客户端每次请求新建连接），而采集是高频路径，跨主机时还要
# 加 TLS 握手。httpx.Client 的连接池本身线程安全，可以跨 asyncio.to_thread 工作线程共享。
#
# 有界 LRU（A2/A7 · P3-4）：key 含令牌，令牌轮换 / 多地址各占一格，无界池等于连接与
# fd 只增不减。容量取 8——必须 ≥2，因为「换令牌不复用旧凭证的连接」是锁死的行为
# （tests/test_vma_r7_seam_fixes.py），新旧两条要能同时在池里。
_CLIENT_POOL: "OrderedDict[tuple, httpx.Client]" = OrderedDict()
_POOL_LOCK = threading.Lock()
POOL_MAX_ENTRIES = 8


def _pooled_client(base_url: str, headers: Dict[str, str]) -> httpx.Client:
    key = (base_url, tuple(sorted(headers.items())))
    evicted = []
    with _POOL_LOCK:
        client = _CLIENT_POOL.get(key)
        if client is not None:
            _CLIENT_POOL.move_to_end(key)
            return client
        client = httpx.Client()
        _CLIENT_POOL[key] = client
        while len(_CLIENT_POOL) > POOL_MAX_ENTRIES:
            _, dead = _CLIENT_POOL.popitem(last=False)
            evicted.append(dead)
    # close 会在 socket 拆除上阻塞，不能占着 _POOL_LOCK 做——否则一个卡住的旧连接
    # 能把所有采集线程一起停住。被淘汰的是最久没被回看的那格，正在传输的概率极低；
    # 真撞上也只是单次 HA 读失败，走各调用点已有的"失败返回 None / 回退逐实体"路径。
    for dead in evicted:
        try:
            dead.close()
        except Exception as exc:  # noqa: BLE001 - 拆旧连接失败不影响新连接交付，但要留痕
            # 只印异常类型：httpx 的异常串里会带 URL，池 key 又含令牌头，日志不该抄过去。
            print(f"[HAClient] 淘汰连接关闭失败（不影响本次请求）: {type(exc).__name__}")
    return client


class HAClient:
    """Home Assistant客户端"""

    # 批次过大时 HA 侧会超时；这两个值是采集吞吐与稳定性的调节旋钮
    HISTORY_BATCH_SIZE = 10
    HISTORY_TIMEOUT = 60

    def __init__(self, config):
        self.config = config
        self.base_url = config.hass_server.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {config.hass_token}",
            "Content-Type": "application/json"
        }

    @contextmanager
    def _session(self) -> Iterator[httpx.Client]:
        """借一条共享连接到 HA。

        退出时**不关闭**：连接归 `_CLIENT_POOL` 管，单个请求没有权利拆掉别人正在用的
        池；配置热更新会换 base_url/令牌，自然落到新的池条目上，旧条目等到 LRU 淘汰
        时才由淘汰方关闭。
        """
        yield _pooled_client(self.base_url, self.headers)
    
    def get_states(self) -> Optional[list]:
        """批量拉取全部实体状态（单请求）。

        用于需要同时读多个实体的轻量轮询场景（如客厅盒侧 AI 事件接入），
        避免逐实体 N 次 HTTP 往返。失败返回 None，调用方可回退逐实体读取。
        """
        try:
            with self._session() as client:
                response = client.get(
                    f"{self.base_url}/api/states",
                    headers=self.headers,
                    timeout=30
                )
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return data
        except Exception as e:
            print(f"批量获取状态失败: {e}")
        return None

    def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """获取实体状态"""
        try:
            with self._session() as client:
                response = client.get(
                    f"{self.base_url}/api/states/{entity_id}",
                    headers=self.headers,
                    timeout=10
                )
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            print(f"获取状态失败: {e}")
        return None
    
    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        service_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """调用HA服务"""
        try:
            with self._session() as client:
                data = {
                    "entity_id": entity_id,
                    **(service_data or {})
                }
                response = client.post(
                    f"{self.base_url}/api/services/{domain}/{service}",
                    headers=self.headers,
                    json=data,
                    timeout=10
                )
                return {
                    "ok": response.status_code == 200,
                    "status_code": response.status_code
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """执行设备动作"""
        device = action.get('device')
        command = action.get('command')
        params = action.get('params', {})
        
        if not device or not command:
            return {"ok": False, "error": "缺少device或command"}
        
        # 映射命令到HA服务
        service_map = {
            "turn_on": ("homeassistant", "turn_on"),
            "turn_off": ("homeassistant", "turn_off"),
            "toggle": ("homeassistant", "toggle"),
            "set_temperature": ("climate", "set_temperature"),
            "set_brightness": ("light", "turn_on"),
            "set_volume": ("media_player", "volume_set"),
            "speak": ("tts", "speak"),
        }
        
        if command not in service_map:
            return {"ok": False, "error": f"不支持的命令: {command}"}
        
        domain, service = service_map[command]
        
        # 构建服务数据
        service_data = {}
        if command == "set_temperature":
            service_data["temperature"] = params.get("temperature")
            service_data["hvac_mode"] = params.get("mode", "auto")
        elif command == "set_brightness":
            service_data["brightness"] = params.get("brightness", 255)
        elif command == "set_volume":
            service_data["volume_level"] = params.get("volume", 0.5)
        elif command == "speak":
            service_data["message"] = params.get("message", "")
            service_data["entity_id"] = params.get("speaker", device)
            # HA 的 tts.speak 只给 entity_id(tts 实体) 不会发声：
            # 必须再给 media_player_entity_id 指定播放设备（target）。
            target = params.get("target") or params.get("media_player")
            if target:
                service_data["media_player_entity_id"] = target
        
        return self.call_service(domain, service, device, service_data)
    
    def get_status(self) -> Dict[str, Any]:
        """获取HA连接状态"""
        try:
            with self._session() as client:
                response = client.get(
                    f"{self.base_url}/api/",
                    headers=self.headers,
                    timeout=5
                )
                return {
                    "connected": response.status_code == 200,
                    "url": self.base_url
                }
        except Exception as e:
            return {
                "connected": False,
                "url": self.base_url,
                "error": str(e)
            }
    
    def get_history(self, entity_ids: list, start_time: str, end_time: str = None) -> Dict[str, list]:
        """查询HA历史数据
        
        Args:
            entity_ids: 实体ID列表
            start_time: 起始时间 ISO格式
            end_time: 结束时间 ISO格式（可选）
            
        Returns:
            {entity_id: [state1, state2, ...]}
        """
        try:
            from datetime import datetime, timedelta
            
            # 解析start_time，统一转成 aware-UTC 发给 HA（HA 端点按 UTC 解释）
            # 修复（审计 P1-17）：原代码去掉时区发 naive 本地时间，HA 按 UTC 解释导致
            # 查询窗口偏移 8 小时，返回 0 条但水位仍前进 → 事件永久丢失
            tz_offset = getattr(self.config, 'tz_offset_hours', 8)
            from datetime import timezone, timedelta as _td
            local_tz = timezone(_td(hours=tz_offset))
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                if start_dt.tzinfo is None:
                    # naive 时间假设为本地时间，加上本地时区后转 UTC
                    start_dt = start_dt.replace(tzinfo=local_tz)
                start_dt = start_dt.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                start_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)

            # 解析end_time（如果提供），同样转 UTC
            if end_time:
                try:
                    end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=local_tz)
                    end_dt = end_dt.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    end_dt = start_dt + timedelta(days=1)
            else:
                end_dt = start_dt + timedelta(days=1)

            safe_start = start_dt.isoformat()

            with self._session() as client:
                result: Dict[str, list] = {}

                for i in range(0, len(entity_ids), self.HISTORY_BATCH_SIZE):
                    batch = entity_ids[i:i + self.HISTORY_BATCH_SIZE]
                    params = {"filter_entity_id": ",".join(batch)}
                    # 把 end_time 交给 HA 做服务端裁剪，而不是拉全量再本地过滤，
                    # 回填场景下这能省掉数量级的带宽与解析开销
                    params["end_time"] = end_dt.isoformat()

                    try:
                        response = client.get(
                            f"{self.base_url}/api/history/period/{safe_start}",
                            headers=self.headers,
                            params=params,
                            timeout=self.HISTORY_TIMEOUT
                        )
                    except Exception as e:
                        print(f"[HA] 历史查询失败({len(batch)}个实体): {e}")
                        continue

                    if response.status_code != 200:
                        print(f"[HA] 历史查询 HTTP {response.status_code}")
                        continue

                    try:
                        data = response.json()
                    except Exception as e:
                        print(f"[HA] 历史响应解析失败: {e}")
                        continue

                    # HA 只为「有历史的实体」返回数组，且顺序不保证与请求一致，
                    # 因此必须按数组内的 entity_id 归位，不能用下标对齐
                    for series in data or []:
                        if not series:
                            continue
                        entity_id = series[0].get("entity_id")
                        if not entity_id:
                            continue
                        result.setdefault(entity_id, []).extend(series)

                return result
        except Exception as e:
            print(f"获取历史数据失败: {e}")
            return {}
                    
    def _fetch_registry(self, client: httpx.Client, endpoint: str) -> list:
        """获取HA注册表数据，失败返回空列表"""
        try:
            resp = client.get(
                f"{self.base_url}{endpoint}",
                headers=self.headers,
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[HA] 获取注册表 {endpoint} 失败: {e}")
        return []

    def _fetch_area_names_via_template(
        self, client: httpx.Client, states: list
    ) -> Dict[str, str]:
        """通过 HA /api/template 批量获取每个实体所在的 area 名称。

        部分 HA 版本没有 /api/areas、/api/devices、/api/entities 这些 REST 端点，
        但模板函数 area_name(entity_id) 一直可用。返回 {entity_id: area_name}。
        """
        if not states:
            return {}
        template = """
{% set result = namespace(names=[]) %}
{% for state in states %}
  {% set result.names = result.names + [area_name(state.entity_id)|default('', true)] %}
{% endfor %}
{{ result.names | to_json }}
""".strip()
        try:
            resp = client.post(
                f"{self.base_url}/api/template",
                headers=self.headers,
                json={"template": template},
                timeout=60
            )
            if resp.status_code != 200:
                print(f"[HA] /api/template area_name 失败: HTTP {resp.status_code}")
                return {}
            names = json.loads(resp.text)
            if not isinstance(names, list) or len(names) != len(states):
                print("[HA] /api/template area_name 返回长度与 states 不一致")
                return {}
            return {
                states[i]["entity_id"]: name
                for i, name in enumerate(names)
                if name and states[i].get("entity_id")
            }
        except Exception as e:
            print(f"[HA] /api/template area_name 异常: {e}")
            return {}

    def discover_entities(self) -> Dict[str, Any]:
        """扫描HA所有实体，按房间分组

        优先从HA的 area/device/entity registry 读取实体真实分区；
        registry 不可用时回退到 home_room / friendly_name 关键词推断。

        Returns:
            {
                "rooms": {
                    "客厅": {
                        "entities": {
                            "light.xxx": {"name": "吊灯", "domain": "light", "state": "on"},
                            ...
                        }
                    },
                    ...
                },
                "persons": [...],
                "total_entities": 123
            }
        """
        try:
            with self._session() as client:
                response = client.get(
                    f"{self.base_url}/api/states",
                    headers=self.headers,
                    timeout=30
                )

                if response.status_code != 200:
                    return {"ok": False, "error": f"HTTP {response.status_code}"}

                states = response.json()

                # 从HA registry构建 area / device / entity 的分区映射
                area_id_to_name: Dict[str, str] = {}
                device_id_to_area_id: Dict[str, str] = {}
                entity_id_to_area_id: Dict[str, str] = {}

                for area in self._fetch_registry(client, "/api/areas"):
                    aid = area.get("area_id")
                    name = area.get("name")
                    if aid and name:
                        area_id_to_name[aid] = name

                for device in self._fetch_registry(client, "/api/devices"):
                    did = device.get("device_id")
                    aid = device.get("area_id")
                    if did and aid:
                        device_id_to_area_id[did] = aid

                for entry in self._fetch_registry(client, "/api/entities"):
                    eid = entry.get("entity_id")
                    if not eid:
                        continue
                    # entity 自身的 area 优先级最高
                    aid = entry.get("area_id")
                    if not aid:
                        did = entry.get("device_id")
                        if did:
                            aid = device_id_to_area_id.get(did)
                    if aid:
                        entity_id_to_area_id[eid] = aid

                # registry 未返回可用分区时，回退到模板批量查询 area_name
                entity_id_to_area_name: Dict[str, str] = {}
                if not entity_id_to_area_id:
                    entity_id_to_area_name = self._fetch_area_names_via_template(
                        client, states
                    )

                # 按房间分组
                rooms = {}
                skip_domains = {"automation", "scene", "script", "weather", "sun",
                               "persistent_notification", "config", "zone", "ai_task",
                               "assist_satellite", "conversation", "ws_mcp_server"}
                skip_prefixes = ("update.", "select.", "number.", "button.",
                                "text.", "input_text.", "input_number.", "input_select.",
                                "notify.", "remote.")

                room_keywords = {
                    "客厅": "客厅", "主卧": "主卧", "卧室": "卧室",
                    "书房": "书房", "厨房": "厨房", "卫生间": "卫生间",
                    "浴室": "卫生间", "阳台": "阳台", "衣帽间": "衣帽间",
                    "餐厅": "餐厅", "玄关": "玄关", "走廊": "走廊",
                }

                for state in states:
                    entity_id = state["entity_id"]
                    domain = entity_id.split(".")[0]

                    # 跳过不需要的域
                    if domain in skip_domains or entity_id.startswith(skip_prefixes):
                        continue

                    attrs = state.get("attributes", {})
                    friendly_name = attrs.get("friendly_name", "")

                    # 1) HA registry 中的 area（entity 自身 > device）
                    room = ""
                    area_id = entity_id_to_area_id.get(entity_id)
                    if area_id:
                        room = area_id_to_name.get(area_id, "")

                    # 2) 模板批量查询到的 area_name（兼容无 registry REST 端点的 HA）
                    if not room:
                        room = entity_id_to_area_name.get(entity_id, "")

                    # 3) 实体属性中的 home_room
                    if not room:
                        room = attrs.get("home_room", "")

                    # 4) friendly_name 关键词推断
                    if not room:
                        for keyword, room_name in room_keywords.items():
                            if keyword in friendly_name:
                                room = room_name
                                break

                    if not room:
                        room = "未分区"

                    if room not in rooms:
                        rooms[room] = {"entities": {}}

                    rooms[room]["entities"][entity_id] = {
                        "name": friendly_name or entity_id,
                        "domain": domain,
                        "state": state.get("state", "unknown"),
                    }

                return {
                    "ok": True,
                    "rooms": rooms,
                    "total_entities": sum(len(r["entities"]) for r in rooms.values())
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}
