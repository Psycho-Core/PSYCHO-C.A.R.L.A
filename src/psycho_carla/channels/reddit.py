"""
Reddit — posting to subreddits and replying to comments.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import base64
import json

from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel


@register_channel("reddit")
class RedditChannel(BaseChannel):
    """Reddit — posting to subreddits and replying to comments."""

    channel_id = "reddit"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._client_id: str = str(self.options.get("client_id", "") or "")
        self._client_secret: str = str(self.options.get("client_secret", "") or "")
        self._refresh_token: str = str(self.options.get("refresh_token", "") or "")
        self._username: str = str(self.options.get("username", "") or "")
        self._password: str = str(self.options.get("password", "") or "")
        self._base = str(self.options.get("base_url", "https://oauth.reddit.com")
                         or "https://oauth.reddit.com")
        self._api = str(self.options.get("api_base", "https://www.reddit.com")
                        or "https://www.reddit.com")
        self._token: str = ""
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._client_id:
            missing.append("client_id")
        if not (self._refresh_token or (self._username and self._password)):
            missing.append("refresh_token or username+password")
        return missing

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Reddit needs a client id and credentials."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        if not self._access_token():
            self.set_status(ChannelStatus.ERROR)
            return
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self._token = ""
        self.set_status(ChannelStatus.DISCONNECTED)

    def _access_token(self) -> bool:
        try:
            if self._refresh_token:
                result = request_json(
                    build_url(self._api, "/api/v1/access_token"),
                    method="POST",
                    headers={"Authorization": "Basic " + base64.b64encode(
                        f"{self._client_id}:{self._client_secret}".encode()
                    ).decode()},
                    data=b"grant_type=refresh_token&refresh_token="
                         + self._refresh_token.encode(),
                    timeout=30.0,
                )
            else:
                result = request_json(
                    build_url(self._api, "/api/v1/access_token"),
                    method="POST",
                    headers={"Authorization": "Basic " + base64.b64encode(
                        f"{self._client_id}:{self._client_secret}".encode()
                    ).decode()},
                    data=f"grant_type=password&username={self._username}"
                         f"&password={self._password}".encode(),
                    timeout=30.0,
                )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False
        self._token = str(result.get("access_token", "") or "")
        if not self._token:
            self._last_error = "Reddit did not return an access token."
            return False
        return True

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        body: Dict[str, Any] = {
            "api_type": "json",
            "text": truncate(content, 30000),
        }
        if conversation_id:
            body["thing_id"] = conversation_id
            body["kind"] = "comment"
        else:
            body["sr"] = channel
            body["kind"] = "self"

        try:
            request_json(
                build_url(self._base, "/api/submit"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": str(self.options.get("user_agent",
                                                      "python:psycho-carla:1.0")),
                },
                data=json.dumps(body).encode(),
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
        data = payload.get("data") or {}
        content = (data.get("body") or "").strip()
        if not content:
            return None
        inbound = ChannelMessage(
            channel="reddit",
            sender=str(data.get("author", "") or ""),
            content=content,
            message_id=str(data.get("id", "") or ""),
            conversation_id=str(data.get("name", "") or ""),
            metadata={"subreddit": (data.get("subreddit") or "")},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# Nostr
# ---------------------------------------------------------------------------
