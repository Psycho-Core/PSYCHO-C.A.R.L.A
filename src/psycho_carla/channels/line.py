"""
LINE Messaging API.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("line")
class LineChannel(RestChatChannel):
    """LINE Messaging API."""

    channel_id = "line"
    send_path = "/v2/bot/message/push"

    def auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"to": dest, "messages": [{"type": "text", "text": content}]}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        for event in payload.get("events") or []:
            message = event.get("message") or {}
            content = (message.get("text") or "").strip()
            if not content:
                continue
            source = event.get("source") or {}
            inbound = ChannelMessage(
                channel="line",
                sender=str(source.get("userId", "") or ""),
                content=content,
                message_id=str(message.get("id", "")),
                conversation_id=str(source.get("groupId", "") or source.get("userId", "")),
                metadata={"reply_token": event.get("replyToken", "")},
            )
            reply = self.deliver(inbound)
            if reply:
                return reply
        return None
