"""SSRF guard for user-supplied OpenAI-compatible endpoints."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit


def _public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def validate_api_base(value: str, *, resolve_dns: bool = True) -> str:
    raw = (value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API 地址必须是完整的 http:// 或 https:// URL")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError("API 地址不能包含账号、密码、查询参数或锚点")
    allow_private = os.environ.get("ALLOW_PRIVATE_CUSTOM_API", "").lower() in {"1", "true", "yes"}
    host = parsed.hostname.lower().rstrip(".")
    if not allow_private:
        if host in {"localhost", "localhost.localdomain"}:
            raise ValueError("默认禁止访问本机或内网 API；可信本地服务可设置 ALLOW_PRIVATE_CUSTOM_API=1")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError("默认只允许公共网络 API，已阻止本机、内网或元数据地址")
        if resolve_dns:
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
            except OSError as exc:
                raise ValueError(f"API 域名无法解析: {host}") from exc
            if not addresses or any(not _public_address(address) for address in addresses):
                raise ValueError("API 域名解析到本机或内网地址，已阻止请求")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
