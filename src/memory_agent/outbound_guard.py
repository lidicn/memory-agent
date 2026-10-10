#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出站 URL 护栏（M16/M18/M19）。

策略：放行私有网段（HA/go2rtc/Arcface 在 192.168.x.x），
拒绝环回（127.0.0.0/8）、链路本地（169.254.0.0/16，含云元数据 169.254.169.254）、
非 http(s) scheme。

注意：放行私有网段意味着内网横向仍可达。若需更严，加显式白名单。
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

_LOG = logging.getLogger(__name__)

# 私有网段（RFC 1918 + loopback 单独处理）
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# 链路本地（含云元数据）
_LINK_LOCAL = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
]

# 环回
_LOOPBACK = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]


def _is_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in _PRIVATE_NETS:
        if ip in net:
            return True
    return False


def _is_link_local(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in _LINK_LOCAL:
        if ip in net:
            return True
    return False


def _is_loopback(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in _LOOPBACK:
        if ip in net:
            return True
    return False


def validate_outbound_url(url: str) -> str | None:
    """校验出站 URL。返回 None 表示通过，返回错误字符串表示拒绝。

    - 只允许 http/https
    - 拒绝环回地址
    - 拒绝链路本地地址（含云元数据 169.254.169.254）
    - 私有网段放行（HA/go2rtc 在 192.168.x.x）
    - 公网域名放行（不做 DNS 解析，避免内网穿透绕过）
    """
    if not url or not url.strip():
        return "URL 为空"
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return f"不允许的协议: {parts.scheme}（仅 http/https）"
    host = parts.hostname
    if not host:
        return "无法解析主机名"
    # IP 字面量：直接判定
    try:
        ip = ipaddress.ip_address(host)
        if _is_loopback(str(ip)):
            return "不允许访问环回地址"
        if _is_link_local(str(ip)):
            return "不允许访问链路本地地址（含云元数据服务）"
        # 私有网段放行
        if _is_private(str(ip)):
            return None
        # 公网 IP 放行
        return None
    except ValueError:
        pass
    # 域名：不做 DNS 解析（避免绕过），直接放行
    # 注意：localhost 等域名可能解析到环回，这里仅做字面量判定
    if host.lower() in ("localhost", "localhost.localdomain"):
        return "不允许访问 localhost"
    return None


def guard_outbound_url(url: str) -> None:
    """校验出站 URL，不通过则 raise ValueError。"""
    err = validate_outbound_url(url)
    if err:
        _LOG.warning("出站 URL 被护栏拒绝: %s (%s)", url, err)
        raise ValueError(f"出站地址不被允许: {err}")
