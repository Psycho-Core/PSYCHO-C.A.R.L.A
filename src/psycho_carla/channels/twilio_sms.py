"""
Twilio Messages API.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import ChannelMessage
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("twilio_sms")
class TwilioSMSChannel(RestChatChannel):
    """Twilio Messages API."""

    channel_id = "twilio_sms"
    send_path = "/Messages.json"

    def auth_header(self) -> Dict[str, str]:
        from .httpclient import basic

        sid = str(self.options.get("account_sid", "") or "")
        token = str(self.options.get("token", "") or "")
        if sid and token:
            return basic(sid, token)
        return {}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {"To": dest, "From": str(self.options.get("from_number", "") or ""),
                "Body": content}

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        body = str(payload.get("Body", "") or "").strip()
        if not body:
            return None
        sender = str(payload.get("From", "") or "")
        inbound = ChannelMessage(
            channel="twilio_sms", sender=sender, content=body,
            conversation_id=sender, metadata={"sid": payload.get("MessageSid", "")},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# Mattermost / Rocket.Chat / Zulip
# ---------------------------------------------------------------------------
