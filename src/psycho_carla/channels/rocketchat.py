"""
Rocket.Chat REST API.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("rocketchat")
class RocketChatChannel(RestChatChannel):
    """Rocket.Chat REST API."""

    channel_id = "rocketchat"
    send_path = "/api/v1/chat.postMessage"

    def auth_header(self) -> Dict[str, str]:
        return {"X-Auth-Token": self._token,
                "X-User-Id": str(self.options.get("user_id", "") or "")}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"channel": dest, "text": content, "asUser": True}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        message = payload.get("messages") or payload.get("message") or []
        if isinstance(message, dict):
            message = [message]
        for item in message:
            content = str(item.get("msg", "") or item.get("text", "") or "").strip()
            if not content:
                continue
            inbound = ChannelMessage(
                channel="rocketchat",
                sender=str(item.get("u", {}).get("id", "") if isinstance(item.get("u"), dict)
                           else item.get("u", "")),
                content=content,
                message_id=str(item.get("_id", "")),
                conversation_id=str(item.get("rid", "") or item.get("channel", "")),
                metadata={},
            )
            reply = self.deliver(inbound)
            if reply:
                return reply
        return None
