"""
Shared HTTP plumbing for channel adapters.

Most messaging platforms are reachable over plain HTTPS with a bearer
token or a bot token. Rather than have thirty adapters each hand-roll
request signing, retries and error shapes, they share this module.

Nothing here is platform-specific — it is the boring 80% that every
adapter needs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: Default timeout for an outbound channel request, in seconds.
DEFAULT_TIMEOUT = 30.0


class ChannelError(RuntimeError):
    """A channel request failed."""


def build_url(base: str, path: str, params: dict[str, Any] | None = None) -> str:
    """Join a base URL and path, appending query parameters."""
    base = base.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    url = f"{base}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean, doseq=True)}"
    return url


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str]:
    """Make an HTTP request. Returns ``(status_code, body_text)``.

    Raises :class:`ChannelError` on a transport failure. HTTP error
    statuses are returned rather than raised, so adapters can decide
    whether a 429 or a 404 matters to them.
    """
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    payload: bytes | None = data
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(
        url, data=payload, headers=hdrs, method=method.upper()
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return (resp.status, body)
    except urllib.error.HTTPError as exc:
        # An HTTP error still carries a body worth reading.
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return (exc.code, body)
    except urllib.error.URLError as exc:
        raise ChannelError(str(exc.reason)) from exc
    except Exception as exc:
        raise ChannelError(str(exc)) from exc


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Make a request and parse the response as JSON."""
    status, body = request(
        url, method=method, headers=headers, json_body=json_body, data=data, timeout=timeout
    )
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        if status >= 400:
            raise ChannelError(f"HTTP {status}: {body[:200]}")
        return {"raw": body}


def bearer(token: str) -> dict[str, str]:
    """An Authorization header for a bearer token."""
    return {"Authorization": f"Bearer {token}"}


def basic(username: str, password: str) -> dict[str, str]:
    """An Authorization header for basic auth."""
    import base64

    raw = f"{username}:{password}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def oauth1_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_secret: str,
    query_params: dict[str, Any] | None = None,
    body_params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Sign a request with OAuth 1.0a (HMAC-SHA1).

    Twitter/X requires this for user-context endpoints such as posting
    a tweet — a bearer token only covers read endpoints. The signature
    base string is built per RFC 5849 from the method, the base URL and
    the sorted, percent-encoded parameters.
    """
    import base64
    import hashlib
    import hmac
    import time
    import uuid

    def pct(value: str) -> str:
        return urllib.parse.quote(str(value), safe="")

    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }

    # Query and body parameters both take part in the signature.
    all_params: dict[str, str] = dict(oauth_params)
    for source in (query_params or {}, body_params or {}):
        for key, value in source.items():
            if value is not None:
                all_params[key] = str(value)

    param_str = "&".join(
        f"{pct(k)}={pct(v)}" for k, v in sorted(all_params.items())
    )
    base_url = str(url).split("?")[0]
    base_string = f"{method.upper()}&{pct(base_url)}&{pct(param_str)}"

    signing_key = f"{pct(consumer_secret)}&{pct(access_secret)}"
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1
        ).digest()
    ).decode("ascii")

    oauth_params["oauth_signature"] = signature
    header = "OAuth " + ", ".join(
        f'{pct(k)}="{pct(v)}"' for k, v in sorted(oauth_params.items())
    )
    return {"Authorization": header}


def truncate(text: str, limit: int = 4096) -> str:
    """Trim a message to a platform's length limit, with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


__all__ = [
    "ChannelError",
    "DEFAULT_TIMEOUT",
    "basic",
    "bearer",
    "build_url",
    "oauth1_header",
    "request",
    "request_json",
    "truncate",
]
