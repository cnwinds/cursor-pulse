"""SSRF protections for web.fetch (scheme, DNS/IP, metadata, redirects)."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse


class SsrfBlockedError(Exception):
    def __init__(self, reason: str, message: str = "目标地址不允许访问"):
        super().__init__(message)
        self.reason = reason


_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.google",
        "instance-data",  # AWS IMDS alias historically
    }
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    ips: tuple[str, ...]


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # Cloud metadata / CGNAT / documentation ranges
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.ip_network("169.254.0.0/16"):
            return True
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return True
        if ip in ipaddress.ip_network("0.0.0.0/8"):
            return True
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_blocked_ip(ip.ipv4_mapped)
        # Unique local (fc00::/7) already covered by is_private on modern Python
        if ip in ipaddress.ip_network("fe80::/10"):
            return True
    return False


def validate_url_scheme(url: str) -> None:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfBlockedError("scheme", f"仅允许 http/https，收到：{scheme or '空'}")
    if not parsed.hostname:
        raise SsrfBlockedError("hostname", "URL 缺少主机名")
    if parsed.username or parsed.password:
        raise SsrfBlockedError("credentials", "URL 不得包含用户名或密码")


def resolve_and_validate_url(
    url: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> ResolvedTarget:
    """Validate scheme and ensure resolved IPs are public (not private/metadata)."""
    validate_url_scheme(url)
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise SsrfBlockedError("hostname", "URL 缺少主机名")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        raise SsrfBlockedError("localhost", "禁止访问本机主机名")

    # Literal IP in hostname
    try:
        literal = ipaddress.ip_address(hostname)
        if _is_blocked_ip(literal):
            raise SsrfBlockedError("private_ip", "禁止访问内网或元数据地址")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return ResolvedTarget(url=url, hostname=hostname, port=port, ips=(str(literal),))
    except ValueError:
        pass

    resolve_fn = resolver or socket.getaddrinfo
    try:
        infos = resolve_fn(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SsrfBlockedError("dns", "无法解析目标主机名") from exc
    if not infos:
        raise SsrfBlockedError("dns", "无法解析目标主机名")

    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise SsrfBlockedError("private_ip", "禁止访问内网或元数据地址")
        ips.append(str(ip))
    if not ips:
        raise SsrfBlockedError("dns", "无法解析目标主机名")

    port = parsed.port or (443 if (parsed.scheme or "").lower() == "https" else 80)
    return ResolvedTarget(url=url, hostname=hostname, port=port, ips=tuple(dict.fromkeys(ips)))


def build_pinned_request(
    url: str,
    target: ResolvedTarget,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return URL, headers, and httpx extensions that connect to validated IP(s).

    Preserves the original hostname in the Host header and TLS SNI so HTTPS
    servers with virtual hosts remain reachable while avoiding DNS rebinding.
    """
    parsed = urlparse(url)
    ip = target.ips[0]
    host = ip if ":" not in ip else f"[{ip}]"
    default_port = 443 if (parsed.scheme or "").lower() == "https" else 80
    netloc = f"{host}:{target.port}" if target.port != default_port else host
    pinned_url = urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path or "",
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    headers = {"Host": target.hostname}
    extensions: dict[str, Any] = {}
    if (parsed.scheme or "").lower() == "https":
        extensions["sni_hostname"] = target.hostname
    return pinned_url, headers, extensions


def join_redirect(base_url: str, location: str | None) -> str:
    if not location or not str(location).strip():
        raise SsrfBlockedError("redirect", "重定向缺少 Location")
    return urljoin(base_url, str(location).strip())
