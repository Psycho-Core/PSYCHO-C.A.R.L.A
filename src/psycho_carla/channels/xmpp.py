"""
XMPP, via the slixmpp library.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations


from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel


@register_channel("xmpp")
class XMPPChannel(BaseChannel):
    """XMPP via the ``slixmpp`` library."""

    channel_id = "xmpp"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._jid: str = str(self.options.get("jid", "") or "")
        self._password: str = str(self.options.get("password", "") or "")
        self._server: str = str(self.options.get("server", "") or "")
        self._contact: str = str(self.options.get("contact", "") or "")
        self._client: Any = None
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._jid:
            missing.append("jid")
        if not self._password:
            missing.append("password")
        return missing

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "XMPP needs a JID and password."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        try:
            import slixmpp  # type: ignore
        except ImportError:
            self._last_error = "XMPP needs the 'slixmpp' package."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self._client = slixmpp.ClientXMPP(self._jid, self._password)
        if self._server:
            self._client.connect(self._server)
        else:
            self._client.connect()

        @self._client.add_event_handler("message")
        def _on_message(msg: Any) -> None:
            content = str(getattr(msg, "body", "") or "").strip()
            if not content:
                return
            inbound = ChannelMessage(
                channel="xmpp",
                sender=str(getattr(msg, "from", "") or ""),
                content=content,
                conversation_id=str(getattr(msg, "to", "") or ""),
                metadata={},
            )
            reply = self.deliver(inbound)
            if reply:
                self.send(self._contact or inbound.sender, reply)

        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self.set_status(ChannelStatus.DISCONNECTED)

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        if self._client is None:
            return False
        try:
            self._client.send_message(mto=channel, mbody=content, mtype="chat")
        except Exception as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return [self._contact] if self._contact else []


# ---------------------------------------------------------------------------
# iMessage daemon (macOS only)
# ---------------------------------------------------------------------------
