"""
Google Chat via an incoming webhook.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import ChannelMessage
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("google_chat")
class GoogleChatChannel(RestChatChannel):
    """Google Chat via an incoming webhook URL."""

    channel_id = "google_chat"
    requires_auth = False

    def auth_header(self) -> Dict[str, str]:
        return {}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"text": content}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        message = payload.get("message") or {}
        content = (message.get("text") or "").strip()
        if not content:
            return None
        sender = (payload.get("sender") or {}).get("displayName", "")
        inbound = ChannelMessage(
            channel="google_chat",
            sender=str(sender),
            content=content,
            conversation_id=str(payload.get("space", "") or ""),
            metadata={},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# SendBlue / BlueBubbles — iMessage
# ---------------------------------------------------------------------------
