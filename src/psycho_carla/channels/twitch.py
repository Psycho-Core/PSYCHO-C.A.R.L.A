"""
Twitch chat — IRC over a WebSocket.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import json
import threading

from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel


@register_channel("twitch")
class TwitchChannel(BaseChannel):
    """Twitch chat — IRC over a WebSocket."""

    channel_id = "twitch"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._channels: list[str] = [
            str(c).lstrip("#").lower()
            for c in (self.options.get("channels") or []) if c
        ]
        self._nick: str = str(self.options.get("nick", "carlabot") or "carlabot")
        self._token: str = str(self.options.get("token", "") or "")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._channels:
            missing.append("channels")
        if not self._token:
            missing.append("token")
        return missing

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Twitch needs channels and an OAuth token."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._chat_loop, name="carla-twitch", daemon=True
        )
        self._thread.start()
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self.set_status(ChannelStatus.DISCONNECTED)

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        try:
            import websockets

            async def _send() -> None:
                url = "wss://irc-ws.chat.twitch.tv:443"
                async with websockets.connect(url) as ws:
                    await ws.send(f"PASS oauth:{self._token}")
                    await ws.send(f"NICK {self._nick}")
                    await ws.send(f"JOIN #{channel}")
                    await ws.send(f"PRIVMSG #{channel} :{truncate(content, 400)}")
                    await asyncio_sleep(0.5)

            import asyncio

            asyncio.run(_send())
        except Exception as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return list(self._channels)

    def _chat_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            self._last_error = "Twitch chat needs the 'websockets' package."
            self.set_status(ChannelStatus.ERROR)
            return

        import asyncio

        async def _run() -> None:
            while not self._stop.is_set():
                try:
                    async with websockets.connect(
                        "wss://irc-ws.chat.twitch.tv:443"
                    ) as ws:
                        await ws.send(f"PASS oauth:{self._token}")
                        await ws.send(f"NICK {self._nick}")
                        for channel in self._channels:
                            await ws.send(f"JOIN #{channel}")
                        async for raw in ws:
                            if self._stop.is_set():
                                return
                            self._handle_line(str(raw))
                except Exception:
                    await asyncio.sleep(5)

        try:
            asyncio.run(_run())
        except Exception as exc:
            self._last_error = str(exc)

    def _handle_line(self, line: str) -> None:
        if not line.startswith("PRIVMSG"):
            if line.startswith("PING"):
                return
            return
        try:
            prefix, _, rest = line.partition(" ")
            command, _, message = rest.partition(" :")
            target = command.split()[0] if command.split() else ""
        except (ValueError, IndexError):
            return
        content = message.strip()
        if not content:
            return
        sender = prefix.split("!", 1)[0]
        inbound = ChannelMessage(
            channel="twitch", sender=sender, content=content,
            conversation_id=target.lstrip("#"),
            metadata={},
        )
        reply = self.deliver(inbound)
        if reply:
            self.send(target.lstrip("#"), reply)


async def asyncio_sleep(seconds: float) -> None:
    """A tiny helper so the send path does not import asyncio twice."""
    import asyncio

    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# XMPP
# ---------------------------------------------------------------------------
