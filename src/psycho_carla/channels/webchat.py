"""
A browser-based chat widget served by C.A.R.L.A.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("webchat")
class WebChatChannel(RestChatChannel):
    """A browser-based chat widget served by C.A.R.L.A. itself.

    Upstream treats webchat as an in-memory channel: sent messages are
    appended to a queue the HUD can read back, rather than being pushed
    anywhere over the network. That queue is kept here too, so the
    behaviour matches.
    """

    channel_id = "webchat"
    requires_auth = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._messages: List[ChannelMessage] = []

    def auth_header(self) -> Dict[str, str]:
        return {}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"text": content}

    # -- send / receive -------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Append a message to the in-memory queue."""
        self._messages.append(
            ChannelMessage(
                channel=channel or self.channel_id,
                sender="carla",
                content=content,
                conversation_id=conversation_id,
                metadata=dict(metadata or {}),
            )
        )
        return True

    def list_channels(self) -> List[str]:
        return ["webchat"]

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        content = str(
            payload.get("text", "") or payload.get("message", "") or ""
        ).strip()
        if not content:
            return None
        sender = str(payload.get("sender", "") or "webchat")
        inbound = ChannelMessage(
            channel="webchat",
            sender=sender,
            content=content,
            conversation_id=sender,
            metadata={},
        )
        self._messages.append(inbound)
        return self.deliver(inbound)

    # -- webchat-specific helpers ---------------------------------------------

    def get_messages(self) -> List[ChannelMessage]:
        """Return all messages stored in the in-memory queue."""
        return self._messages

    def clear_messages(self) -> None:
        """Clear all messages from the in-memory queue."""
        self._messages.clear()


__all__ = ["WebChatChannel"]
