"""
Generic webhook channel.

A deliberately simple adapter: point anything at C.A.R.L.A.'s
``/api/channels/webhook`` endpoint with a JSON body, and you get a reply
in the response.

This is the escape hatch — it makes C.A.R.L.A. reachable from anything
that can POST JSON, including Home Assistant, Shortcuts, Zapier, n8n, or
a shell script with ``curl``.

    {"text": "What is the weather?", "sender": "alice"}
"""

from __future__ import annotations

import hmac
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .registry import register_channel

#: Shared secret for signing requests (HMAC-SHA256 of the body).
SIGNATURE_HEADER = "x-carla-signature"


@register_channel("webhook")
class WebhookChannel(BaseChannel):
    """A channel that accepts inbound JSON over HTTP."""

    channel_id = "webhook"
    requires_auth = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._secret: str = str(self.options.get("secret", "") or "")
        self._allowed_senders: list[str] = [
            str(s) for s in (self.options.get("allowed_senders") or []) if s
        ]
        self._prefix: str = str(self.options.get("prefix", "") or "")

    def missing_credentials(self) -> list[str]:
        # The webhook works without a secret — it is just unauthenticated.
        return []

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
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
        """The webhook replies inline, so there is nothing to send."""
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return list(self._allowed_senders)

    # -- inbound -----------------------------------------------------------

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Check an HMAC-SHA256 signature over the raw body."""
        if not self._secret:
            return True
        if not signature:
            return False
        expected = hmac.new(
            self._secret.encode("utf-8"), body, "sha256"
        ).hexdigest()
        return hmac.compare_digest(expected, signature.strip())

    def handle_event(
        self, payload: dict[str, Any], *, signature: str = ""
    ) -> Optional[str]:
        """Handle one inbound webhook payload."""
        if not self.verify_signature(b"", signature) and self._secret:
            # Signature checking is done at the HTTP layer where the raw
            # body is available; this guard covers the direct-call path.
            return None

        sender = str(
            payload.get("sender")
            or payload.get("user")
            or payload.get("from")
            or "webhook"
        )
        if self._allowed_senders and sender not in self._allowed_senders:
            return None

        content = (
            payload.get("text")
            or payload.get("message")
            or payload.get("content")
            or payload.get("prompt")
            or ""
        ).strip()
        if not content:
            return None

        inbound = ChannelMessage(
            channel="webhook",
            sender=sender,
            content=content,
            message_id=str(payload.get("id", "")),
            conversation_id=str(payload.get("conversation_id", "") or sender),
            metadata={k: v for k, v in payload.items()
                      if k not in ("text", "message", "content", "prompt")},
        )
        return self.deliver(inbound)


__all__ = ["WebhookChannel", "SIGNATURE_HEADER"]
