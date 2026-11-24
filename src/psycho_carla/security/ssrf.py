"""
SSRF protection — refuse URLs that point at the machine itself.

Any tool that fetches a model-supplied URL (web_fetch, RSS sync, image
tools) should pass the URL through :func:`validate_url` first. Private,
loopback and link-local targets are refused unless explicitly allowed;
hostnames are resolved and every resulting address is checked, which
also closes the DNS-rebinding gap at validation time.

Layer: security.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SSRFError(ValueError):
    """Raised when a URL fails SSRF validation."""


def _is_dangerous_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_url(url: str, *, allow_private: bool = False) -> str:
    """Return the URL if it is safe to fetch, else raise SSRFError."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"URL scheme '{parsed.scheme or '(none)'}' is not allowed")
    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host")
    if allow_private:
        return url
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_dangerous_ip(literal):
            raise SSRFError(f"URL points at a non-public address ({host})")
        return url
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise SSRFError(f"Could not resolve host '{host}': {exc}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_dangerous_ip(ip):
            raise SSRFError(
                f"Host '{host}' resolves to a non-public address ({address})")
    return url


def is_safe_url(url: str, *, allow_private: bool = False) -> bool:
    try:
        validate_url(url, allow_private=allow_private)
        return True
    except SSRFError:
        return False


__all__ = ["SSRFError", "is_safe_url", "validate_url"]
