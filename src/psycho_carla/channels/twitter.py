"""
X (Twitter) API v2 — posting and reading mentions.

Part of the built-in channel set. See
:mod:`psycho_carla.channels` for the full list.
"""

from __future__ import annotations

import os

from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import (
    ChannelError,
    build_url,
    oauth1_header,
    request_json,
    truncate,
)
from .registry import register_channel


@register_channel("twitter")
class TwitterChannel(BaseChannel):
    """X (Twitter) API v2 — posting and reading mentions.

    Reading uses the bearer token. *Posting* needs OAuth 1.0a user
    context (an API key, secret and access token pair), because the
    bearer token only covers public read endpoints. When the OAuth
    credentials are present the tweet is signed with them; otherwise
    the bearer token is used, which the API will reject for posting but
    keeps the adapter usable for reads.
    """

    channel_id = "twitter"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(
            self.options.get("token", "")
            or os.environ.get("TWITTER_BEARER_TOKEN", "")
        )
        self._user_id: str = str(self.options.get("user_id", "") or "")
        self._api_key: str = str(
            self.options.get("api_key", "") or os.environ.get("TWITTER_API_KEY", "")
        )
        self._api_secret: str = str(
            self.options.get("api_secret", "")
            or os.environ.get("TWITTER_API_SECRET", "")
        )
        self._access_token: str = str(
            self.options.get("access_token", "")
            or os.environ.get("TWITTER_ACCESS_TOKEN", "")
        )
        self._access_secret: str = str(
            self.options.get("access_secret", "")
            or os.environ.get("TWITTER_ACCESS_SECRET", "")
        )
        self._base = str(self.options.get("base_url", "https://api.twitter.com/2")
                         or "https://api.twitter.com/2")
        self._last_error: str = ""

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._token:
            missing.append("token")
        if not self._user_id:
            missing.append("user_id")
        return missing

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Twitter needs a bearer token and user id."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self.set_status(ChannelStatus.DISCONNECTED)

    def _oauth_credentials(self) -> bool:
        """True when the full OAuth 1.0a user-context set is present."""
        return all(
            (
                self._api_key,
                self._api_secret,
                self._access_token,
                self._access_secret,
            )
        )

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        url = build_url(self._base, "/tweets")
        body: Dict[str, Any] = {"text": truncate(content, 280)}
        if conversation_id:
            body["reply"] = {"in_reply_to_tweet_id": conversation_id}

        if self._oauth_credentials():
            headers = oauth1_header(
                "POST",
                url,
                consumer_key=self._api_key,
                consumer_secret=self._api_secret,
                access_token=self._access_token,
                access_secret=self._access_secret,
            )
        else:
            headers = {"Authorization": f"Bearer {self._token}"}

        try:
            request_json(url, method="POST", headers=headers, json_body=body,
                         timeout=30.0)
        except ChannelError as exc:
            self._last_error = str(exc)
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        return [self._user_id] if self._user_id else []

    def handle_event(self, payload: dict[str, Any]) -> Optional[str]:
        text = (payload.get("text") or "").strip()
        if not text:
            return None
        inbound = ChannelMessage(
            channel="twitter",
            sender=str(payload.get("author_id", "") or ""),
            content=text,
            message_id=str(payload.get("id", "")),
            conversation_id=str(payload.get("author_id", "") or ""),
            metadata={},
        )
        return self.deliver(inbound)


# ---------------------------------------------------------------------------
# Mastodon
# ---------------------------------------------------------------------------
