"""
WhatsApp channel (Meta Cloud API).

Meta's Cloud API is webhook-driven: you register a callback URL and
Meta POSTs every inbound message to it. The response you send back
within the timeout is delivered as the reply — so this adapter's
``send()`` is really "return this text from the webhook handler".

Setup:
1. Create a Meta developer app, add the WhatsApp product.
2. Get a phone number id and a permanent access token.
3. Point the webhook at your C.A.R.L.A. instance (a tunnel works).

    carla channels add whatsapp --token <token> --phone-number-id <id>
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel

#: WhatsApp's text body limit.
MAX_MESSAGE_LENGTH = 4096


@register_channel("whatsapp")
class WhatsAppChannel(BaseChannel):
    """WhatsApp Cloud API adapter."""

    channel_id = "whatsapp"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(self.options.get("token", "") or "")
        self._phone_id: str = str(self.options.get("phone_number_id", "") or "")
        self._api = str(
            self.options.get("api_base", "https://graph.facebook.com/v21.0")
            or "https://graph.facebook.com/v21.0"
        )
        self._verify_token: str = str(self.options.get("verify_token", "") or "")
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._token:
            missing.append("token")
        if not self._phone_id:
            missing.append("phone_number_id")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "WhatsApp token and phone_number_id are required."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self.set_status(ChannelStatus.DISCONNECTED)

    # -- messaging ---------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "to": channel,
            "type": "text",
            "text": {"body": truncate(content, MAX_MESSAGE_LENGTH)},
        }
        try:
            result = request_json(
                build_url(self._api, f"/{self._phone_id}/messages"),
                method="POST",
                headers={"Authorization": f"Bearer {self._token}"},
                json_body=payload,
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False

        if isinstance(result, dict) and result.get("error"):
            self._last_error = str(result["error"].get("message", "send failed."))
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return []

    # -- inbound -----------------------------------------------------------

    def verify(self, mode: str, token: str, challenge: str) -> Optional[str]:
        """Answer Meta's webhook verification handshake."""
        if self._verify_token and token == self._verify_token and mode == "subscribe":
            return challenge
        return None

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        """Handle one inbound WhatsApp payload."""
        try:
            entry = (payload.get("entry") or [])[0]
            change = (entry.get("changes") or [])[0]
            value = change.get("value") or {}
            messages = value.get("messages") or []
        except (IndexError, TypeError):
            return None

        for message in messages:
            text = (
                (message.get("text") or {}).get("body", "")
                or (message.get("button") or {}).get("text", "")
                or (message.get("interactive") or {}).get("button_reply", {}).get(
                    "title", ""
                )
            ).strip()
            if not text:
                continue

            sender = str((message.get("from") or ""))
            inbound = ChannelMessage(
                channel="whatsapp",
                sender=sender,
                content=text,
                message_id=str(message.get("id", "")),
                conversation_id=sender,
                metadata={"phone_number_id": self._phone_id},
            )
            reply = self.deliver(inbound)
            if reply:
                return reply
        return None


@register_channel("whatsapp_baileys")
class WhatsAppBaileysChannel(WhatsAppChannel):
    """Upstream-compatible alias for the Baileys bridge flavour."""

    channel_id = "whatsapp_baileys"


__all__ = ["WhatsAppChannel", "WhatsAppBaileysChannel"]
