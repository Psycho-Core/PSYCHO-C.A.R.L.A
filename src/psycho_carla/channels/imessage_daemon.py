"""
Apple Messages on macOS — polling daemon.

Upstream treats iMessage as a *daemon* rather than an ordinary channel:
it polls the macOS Messages SQLite database for inbound rows and sends
outbound messages through AppleScript. Both halves are implemented here,
along with the process-lifecycle helpers (``start``/``stop``/``is_running``)
so the channel can be supervised like the other background services.

macOS-only. On other platforms the channel reports itself unconfigured
rather than raising.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .registry import register_channel

logger = logging.getLogger(__name__)

#: Where the macOS Messages database lives by default.
DEFAULT_DB_PATH = str(Path.home() / "Library" / "Messages" / "chat.db")

#: How often the daemon polls the database, in seconds.
POLL_INTERVAL = 5.0

#: Upper bound on messages held in the in-memory buffer.
MAX_BUFFER = 500


def poll_new_messages(
    *,
    db_path: str = DEFAULT_DB_PATH,
    last_rowid: int = 0,
    chat_identifier: str = "",
) -> List[Dict[str, Any]]:
    """Return messages received since *last_rowid*.

    The macOS Messages database is readable read-only, so this works
    without the database being unlocked by Messages itself. Returns an
    empty list when the database is absent or unreadable — on
    non-macOS hosts it simply never exists.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            "SELECT m.ROWID as rowid, m.text, m.date, "
            "c.chat_identifier "
            "FROM message m "
            "JOIN chat_message_join cmj "
            "ON cmj.message_id = m.ROWID "
            "JOIN chat c ON c.ROWID = cmj.chat_id "
            "WHERE m.ROWID > ? AND m.is_from_me = 0 "
            "AND m.text IS NOT NULL "
            "AND c.chat_identifier = ? "
            "ORDER BY m.ROWID ASC",
            (last_rowid, chat_identifier),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        logger.debug("Could not read the Messages database", exc_info=True)
        return []
    finally:
        conn.close()


