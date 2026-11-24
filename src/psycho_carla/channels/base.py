"""
Channel abstraction — the contract every messaging adapter implements.

A *channel* is a way for C.A.R.L.A. to be reached, and a way for it to
reply. Each adapter is a thin, well-behaved translation layer between one
messaging platform and the :class:`ChannelBridge`.

The design mirrors upstream OpenJarvis deliberately: the platform-specific
complexity (long-polling vs webhooks vs SMTP) lives inside the adapter,
while everything above it — session mapping, command routing, agent
dispatch — is platform-agnostic.

The contract is small and absolute:

* ``connect()`` / ``disconnect()`` — lifecycle.
* ``send()`` — deliver a message to a destination.
* ``status()`` — are we connected?
* ``list_channels()`` — which destinations do we know about?
* ``on_message()`` — register the callback for inbound traffic.

Layer: Pillar 3 (Channels).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class ChannelStatus(str, Enum):
    """Connection state of a channel adapter."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    ERROR = "error"
    #: Configured, but no credentials were supplied.
    UNCONFIGURED = "unconfigured"


@dataclass(slots=True)
class ChannelMessage:
    """A message received from, or destined for, a channel."""

    channel: str
    """The channel *type* label — ``"telegram"``, ``"slack"``…"""

    sender: str
    """Who sent it. Platform-native user id."""

    content: str
    """The body of the message."""

    message_id: str = ""
    """Platform-native id of this message, used for reply threading."""

    conversation_id: str = ""
    """The *destination* this message belongs to — a chat id, channel id,
    email thread. This is what :meth:`BaseChannel.send` expects as its
    ``channel`` argument when replying."""

    session_id: str = ""
    """The C.A.R.L.A. session this message maps to."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Anything platform-specific the adapter wants to carry along."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "sender": self.sender,
            "content": self.content,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "session_id": self.session_id,
            "metadata": dict(self.metadata),
        }


#: Signature for inbound message callbacks. Return a string to reply.
ChannelHandler = Callable[[ChannelMessage], Optional[str]]


class BaseChannel(ABC):
    """Base class for every channel adapter.

    Subclasses register themselves with :func:`register_channel` so the
    registry can discover them.
    """

    #: The registry key, e.g. ``"telegram"``.
    channel_id: str = ""

    #: Whether this adapter needs credentials to function.
    requires_auth: bool = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = dict(options or {})
        self._handler: Optional[ChannelHandler] = None
        self._status = ChannelStatus.DISCONNECTED

    # -- lifecycle ---------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Establish the connection to the platform.

        Must be idempotent and must not raise on ordinary failure — set
        :attr:`status` to ``ERROR`` and record the reason instead.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Tear the connection down. Must be idempotent."""

    # -- messaging ---------------------------------------------------------

    @abstractmethod
    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a message. Returns True on success.

        ``channel`` is the **destination** identifier — the native id of
        the place the message goes (a Telegram chat id, a Slack channel
        id, an email address). It is *not* the channel type label. An
        inbound :class:`ChannelMessage` carries that destination in its
        ``conversation_id``.

        ``conversation_id`` is the reply reference — the native id of the
        message being replied to (a Slack ``thread_ts``, a Telegram
        ``reply_to_message_id``…). When replying to an inbound message,
        pass ``cm.message_id`` here.
        """

    @abstractmethod
    def status(self) -> ChannelStatus:
        """The current connection status."""

    @abstractmethod
    def list_channels(self) -> List[str]:
        """The destinations this adapter knows about."""

    def on_message(self, handler: ChannelHandler) -> None:
        """Register the callback invoked for inbound messages."""
        self._handler = handler

    # -- shared helpers ----------------------------------------------------

    def deliver(self, message: ChannelMessage) -> Optional[str]:
        """Route an inbound message to the registered handler.

        Adapters call this when they receive traffic. Returns whatever the
        handler returned, so the adapter can send the reply itself.
        """
        if self._handler is None:
            return None
        try:
            return self._handler(message)
        except Exception:
            # A failing handler must never take the channel down.
            return None

    def set_status(self, status: ChannelStatus) -> None:
        self._status = status

    def is_connected(self) -> bool:
        return self._status == ChannelStatus.CONNECTED

    # -- configuration -----------------------------------------------------

    def missing_credentials(self) -> list[str]:
        """Which required options are absent.

        Adapters override this; the bridge uses it to report
        ``UNCONFIGURED`` rather than failing on connect.
        """
        return []

    def is_configured(self) -> bool:
        """Is this adapter ready to connect?"""
        return not self.missing_credentials()

    def describe(self) -> dict[str, Any]:
        """A JSON-friendly summary, for the HUD and the CLI."""
        return {
            "channel_id": self.channel_id,
            "status": self.status().value,
            "requires_auth": self.requires_auth,
            "configured": self.is_configured(),
            "missing": self.missing_credentials(),
            "destinations": self.list_channels(),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.channel_id} {self.status().value}>"


__all__ = [
    "BaseChannel",
    "ChannelHandler",
    "ChannelMessage",
    "ChannelStatus",
]
