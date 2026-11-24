"""
Microsoft Teams channel.

Teams messages arrive through a Bot Framework webhook, and replies go
back through the Bot Framework's ``reply to activity`` endpoint using
the conversation reference that arrived with the activity.

Setup:
1. Register a bot in the Azure Bot Framework portal.
2. Get the Microsoft App ID and password.
3. Point the messaging endpoint at C.A.R.L.A.

    carla channels add teams --app-id <guid> --password <secret>
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel

#: Teams' message limit.
MAX_MESSAGE_LENGTH = 4000


@register_channel("teams")
class TeamsChannel(BaseChannel):
    """Microsoft Teams via the Bot Framework."""

    channel_id = "teams"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._app_id: str = str(self.options.get("app_id", "") or "")
        self._password: str = str(self.options.get("password", "") or "")
        self._base: str = str(
            self.options.get("base_url", "https://api.botframework.com") or
            "https://api.botframework.com"
        )
        self._service_url: str = str(self.options.get("service_url", "") or "")
        self._conversation: Dict[str, Any] = {}
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._app_id:
            missing.append("app_id")
        if not self._password:
            missing.append("password")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Teams needs an app id and password."
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
        service_url = str((metadata or {}).get("service_url", "") or
                          self._service_url)
        conversation = (metadata or {}).get("conversation") or self._conversation
        if not service_url or not conversation:
            self._last_error = "Teams has no conversation to reply to."
            return False

        body = {
            "type": "message",
            "text": truncate(content, MAX_MESSAGE_LENGTH),
        }

        try:
            request_json(
                build_url(
                    service_url,
                    f"/v3/conversations/{conversation.get('id', '')}/activities",
                ),
                method="POST",
                headers={"Content-Type": "application/json"},
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
        conversation_id = str(self._conversation.get("id", "") or "")
        return [conversation_id] if conversation_id else []

    # -- inbound -----------------------------------------------------------

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        """Handle one inbound Bot Framework activity."""
        activity_type = payload.get("type")
        if activity_type == "conversationUpdate":
            # Remember the conversation so we can reply later.
            conversation = payload.get("conversation") or {}
            if conversation.get("id"):
                self._conversation = conversation
            return None

        if activity_type != "message":
            return None

        text = (payload.get("text") or "").strip()
        if not text:
            return None

        conversation = payload.get("conversation") or {}
        conversation_id = str(conversation.get("id", "") or "")
        if conversation_id and not self._conversation:
            self._conversation = conversation

        from_user = payload.get("from") or {}
        inbound = ChannelMessage(
            channel="teams",
            sender=str(from_user.get("id", "") or ""),
            content=text,
            message_id=str(payload.get("id", "") or ""),
            conversation_id=conversation_id,
            metadata={
                "service_url": payload.get("serviceUrl", ""),
                "conversation": conversation,
                "name": from_user.get("name", ""),
            },
        )

        reply = self.deliver(inbound)
        if reply:
            # Reply through the activity endpoint, using the activity id.
            service_url = str(payload.get("serviceUrl", "") or self._service_url)
            try:
                request_json(
                    build_url(service_url,
                              f"/v3/conversations/{conversation_id}/activities/"
                              f"{payload.get('id', '')}"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    json_body={"type": "message",
                               "text": truncate(reply, MAX_MESSAGE_LENGTH)},
                    timeout=30.0,
                )
            except ChannelError as exc:
                self._last_error = str(exc)
                return None
        return None


@register_channel("messenger_channel")
class MessengerChannel(TeamsChannel):
    """Facebook Messenger, which shares the Bot Framework reply shape."""

    channel_id = "messenger_channel"

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        service_url = str((metadata or {}).get("service_url", "") or
                          self._service_url)
        if not service_url:
            self._last_error = "Messenger has no service URL to reply to."
            return False
        try:
            request_json(
                build_url(service_url,
                          f"/v3/conversations/{channel}/activities"),
                method="POST",
                headers={"Content-Type": "application/json"},
                json_body={"type": "message",
                           "text": truncate(content, MAX_MESSAGE_LENGTH)},
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False
        return True

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        message = payload.get("message") or {}
        text = (message.get("text") or payload.get("text") or "").strip()
        if not text:
            return None
        sender = payload.get("sender") or {}
        inbound = ChannelMessage(
            channel="messenger_channel",
            sender=str(sender.get("id", "") or ""),
            content=text,
            message_id=str(message.get("mid", "") or ""),
            conversation_id=str(sender.get("id", "") or ""),
            metadata={"service_url": payload.get("serviceUrl", "")},
        )
        reply = self.deliver(inbound)
        if reply:
            self.send(inbound.sender, reply,
                      metadata={"service_url": payload.get("serviceUrl", "")})
        return None


__all__ = ["MessengerChannel", "TeamsChannel"]