def send_imessage(chat_identifier: str, message: str) -> bool:
    """Send an iMessage via AppleScript.

    *chat_identifier* is the recipient handle — a phone number in E.164
    format, or an email address registered with iMessage.
    """
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Messages"\n'
        "  set targetService to 1st account whose service type = iMessage\n"
        f'  set targetBuddy to participant "{chat_identifier}" of targetService\n'
        f'  send "{escaped}" to targetBuddy\n'
        "end tell"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.error("Failed to invoke osascript for iMessage send")
        return False

    if result.returncode != 0:
        logger.error(
            "AppleScript iMessage send failed (rc=%s): %s",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return False
    return True


def _get_max_rowid(db_path: str) -> int:
    """The highest message ROWID in the database, or 0 if unavailable."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0
    try:
        row = conn.execute("SELECT MAX(ROWID) FROM message").fetchone()
        return int(row[0] or 0) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


@register_channel("imessage_daemon")
class IMessageDaemonChannel(BaseChannel):
    """Apple Messages on macOS, driven by AppleScript and a DB poller.

    macOS-only — it uses ``osascript`` to talk to the Messages app.
    """

    channel_id = "imessage_daemon"
    requires_auth = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._target: str = str(self.options.get("target", "") or "")
        self._db_path: str = str(
            self.options.get("db_path", "") or DEFAULT_DB_PATH
        )
        self._poll_interval: float = float(
            self.options.get("poll_interval", "") or POLL_INTERVAL
        )
        self._last_error: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_rowid: int = 0
        self._buffer: List[ChannelMessage] = []

    # -- configuration -------------------------------------------------------

    def missing_credentials(self) -> list[str]:
        import sys

        if sys.platform != "darwin":
            return ["macOS only"]
        if not self._target:
            return ["target"]
        return []

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "iMessage needs macOS and a target chat."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self.stop_daemon()
        self.set_status(ChannelStatus.DISCONNECTED)

    # -- sending -------------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        target = conversation_id or channel or self._target
        ok = send_imessage(target, content)
        if not ok:
            self._last_error = "AppleScript iMessage send failed."
        return ok

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return [self._target] if self._target else []

    # -- inbound -------------------------------------------------------------

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        """Accept an inbound message supplied directly."""
        content = str(payload.get("text", "") or "").strip()
        if not content:
            return None
        sender = str(payload.get("sender", "") or self._target)
        inbound = ChannelMessage(
            channel="imessage_daemon",
            sender=sender,
            content=content,
            conversation_id=sender,
            metadata={},
        )
        self._buffer.append(inbound)
        return self.deliver(inbound)

    def get_messages(self) -> List[ChannelMessage]:
        """The inbound messages seen by the poller."""
        return self._buffer

    def clear_messages(self) -> None:
        self._buffer.clear()

    # -- daemon --------------------------------------------------------------

    def is_running(self) -> bool:
        """True when the polling thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def stop_daemon(self) -> bool:
        """Stop the polling thread. Returns True if it was running."""
        if self._thread is None:
            return False
        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        return True

    def run_daemon(
        self,
        *,
        chat_identifier: str = "",
        db_path: str = "",
        poll_interval: float = 0.0,
        max_iterations: int = 0,
    ) -> None:
        """Poll the Messages database until stopped.

        Blocks the calling thread, so :meth:`start_daemon` is the usual
        entry point.
        """
        target = chat_identifier or self._target
        path = db_path or self._db_path
        interval = poll_interval or self._poll_interval

        self._last_rowid = _get_max_rowid(path)
        logger.info(
            "iMessage daemon started — monitoring %s from ROWID %d",
            target, self._last_rowid,
        )

        iterations = 0
        while not self._stop_event.is_set():
            if max_iterations and iterations >= max_iterations:
                break
            iterations += 1

            for row in poll_new_messages(
                db_path=path,
                last_rowid=self._last_rowid,
                chat_identifier=target,
            ):
                self._last_rowid = max(self._last_rowid, int(row.get("rowid", 0)))
                content = str(row.get("text", "") or "").strip()
                if not content:
                    continue
                inbound = ChannelMessage(
                    channel="imessage_daemon",
                    sender=target,
                    content=content,
                    conversation_id=target,
                    metadata={"date": row.get("date", "")},
                )
                self._buffer.append(inbound)
                if len(self._buffer) > MAX_BUFFER:
                    del self._buffer[:-MAX_BUFFER]
                self.deliver(inbound)

            self._stop_event.wait(interval)

        logger.info("iMessage daemon stopped")

    def start_daemon(
        self,
        *,
        chat_identifier: str = "",
        db_path: str = "",
        poll_interval: float = 0.0,
    ) -> bool:
        """Start the poller on a background thread."""
        if self.is_running():
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_daemon,
            kwargs={
                "chat_identifier": chat_identifier,
                "db_path": db_path,
                "poll_interval": poll_interval,
            },
            daemon=True,
        )
        self._thread.start()
        return True


# Module-level daemon handle, mirroring upstream's process-wide API.
_daemon_channel: Optional[IMessageDaemonChannel] = None


def is_running() -> bool:
    """True when the module-level iMessage daemon is running."""
    return _daemon_channel is not None and _daemon_channel.is_running()


def run_daemon(
    *,
    chat_identifier: str = "",
    db_path: str = "",
    poll_interval: float = 0.0,
    max_iterations: int = 0,
) -> None:
    """Run the iMessage polling daemon (blocking).

    This is the module-level entry point, matching upstream's API. Use
    :func:`start_daemon` to run it on a background thread instead.
    """
    global _daemon_channel
    if _daemon_channel is None:
        _daemon_channel = IMessageDaemonChannel({"target": chat_identifier})
    _daemon_channel.run_daemon(
        chat_identifier=chat_identifier,
        db_path=db_path,
        poll_interval=poll_interval,
        max_iterations=max_iterations,
    )


def stop_daemon() -> bool:
    """Stop the module-level iMessage daemon."""
    if _daemon_channel is None:
        return False
    return _daemon_channel.stop_daemon()


def start_daemon(
    *,
    target: str = "",
    db_path: str = "",
    poll_interval: float = 0.0,
) -> IMessageDaemonChannel:
    """Start a module-level iMessage daemon and return its channel."""
    global _daemon_channel
    if _daemon_channel is None:
        _daemon_channel = IMessageDaemonChannel({"target": target})
    _daemon_channel.start_daemon(
        chat_identifier=target,
        db_path=db_path,
        poll_interval=poll_interval,
    )
    return _daemon_channel


__all__ = [
    "DEFAULT_DB_PATH",
    "IMessageDaemonChannel",
    "POLL_INTERVAL",
    "is_running",
    "poll_new_messages",
    "run_daemon",
    "send_imessage",
    "start_daemon",
    "stop_daemon",
]
