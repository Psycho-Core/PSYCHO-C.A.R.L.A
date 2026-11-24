"""
OAuth 2.0 — the authorisation-code flow with a localhost callback.

The flow:

  1. Resolve client credentials (argument → stored file → environment).
  2. Build the authorisation URL and open the user's browser.
  3. Serve a short-lived HTTP listener on localhost and wait for the
     redirect to deliver the authorisation code.
  4. Exchange the code for tokens.
  5. Persist the tokens with owner-only permissions.

Tokens are secrets. The refresh token in particular grants long-lived
access, so the credential file is written 0600 on POSIX and the code never
logs one.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

#: Where connector credentials live.
CONNECTORS_DIR = Path.home() / ".psycho_carla" / "connectors"

#: Combined scopes for all Google connectors — one consent covers them all.
GOOGLE_ALL_SCOPES: list[str] = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/tasks.readonly",
]


@dataclass(frozen=True)
class OAuthProvider:
    """Configuration for an OAuth 2.0 provider."""

    name: str
    display_name: str
    auth_endpoint: str
    token_endpoint: str
    scopes: list[str]
    setup_url: str
    setup_hint: str
    callback_port: int = 8789
    callback_host: str = "127.0.0.1"
    callback_path: str = "/callback"
    token_auth: str = "body"  # "body" or "basic"
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    #: Which connector ids this provider covers.
    connector_ids: tuple[str, ...] = ()
    #: Files in the connectors dir the tokens are saved to.
    credential_files: tuple[str, ...] = ()


OAUTH_PROVIDERS: dict[str, OAuthProvider] = {
    "google": OAuthProvider(
        name="google",
        display_name="Google",
        auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        scopes=GOOGLE_ALL_SCOPES,
        setup_url="https://console.cloud.google.com/apis/credentials",
        setup_hint="Create an OAuth 2.0 Client ID (Desktop app type)",
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
        connector_ids=("gdrive", "gcalendar", "gmail", "google_tasks"),
        credential_files=(
            "google.json",
            "gdrive.json",
            "gcalendar.json",
            "gmail.json",
            "google_tasks.json",
        ),
    ),
    "github": OAuthProvider(
        name="github",
        display_name="GitHub",
        auth_endpoint="https://github.com/login/oauth/authorize",
        token_endpoint="https://github.com/login/oauth/access_token",
        scopes=["repo", "read:user"],
        setup_url="https://github.com/settings/developers",
        setup_hint=(
            "Create an OAuth App, set the callback URL to "
            "http://127.0.0.1:8789/callback"
        ),
        connector_ids=("github",),
        credential_files=("github.json",),
    ),
    "strava": OAuthProvider(
        name="strava",
        display_name="Strava",
        auth_endpoint="https://www.strava.com/oauth/authorize",
        token_endpoint="https://www.strava.com/oauth/token",
        scopes=["activity:read_all"],
        setup_url="https://www.strava.com/settings/api",
        setup_hint="Create an API Application (callback domain: localhost)",
        connector_ids=("strava",),
        credential_files=("strava.json",),
    ),
    "spotify": OAuthProvider(
        name="spotify",
        display_name="Spotify",
        auth_endpoint="https://accounts.spotify.com/authorize",
        token_endpoint="https://accounts.spotify.com/api/token",
        scopes=["user-read-recently-played"],
        setup_url="https://developer.spotify.com/dashboard",
        setup_hint=(
            "Create an app, add redirect URI: http://127.0.0.1:8888/callback"
        ),
        callback_port=8888,
        token_auth="basic",
        connector_ids=("spotify",),
        credential_files=("spotify.json",),
    ),
}


def get_provider_for_connector(connector_id: str) -> OAuthProvider | None:
    """The provider that covers a connector id, if any."""
    for provider in OAUTH_PROVIDERS.values():
        if connector_id in provider.connector_ids:
            return provider
    return None


# --------------------------------------------------------------------------- #
# Credential storage
# --------------------------------------------------------------------------- #
def save_tokens(path: str | Path, tokens: dict[str, Any]) -> None:
    """Persist tokens to a file with owner-only permissions."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_tokens(path: str | Path) -> dict[str, Any]:
    """Load tokens from a file. Returns {} when absent or unreadable."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def delete_tokens(path: str | Path) -> bool:
    """Delete a credential file."""
    p = Path(path)
    if p.exists():
        p.unlink()
        return True
    return False


def get_client_credentials(provider: OAuthProvider) -> tuple[str, str] | None:
    """Resolve client_id and client_secret for a provider.

    Checks the stored credential files, then the environment
    (``PSYCHO_CARLA_{NAME}_CLIENT_ID`` / ``_CLIENT_SECRET``).
    """
    for filename in provider.credential_files:
        tokens = load_tokens(CONNECTORS_DIR / filename)
        if tokens.get("client_id") and tokens.get("client_secret"):
            return tokens["client_id"], tokens["client_secret"]
    prefix = f"PSYCHO_CARLA_{provider.name.upper()}"
    env_id = os.environ.get(f"{prefix}_CLIENT_ID", "")
    env_secret = os.environ.get(f"{prefix}_CLIENT_SECRET", "")
    if env_id and env_secret:
        return env_id, env_secret
    return None


def require_access_token(tokens: Any) -> str:
    """Return a non-empty access token, or reject the response.

    An error-shaped token response is not an authenticated session —
    persisting one would leave a false-success credential on disk.
    """
    if not isinstance(tokens, dict):
        raise RuntimeError("OAuth token response was not a JSON object")
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError(
            "OAuth token response did not include an access_token"
        )
    return access_token.strip()


# --------------------------------------------------------------------------- #
# The flow
# --------------------------------------------------------------------------- #
def build_auth_url(
    provider: OAuthProvider,
    client_id: str,
    *,
    state: str = "",
) -> str:
    """Build the provider's authorisation URL."""
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(provider),
        "response_type": "code",
        "scope": " ".join(provider.scopes),
    }
    if state:
        params["state"] = state
    params.update(provider.extra_auth_params)
    return f"{provider.auth_endpoint}?{urlencode(params)}"


