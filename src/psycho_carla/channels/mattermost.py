"""
Mattermost REST API.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("mattermost")
class MattermostChannel(RestChatChannel):
    """Mattermost REST API."""

    channel_id = "mattermost"
    send_path = "/api/v4/posts"

    def auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"channel_id": dest, "message": content}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        post = payload.get("post") or payload
        content = str(post.get("message", "") or "").strip()
        if not content:
            return None
        inbound = ChannelMessage(
            channel="mattermost",
            sender=str(post.get("user_id", "") or ""),
            content=content,
            message_id=str(post.get("id", "")),
            conversation_id=str(post.get("channel_id", "") or ""),
            metadata={},
        )
        return self.deliver(inbound)
