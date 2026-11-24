"""
The Gmail API — for OAuth rather than a password.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations


from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json
from .registry import register_channel


@register_channel("gmail")
class GmailChannel(BaseChannel):
    """The Gmail API — used when you have OAuth rather than a password."""

    channel_id = "gmail"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(self.options.get("token", "") or "")
        self._base = str(self.options.get("base_url", "https://gmail.googleapis.com")
                         or "https://gmail.googleapis.com")
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        return ["token"] if not self._token else []

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Gmail needs an OAuth access token."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self.set_status(ChannelStatus.DISCONNECTED)

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        raw = (
            f"To: {channel}\r\n"
            f"Subject: {str((metadata or {}).get('subject', 'C.A.R.L.A.'))}\r\n\r\n"
            f"{content}"
        )
        import base64 as _b64

        body = _b64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        try:
            request_json(
                build_url(self._base, "/gmail/v1/users/me/messages/send"),
                method="POST",
                headers={"Authorization": f"Bearer {self._token}"},
                json_body={"raw": body},
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return []

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        content = str(payload.get("text", "") or payload.get("body", "") or
                      "").strip()
        if not content:
            return None
        sender = str(payload.get("from", "") or "")
        inbound = ChannelMessage(
            channel="gmail", sender=sender, content=content,
            conversation_id=sender,
            metadata={"thread_id": payload.get("thread_id", "")},
        )
        return self.deliver(inbound)
