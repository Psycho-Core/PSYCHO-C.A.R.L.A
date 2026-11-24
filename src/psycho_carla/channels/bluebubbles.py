"""
BlueBubbles server for iMessage.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import ChannelMessage
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("bluebubbles")
class BlueBubblesChannel(RestChatChannel):
    """BlueBubbles server for iMessage."""

    channel_id = "bluebubbles"
    send_path = "/api/v1/message"

    def auth_header(self) -> Dict[str, str]:
        password = str(self.options.get("password", "") or "")
        if not password:
            return {}
        return {"Authorization": f"Bearer {password}"}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"chatGuid": dest, "text": content, "tempGuid": ""}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        content = str(payload.get("text", "") or "").strip()
        if not content:
            return None
        sender = str(payload.get("handle", "") or "")
        inbound = ChannelMessage(
            channel="bluebubbles", sender=sender, content=content,
            conversation_id=str(payload.get("chatGuid", "") or sender),
            metadata={},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# Webchat — a built-in browser widget
# ---------------------------------------------------------------------------
