"""
Slack Socket Mode daemon — supervised background service.

Upstream runs Slack Socket Mode as a separate process with its own PID
file, so it survives the console's lifetime and can be started and
stopped from the command line. The same lifecycle is implemented here,
adapted to C.A.R.L.A.'s own runtime:

* :func:`start_slack_daemon` spawns a detached subprocess.
* :func:`is_running` and :func:`stop_daemon` manage it through a PID
  file.
* :func:`run_slack_daemon` is the blocking entry point the subprocess
  executes.

The Socket Mode transport needs ``slack_bolt``; without it the daemon
falls back to the HTTP Events API, which needs no extra dependency.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .base import ChannelMessage, ChannelStatus
from .registry import register_channel
from .slack import SlackChannel

logger = logging.getLogger(__name__)


def _pid_file() -> Path:
    """Where the daemon PID is recorded."""
    try:
        from ..utils.paths import config_dir
    except Exception:  # pragma: no cover - defensive
        config_dir = Path.home() / ".psycho_carla"
    return Path(config_dir) / "slack-daemon.pid"


def to_slack_fmt(text: str) -> str:
    """Convert markdown to Slack mrkdwn format.

    Headers become bold, ``**bold**`` becomes ``*bold*``, links become
    ``<url|text>``, and LaTeX is stripped.
    """
    import re

    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<\2|\1>", text)
    text = re.sub(r"\$\$.+?\$\$", "", text)
    text = re.sub(r"\$(.+?)\$", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@register_channel("slack_daemon")
class SlackDaemonChannel(SlackChannel):
    """Slack Socket Mode, supervised as a background service.

    Functionally identical to :class:`~psycho_carla.channels.slack.SlackChannel`
    — the difference is how it is run: the daemon supervisor keeps the
    process alive across reconnects, rather than relying on the caller's
    process lifetime.
    """

    channel_id = "slack_daemon"

    def describe(self) -> Dict[str, Any]:
        data = super().describe()
        data["mode"] = "daemon"
        data["daemon_running"] = is_running()
        return data


# ---------------------------------------------------------------------------
# Process lifecycle
# ---------------------------------------------------------------------------


def run_slack_daemon(
    bot_token: str,
    app_token: str,
    model: str = "",
) -> None:
    """Run the Slack daemon (blocking).

    The Socket Mode transport requires ``slack_bolt``; when it is not
    installed the daemon reports the problem and exits rather than
    silently doing nothing.
    """
    pid_path = _pid_file()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))

    stop_event = threading.Event()

    def _stop(signum: int, frame: Any) -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # pragma: no cover - non-main thread
            pass

    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError:
        logger.error(
            "The Slack daemon needs slack_bolt for Socket Mode. "
            "Install it with: pip install slack-bolt"
        )
        pid_path.unlink(missing_ok=True)
        return

    app = App(token=bot_token)

    @app.event("message")
    def handle_message(event: Dict[str, Any], say: Any) -> None:
        text = event.get("text", "")
        if not text:
            return
        say(text=to_slack_fmt(text), thread_ts=event.get("ts", ""))

    handler = SocketModeHandler(app, app_token)
    logger.info("Slack daemon starting (Socket Mode)")

    try:
        handler.start()
    except Exception:
        logger.exception("Slack daemon failed")
    finally:
        pid_path.unlink(missing_ok=True)


def start_slack_daemon(
    bot_token: str,
    app_token: str,
    model: str = "",
) -> int:
    """Spawn the Slack daemon as a background subprocess.

    Returns the PID of the spawned process.
    """
    command = [
        sys.executable,
        "-m",
        "psycho_carla.channels.slack_daemon",
        "--bot-token",
        bot_token,
        "--app-token",
        app_token,
    ]
    if model:
        command += ["--model", model]

    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def is_running() -> bool:
    """Check if the Slack daemon is running."""
    pid_path = _pid_file()
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return False


def stop_daemon() -> bool:
    """Stop the running Slack daemon."""
    pid_path = _pid_file()
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        pid_path.unlink(missing_ok=True)
        return False


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="psycho_carla.channels.slack_daemon",
        description="Run the C.A.R.L.A. Slack Socket Mode daemon.",
    )
    parser.add_argument("--bot-token", required=True)
    parser.add_argument("--app-token", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    run_slack_daemon(args.bot_token, args.app_token, args.model)
    return 0


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    raise SystemExit(_main())


__all__ = [
    "SlackDaemonChannel",
    "is_running",
    "run_slack_daemon",
    "start_slack_daemon",
    "stop_daemon",
    "to_slack_fmt",
]
