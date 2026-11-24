"""
Discord channel.

Discord delivers inbound events by POSTing to a webhook-style URL you
expose, but it also supports a gateway connection. This adapter uses the
**HTTP interaction endpoint** model — a plain POST endpoint you point
Discord at (via a tunnel, or a local reverse proxy).

The alternative, the gateway, needs a privileged bot intent and is more
involved; the HTTP model keeps the adapter simple and testable.

Setup:
1. Create a bot at discord.com/developers/applications.
2. Copy the bot token.
3. Copy the channel ID you want it to listen in (enable Developer Mode
   in Discord settings to see IDs).

    carla channels add discord --token <token> --channel <id>
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel

#: Discord's message limit.
MAX_MESSAGE_LENGTH = 2000


@register_channel("discord")
class DiscordChannel(BaseChannel):
    """Discord adapter driven by an inbound HTTP endpoint."""

    channel_id = "discord"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(self.options.get("token", "") or "")
        self._base = str(self.options.get("base_url", "https://discord.com") or
                         "https://discord.com")
        self._api = str(self.options.get("api_base", "https://discord.com/api/v10")
                        or "https://discord.com/api/v10")
        self._channel_ids: list[str] = [
            str(c) for c in (self.options.get("channels") or []) if c
        ]
        if self.options.get("channel"):
            self._channel_ids.append(str(self.options["channel"]))
        self._bot_user_id: str = ""
        self._last_error: str = ""
        self._connected = False

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._token:
            missing.append("token")
        if not self._channel_ids:
            missing.append("channel")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self._token:
            self._last_error = "Discord token is not configured."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self.set_status(ChannelStatus.CONNECTING)
        try:
            me = request_json(
                build_url(self._api, "/users/@me"),
                headers={"Authorization": f"Bot {self._token}"},
                timeout=15.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            self.set_status(ChannelStatus.ERROR)
            return

        if not isinstance(me, dict) or not me.get("id"):
            self._last_error = str(
                me.get("message", "Discord rejected the token.") if isinstance(me, dict)
                else "Discord rejected the token."
            )
            self.set_status(ChannelStatus.ERROR)
            return

        self._bot_user_id = str(me.get("id", ""))
        self._connected = True
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self._connected = False
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
        payload: Dict[str, Any] = {
            "content": truncate(content, MAX_MESSAGE_LENGTH),
        }

        # Reply references use message_reference with the message id.
        reference = (metadata or {}).get("reply_to_message_id") or conversation_id
        if reference:
            payload["message_reference"] = {"message_id": str(reference)}

        try:
            request_json(
                build_url(self._api, f"/channels/{channel}/messages"),
                method="POST",
                headers={"Authorization": f"Bot {self._token}"},
                json_body=payload,
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return list(self._channel_ids)

    # -- inbound -----------------------------------------------------------

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        """Handle one inbound Discord event payload.

        The HTTP endpoint calls this with the parsed request body. Returns
        the reply text if there is one.
        """
        # Discord sends a PING to verify the endpoint.
        if int(payload.get("type", 0) or 0) == 1:
            return None

        author = payload.get("author") or {}
        if author.get("bot"):
            return None
        if self._bot_user_id and str(author.get("id", "")) == self._bot_user_id:
            return None

        content = (payload.get("content") or "").strip()
        if not content:
            return None

        channel = str(payload.get("channel_id", ""))
        if self._channel_ids and channel not in self._channel_ids:
            return None

        inbound = ChannelMessage(
            channel="discord",
            sender=str(author.get("id", "")),
            content=content,
            message_id=str(payload.get("id", "")),
            conversation_id=channel,
            metadata={
                "guild_id": payload.get("guild_id", ""),
                "username": author.get("username", ""),
                "bot_id": author.get("bot", False),
            },
        )

        return self.deliver(inbound)


@register_channel("discord_channel")
class DiscordChannelAlias(DiscordChannel):
    """Upstream-compatible alias for :class:`DiscordChannel`."""

    channel_id = "discord_channel"


__all__ = ["DiscordChannel", "DiscordChannelAlias"]
