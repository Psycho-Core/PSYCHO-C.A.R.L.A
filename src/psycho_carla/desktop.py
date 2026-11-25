"""
PSYCHO-C.A.R.L.A. — native desktop application.

This is the **graphical entry point**. Double-clicking the built executable
(or running ``python -m psycho_carla.desktop``) opens a real native window
containing the holographic HUD. No terminal, no commands.

What it does:

1. Starts the FastAPI server in a background thread.
2. Opens a native window (pywebview → WebView2 on Windows 11).
3. Provides a real menu bar — Models, Engine, Voice, Window, Help — so
   every action the console offers is reachable by mouse.

The GUI is intentionally thin — the HUD is the UI, and this module is the
window, the menu and the lifecycle around it. That keeps the web HUD
portable (it still runs standalone via ``carla gui export``) while giving
Windows users a real application.

Falls back to the browser launcher if ``pywebview`` is unavailable, so the
app still works on a machine where WebView2 is missing.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any

from .gui.launcher import hud_url, server_is_up

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

WINDOW_TITLE = "PSYCHO-C.A.R.L.A."


def log_path() -> "os.PathLike[str]":
    """``<home>/logs/desktop.log`` — the windowed exe has no console, so errors go here."""
    from .core.config import default_config_path

    d = default_config_path().parent / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "desktop.log"


def ensure_std_streams() -> None:
    """Give a windowed (no-console) exe real ``stdout``/``stderr`` objects.

    PyInstaller's ``console=False`` build starts with ``sys.stdout`` and
    ``sys.stderr`` set to ``None``; uvicorn's logging setup calls
    ``sys.stdout.isatty()`` and the server then never starts. Output is
    discarded; anything written to stderr goes to the desktop log instead.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        try:
            sys.stderr = open(log_path(), "a", encoding="utf-8", buffering=1)
        except Exception:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")


def _log(message: str) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def _start_server(host: str, port: int) -> tuple[bool, str]:
    """Start the HUD server in a daemon thread. Returns (ok, message)."""
    try:
        from .server.app import create_app
    except Exception as exc:  # pragma: no cover - import guard
        import traceback

        _log("could not import the server:\n" + traceback.format_exc())
        return (False, f"server unavailable: {exc}")

    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover
        return (False, f"uvicorn unavailable: {exc}")

    def _run() -> None:
        try:
            uvicorn.run(
                create_app(),
                host=host,
                port=port,
                log_level="warning",
                access_log=False,
            )
        except BaseException:
            import traceback

            _log("server thread crashed:\n" + traceback.format_exc())

    thread = threading.Thread(target=_run, name="carla-hud", daemon=True)
    thread.start()
    return (True, "")


def _wait_for_http(host: str, port: int, timeout: float = 30.0) -> bool:
    """Poll the server until it answers, rather than sleeping blindly."""
    deadline = time.monotonic() + timeout
    url = f"http://{host}:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _api_get(host: str, port: int, path: str) -> Any:
    """GET a JSON endpoint from the local server."""
    url = f"http://{host}:{port}{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_post(host: str, port: int, path: str, payload: dict[str, Any]) -> Any:
    """POST JSON to the local server."""
    url = f"http://{host}:{port}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------

