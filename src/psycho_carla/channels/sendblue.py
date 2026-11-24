"""
SendBlue iMessage/SMS bridge.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import ChannelMessage
from .registry import register_channel
from .rest_base import RestChatChannel


@register_channel("sendblue")
class SendBlueChannel(RestChatChannel):
    """SendBlue iMessage/SMS bridge.

    Credentials may be supplied through the channel options or, as
    upstream does, through the environment:

    ``SENDBLUE_API_KEY_ID``, ``SENDBLUE_API_SECRET_KEY``,
    ``SENDBLUE_FROM_NUMBER``.
    """

    channel_id = "sendblue"
    send_path = "/api/v1/messages/send"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._api_key_id: str = str(
            self.options.get("api_key_id", "")
            or os.environ.get("SENDBLUE_API_KEY_ID", "")
        )
        self._api_secret_key: str = str(
            self.options.get("api_secret_key", "")
            or os.environ.get("SENDBLUE_API_SECRET_KEY", "")
        )
        self._from_number: str = str(
            self.options.get("from_number", "")
            or os.environ.get("SENDBLUE_FROM_NUMBER", "")
        )
        self._webhook_secret: str = str(
            self.options.get("webhook_secret", "") or ""
        )

    # -- credentials ---------------------------------------------------------

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._api_key_id:
            missing.append("api_key_id")
        if not self._api_secret_key:
            missing.append("api_secret_key")
        if not self._from_number:
            missing.append("from_number")
        return missing

    def auth_header(self) -> Dict[str, str]:
        return {"x-api-key": self._api_key_id or self._token}

    def build_body(self, dest: str, content: str) -> Dict[str, Any]:
        return {
            "content": content,
            "to": dest,
            "number": self._from_number,
            "platform": str(self.options.get("platform", "imessage")),
            "messageType": "individual",
        }

    # -- inbound -------------------------------------------------------------

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        """Process an incoming SendBlue webhook payload.

        Expected fields: ``from_number``, ``content``, ``to_number``,
        ``message_handle``, ``is_outbound``, ``status``, ``service``.
        Outbound status callbacks are ignored.
        """
        if payload.get("is_outbound", False):
            return None

        content = str(payload.get("content", "") or "").strip()
        if not content:
            return None

        sender = str(
            payload.get("from_number", "") or payload.get("from", "") or ""
        )
        if not sender:
            return None

        inbound = ChannelMessage(
            channel="sendblue",
            sender=sender,
            content=content,
            message_id=str(payload.get("message_handle", "") or ""),
            conversation_id=sender,
            metadata={
                "number": payload.get("number", ""),
                "service": payload.get("service", ""),
            },
        )
        return self.deliver(inbound)

    # -- properties ----------------------------------------------------------

    @property
    def from_number(self) -> str:
        """The SendBlue phone number this channel sends from."""
        return self._from_number

    @property
    def api_key_id(self) -> str:
        return self._api_key_id

    @property
    def api_secret_key(self) -> str:
        return self._api_secret_key

    @property
    def webhook_secret(self) -> str:
        return self._webhook_secret


__all__ = ["SendBlueChannel"]
