"""
Shared base for REST-API chat platforms.

Several messaging platforms are the same shape: send a JSON POST to a
URL, receive the reply in the response body. Rather than eleven
near-identical modules, they share one adapter parameterised by URL,
auth header and the JSON paths to the message and the reply.

This module holds that shared machinery; each platform lives in its own
module (:mod:`~psycho_carla.channels.line`,
:mod:`~psycho_carla.channels.twilio_sms`, …) and subclasses it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate

#: Generic ceiling for platforms using this base.
MAX_MESSAGE_LENGTH = 4096


class RestChatChannel(BaseChannel):
    """A chat platform reached through a REST endpoint."""

    requires_auth = True

    #: Where to POST a message, relative to ``base_url``.
    send_path: str = ""

    #: Build the request body from (destination, content).
    build_body: Callable[[str, str], Dict[str, Any]] = staticmethod(
        lambda dest, content: {"text": content}
    )

    #: Extra headers for each request.
    auth_header: Callable[["RestChatChannel"], Dict[str, str]] = staticmethod(
        lambda self: {}
    )

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._base: str = str(self.options.get("base_url", "") or "")
        self._token: str = str(self.options.get("token", "") or "")
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._base:
            missing.append("base_url")
        if self.requires_auth and not self._token:
            missing.append("token")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = f"{self.channel_id} is missing configuration."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
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
        try:
            result = request_json(
                build_url(self._base, self.send_path),
                method="POST",
                headers=self.auth_header(self),
                json_body=self.build_body(
                    channel, truncate(content, MAX_MESSAGE_LENGTH)
                ),
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False

        if isinstance(result, dict) and result.get("error"):
            error = result["error"]
            self._last_error = str(
                error.get("message", error) if isinstance(error, dict) else error
            )
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return []

    # -- inbound -----------------------------------------------------------

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        """Handle a platform-specific inbound payload."""
        raise NotImplementedError

    def handle_webhook(self, payload: Dict[str, Any]) -> Optional[str]:
        """Handle an inbound webhook payload.

        Upstream exposes this name on the platforms that receive traffic
        through a webhook (Twilio, SendBlue, …). It is the same hook as
        :meth:`handle_event` — kept as a distinct name for parity, and so
        an adapter can override one without the other.
        """
        return self.handle_event(payload)


__all__ = ["MAX_MESSAGE_LENGTH", "RestChatChannel"]
