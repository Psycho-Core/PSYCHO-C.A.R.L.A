"""Browser-origin safety for the local web server.

A server on ``127.0.0.1`` is still reachable from *any web page open in your
browser* — a page can send requests to it, and with a permissive CORS setting it
can even read the answers. Since C.A.R.L.A. has shell and file tools, that would
let a random website drive your PC. These checks close that door:

* **Origin allow-list** — only the HUD itself (loopback origins) and origins you
  list in ``[server] cors_origins`` may call the API from a browser. A ``"*"``
  entry is ignored on a loopback bind.
* **Cross-site write guard** — a POST/PUT/PATCH/DELETE carrying a foreign
  ``Origin`` header is refused outright (browsers always send ``Origin`` on
  cross-site writes; scripts like curl don't, and are unaffected).
* **Host check** — on a loopback bind the ``Host`` header must be a loopback
  name, which defeats DNS-rebinding (a hostile domain re-pointed at 127.0.0.1).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Any port on a loopback name — the HUD, the desktop window, curl.
LOOPBACK_ORIGIN = re.compile(r"^https?://(127\.\d+\.\d+\.\d+|localhost|\[::1\])(:\d+)?$", re.I)
LOOPBACK_IPV4 = re.compile(r"^127(\.\d{1,3}){3}$")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "testserver"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def extra_origins(configured: Iterable[str] | None, loopback_bind: bool) -> list[str]:
    """Origins from config, minus a blanket ``*`` when the bind is loopback."""
    out = []
    for origin in configured or []:
        origin = str(origin).strip().rstrip("/")
        if not origin or (origin == "*" and loopback_bind):
            continue
        out.append(origin)
    return out


def origin_allowed(origin: str, extra: Iterable[str] = ()) -> bool:
    """May a page from ``origin`` talk to this server?"""
    if not origin:
        return True                       # not a browser cross-site request
    origin = origin.strip().rstrip("/")
    if origin.lower() == "null":
        return False                      # sandboxed iframes, file:// pages
    if LOOPBACK_ORIGIN.match(origin):
        return True
    extra = list(extra)
    return "*" in extra or origin in extra


def same_origin(origin: str, host_header: str) -> bool:
    """Is ``origin`` just this server's own address (the HUD talking to the server that served it)?"""
    from urllib.parse import urlparse

    netloc = urlparse(origin or "").netloc.lower()
    return bool(netloc) and netloc == (host_header or "").strip().lower()


def host_allowed(host_header: str) -> bool:
    """Is the ``Host`` header a loopback name (any port)?"""
    host = (host_header or "").strip().lower()
    if host.startswith("["):                       # [::1]:8787
        host = host.split("]")[0] + "]"
    elif host.count(":") == 1:                     # name:port
        host = host.split(":")[0]
    return (host in LOOPBACK_HOSTS or bool(LOOPBACK_IPV4.match(host))
            or host.endswith(".localhost"))
