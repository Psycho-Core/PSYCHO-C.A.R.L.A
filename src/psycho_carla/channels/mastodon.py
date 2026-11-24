"""
Mastodon — posting to and reading mentions on any instance.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations


from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel


@register_channel("mastodon")
class MastodonChannel(BaseChannel):
    """Mastodon — posting to and reading mentions on any instance."""

    channel_id = "mastodon"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(self.options.get("token", "") or "")
        self._base: str = str(self.options.get("base_url", "") or "")
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._base:
            missing.append("base_url")
        if not self._token:
            missing.append("token")
        return missing

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Mastodon needs an instance URL and access token."
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
        body: Dict[str, Any] = {"status": truncate(content, 500)}
        if conversation_id:
            body["in_reply_to_id"] = conversation_id
        try:
            request_json(
                build_url(self._base, "/api/v1/statuses"),
                method="POST",
                headers={"Authorization": f"Bearer {self._token}"},
                json_body=body,
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
        content = (payload.get("content") or "").strip()
        if not content:
            return None
        inbound = ChannelMessage(
            channel="mastodon",
            sender=str((payload.get("account") or {}).get("acct", "")),
            content=content,
            message_id=str(payload.get("id", "")),
            conversation_id=str(payload.get("uri", "") or ""),
            metadata={},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
