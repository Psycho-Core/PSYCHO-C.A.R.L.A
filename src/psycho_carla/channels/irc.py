"""
IRC channel.

IRC is the simplest possible text transport: a TCP socket, a few
commands, and a nick. No OAuth, no webhook, no cloud. It is included
because it still works, and because it is a good test of the channel
contract — nothing platform-specific to lean on.

Setup::

    carla channels add irc --host irc.libera.chat --port 6697 \\
        --nick carla --channels "#openjarvis"

The nick must be registered with NickServ on most networks, or the
server will refuse the connection.
"""

from __future__ import annotations

import socket
import ssl
import threading
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .registry import register_channel

#: IRC's practical line length, including the command prefix.
MAX_MESSAGE_LENGTH = 400


@register_channel("irc")
class IRCChannel(BaseChannel):
    """A plain IRC client."""

    channel_id = "irc"
    requires_auth = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._host: str = str(self.options.get("host", "") or "")
        self._port: int = int(self.options.get("port", 6697) or 6697)
        self._nick: str = str(self.options.get("nick", "carla") or "carla")
        self._use_tls: bool = bool(self.options.get("tls", True))
        self._password: str = str(self.options.get("password", "") or "")
        self._channels: list[str] = [
            str(c) for c in (self.options.get("channels") or []) if c
        ]
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._buffer = b""
        self._last_error: str = ""
        self._joined: set[str] = set()

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._host:
            missing.append("host")
        if not self._channels:
            missing.append("channels")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "IRC needs a host and at least one channel."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self.set_status(ChannelStatus.CONNECTING)
        try:
            sock = socket.create_connection((self._host, self._port), timeout=15)
            if self._use_tls:
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=self._host)
        except OSError as exc:
            self._last_error = f"Could not reach {self._host}:{self._port} — {exc}"
            self.set_status(ChannelStatus.ERROR)
            return

        self._socket = sock
        self._send(f"NICK {self._nick}")
        self._send(f"USER {self._nick} 0 * :C.A.R.L.A.")
        if self._password:
            self._send(f"PASS {self._password}")
        for channel in self._channels:
            self._send(f"JOIN {channel}")

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._receive_loop, name="carla-irc", daemon=True
        )
        self._thread.start()
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._send("QUIT :leaving")
            except Exception:
                pass
            try:
                self._socket.close()
            except Exception:
                pass
        self._socket = None
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.set_status(ChannelStatus.DISCONNECTED)

    # -- messaging ---------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        # Split long lines — IRC servers drop over-long lines.
        text = content.strip()
        if not text:
            return False

        for line in _split_lines(text, MAX_MESSAGE_LENGTH - len(self._nick) - 2):
            try:
                self._send(f"PRIVMSG {channel} :{line}")
            except OSError as exc:
                self._last_error = str(exc)
                return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return sorted(self._joined or set(self._channels))

    # -- wire --------------------------------------------------------------

    def _send(self, line: str) -> None:
        if self._socket is None:
            raise OSError("IRC socket is not connected.")
        self._socket.sendall((line + "\r\n").encode("utf-8"))

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._socket is None:
                    return
                chunk = self._socket.recv(4096)
                if not chunk:
                    self._last_error = "The IRC server closed the connection."
                    self.set_status(ChannelStatus.ERROR)
                    return
                self._buffer += chunk
            except OSError as exc:
                if not self._stop.is_set():
                    self._last_error = str(exc)
                    self.set_status(ChannelStatus.ERROR)
                return

            while b"\r\n" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\r\n", 1)
                line = raw.decode("utf-8", "replace")
                if line.startswith("PING"):
                    self._send(f"PONG {line[5:].strip()}")
                    continue
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        if not line.startswith("PRIVMSG"):
            if " JOIN " in f" {line} ":
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "JOIN":
                    self._joined.add(parts[2])
            return

        try:
            prefix, _, rest = line.partition(" ")
            command, _, message = rest.partition(" :")
        except ValueError:
            return

        if command != "PRIVMSG":
            return

        sender = prefix.split("!", 1)[0]
        target = command.split()[0] if command.split() else ""
        content = message.strip()
        if not content:
            return

        inbound = ChannelMessage(
            channel="irc",
            sender=sender,
            content=content,
            message_id="",
            conversation_id=target,
            metadata={"target": target},
        )
        reply = self.deliver(inbound)
        if reply:
            self.send(target, reply)


def _split_lines(text: str, limit: int) -> list[str]:
    """Split text into IRC-safe lines."""
    limit = max(1, limit)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        while len(paragraph) > limit:
            cut = paragraph.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            lines.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        lines.append(paragraph)
    return lines


__all__ = ["IRCChannel"]
