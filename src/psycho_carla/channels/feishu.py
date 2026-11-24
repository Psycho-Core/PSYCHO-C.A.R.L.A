"""
Feishu / Lark open platform.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import json

from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("feishu")
class FeishuChannel(RestChatChannel):
    """Feishu / Lark open platform."""

    channel_id = "feishu"
    send_path = "/open-apis/im/v1/messages?receive_id_type=chat_id"

    def auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {
            "receive_id": dest,
            "msg_type": "text",
            "content": json.dumps({"text": content}),
        }

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        event = (payload.get("event") or {})
        message = event.get("message") or {}
        content = (message.get("content") or {})
        text = ""
        if isinstance(content, dict):
            text = str(content.get("text", "") or "")
        text = text.strip()
        if not text:
            return None
        inbound = ChannelMessage(
            channel="feishu",
            sender=str((event.get("sender") or {}).get("sender_id", {}).get("open_id", "")),
            content=text,
            message_id=str(message.get("message_id", "")),
            conversation_id=str(message.get("chat_id", "") or ""),
            metadata={},
        )
        return self.deliver(inbound)
