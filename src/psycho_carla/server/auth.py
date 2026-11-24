"""
PSYCHO-C.A.R.L.A. — Server authentication.

OpenJarvis refuses to bind a non-loopback host without an API key,
because an exposed agent carries shell and file tools. This module
implements the same guard, plus a small key-generation helper.

Behaviour:
  * Loopback bind (``127.0.0.1`` / ``localhost`` / ``::1``) → no key
    required. The agent is only reachable from this machine.
  * Non-loopback bind (``0.0.0.0`` / any LAN address) → an API key is
    **required**; startup refuses without one.
  * When a key is configured, every HTTP request must present it as
    ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass

# Environment variable names, checked in order.
API_KEY_ENV_VARS = (
    "PSYCHO_CARLA_API_KEY",
    "CARLA_API_KEY",
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def is_loopback(host: str) -> bool:
    """True when the bind address is not exposed to the network."""
    if not host:
        return True
    h = host.strip().lower()
    if h in LOOPBACK_HOSTS:
        return True
    # 127.x.x.x is loopback in its entirety.
    if h.startswith("127."):
        return True
    return False


def load_api_key(explicit: str = "") -> str:
    """Resolve the API key from an explicit value or the environment."""
    if explicit:
        return explicit.strip()
    for var in API_KEY_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return ""


def generate_api_key(length: int = 32) -> str:
    """Generate a URL-safe API key."""
    return secrets.token_urlsafe(length)[: length * 2]


def constant_time_equals(a: str, b: str) -> bool:
    """Compare two secrets without leaking length or content via timing."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass
class AuthConfig:
    """Resolved authentication settings for the server."""

    api_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    require_key: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def exposed(self) -> bool:
        """True when the server is bound to a non-loopback address."""
        return not is_loopback(self.host)

    def bind_error(self) -> str:
        """The error message to raise when the bind is unsafe."""
        return (
            "Refusing to bind to a non-loopback address without an API key.\n"
            f"  host: {self.host}:{self.port}\n"
            f"  An exposed agent carries shell and file tools.\n"
            "\n"
            "  Fix it by doing one of:\n"
            "    1. Bind to loopback:  set host = \"127.0.0.1\" in [server]\n"
            "    2. Provide a key:      set api_key_env = \"PSYCHO_CARLA_API_KEY\"\n"
            "                          and export PSYCHO_CARLA_API_KEY=<secret>\n"
            "    3. Generate one:       carla auth generate-key\n"
        )


def resolve_auth(
    host: str,
    port: int = 8787,
    api_key_env: str = "",
    api_key: str = "",
    *,
    enforce: bool = True,
) -> AuthConfig:
    """Resolve the auth configuration and enforce the bind guard.

    Raises RuntimeError when the server would be exposed without a key.
    """
    resolved_key = load_api_key(api_key)
    if api_key_env:
        env_val = os.environ.get(api_key_env, "").strip()
        if env_val:
            resolved_key = env_val

    cfg = AuthConfig(
        api_key=resolved_key,
        host=host,
        port=port,
        require_key=enforce,
    )

    if enforce and cfg.exposed and not cfg.enabled:
        raise RuntimeError(cfg.bind_error())
    return cfg


def extract_key(authorization: str = "", x_api_key: str = "") -> str:
    """Pull the presented key out of the request headers."""
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            return parts[1].strip()
        return authorization.strip()
    return ""


def check_key(presented: str, expected: str) -> bool:
    """Verify a presented key against the expected one."""
    if not expected:
        return True
    if not presented:
        return False
    return constant_time_equals(presented, expected)
