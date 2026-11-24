"""
GUI launcher — start the console if needed, then open the HUD.

The HUD is a desktop interface for a desktop screen. The launcher opens
it in the user's default browser, preferring an *application* window (no
tabs, no address bar) when a browser that supports one is available.

Layer: Pillar 5 (Interface).
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

#: Browsers that can open an application-mode window, per platform.
_APP_BROWSERS: dict[str, list[tuple[str, list[str]]]] = {
    "darwin": [
        ("Google Chrome", ["--app=", "--window-size=1600,1000"]),
        ("Chromium", ["--app=", "--window-size=1600,1000"]),
        ("Microsoft Edge", ["--app=", "--window-size=1600,1000"]),
    ],
    "linux": [
        ("google-chrome", ["--app=", "--window-size=1600,1000"]),
        ("chromium", ["--app=", "--window-size=1600,1000"]),
        ("microsoft-edge", ["--app=", "--window-size=1600,1000"]),
    ],
    "windows": [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
         ["--app=", "--window-size=1600,1000"]),
        (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
         ["--app=", "--window-size=1600,1000"]),
    ],
}


def hud_url(host: str = "127.0.0.1", port: int = 8787, path: str = "/") -> str:
    """The HUD's URL."""
    shown = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{shown}:{port}{path}"


def server_is_up(host: str = "127.0.0.1", port: int = 8787, timeout: float = 2.0) -> bool:
    """Is the HUD server accepting connections?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_server(
    host: str = "127.0.0.1", port: int = 8787, timeout: float = 30.0
) -> bool:
    """Wait for the server to accept connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_is_up(host, port):
            return True
        time.sleep(0.25)
    return False


def _find_app_browser(url: str) -> bool:
    """Try to open the HUD in an application-mode browser window."""
    system = platform.system().lower()
    candidates = _APP_BROWSERS.get(system, [])
    for command, flags in candidates:
        executable = command
        if system == "windows":
            if not Path(command).exists():
                continue
        else:
            import shutil

            resolved = shutil.which(command)
            if not resolved:
                continue
            executable = resolved
        args = [executable, *[f.replace("=", f"={url}", 1) if f.endswith("=") else f
                              for f in flags]]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def open_hud(url: str, *, app_mode: bool = True) -> str:
    """Open the HUD.

    Returns ``"app"`` when an application window was opened, ``"browser"``
    when the default browser was used, or ``"manual"`` when it could not
    be opened automatically.
    """
    if app_mode and _find_app_browser(url):
        return "app"
    try:
        opened = webbrowser.open(url)
        if opened:
            return "browser"
    except Exception:
        pass
    return "manual"


def gui_status(host: str = "127.0.0.1", port: int = 8787) -> dict[str, Any]:
    """Report whether the HUD is reachable."""
    url = hud_url(host, port)
    up = False
    detail = ""
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            up = resp.status < 500
            detail = f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        up = exc.code < 500
        detail = f"HTTP {exc.code}"
    except Exception as exc:
        detail = str(exc)
    return {
        "url": url,
        "reachable": up,
        "detail": detail,
        "port_in_use": server_is_up(host, port),
    }


def launch_gui(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    auto_start: bool = True,
    app_mode: bool = True,
    wait: float = 30.0,
) -> dict[str, Any]:
    """Start the console if needed and open the HUD."""
    url = hud_url(host, port)
    started = False

    if not server_is_up(host, port):
        if not auto_start:
            return {
                "url": url,
                "started": False,
                "opened": "manual",
                "detail": "The server is not running and auto-start is off.",
            }
        started = _spawn_server(host, port)
        if started and not wait_for_server(host, port, wait):
            return {
                "url": url,
                "started": True,
                "opened": "manual",
                "detail": (
                    "The console started but did not become reachable in time. "
                    "Check the logs."
                ),
            }

    mode = open_hud(url, app_mode=app_mode)
    return {
        "url": url,
        "started": started,
        "opened": mode,
        "detail": {
            "app": "Opened in an application window.",
            "browser": "Opened in the default browser.",
            "manual": f"Open {url} yourself.",
        }[mode],
    }


def _spawn_server(host: str, port: int) -> bool:
    """Start the console as a detached child process."""
    env = dict(os.environ)
    env.setdefault("PSYCHO_CARLA_HOME", str(_default_home()))
    log_dir = _default_home() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        out = open(log_dir / "gui-console.log", "ab")
        err = open(log_dir / "gui-console.err.log", "ab")
    except Exception:
        out = err = subprocess.DEVNULL  # type: ignore[assignment]
    try:
        subprocess.Popen(  # noqa: S603 - our own command
            [sys.executable, "-m", "psycho_carla", "serve"],
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def _default_home() -> Path:
    override = os.environ.get("PSYCHO_CARLA_HOME")
    if override:
        return Path(override)
    return Path.home() / ".psycho_carla"


__all__ = [
    "gui_status",
    "hud_url",
    "launch_gui",
    "open_hud",
    "server_is_up",
    "wait_for_server",
]
