"""
Slack channel.

Slack delivers inbound events through an HTTP endpoint (an Event
Subscription) or through Socket Mode, which opens a WebSocket to Slack
and needs no public URL. This adapter uses **Socket Mode** by default —
it is the option that works from a home machine with no inbound
firewall.

Setup:
1. Create a Slack app at api.slack.com/apps, enable Socket Mode.
2. Add the bot token (``xoxb-…``) and an app-level token (``xapp-…``).
3. Subscribe to message events and invite the bot to the channels.

    carla channels add slack --token xoxb-… --app-token xapp-…
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel

#: Slack's chat.postMessage limit.
MAX_MESSAGE_LENGTH = 3000


@register_channel("slack")
class SlackChannel(BaseChannel):
    """Slack adapter using Socket Mode."""

    channel_id = "slack"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(self.options.get("token", "") or "")
        self._app_token: str = str(self.options.get("app_token", "") or "")
        self._base = str(self.options.get("base_url", "https://slack.com") or
                         "https://slack.com")
        self._ws_url: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._known_channels: set[str] = set()
        self._bot_user_id: str = ""
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._token:
            missing.append("token")
        if not self._app_token:
            missing.append("app_token")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Slack token and app_token are required."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self.set_status(ChannelStatus.CONNECTING)
        try:
            auth = request_json(
                build_url(self._base, "/api/auth.test"),
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=15.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            self.set_status(ChannelStatus.ERROR)
            return

        if not auth.get("ok", False):
            self._last_error = str(auth.get("error", "Slack rejected the token."))
            self.set_status(ChannelStatus.ERROR)
            return

        self._bot_user_id = str(auth.get("user_id", ""))

        try:
            mode = request_json(
                build_url(self._base, "/api/apps.connections.open"),
                headers={"Authorization": f"Bearer {self._app_token}"},
                timeout=15.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            self.set_status(ChannelStatus.ERROR)
            return

        if not mode.get("ok", False):
            self._last_error = str(
                mode.get("error", "apps.connections.open failed.")
            )
            self.set_status(ChannelStatus.ERROR)
            return

        self._ws_url = str(mode.get("url", ""))
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._socket_loop, name="carla-slack", daemon=True
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

    # -- messaging ---------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        payload: Dict[str, Any] = {
            "channel": channel,
            "text": truncate(content, MAX_MESSAGE_LENGTH),
        }
        # Slack threads via thread_ts — the timestamp of the parent message.
        thread_ts = (metadata or {}).get("thread_ts") or conversation_id
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            result = request_json(
                build_url(self._base, "/api/chat.postMessage"),
                method="POST",
                headers={"Authorization": f"Bearer {self._token}"},
                json_body=payload,
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False

        if not result.get("ok", False):
            self._last_error = str(result.get("error", "chat.postMessage failed."))
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return sorted(self._known_channels)

    # -- socket mode -------------------------------------------------------

    def _socket_loop(self) -> None:
        """Maintain the Socket Mode WebSocket."""
        import asyncio

        try:
            asyncio.run(self._async_socket_loop())
        except Exception as exc:  # pragma: no cover - transport failure
            self._last_error = str(exc)
            self.set_status(ChannelStatus.ERROR)

    async def _async_socket_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            self._last_error = (
                "The 'websockets' package is required for Slack Socket Mode."
            )
            self.set_status(ChannelStatus.ERROR)
            return

        while not self._stop.is_set():
            try:
                async with websockets.connect(self._ws_url) as ws:
                    while not self._stop.is_set():
                        raw = await ws.recv()
                        try:
                            envelope = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_envelope(ws, envelope)
            except Exception as exc:
                if self._stop.is_set():
                    return
                self._last_error = str(exc)
                self._stop.wait(5.0)

    async def _handle_envelope(self, ws: Any, envelope: dict[str, Any]) -> None:
        """Reply to the Socket Mode hello and dispatch events."""
        kind = envelope.get("type")

        if kind == "hello":
            # Acknowledge the connection.
            await ws.send(json.dumps({"type": "hello"}))
            return

        if kind != "events_api":
            return

        envelope_id = envelope.get("envelope_id")
        if envelope_id:
            await ws.send(json.dumps({"envelope_id": envelope_id}))

        event = (envelope.get("payload") or {}).get("event") or {}
        if event.get("type") not in ("message", "app_mention"):
            return

        # Never answer ourselves.
        if event.get("user") == self._bot_user_id:
            return
        if event.get("bot_id"):
            return

        text = (event.get("text") or "").strip()
        if not text:
            return

        channel = str(event.get("channel", ""))
        self._known_channels.add(channel)

        inbound = ChannelMessage(
            channel="slack",
            sender=str(event.get("user", "")),
            content=text,
            message_id=str(event.get("ts", "")),
            conversation_id=channel,
            metadata={
                "team": event.get("team", ""),
                "thread_ts": event.get("thread_ts", ""),
                "user_name": event.get("user_name", ""),
            },
        )

        reply = self.deliver(inbound)
        if reply:
            self.send(
                channel,
                reply,
                metadata={"thread_ts": inbound.metadata.get("thread_ts") or
                          inbound.message_id},
            )


__all__ = ["SlackChannel"]