class DesktopApp:
    """The native shell around the HUD."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        open_browser_fallback: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.open_browser_fallback = open_browser_fallback
        self.window: Any = None
        self._server_started = False
        self._voice_on = False

    # -- lifecycle ---------------------------------------------------------

    def ensure_server(self) -> bool:
        """Make sure the HUD server is up. Returns True if reachable."""
        if server_is_up(self.host, self.port, timeout=1.0):
            return True

        ok, err = _start_server(self.host, self.port)
        if not ok:
            print(f"[CARLA] {err}", file=sys.stderr)
            return False

        self._server_started = True
        return _wait_for_http(self.host, self.port, timeout=30.0)

    # -- window ------------------------------------------------------------

    def _build_window(self) -> Any:
        import webview

        self.window = webview.create_window(
            WINDOW_TITLE,
            hud_url(self.host, self.port),
            width=1600,
            height=1000,
            min_size=(1100, 700),
            confirm_close=False,
            background_color="#05070d",
        )
        return self.window

    # -- the menu ----------------------------------------------------------

    def _menu(self) -> Any:
        """Build the native menu bar.

        Every entry maps to an action the user can take without typing
        anything. The model entries are read live from the catalogue so
        the menu always reflects what this machine can actually run.
        """
        import webview

        def _open(path: str) -> None:
            """Send the HUD to a route."""
            try:
                self.window.evaluate_js(
                    "window.location.hash = '%s';" % path
                )
            except Exception:
                pass

        def _models_menu() -> Any:
            """The Models submenu, built from the live catalogue."""
            items = []
            try:
                data = _api_get(self.host, self.port, "/api/models?engine=native_onnx")
                for spec in data.get("models", []):
                    model_id = spec.get("model_id", "")
                    items.append((
                        f"Download {model_id}",
                        lambda mid=model_id: self._download_native(mid),
                    ))
            except Exception:
                pass

            items.append(("---", None))
            items.append((
                "Model Manager…",
                lambda: self._open("/models"),
            ))
            return webview.menu(items)

        def _installed_menu() -> Any:
            """Start, stop and remove the models on this machine."""
            items = []
            try:
                data = _api_get(self.host, self.port, "/api/models/local")
            except Exception:
                data = {}
            models = data.get("models", [])
            for model in models:
                model_id = model.get("model_id", "")
                if not model_id:
                    continue
                if model.get("running"):
                    items.append((
                        f"Stop {model_id}",
                        lambda mid=model_id: self._stop_model(mid),
                    ))
                else:
                    items.append((
                        f"Start {model_id}",
                        lambda mid=model_id: self._start_model(mid),
                    ))
            if not items:
                items.append(("No models installed yet", None))

            # Downloads that never finished can be cleared out.
            partials = data.get("partial", [])
            if partials:
                items.append(("---", None))
                items.append((
                    f"Clean up {len(partials)} partial download"
                    f"{'s' if len(partials) != 1 else ''}",
                    lambda: self._clear_partials(),
                ))
            return webview.menu(items)

        def _engine_menu() -> Any:
            """Which engine the console talks to."""
            items = []
            try:
                data = _api_get(self.host, self.port, "/api/models/local")
                engines = sorted({
                    m.get("engine", "") for m in data.get("models", [])
                })
            except Exception:
                engines = []
            for engine in engines:
                items.append((
                    engine,
                    lambda e=engine: self._open("/models"),
                ))
            if not items:
                items.append(("No models installed yet", None))
            return webview.menu(items)

        def _voice_menu() -> Any:
            items = [
                (
                    "Toggle Voice" + ("  (on)" if self._voice_on else ""),
                    self._toggle_voice,
                ),
                ("Wake Word: ONKEN", lambda: self._open("/voice")),
            ]
            return webview.menu(items)

        return webview.menu(
            "C.A.R.L.A.",
            [
                "File",
                [
                    ("Open Console", lambda: _open("/console")),
                    ("Model Manager…", lambda: _open("/models")),
                    ("---",
                     None),
                    ("Quit", lambda: self._quit()),
                ],
                "Models",
                [
                    ("Native (runs inside C.A.R.L.A.)", _models_menu),
                    ("Installed on this machine", _installed_menu),
                    ("Installed Engines", _engine_menu),
                ],
                "Voice",
                [
                    ("Toggle Voice", self._toggle_voice),
                    ("Voice Settings…", lambda: _open("/voice")),
                ],
                "View",
                [
                    ("Reload", lambda: self._reload()),
                    ("Toggle Fullscreen",
                     lambda: self._toggle_fullscreen()),
                ],
                "Help",
                [
                    ("Open Logs Folder", self._open_logs),
                    ("About", self._show_about),
                ],
            ],
        )

    # -- menu actions ------------------------------------------------------

    def _download_native(self, model_id: str) -> None:
        """Download a native model and point the tier at it."""
        try:
            result = _api_post(
                self.host, self.port, "/api/models/pull",
                {"model_id": model_id, "engine": "native_onnx"},
            )
        except Exception as exc:
            self._notify(f"Download failed: {exc}")
            return

        if not result.get("ok"):
            self._notify(f"Download failed: {result.get('message')}")
            return

        try:
            folder = (result.get("path") or "").rstrip("/").split("/")[-1]
            _api_post(
                self.host, self.port, "/api/models/activate",
                {"model_id": folder, "tier": "fast_tier"},
            )
        except Exception:
            pass

        self._notify(f"{model_id} is ready. Restart the console to use it.")

    def _start_model(self, model_id: str) -> None:
        """Load a model into memory so it is ready to answer."""
        try:
            result = _api_post(
                self.host, self.port, "/api/models/start",
                {"model_id": model_id},
            )
            self._notify(result.get("message", f"started {model_id}"))
        except Exception as exc:
            self._notify(f"could not start {model_id}: {exc}")

    def _stop_model(self, model_id: str) -> None:
        """Unload the model and give the memory back."""
        try:
            result = _api_post(
                self.host, self.port, "/api/models/stop",
                {"model_id": model_id},
            )
            self._notify(result.get("message", f"stopped {model_id}"))
        except Exception as exc:
            self._notify(f"could not stop {model_id}: {exc}")

    def _clear_partials(self) -> None:
        """Delete the downloads that never finished."""
        try:
            result = _api_post(
                self.host, self.port, "/api/models/partial/clear", {},
            )
            self._notify(result.get("message", "cleaned up"))
        except Exception as exc:
            self._notify(f"could not clean up: {exc}")

    def _toggle_voice(self) -> None:
        self._voice_on = not self._voice_on
        state = "on" if self._voice_on else "off"
        self._notify(f"Voice {state}.")

    def _reload(self) -> None:
        try:
            self.window.evaluate_js("window.location.reload();")
        except Exception:
            pass

    def _toggle_fullscreen(self) -> None:
        try:
            self.window.toggle_fullscreen()
        except Exception:
            pass

    def _open_logs(self) -> None:
        try:
            from .utils.paths import carla_home

            logs = carla_home() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(logs))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{logs}"')
            else:
                os.system(f'xdg-open "{logs}"')
        except Exception:
            self._notify("Could not open the logs folder.")

    def _show_about(self) -> None:
        try:
            import webview

            webview.create_confirmation_dialog(
                "PSYCHO-C.A.R.L.A.",
                "A local-first AI console.\n\n"
                "Models can be downloaded and run inside the app itself — "
                "no Ollama, no external server.\n\n"
                "Everything stays on this machine.",
            )
        except Exception:
            pass

    def _notify(self, message: str) -> None:
        """Show a short message to the user."""
        try:
            import webview

            webview.create_confirmation_dialog("C.A.R.L.A.", message)
        except Exception:
            print(f"[CARLA] {message}")

    def _quit(self) -> None:
        try:
            self.window.destroy()
        except Exception:
            os._exit(0)

    # -- the bridge to the HUD ---------------------------------------------

    def _install_api(self) -> Any:
        """Expose a small API the HUD's buttons can call."""
        import webview

        @webview.expose
        def carla_action(action: str) -> str:
            """Called by the HUD's in-app buttons."""
            if action == "open_console":
                webbrowser.open(hud_url(self.host, self.port, "/console"))
                return "ok"
            if action == "open_models":
                try:
                    self.window.evaluate_js(
                        "document.getElementById('modelMgr')"
                        "?.classList.add('open');"
                    )
                except Exception:
                    pass
                return "ok"
            return "unknown"

        return carla_action

    # -- run ---------------------------------------------------------------

    def serve_only(self) -> int:
        """Run just the HUD server, with no window (for a server PC or a smoke test)."""
        if not self.ensure_server():
            _log("serve_only: the HUD server could not start")
            return 1
        print(f"[CARLA] serving on {hud_url(self.host, self.port)} — Ctrl+C to stop")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            return 0

    def run(self) -> int:
        """Start the server and open the window. Blocks until closed."""
        if not self.ensure_server():
            print(
                "[CARLA] The HUD server could not start.\n"
                "         Run 'carla doctor' to diagnose the install.",
                file=sys.stderr,
            )
            return 1

        url = hud_url(self.host, self.port)

        try:
            import webview  # noqa: F401
        except ImportError:
            if not self.open_browser_fallback:
                print(
                    "[CARLA] pywebview is not installed.\n"
                    "         Install it with:  pip install pywebview\n"
                    "         On Windows 11 this also needs the WebView2 "
                    "runtime (preinstalled by default).",
                    file=sys.stderr,
                )
                return 2
            print(
                "[CARLA] pywebview is not installed — opening the HUD in "
                "your browser instead.\n"
                "         For a real application window: pip install pywebview",
                file=sys.stderr,
            )
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                return 0

        try:
            window = self._build_window()
        except Exception as exc:
            _log(f"could not create the window: {exc!r}")
            print(f"[CARLA] Could not create the window: {exc}", file=sys.stderr)
            webbrowser.open(url)
            return 1

        try:
            webview.start(self._install_api(), window, menu=self._menu())
        except Exception:
            # No menu support on this platform — the window still works.
            try:
                webview.start(self._install_api(), window)
            except Exception as exc:
                _log(f"webview.start failed: {exc!r}")
                return 1
        return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the frozen executable."""
    ensure_std_streams()
    argv = list(sys.argv[1:] if argv is None else argv)

    host = os.environ.get("CARLA_HOST", DEFAULT_HOST)
    port = int(os.environ.get("CARLA_PORT", DEFAULT_PORT))

    args = list(argv)
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            port = int(args[idx + 1])
    if "--host" in args:
        idx = args.index("--host")
        if idx + 1 < len(args):
            host = args[idx + 1]

    _log(f"starting (port {port}, args {args})")
    app = DesktopApp(host=host, port=port)
    try:
        if "--no-window" in args:
            return app.serve_only()
        return app.run()
    except SystemExit:
        raise
    except BaseException:
        import traceback

        _log("crashed:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(main())
