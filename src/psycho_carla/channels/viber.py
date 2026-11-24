"""
Viber Business Messages API.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("viber")
class ViberChannel(RestChatChannel):
    """Viber Business Messages API."""

    channel_id = "viber"
    send_path = "/pa/send_messages"

    def auth_header(self) -> Dict[str, str]:
        return {"X-Viber-Auth-Token": self._token}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"receiver": dest, "type": "text", "text": content}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        message = payload.get("message") or {}
        content = (message.get("text") or "").strip()
        if not content:
            return None
        sender = (payload.get("sender") or {}).get("id", "")
        inbound = ChannelMessage(
            channel="viber", sender=str(sender), content=content,
            conversation_id=str(sender),
            metadata={"token": payload.get("token", "")},
        )
        return self.deliver(inbound)
