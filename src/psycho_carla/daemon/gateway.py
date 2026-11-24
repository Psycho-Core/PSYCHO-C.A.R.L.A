"""
GatewayDaemon — owns the runtime, scheduler and HTTP server as one unit.

The daemon is what makes `carla daemon start` different from
`carla serve`: it supervises the process, restarts on failure, and
shuts everything down in the right order.

It also defines the **session key** scheme, which is how a conversation is
identified across channels: the same person on the HUD and on the CLI
resolves to the same session key when their channel ids are linked.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..core.config import CarlaConfig, load_config


class GatewayDaemon:
    """Composes the runtime, scheduler and server into a supervised daemon."""

    def __init__(
        self,
        config: CarlaConfig | None = None,
        *,
        runtime: Any = None,
    ) -> None:
        self.config = config or load_config()
        self._runtime = runtime
        self._server: Any = None
        self._running = False
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._started_at = 0.0

    # -- session keys -------------------------------------------------------- #
    @staticmethod
    def session_key(
        platform: str,
        chat_type: str,
        chat_id: str,
        thread_id: str | None = None,
    ) -> str:
        """The canonical session key for a conversation.

        The shape is stable across channels: ``agent:main:<platform>:
        <chat_type>:<chat_id>:<thread_id>``.
        """
        return f"agent:main:{platform}:{chat_type}:{chat_id}:{thread_id}"

    # -- lifecycle ----------------------------------------------------------- #
    @property
    def running(self) -> bool:
        return self._running

    @property
    def uptime(self) -> float:
        return time.time() - self._started_at if self._started_at else 0.0

    @property
    def runtime(self) -> Any:
        return self._runtime

    def start(self, *, background: bool = False) -> None:
        """Start the daemon.

        With ``background=True`` the console is launched as a detached
        child process. Otherwise the daemon runs in the foreground and
        blocks until stopped.
        """
        with self._lock:
            if self._running:
                return
            self._running = True
            self._started_at = time.time()
            self._stop_event.clear()

        if background:
            self._spawn_background()
            return

        self._run_foreground()

    def _spawn_background(self) -> None:
        """Launch the console as a detached child process."""
        from .service import console_command

        cmd = console_command()
        log_dir = Path(self.config.home) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        out = open(log_dir / "console.log", "ab")
        err = open(log_dir / "console.err.log", "ab")
        env = dict(os.environ)
        env.setdefault("PSYCHO_CARLA_HOME", str(self.config.home))
        try:
            subprocess.Popen(  # noqa: S603 - the command is ours, not user input
                cmd,
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except Exception:
            self._running = False
            raise
        finally:
            out.close()
            err.close()

    def _run_foreground(self) -> None:
        """Run the console in the foreground until interrupted."""
        from ..core.runtime import CarlaRuntime
        from ..server.app import create_app

        rt = self._runtime or CarlaRuntime(self.config)
        self._runtime = rt
        rt.start()

        import uvicorn

        server_cfg = self.config.server
        uvicorn.run(
            create_app(self.config, runtime=rt),
            host=server_cfg.host,
            port=server_cfg.port,
            log_level=self.config.log_level.lower(),
        )

    def stop(self) -> None:
        """Stop the daemon and release its resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_event.set()
        if self._runtime is not None:
            try:
                self._runtime.stop()
            except Exception:
                pass

    def request_stop(self, *_: Any) -> None:
        """Signal handler entry point."""
        self.stop()
        sys.exit(0)

    def install_signal_handlers(self) -> None:
        """Stop cleanly on SIGINT/SIGTERM."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                # Not on the main thread — nothing to do.
                pass

    # -- status ---------------------------------------------------------------- #
    def status(self) -> dict[str, Any]:
        """A status report for `carla daemon status`."""
        report: dict[str, Any] = {
            "running": self._running,
            "uptime_seconds": round(self.uptime, 1),
            "home": str(self.config.home),
            "pid": os.getpid(),
        }
        if self._runtime is not None:
            try:
                report["runtime"] = self._runtime.status()
            except Exception:
                pass
        return report


def write_pid_file(path: str | Path) -> Path:
    """Write the current PID to a file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")
    return p


def read_pid_file(path: str | Path) -> int:
    """Read a PID from a file, or 0 when it is absent or unreadable."""
    p = Path(path)
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def is_process_alive(pid: int) -> bool:
    """Is a PID still running?"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


__all__ = [
    "GatewayDaemon",
    "is_process_alive",
    "read_pid_file",
    "write_pid_file",
]