def _redirect_uri(provider: OAuthProvider) -> str:
    return (
        f"http://{provider.callback_host}:{provider.callback_port}"
        f"{provider.callback_path}"
    )


def _wait_for_callback_code(
    *,
    host: str,
    port: int,
    path: str,
    timeout: float = 300.0,
) -> str:
    """Serve a one-shot HTTP listener and wait for the authorisation code.

    The listener binds to loopback only and answers exactly one request.
    It exists solely to receive the redirect.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    result: dict[str, str] = {}
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path != path:
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            code = (params.get("code") or [""])[0]
            error = (params.get("error") or [""])[0]
            if code:
                result["code"] = code
                body = (
                    b"<html><body><h2>Connected.</h2>"
                    b"<p>You can close this tab.</p></body></html>"
                )
                status = 200
            else:
                result["error"] = error or "authorization_denied"
                body = (
                    b"<html><body><h2>Not connected.</h2>"
                    b"<p>The authorisation was not completed.</p></body></html>"
                )
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            received.set()

        def log_message(self, *args: Any) -> None:
            """Silence the default stderr logging."""
            return

    try:
        server = HTTPServer((host, port), Handler)
    except OSError as exc:
        raise RuntimeError(
            f"Could not bind the OAuth callback to {host}:{port} — {exc}"
        ) from exc

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    if not received.wait(timeout):
        server.server_close()
        raise TimeoutError(
            f"Timed out after {timeout:.0f}s waiting for the OAuth callback. "
            "The browser may not have been able to reach the callback URL."
        )
    server.server_close()
    if "error" in result:
        raise RuntimeError(f"OAuth authorisation failed: {result['error']}")
    return result.get("code", "")


def _exchange_token(
    provider: OAuthProvider,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Exchange an authorisation code for tokens.

    ``extra`` carries the grant parameters for a refresh, where the grant
    type is ``refresh_token`` rather than ``authorization_code``.
    """
    import urllib.error
    import urllib.request

    body = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if extra:
        body.update(extra)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if provider.token_auth == "basic":
        import base64

        raw = f"{client_id}:{client_secret}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        body.pop("client_secret", None)

    req = urllib.request.Request(
        provider.token_endpoint,
        data=urlencode(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(
            f"Token exchange failed (HTTP {exc.code}): {detail}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Token exchange failed: {exc}") from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise RuntimeError("Token endpoint did not return JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Token endpoint did not return a JSON object")
    return data


def run_connector_oauth(
    connector_id: str,
    client_id: str = "",
    client_secret: str = "",
    *,
    open_browser: bool = True,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Run the complete OAuth flow for a connector.

    Returns the raw token response.
    """
    provider = get_provider_for_connector(connector_id)
    if provider is None:
        raise ValueError(f"No OAuth provider configured for '{connector_id}'")

    if not (client_id and client_secret):
        creds = get_client_credentials(provider)
        if creds:
            client_id, client_secret = creds
    if not (client_id and client_secret):
        raise RuntimeError(
            f"No client credentials for {provider.display_name}. "
            f"Set them up at: {provider.setup_url}\n"
            f"({provider.setup_hint})"
        )

    auth_url = build_auth_url(provider, client_id)
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    code = _wait_for_callback_code(
        host=provider.callback_host,
        port=provider.callback_port,
        path=provider.callback_path,
        timeout=timeout,
    )
    tokens = _exchange_token(
        provider, code, client_id, client_secret, _redirect_uri(provider)
    )
    access_token = require_access_token(tokens)

    payload = {
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token", ""),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": tokens.get("expires_in", 3600),
        "obtained_at": time.time(),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    for filename in provider.credential_files:
        save_tokens(str(CONNECTORS_DIR / filename), payload)
    return tokens


def refresh_access_token(
    connector_id: str,
    *,
    credentials_path: str | Path | None = None,
) -> dict[str, Any]:
    """Refresh an expired access token using the stored refresh token."""
    provider = get_provider_for_connector(connector_id)
    if provider is None:
        raise ValueError(f"No OAuth provider configured for '{connector_id}'")

    if credentials_path:
        tokens = load_tokens(credentials_path)
    else:
        tokens = {}
        for filename in provider.credential_files:
            tokens = load_tokens(CONNECTORS_DIR / filename)
            if tokens:
                break
    if not tokens:
        raise RuntimeError(f"No stored credentials for '{connector_id}'")

    refresh_token = tokens.get("refresh_token", "")
    client_id = tokens.get("client_id", "")
    client_secret = tokens.get("client_secret", "")
    if not (refresh_token and client_id and client_secret):
        raise RuntimeError(
            f"The stored credentials for '{connector_id}' have no refresh token."
        )

    fresh = _exchange_token(
        provider,
        code="",
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=_redirect_uri(provider),
        extra={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    payload = {
        "access_token": require_access_token(fresh),
        "refresh_token": fresh.get("refresh_token", refresh_token),
        "token_type": fresh.get("token_type", "Bearer"),
        "expires_in": fresh.get("expires_in", 3600),
        "obtained_at": time.time(),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    for filename in provider.credential_files:
        save_tokens(str(CONNECTORS_DIR / filename), payload)
    return payload


def connector_status(connector_id: str) -> dict[str, Any]:
    """Report whether a connector is connected, and to which provider."""
    provider = get_provider_for_connector(connector_id)
    if provider is None:
        return {
            "connector": connector_id,
            "connected": False,
            "reason": "no OAuth provider for this connector",
            "provider": "",
        }
    tokens: dict[str, Any] = {}
    for filename in provider.credential_files:
        tokens = load_tokens(CONNECTORS_DIR / filename)
        if tokens:
            break
    connected = bool(tokens.get("access_token"))
    expires_in = tokens.get("expires_in", 0)
    obtained_at = tokens.get("obtained_at", 0)
    expired = False
    if connected and expires_in and obtained_at:
        expired = (time.time() - obtained_at) > float(expires_in)
    return {
        "connector": connector_id,
        "provider": provider.name,
        "display_name": provider.display_name,
        "connected": connected,
        "expired": expired,
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "scopes": list(provider.scopes),
        "setup_url": provider.setup_url,
        "setup_hint": provider.setup_hint,
    }


__all__ = [
    "CONNECTORS_DIR",
    "GOOGLE_ALL_SCOPES",
    "OAUTH_PROVIDERS",
    "OAuthProvider",
    "build_auth_url",
    "connector_status",
    "delete_tokens",
    "get_client_credentials",
    "get_provider_for_connector",
    "load_tokens",
    "refresh_access_token",
    "require_access_token",
    "run_connector_oauth",
    "save_tokens",
]
