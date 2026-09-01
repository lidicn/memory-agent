"""人脸识别节点池（ArcFace 可插拔节点）

设计要点
--------
* 节点（TV / 手机）启动后 ``register``，定时 ``heartbeat`` 续活；
* memory-agent 按节点类型给稳定性权重（``phone=100`` / ``tv=20``），
  选路时取权重最高的**在线**节点；
* 心跳超时（>30s）自动剔除，避免打到已死节点拖垮识别链路；
* 选路失败 / 节点调用失败 → 返回 ``None``，由上层降级到 VLM（不报错）。

节点契约（memory-agent → 节点）
---------------------------------
* 注册：``POST /api/face/node/register``  ``{node_id, node_type, url, room?}``
* 心跳：``POST /api/face/node/heartbeat`` ``{node_id}``
* 识别：``POST {node.url}/recognize``      ``{image, min_conf}``
         → ``{name, confidence}`` 或 ``{faces:[{name, confidence, box?}]}``
* 人脸库下行：节点 ``GET /face/lib`` 拿全部成员特征；
  上行回写：节点 ``POST /face/lib`` ``{member_id, face_feature}``。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

_HEARTBEAT_TTL_S = 30.0          # 心跳超时阈值：超过即视为离线并剔除
_NODE_WEIGHTS = {"phone": 100, "tv": 20, "default": 10}


@dataclass
class FaceNode:
    node_id: str
    node_type: str
    url: str
    room: str = ""
    weight: int = 0
    last_heartbeat: float = 0.0
    registered_at: float = 0.0
    last_error: str = ""


class FaceNodeRegistry:
    """进程内 ArcFace 节点注册表（不落盘，重启后由节点重新注册）。"""

    def __init__(self, heartbeat_ttl_s: float = _HEARTBEAT_TTL_S) -> None:
        self._ttl = heartbeat_ttl_s
        self._lock = threading.RLock()
        self._nodes: dict[str, FaceNode] = {}

    # ── 写 ────────────────────────────────────────────────────────────────

    def register(
        self, node_id: str, node_type: str, url: str, room: str = ""
    ) -> dict:
        node_id = (node_id or "").strip()
        if not node_id or not url:
            return {"ok": False, "error": "node_id 与 url 必填"}
        node_type = (node_type or "default").strip().lower()
        weight = _NODE_WEIGHTS.get(node_type, _NODE_WEIGHTS["default"])
        now = time.monotonic()
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                node = FaceNode(
                    node_id=node_id, node_type=node_type, url=url, room=room,
                    weight=weight, last_heartbeat=now, registered_at=now,
                )
                self._nodes[node_id] = node
            else:
                # 已存在：更新元数据并刷新心跳（幂等重注册）
                node.node_type = node_type
                node.url = url
                node.room = room
                node.weight = weight
                node.last_heartbeat = now
                node.last_error = ""
        return {"ok": True, "node_id": node_id, "node_type": node_type, "weight": weight}

    def heartbeat(self, node_id: str) -> dict:
        node_id = (node_id or "").strip()
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return {"ok": False, "error": "节点未注册", "registered": False}
            node.last_heartbeat = time.monotonic()
            node.last_error = ""
            return {"ok": True, "node_id": node_id}

    def unregister(self, node_id: str) -> dict:
        node_id = (node_id or "").strip()
        with self._lock:
            self._nodes.pop(node_id, None)
        return {"ok": True, "node_id": node_id}

    def mark_error(self, node_id: str, err: str) -> None:
        """记录节点最近一次调用错误（仅诊断用；剔除仍靠心跳 TTL）。"""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node.last_error = str(err)[:200]

    # ── 读 / 选路 ──────────────────────────────────────────────────────────

    def _online(self, node: FaceNode) -> bool:
        return (time.monotonic() - node.last_heartbeat) <= self._ttl

    def list_nodes(self) -> list[dict]:
        with self._lock:
            out = []
            for n in self._nodes.values():
                out.append({
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "url": n.url,
                    "room": n.room,
                    "weight": n.weight,
                    "online": self._online(n),
                    "last_heartbeat_s_ago": round(time.monotonic() - n.last_heartbeat, 1),
                    "last_error": n.last_error,
                })
            # 在线优先，其次权重降序
            out.sort(key=lambda d: (not d["online"], -d["weight"]))
            return out

    def select_node(self, room: str | None = None) -> FaceNode | None:
        """选权重最高的在线节点；可选按 room 优先同房间节点。

        无在线节点返回 None（上层降级 VLM）。"""
        with self._lock:
            candidates = [n for n in self._nodes.values() if self._online(n)]
            if not candidates:
                return None

            def _key(n: FaceNode):
                same_room = 1 if (room and n.room and n.room == room) else 0
                return (same_room, n.weight)

            return max(candidates, key=_key)
