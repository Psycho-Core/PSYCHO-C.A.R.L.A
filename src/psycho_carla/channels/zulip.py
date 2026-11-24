"""
Zulip messages API.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("zulip")
class ZulipChannel(RestChatChannel):
    """Zulip messages API."""

    channel_id = "zulip"
    send_path = "/api/v1/messages"

    def auth_header(self) -> Dict[str, str]:
        import base64

        user = str(self.options.get("user", "") or "")
        raw = f"{user}:{self._token}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"type": "stream", "to": dest, "content": content}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        content = str(payload.get("content", "") or "").strip()
        if not content:
            return None
        inbound = ChannelMessage(
            channel="zulip",
            sender=str((payload.get("sender") or {}).get("email", "")
                       if isinstance(payload.get("sender"), dict)
                       else payload.get("sender", "")),
            content=content,
            message_id=str(payload.get("id", "")),
            conversation_id=str(payload.get("stream", "") or ""),
            metadata={},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# LINE / Viber / Feishu
# ---------------------------------------------------------------------------
