"""
Nostr — the relay protocol. A private key is the identity.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import json
import threading

from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import truncate
from .registry import register_channel


@register_channel("nostr")
class NostrChannel(BaseChannel):
    """Nostr — the relay protocol.

    Nostr has no accounts and no auth: a private key *is* the identity.
    """

    channel_id = "nostr"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._private_key: str = str(self.options.get("private_key", "") or "")
        self._relays: list[str] = [
            str(r) for r in (self.options.get("relays") or []) if r
        ] or ["wss://relay.damus.io"]
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        return ["private_key"] if not self._private_key else []

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Nostr needs a private key."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._relay_loop, name="carla-nostr", daemon=True
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
            from nostr_pynacl import nostr_event  # type: ignore
        except ImportError:
            self._last_error = (
                "Nostr posting needs the 'nostr-pynacl' package."
            )
            return False

        event = nostr_event.Event(
            private_key=self._private_key,
            kind=1,
            content=truncate(content, 4000),
            tags=[["e", conversation_id]] if conversation_id else [],
        )
        try:
            import websockets

            async def _post() -> None:
                for relay in self._relays:
                    try:
                        async with websockets.connect(relay) as ws:
                            await ws.send(event.serialize())
                    except Exception:
                        continue

            import asyncio

            asyncio.run(_post())
        except Exception as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return list(self._relays)

    def _relay_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            self._last_error = "Nostr needs the 'websockets' package."
            self.set_status(ChannelStatus.ERROR)
            return

        import asyncio

        async def _run() -> None:
            while not self._stop.is_set():
                for relay in self._relays:
                    try:
                        async with websockets.connect(relay) as ws:
                            async for raw in ws:
                                if self._stop.is_set():
                                    return
                                self._handle_raw(raw)
                    except Exception:
                        continue
                await asyncio.sleep(3)

        try:
            asyncio.run(_run())
        except Exception as exc:
            self._last_error = str(exc)

    def _handle_raw(self, raw: Any) -> None:
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(event, list) or len(event) < 3:
            return
        if event[0] != "EVENT":
            return
        payload = event[2]
        content = str(payload.get("content", "") or "").strip()
        if not content:
            return
        inbound = ChannelMessage(
            channel="nostr",
            sender=str(payload.get("pubkey", "") or ""),
            content=content,
            message_id=str(payload.get("id", "") or ""),
            conversation_id=str(payload.get("id", "") or ""),
            metadata={},
        )
        reply = self.deliver(inbound)
        if reply:
            self.send("", reply, conversation_id=inbound.message_id)


# ---------------------------------------------------------------------------
# Twitch chat
# ---------------------------------------------------------------------------
