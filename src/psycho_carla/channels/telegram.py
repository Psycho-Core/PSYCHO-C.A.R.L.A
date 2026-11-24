"""
Telegram channel.

Uses the Bot API's ``getUpdates`` long-polling — no inbound webhook, no
public URL, nothing to configure on a server. That makes it the easiest
real channel to get running locally, which is why it leads the set.

Setup:
1. Talk to ``@BotFather`` and create a bot. You get a token like
   ``123456789:AAExample...``.
2. ``carla channels add telegram --token <token>``
   (the token goes to the encrypted vault, never the config file)

Then message your bot. C.A.R.L.A. replies in the same chat.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from .base import BaseChannel, ChannelMessage, ChannelStatus
from .httpclient import ChannelError, build_url, request_json, truncate
from .registry import register_channel

#: Telegram's message limit for bots.
MAX_MESSAGE_LENGTH = 4096

#: Long-poll timeout, in seconds. Telegram caps this at 50.
POLL_TIMEOUT = 30


@register_channel("telegram")
class TelegramChannel(BaseChannel):
    """Telegram Bot API adapter."""

    channel_id = "telegram"
    requires_auth = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._token: str = str(self.options.get("token", "") or "")
        self._base = str(
            self.options.get("base_url", "https://api.telegram.org") or
            "https://api.telegram.org"
        )
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._offset: int = 0
        self._known_chats: set[str] = set()
        self._last_error = ""

    # -- configuration -----------------------------------------------------

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self._token:
            missing.append("token")
        return missing

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        if not self.is_configured():
            self._last_error = "Telegram token is not configured."
            self.set_status(ChannelStatus.UNCONFIGURED)
            return

        self.set_status(ChannelStatus.CONNECTING)
        try:
            me = request_json(
                build_url(self._base, f"/bot{self._token}/getMe"),
                timeout=15.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            self.set_status(ChannelStatus.ERROR)
            return

        if not me.get("ok", False):
            self._last_error = str(me.get("description", "Telegram rejected the token."))
            self.set_status(ChannelStatus.ERROR)
            return

        bot = me.get("result", {})
        self.options["username"] = bot.get("username", "")
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="carla-telegram", daemon=True
        )
        self._poll_thread.start()
        self.set_status(ChannelStatus.CONNECTED)

    def disconnect(self) -> None:
        self._stop.set()
        thread = self._poll_thread
        self._poll_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
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
        if not self._token:
            return False

        payload: Dict[str, Any] = {
            "chat_id": channel,
            "text": truncate(content, MAX_MESSAGE_LENGTH),
            "disable_web_page_preview": True,
        }

        # Reply threading — Telegram wants the id of the message replied to.
        reply_to = (metadata or {}).get("reply_to_message_id") or conversation_id
        if reply_to:
            try:
                payload["reply_to_message_id"] = int(reply_to)
            except (TypeError, ValueError):
                pass

        try:
            result = request_json(
                build_url(self._base, f"/bot{self._token}/sendMessage"),
                method="POST",
                json_body=payload,
                timeout=30.0,
            )
        except ChannelError as exc:
            self._last_error = str(exc)
            return False

        if not result.get("ok", False):
            self._last_error = str(result.get("description", "sendMessage failed."))
            return False
        return True

    def status(self) -> ChannelStatus:
        return self._status

    def list_channels(self) -> List[str]:
        """The chat ids this bot has seen."""
        return sorted(self._known_chats)

    # -- polling -----------------------------------------------------------

    def _poll_loop(self) -> None:
        """Long-poll for updates until disconnected."""
        while not self._stop.is_set():
            try:
                updates = self._fetch_updates()
            except ChannelError as exc:
                self._last_error = str(exc)
                # A transient network failure should not kill the channel.
                self._stop.wait(5.0)
                continue

            for update in updates:
                self._handle_update(update)

            # Respect the stop flag between polls.
            self._stop.wait(0.5)

    def _fetch_updates(self) -> list[dict[str, Any]]:
        url = build_url(
            self._base,
            f"/bot{self._token}/getUpdates",
            {
                "timeout": POLL_TIMEOUT,
                "offset": self._offset or None,
                "allowed_updates": json.dumps(["message"]),
            },
        )
        data = request_json(url, timeout=POLL_TIMEOUT + 10.0)
        if not data.get("ok", False):
            raise ChannelError(str(data.get("description", "getUpdates failed.")))

        result = data.get("result") or []
        if result:
            last_id = result[-1].get("update_id", 0)
            self._offset = int(last_id) + 1
        return list(result)

    def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        self._known_chats.add(chat_id)

        sender = message.get("from") or {}
        inbound = ChannelMessage(
            channel="telegram",
            sender=str(sender.get("id", chat_id)),
            content=text,
            message_id=str(message.get("message_id", "")),
            conversation_id=chat_id,
            metadata={
                "username": sender.get("username", ""),
                "chat_title": chat.get("title", ""),
                "first_name": sender.get("first_name", ""),
                "reply_to_message_id": message.get(
                    "reply_to_message", {}
                ).get("message_id"),
            },
        )

        reply = self.deliver(inbound)
        if reply:
            self.send(
                chat_id,
                reply,
                metadata={"reply_to_message_id": inbound.message_id},
            )


__all__ = ["TelegramChannel"]
